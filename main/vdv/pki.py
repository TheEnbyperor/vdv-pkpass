import dataclasses
import typing
import pathlib
import ber_tlv.tlv
import hashlib
import string
import django.core.files.storage

from . import iso9796, util

ROOT = pathlib.Path(__file__).parent
SHA1 = [1, 3, 14, 3, 2, 26]
RSA_ENCRYPTION = [1, 2, 840, 113549, 1, 1, 1]
SHA1_WITH_RSA_SIGNATURE = [1, 2, 840, 113549, 1, 1, 5]
TELETRUST_ISO9796_2_WITH_SHA1_AND_RSA = [1, 3, 36, 3, 4, 2, 2, 1]
KNOWN_OIDS = (
    RSA_ENCRYPTION,
    SHA1_WITH_RSA_SIGNATURE,
    TELETRUST_ISO9796_2_WITH_SHA1_AND_RSA
)


@dataclasses.dataclass
class CAReference:
    name: bytes
    service_indicator: int
    algorithm_reference: int
    year: int

    def __str__(self):
        if name := self.ascii_name():
            region, name = name
            return (f"region={region}, name={name}, "
                    f"service_indicator={self.service_indicator}, algorithm_reference={self.algorithm_reference}, "
                    f"year={self.year}")
        else:
            name = self.hex_name()
            return f"raw_name={name}"

    def ascii_name(self):
        try:
            name = self.name.decode("ascii")
            if any(c not in string.printable for c in name):
                return None
            return name[0:2], name[2:]
        except UnicodeDecodeError:
            return None

    def hex_name(self):
        full_name = self.name + bytes([self.service_indicator, self.algorithm_reference, self.year - 1990])
        return ":".join(f"{full_name[i]:02x}" for i in range(len(full_name)))

    @classmethod
    def from_bytes(cls, data: bytes) -> "CAReference":
        if len(data) != 8:
            raise ValueError("Invalid CA reference length")
        return cls(
            name=data[0:5],
            service_indicator=data[5],
            algorithm_reference=data[6],
            year=1990 + data[7]
        )

    @classmethod
    def root(cls):
        return cls(b"EUVDV", 16, 1, 1996)


@dataclasses.dataclass
class RawCertificate:
    filename: str
    ca_reference: CAReference
    data: bytes

class CertificateStore:
    certificates: typing.List[RawCertificate]

    def __init__(self):
        self.certificates = []

    def load_certificates(self):
        certificates = []
        certificate_storage = django.core.files.storage.storages["vdv-certs"]
        for filename in certificate_storage.listdir("")[1]:
            if not filename.endswith(".der"):
                continue
            with certificate_storage.open(filename, "rb") as f:
                data = f.read()
            try:
                car_bytes = bytes.fromhex(filename[:-4])
            except ValueError:
                continue
            certificates.append(RawCertificate(
                filename=filename,
                ca_reference=CAReference.from_bytes(car_bytes),
                data=data
            ))
        self.certificates = certificates

    def find_certificate(self, ca_reference: CAReference) -> typing.Optional[RawCertificate]:
        for certificate in self.certificates:
            if certificate.ca_reference.name == ca_reference.name and \
                    certificate.ca_reference.service_indicator == ca_reference.service_indicator and \
                    certificate.ca_reference.algorithm_reference == ca_reference.algorithm_reference and \
                    certificate.ca_reference.year == ca_reference.year:
                return certificate
        return None


@dataclasses.dataclass
class Certificate:
    content: typing.Optional[bytes]
    signature: bytes
    signature_residual: typing.Optional[bytes]

    @classmethod
    def parse(cls, raw_cert: RawCertificate):
        try:
            elms = ber_tlv.tlv.Tlv.Parser.parse(raw_cert.data, True, [], False, 0)
        except Exception as e:
            raise util.VDVException("Failed to parse certificate") from e

        certificate = None

        for tag, data in elms:
            if tag == util.TAG_CERTIFICATE:
                certificate = data
            else:
                raise util.VDVException(f"Unknown tag: {hex(tag)}; likely not a certificate")

        if not certificate:
            raise util.VDVException("No certificate present")

        return cls.parse_tags(certificate)

    @classmethod
    def parse_tags(cls, certificate: typing.List[typing.Tuple[int, typing.Any]]):
        certificate_content = None
        certificate_signature = None
        certificate_signature_remainder = None

        for tag, data in certificate:
            if tag == util.TAG_CERTIFICATE_CONTENT:
                certificate_content = data
            elif tag == util.TAG_CERTIFICATE_SIGNATURE:
                certificate_signature = data
            elif tag == util.TAG_CERTIFICATE_SIGNATURE_REMAINDER:
                certificate_signature_remainder = data
            else:
                raise util.VDVException(f"Unknown tag: {hex(tag)}")

        if not certificate_signature:
            raise util.VDVException("No certificate signature")

        if not certificate_content:
            if not certificate_signature_remainder:
                raise util.VDVException("No certificate content")

        return cls(
            certificate_content,
            certificate_signature,
            certificate_signature_remainder
        )

    def needs_ca_key(self):
        return self.signature_residual is not None and self.content is None

    def decrypt_with_ca_key(self, ca: "CertificateData"):
        self.content = iso9796.decrypt_with_cert(self.signature, self.signature_residual, ca)

    def verify_signature(self, ca: "CertificateData"):
        assert self.content is not None
        assert isinstance(ca.public_key, RSAPublicKey)

        h = int.from_bytes(self.signature, 'big')
        m = pow(h, ca.public_key.exponent, ca.public_key.modulus)
        data = m.to_bytes(ca.public_key.modulus_len, 'big')

        if data[0:2] != b'\x00\x01':
            raise util.VDVException("Invalid message padding - signature verification failed")
        offset = 2
        while data[offset] == 0xff:
            offset += 1
        if data[offset] != 0:
            raise util.VDVException("Invalid message padding - signature verification failed")
        data = data[offset + 1:]

        data = ber_tlv.tlv.Tlv.Parser.parse(data, True, [], False, 0)
        if len(data) != 1:
            raise util.VDVException("Invalid message structure - signature verification failed")
        if data[0][0] != util.TAG_SEQUENCE:
            raise util.VDVException("Invalid message structure - signature verification failed")
        algorithm, signature = data[0][1][0], data[0][1][1]

        if algorithm[0] != util.TAG_SEQUENCE:
            raise util.VDVException("Invalid message structure - signature verification failed")
        if algorithm[1][0][0] != util.TAG_OID:
            raise util.VDVException("Invalid message structure - signature verification failed")

        signature_oid = decode_oid(algorithm[1][0][1])
        if signature_oid != SHA1:
            raise util.VDVException("Invalid signature algorithm - signature verification failed")

        if len(algorithm[1]) != 2:
            raise util.VDVException("Invalid message structure - signature verification failed")
        if algorithm[1][1][0] != util.TAG_NULL:
            raise util.VDVException("Invalid message structure - signature verification failed")

        if signature[0] != util.TAG_OCTET_STRING:
            raise util.VDVException("Invalid message structure - signature verification failed")
        signature = signature[1]

        if signature != hashlib.sha1(self.content).digest():
            raise util.VDVException("Invalid signature - signature verification failed")


@dataclasses.dataclass
class CertificateHolderAuthorization:
    name: str
    service_indicator: int

    def __str__(self):
        return f"{self.name}:{self.service_indicator}"


@dataclasses.dataclass
class RSAPublicKey:
    modulus: int
    modulus_len: int
    exponent: int

    def __str__(self):
        return f"n={self.format_int(self.modulus, self.modulus_len)}, " + \
                f"e={self.exponent}"

    @staticmethod
    def format_int(value: int, length: int) -> str:
        val = value.to_bytes(length, 'big')
        return ":".join(f"{val[i]:02x}" for i in range(length))

    @classmethod
    def from_bytes(cls, data: bytes, certificate_profile_identifier: int):
        if certificate_profile_identifier == 3:
            modulus_len = 1536 // 8
        elif certificate_profile_identifier == 4:
            modulus_len = 1024 // 8
        elif certificate_profile_identifier == 7:
            modulus_len = 1984 // 8
        else:
            raise util.VDVException("Unknown certificate profile identifier")

        return cls(
            modulus=int.from_bytes(data[0:modulus_len], 'big'),
            modulus_len=modulus_len,
            exponent=int.from_bytes(data[modulus_len:], 'big')
        )


def read_oid_component(int_bytes):
    ret = 0
    i = 0
    while int_bytes[i] & 0x80:
        num = int_bytes[i] & 0x7f
        if not ret and not num:
            raise util.VDVException("Leading 0x80 octets in the encoding of an OID component")
        ret |= num
        ret <<= 7
        i += 1

    ret |= int_bytes[i]
    return ret, i + 1

def decode_oid(data: bytes):
    components = []
    oid_offset = 0

    first, num = read_oid_component(data[oid_offset:])
    oid_offset += num
    if first < 40:
        components += [0, first]
    elif first < 80:
        components += [1, first - 40]
    else:
        components += [2, first - 80]

    while data[oid_offset:]:
        component, num = read_oid_component(data[oid_offset:])
        oid_offset += num
        components.append(component)

    return components


@dataclasses.dataclass
class CertificateData:
    certificate_profile_identifier: int
    ca_reference: CAReference
    certificate_holder_reference: CAReference
    certificate_holder_authorization: CertificateHolderAuthorization
    expiry_date: util.Date
    public_key: RSAPublicKey

    def __str__(self):
        return "Certificate:\n" + \
                f"  Certificate Profile Identifier: {self.certificate_profile_identifier}\n" + \
                f"  CA Reference: {self.ca_reference}\n" + \
                f"  Certificate Holder Reference: {self.certificate_holder_reference}\n" + \
                f"  Certificate Holder Authorization: {self.certificate_holder_authorization}\n" + \
                f"  Expiry Date: {self.expiry_date}\n" + \
                f"  Public Key: {self.public_key}"

    @classmethod
    def parse(cls, data: Certificate) -> "CertificateData":
        assert not data.needs_ca_key()

        oid_offset = 32
        components = []

        first, num = read_oid_component(data.content[oid_offset:])
        oid_offset += num
        if first < 40:
            components += [0, first]
        elif first < 80:
            components += [1, first - 40]
        else:
            components += [2, first - 80]

        while data.content[oid_offset:]:
            component, num = read_oid_component(data.content[oid_offset:])
            oid_offset += num
            components.append(component)

            if components in KNOWN_OIDS:
                break

        if components not in (SHA1_WITH_RSA_SIGNATURE, TELETRUST_ISO9796_2_WITH_SHA1_AND_RSA):
            raise util.VDVException("Unknown public key OID")

        return cls(
            certificate_profile_identifier=data.content[0],
            ca_reference=CAReference.from_bytes(data.content[1:9]),
            certificate_holder_reference=CAReference.from_bytes(data.content[13:21]),
            certificate_holder_authorization=CertificateHolderAuthorization(
                name=data.content[21:27].decode("ascii"),
                service_indicator=data.content[27]
            ),
            expiry_date=util.Date.from_bytes(data.content[28:32]),
            public_key=RSAPublicKey.from_bytes(data.content[oid_offset:], data.content[0])
        )
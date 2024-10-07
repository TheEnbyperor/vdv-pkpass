import dataclasses
import ber_tlv.tlv

from . import pki, util, iso9796


@dataclasses.dataclass
class EnvelopeV2:
    signature: bytes
    residual_data: bytes

    certificate: pki.Certificate
    ca_reference: pki.CAReference

    def decrypt_with_cert(self, cert: pki.CertificateData) -> bytes:
        return iso9796.decrypt_with_cert(self.signature, self.residual_data, cert)

    @classmethod
    def parse(cls, data: bytes) -> "EnvelopeV2":
        try:
            elms = ber_tlv.tlv.Tlv.Parser.parse(data, True, [], False, 0)
        except Exception as e:
            raise util.VDVException("Failed to parse envelope, invalid BER-TLV") from e

        signature = None
        residual_data = None
        certificate = None
        ca_reference = None

        for tag, data in elms:
            if tag == util.TAG_SIGNATURE:
                if len(data) != 128:
                    raise util.VDVException("Invalid signature length")
                if signature:
                    raise util.VDVException("Multiple signatures")
                signature = data

            elif tag == util.REMAINING_DATA:
                if residual_data:
                    raise util.VDVException("Multiple residual signature data")
                residual_data = data

            elif tag == util.TAG_CERTIFICATE:
                certificate = pki.Certificate.parse_tags(data)

            elif tag == util.TAG_CA_REFERENCE:
                if len(data) != 8:
                    raise util.VDVException("Invalid certification authority reference length")

                if ca_reference:
                    raise util.VDVException("Multiple certification authority references")

                ca_reference = pki.CAReference.from_bytes(data)
            else:
                raise util.VDVException(f"Unknown tag: 0x{tag:02X}")

        if not signature:
            raise util.VDVException("No signature")
        if not residual_data:
            raise util.VDVException("No residual signature data")
        if not certificate:
            raise util.VDVException("No CV certificate")
        if not ca_reference:
            raise util.VDVException("No certification authority reference")

        return cls(
            signature=signature,
            residual_data=residual_data,
            certificate=certificate,
            ca_reference=ca_reference,
        )
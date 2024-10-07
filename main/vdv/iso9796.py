import hashlib
from . import util, pki


def decrypt_with_cert(signature: bytes, signature_residual: bytes, ca: "pki.CertificateData") -> bytes:
    assert isinstance(ca.public_key, pki.RSAPublicKey)

    h = int.from_bytes(signature, 'big')
    m = pow(h, ca.public_key.exponent, ca.public_key.modulus)
    data = m.to_bytes(ca.public_key.modulus_len, 'big')

    if data[0] != 0x6A:
        raise util.VDVException("Invalid message header - signature verification failed")
    if data[-1] != 0xBC:
        raise util.VDVException("Invalid message trailer - signature verification failed")

    data = data[1:-1]
    if len(data) < 20:
        raise util.VDVException("Invalid message length - signature verification failed")

    message_part1, message_hash = data[:-20], data[-20:]

    message = message_part1 + signature_residual

    if hashlib.sha1(message).digest() != message_hash:
        raise util.VDVException("Invalid message hash - signature verification failed")

    return message
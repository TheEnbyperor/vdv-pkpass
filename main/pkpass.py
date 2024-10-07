import hashlib
import json
import zipfile
import io
import cryptography.hazmat.primitives.hashes
import cryptography.hazmat.primitives.serialization.pkcs7
from django.conf import settings

class PKPass:
    def __init__(self):
        self.manifest = {}
        self.zip_buffer = io.BytesIO()
        self.zip = zipfile.ZipFile(self.zip_buffer, "w")

    def add_file(self, filename: str, data: bytes):
        file_hash = hashlib.sha1(data).hexdigest()
        self.zip.writestr(filename, data)
        self.manifest[filename] = file_hash

    def sign(self):
        manifest = json.dumps(self.manifest).encode("utf-8")
        self.zip.writestr("manifest.json", manifest)
        signature = cryptography.hazmat.primitives.serialization.pkcs7.PKCS7SignatureBuilder()\
                .set_data(manifest)\
                .add_signer(
                    settings.PKPASS_CERTIFICATE, settings.PKPASS_KEY,
                    cryptography.hazmat.primitives.hashes.SHA256()
                )\
                .add_certificate(settings.WWDR_CERTIFICATE)\
                .sign(cryptography.hazmat.primitives.serialization.Encoding.DER, [
                    cryptography.hazmat.primitives.serialization.pkcs7.PKCS7Options.Binary,
                    cryptography.hazmat.primitives.serialization.pkcs7.PKCS7Options.DetachedSignature,
                ])
        self.zip.writestr("signature", signature)

    def get_buffer(self) -> bytes:
        self.zip.close()
        return self.zip_buffer.getvalue()
from django.core.management.base import BaseCommand
import django.core.files.storage
import ldap


class Command(BaseCommand):
    help = "Download VDV certificates from LDAP"

    def handle(self, *args, **options):
        certificate_storage = django.core.files.storage.storages["vdv-certs"]

        conn = ldap.initialize("ldap://ldap-vdv-ion.telesec.de:389")

        conn.search("ou=VDV KA,o=VDV Kernapplikations GmbH,c=de", ldap.SCOPE_SUBTREE, "(objectClass=*)", attrlist=["cn", "caCertificate"])
        certs = conn.result()[1]

        for cert in certs:
            attrs = cert[1]
            if "cACertificate" not in attrs:
                continue
            cert_data = attrs["cACertificate"][0]
            common_name = attrs["cn"][0].decode("ascii")

            with certificate_storage.open(f"{common_name}.der", "wb") as f:
                f.write(cert_data)
            print(f"Downloaded {common_name}")
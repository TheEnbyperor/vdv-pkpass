import base64
import secrets
import dacite
from django.db import models
from . import ticket as t
from . import vdv


def make_pass_token():
    return secrets.token_urlsafe(32)


class Ticket(models.Model):
    TYPE_DEUTCHLANDTICKET = "deutschlandticket"
    TYPE_UNKNOWN = "unknown"

    TICKET_TYPES = (
        (TYPE_DEUTCHLANDTICKET, "Deutschlandticket"),
        (TYPE_UNKNOWN, "Unknown"),
    )

    id = models.CharField(max_length=32, primary_key=True, verbose_name="ID")
    ticket_type = models.CharField(max_length=255, choices=TICKET_TYPES, verbose_name="Ticket type", default=TYPE_UNKNOWN)
    pkpass_authentication_token = models.CharField(max_length=255, verbose_name="Pass authentication token", default=make_pass_token)


class TicketInstance(models.Model):
    ticket = models.ForeignKey(Ticket, on_delete=models.CASCADE, related_name="instances")
    ticket_number = models.PositiveIntegerField(verbose_name="Ticket number")
    ticket_org_id = models.PositiveIntegerField(verbose_name="Organization ID")
    validity_start = models.DateTimeField()
    validity_end = models.DateTimeField()
    barcode_data = models.BinaryField()
    decoded_data = models.JSONField()

    class Meta:
        unique_together = [
            ["ticket_number", "ticket_org_id"],
        ]
        ordering = ["-validity_start"]

    def as_ticket(self) -> t.Ticket:
        config = dacite.Config(type_hooks={bytes: base64.b64decode})
        raw_ticket = base64.b64decode(self.decoded_data["ticket"])
        return t.Ticket(
            root_ca=dacite.from_dict(data_class=vdv.CertificateData, data=self.decoded_data["root_ca"], config=config),
            issuing_ca=dacite.from_dict(data_class=vdv.CertificateData, data=self.decoded_data["issuing_ca"], config=config),
            envelope_certificate=dacite.from_dict(data_class=vdv.CertificateData, data=self.decoded_data["envelope_certificate"], config=config),
            raw_ticket=raw_ticket,
            ticket=vdv.VDVTicket.parse(raw_ticket)
        )
import base64
import secrets
import dacite
import datetime
from django.utils import timezone
from django.shortcuts import reverse
from django.conf import settings
from django.db import models
from django.core import validators
from django.db.models.signals import post_save
from django.dispatch import receiver
from . import ticket as t
from . import vdv, uic


def make_pass_token():
    return secrets.token_urlsafe(32)


class Account(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    db_token = models.TextField(null=True, blank=True, verbose_name="Deutsche Bahn Bearer token")
    db_token_expires_at = models.DateTimeField(blank=True, null=True, verbose_name="Deutsche Bahn Bearer token expiration")
    db_refresh_token = models.TextField(null=True, blank=True, verbose_name="Deutsche Bahn refresh token")
    db_refresh_token_expires_at = models.DateTimeField(blank=True, null=True, verbose_name="Deutsche Bahn refresh token expiration")
    db_account_id = models.CharField(max_length=255, null=True, blank=True, verbose_name="Deutsche Bahn Account ID")
    saarvv_token = models.TextField(null=True, blank=True, verbose_name="SaarVV Token")
    saarvv_device_id = models.CharField(max_length=255, null=True, blank=True, verbose_name="SaarVV Device ID")

    def __str__(self):
        return str(self.user)

    def is_db_authenticated(self) -> bool:
        now = timezone.now()
        if self.db_token and self.db_token_expires_at and self.db_token_expires_at > now:
            return True
        elif self.db_refresh_token and self.db_refresh_token_expires_at and self.db_refresh_token_expires_at > now:
            return True
        else:
            return False

    def is_saarvv_authenticated(self) -> bool:
        return bool(self.saarvv_token)


@receiver(post_save, sender=settings.AUTH_USER_MODEL)
def create_user_profile(instance, created, **kwargs):
    if created or not hasattr(instance, "account"):
        Account.objects.create(user=instance)
    instance.account.save()


class Ticket(models.Model):
    TYPE_DEUTCHLANDTICKET = "deutschlandticket"
    TYPE_KLIMATICKET = "klimaticket"
    TYPE_BAHNCARD = "bahncard"
    TYPE_FAHRKARTE = "fahrkarte"
    TYPE_RESERVIERUNG = "reservierung"
    TYPE_INTERRAIL = "interrail"
    TYPE_UNKNOWN = "unknown"

    TICKET_TYPES = (
        (TYPE_DEUTCHLANDTICKET, "Deutschlandticket"),
        (TYPE_KLIMATICKET, "Klimaticket"),
        (TYPE_BAHNCARD, "Bahncard"),
        (TYPE_FAHRKARTE, "Fahrkarte"),
        (TYPE_RESERVIERUNG, "Reservierung"),
        (TYPE_INTERRAIL, "Interrail"),
        (TYPE_UNKNOWN, "Unknown"),
    )

    id = models.CharField(max_length=32, primary_key=True, verbose_name="ID")
    ticket_type = models.CharField(max_length=255, choices=TICKET_TYPES, verbose_name="Ticket type", default=TYPE_UNKNOWN)
    pkpass_authentication_token = models.CharField(max_length=255, verbose_name="PKPass authentication token", default=make_pass_token)
    last_updated = models.DateTimeField()
    account = models.ForeignKey(Account, on_delete=models.SET_NULL, null=True, blank=True, related_name="tickets")
    db_subscription = models.ForeignKey(
        "DBSubscription", on_delete=models.SET_NULL, null=True, blank=True, related_name="tickets", verbose_name="DB Subscription"
    )
    saarvv_account = models.ForeignKey(
        "Account", on_delete=models.SET_NULL, null=True, blank=True, related_name="saarvv_tickets"
    )

    def __str__(self):
        return f"{self.get_ticket_type_display()} - {self.id}"

    def get_absolute_url(self):
        return reverse("ticket", kwargs={"pk": self.id})

    def public_id(self):
        return self.pk.upper()[0:8]


class VDVTicketInstance(models.Model):
    ticket = models.ForeignKey(Ticket, on_delete=models.CASCADE, related_name="vdv_instances")
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
        verbose_name = "VDV ticket"

    def __str__(self):
        return f"{self.ticket_org_id} - {self.ticket_number}"

    def as_ticket(self) -> t.VDVTicket:
        config = dacite.Config(type_hooks={bytes: base64.b64decode})
        raw_ticket = base64.b64decode(self.decoded_data["ticket"])
        return t.VDVTicket(
            root_ca=dacite.from_dict(data_class=vdv.CertificateData, data=self.decoded_data["root_ca"], config=config),
            issuing_ca=dacite.from_dict(data_class=vdv.CertificateData, data=self.decoded_data["issuing_ca"], config=config),
            envelope_certificate=dacite.from_dict(data_class=vdv.CertificateData, data=self.decoded_data["envelope_certificate"], config=config),
            raw_ticket=raw_ticket,
            ticket=vdv.VDVTicket.parse(raw_ticket, vdv.ticket.Context(
                account_forename=self.ticket.account.user.first_name if self.ticket.account else None,
                account_surname=self.ticket.account.user.last_name if self.ticket.account else None,
            ))
        )


class UICTicketInstance(models.Model):
    ticket = models.ForeignKey(Ticket, on_delete=models.CASCADE, related_name="uic_instances")
    reference = models.CharField(max_length=20, verbose_name="Ticket ID")
    distributor_rics = models.PositiveIntegerField(validators=[validators.MaxValueValidator(9999)], verbose_name="Distributor RICS")
    issuing_time = models.DateTimeField()
    barcode_data = models.BinaryField()
    decoded_data = models.JSONField()
    validity_start = models.DateTimeField(blank=True, null=True)
    validity_end = models.DateTimeField(blank=True, null=True)

    class Meta:
        unique_together = [
            ["reference", "distributor_rics"],
        ]
        ordering = ["-issuing_time"]
        verbose_name = "UIC ticket"

    def __str__(self):
        return f"{self.distributor_rics} - {self.reference}"

    def as_ticket(self) -> t.UICTicket:
        config = dacite.Config(type_hooks={bytes: base64.b64decode})
        ticket_envelope = dacite.from_dict(data_class=uic.Envelope, data=self.decoded_data["envelope"], config=config)
        return t.UICTicket(
            raw_bytes=self.barcode_data,
            envelope=ticket_envelope,
            head=t.parse_ticket_uic_head(ticket_envelope),
            layout=t.parse_ticket_uic_layout(ticket_envelope),
            flex=t.parse_ticket_uic_flex(ticket_envelope),
            db_bl=t.parse_ticket_uic_db_bl(ticket_envelope),
            cd_ut=t.parse_ticket_uic_cd_ut(ticket_envelope),
            oebb_99=t.parse_ticket_uic_oebb_99(ticket_envelope),
            other_records=[r for r in ticket_envelope.records if not (
                    r.id.startswith("U_") or r.id == "0080BL" or r.id == "1154UT" or r.id == "118199"
            )]
        )


class AppleDevice(models.Model):
    device_id = models.CharField(max_length=255, primary_key=True, verbose_name="Device ID")
    push_token = models.CharField(max_length=255, verbose_name="Push token")

    def __str__(self):
        return self.device_id

    def accounts(self):
        accounts = []
        for reg in self.registrations.all():
            if reg.ticket.account_id:
                accounts.append(reg.ticket.account_id)
        return accounts


class AppleRegistration(models.Model):
    ticket = models.ForeignKey(Ticket, on_delete=models.CASCADE, related_name="apple_registrations")
    device = models.ForeignKey(AppleDevice, on_delete=models.CASCADE, related_name="registrations")

    class Meta:
        unique_together = [
            ["ticket", "device"],
        ]


class DBSubscription(models.Model):
    account = models.ForeignKey(Account, on_delete=models.CASCADE, related_name="subscriptions")
    device_token = models.CharField(max_length=255, verbose_name="Device token", unique=True)
    refresh_at = models.DateTimeField(verbose_name="Refresh at")
    info = models.JSONField(verbose_name="Info", default=dict)

    class Meta:
        verbose_name = "DB Subscription"
        verbose_name_plural = "DB Subscriptions"

    def __str__(self):
        return str(self.device_token)

    def get_current_info(self):
        if "type" not in self.info:
            return None

        if self.info["type"] == "VendoHuelle":
            return self.info
        elif self.info["type"] == "TicketHuelle":
            now = timezone.now()
            for info in self.info["ticketHuellen"]:
                start = datetime.datetime.fromisoformat(info["anzeigeAb"])
                end = datetime.datetime.fromisoformat(info["anzeigeBis"])
                if start > now and end < now:
                    return info["huelleInfo"]
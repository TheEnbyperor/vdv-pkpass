import niquests
from django.conf import settings
from django.db.models import Q
from django.utils import timezone
from . import models

def notify_device(device: "models.AppleDevice"):
    r = niquests.post(f"https://api.push.apple.com/3/device/{device.push_token}", headers={
        "apns-push-type": "alert",
        "apns-priority": "10"
    }, json={
        "aps": {
            "content-available": 1
        }
    }, cert=(str(settings.PKPASS_CERTIFICATE_LOCATION), str(settings.PKPASS_KEY_LOCATION)))
    r.raise_for_status()


def notify_ticket(ticket: "models.Ticket"):
    for registration in ticket.apple_registrations.all():
        notify_device(registration.device)


def notify_ticket_if_renewed(ticket: "models.Ticket"):
    now = timezone.now()
    current_ticket_valid_from = None
    previous_ticket_valid_until = None
    uic_tickets = ticket.uic_instances.filter(
        Q(validity_start__lt=now) | Q(validity_start__isnull=True)
    )
    if uic_tickets.count() != 0:
        if uic_tickets.count() > 1:
            current_ticket_valid_from = uic_tickets[0].validity_start
            previous_ticket_valid_until = uic_tickets[1].validity_end

    vdv_tickets = ticket.vdv_instances.filter(
        Q(validity_start__lt=now) | Q(validity_start__isnull=True)
    )
    if vdv_tickets.count() != 0:
        if vdv_tickets.count() > 1:
            current_ticket_valid_from = vdv_tickets[0].validity_start
            previous_ticket_valid_until = vdv_tickets[1].validity_end

    if current_ticket_valid_from and previous_ticket_valid_until:
        if current_ticket_valid_from < now and previous_ticket_valid_until < now:
            ticket.last_updated = now
            ticket.save()
            notify_ticket(ticket)
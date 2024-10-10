import niquests
from django.conf import settings
from . import models

def notify_device(device: models.AppleDevice):
    r = niquests.post(f"https://api.push.apple.com/3/device/{device.push_token}", headers={
        "apns-push-type": "alert",
        "apns-priority": "10"
    }, json={
        "aps": {
            "content-available": 1
        }
    }, cert=(str(settings.PKPASS_CERTIFICATE_LOCATION), str(settings.PKPASS_KEY_LOCATION)))
    r.raise_for_status()

def notify_ticket(ticket: models.Ticket):
    for registration in ticket.apple_registrations.all():
        notify_device(registration.device)
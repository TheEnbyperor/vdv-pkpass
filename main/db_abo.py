import base64
import json
import niquests
import datetime
import logging
import bs4
from django.utils import timezone
from . import models, aztec, ticket, apn

logger = logging.getLogger(__name__)


def update_all():
    now = timezone.now()
    for abo in models.DBSubscription.objects.all():
        if abo.refresh_at <= now:
            update_abo_tickets(abo)


def update_abo_tickets(abo: models):
    r = niquests.post("https://dig-aboprod.noncd.db.de/aboticket/refreshmultiple", json={
        "aboTicketCheckRequestList": [{
            "deviceToken": abo.device_token,
        }]
    }, headers={
        "X-User-Agent": "com.deutschebahn.abo.navigatorV2.modul"
    })
    r.raise_for_status()
    data = r.json()

    if len(data) == 0:
        abo.delete()
        return

    tickets = data[0]
    abo.refresh_at = datetime.datetime.fromisoformat(tickets['refreshDatum'])
    abo.info = tickets["ticketHuelle"]
    abo.save()

    for t in tickets["tickets"]:
        ticket_data = base64.urlsafe_b64decode(t["payload"] + '==')
        ticket_data = json.loads(ticket_data.decode('utf-8'))
        ticket_layout = bs4.BeautifulSoup(ticket_data["ticketLayoutTemplate"], 'html.parser')
        barcode_elm = ticket_layout.find("nativeimg", attrs={
            "id": "ticketbarcode"
        }, recursive=True)
        if not barcode_elm:
            logger.error("Could not find barcode element")
            continue
        barcode_img = barcode_elm.attrs["src"]
        if not barcode_img.startswith("data:"):
            logger.error("Barcode image not a data URL")
            continue
        media_type, data = barcode_img[5:].split(";", 1)
        encoding, data = data.split(",", 1)
        if not media_type.startswith("image/"):
            logger.error("Unsupported media type '%s'", media_type)
            continue
        if encoding != "base64":
            logger.error("Unsupported encoding type '%s' in barcode image", encoding)
            continue
        barcode_img_data = base64.urlsafe_b64decode(data)
        try:
            barcode_data = aztec.decode(barcode_img_data)
        except aztec.AztecError as e:
            logger.error("Error decoding barcode image: %s", e)
            continue

        try:
            decoded_ticket = ticket.parse_ticket(barcode_data)
        except ticket.TicketError as e:
            logger.error("Error decoding barcode ticket: %s", e)
            continue

        should_update = False
        ticket_pk = decoded_ticket.pk()
        ticket_obj = models.Ticket.objects.filter(pk=ticket_pk).first()
        if not ticket_obj:
            should_update = True
            ticket_obj = models.Ticket.objects.create(
                pk=ticket_pk,
                last_updated=timezone.now(),
                account=abo.account,
            )

        ticket_obj.ticket_type = decoded_ticket.type()
        ticket_obj.db_subscription = abo
        if ticket.create_ticket_obj(ticket_obj, barcode_data, decoded_ticket):
            should_update = True

        if should_update:
            ticket_obj.last_updated = timezone.now()

        ticket_obj.save()

        if should_update:
            apn.notify_ticket(ticket_obj)
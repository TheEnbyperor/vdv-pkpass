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
        else:
            logging.info(f"Not updating DB subscription {abo.device_token} - not due for refresh")

        for t in abo.tickets.all():
            apn.notify_ticket_if_renewed(t)


def update_abo_tickets(abo: models.DBSubscription):
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
        if "barcode" in ticket_data and ticket_data["barcode"]:
            barcode_url = ticket_data["barcode"]
        else:
            ticket_layout = bs4.BeautifulSoup(ticket_data["ticketLayoutTemplate"], 'html.parser')
            barcode_elm = ticket_layout.find("nativeimg", attrs={
                "id": "ticketbarcode"
            }, recursive=True)
            if not barcode_elm:
                logger.error("Could not find barcode element")
                continue
            barcode_url = barcode_elm.attrs["src"]

        if not barcode_url.startswith("data:"):
            logger.error("Barcode image not a data URL")
            continue
        media_type, data = barcode_url[5:].split(";", 1)
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
            ticket_obj = ticket.update_from_subscription_barcode(barcode_data, account=abo.account)
            ticket_obj.db_subscription = abo
            ticket_obj.save()
        except ticket.TicketError as e:
            logger.error("Error decoding barcode ticket: %s", e)
            continue

    logging.info(f"Successfully updated DB subscription {abo.device_token}")
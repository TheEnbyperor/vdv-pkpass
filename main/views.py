import base64
import json
import typing
import pytz
import zxing
import PIL.Image
import dataclasses

from django.shortcuts import render, redirect, get_object_or_404
from django.http import HttpResponse
from django.core.files.storage import storages
from django.conf import settings
from . import forms, models, ticket, pkpass, vdv


def to_dict_json(elements: typing.List[typing.Tuple[str, typing.Any]]) -> dict:
    def encode_value(v):
        if isinstance(v, bytes):
            return base64.b64encode(v).decode("ascii")
        else:
            return v
    return {k: encode_value(v) for k, v in elements}

def index(request):
    ticket_bytes = None
    error = None

    if request.method == "POST":
        if request.POST.get("type") == "scan":
            try:
                ticket_bytes = bytes.fromhex(request.POST.get("ticket_hex"))
            except ValueError:
                pass

            image_form = forms.TicketImageForm()
        else:
            image_form = forms.TicketImageForm(request.POST, request.FILES)
            if image_form.is_valid():
                reader = zxing.BarCodeReader()
                image = image_form.cleaned_data["ticket"]
                if image.size > 2 * 1024 * 1024:
                    image_form.add_error(None, "The image must be less than 2MB")
                else:
                    image = PIL.Image.open(image)
                    barcode = reader.decode(image)
                    if barcode.raw:
                        ticket_bytes = bytes([ord(c) for c in barcode.raw])
                    else:
                        image_form.add_error(None, "No barcode was found in the image")
    else:
        image_form = forms.TicketImageForm()

    if ticket_bytes:
        try:
            ticket_data = ticket.parse_ticket(ticket_bytes)
        except ticket.TicketError as e:
            error = {
                "title": e.title,
                "message": e.message,
                "exception": e.exception,
                "ticket_contents": ticket_bytes.hex()
            }
        else:
            ticket_pk = ticket_data.pk()
            ticket_obj, _ = models.Ticket.objects.get_or_create(id=ticket_pk, defaults={
                "ticket_type": ticket_data.type()
            })
            ticket_obj.instances.get_or_create(
                ticket_number=ticket_data.ticket.ticket_id,
                ticket_org_id=ticket_data.ticket.ticket_org_id,
                defaults={
                    "validity_start": ticket_data.ticket.validity_start.as_datetime(),
                    "validity_end": ticket_data.ticket.validity_end.as_datetime(),
                    "barcode_data": ticket_bytes,
                    "decoded_data": {
                        "root_ca": dataclasses.asdict(ticket_data.root_ca, dict_factory=to_dict_json),
                        "issuing_ca": dataclasses.asdict(ticket_data.issuing_ca, dict_factory=to_dict_json),
                        "envelope_certificate": dataclasses.asdict(ticket_data.envelope_certificate, dict_factory=to_dict_json),
                        "ticket": base64.b64encode(ticket_data.raw_ticket).decode("ascii"),
                    }
                }
            )
            return redirect('ticket', pk=ticket_obj.id)

    return render(request, "main/index.html", {
        "image_form": image_form,
        "error": error,
    })


def view_ticket(request, pk):
    ticket_obj = get_object_or_404(models.Ticket, id=pk)
    ticket_id = ticket_obj.pk.upper()[0:8]
    return render(request, "main/ticket.html", {
        "ticket": ticket_obj,
        "ticket_id": ticket_id,
    })


def ticket_pkpass(request, pk):
    ticket_obj: models.Ticket = get_object_or_404(models.Ticket, id=pk)
    ticket_instance: models.TicketInstance = ticket_obj.instances.first()
    ticket_data = ticket_instance.as_ticket()

    tz = pytz.timezone("Europe/Berlin")
    validity_start = tz.localize(ticket_data.ticket.validity_start.as_datetime())\
        .astimezone(pytz.utc)
    validity_end = tz.localize(ticket_data.ticket.validity_end.as_datetime())\
        .astimezone(pytz.utc)
    issued_at = tz.localize(ticket_data.ticket.transaction_time.as_datetime())\
        .astimezone(pytz.utc)

    pass_json = {
        "formatVersion": 1,
        "organizationName": settings.PKPASS_CONF["organization_name"],
        "passTypeIdentifier": settings.PKPASS_CONF["pass_type"],
        "teamIdentifier": settings.PKPASS_CONF["team_id"],
        "serialNumber": ticket_obj.pk,
        # "authenticationToken": ticket_obj.pkpass_authentication_token,
        # "webServiceURL": "https://vdv-pass.magicalcodewit.ch/api/apple/",
        "sharingProhibited": True,
        "expirationDate": validity_end.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "backgroundColor": "rgb(255, 255, 255)",
        "foregroundColor": "rgb(0, 0, 0)",
        "generic": {
            "headerFields": [{
                "key": "product",
                "label": "Product",
                "value": ticket_data.ticket.product_name()
            }],
            "primaryFields": [],
            "secondaryFields": [{
                "key": "validity-start",
                "label": "Valid from",
                "dateStyle": "PKDateStyleMedium",
                "value": validity_start.strftime("%Y-%m-%dT%H:%M:%SZ"),
            }, {
                "key": "validity-end",
                "label": "Valid until",
                "dateStyle": "PKDateStyleMedium",
                "value": validity_end.strftime("%Y-%m-%dT%H:%M:%SZ"),
            }],
            "backFields": [{
                "key": "validity-start-back",
                "label": "Valid from",
                "dateStyle": "PKDateStyleMedium",
                "timeStyle": "PKDateStyleFull",
                "value": validity_start.strftime("%Y-%m-%dT%H:%M:%SZ"),
            }, {
                "key": "validity-end-back",
                "label": "Valid until",
                "dateStyle": "PKDateStyleFull",
                "timeStyle": "PKDateStyleFull",
                "value": validity_end.strftime("%Y-%m-%dT%H:%M:%SZ"),
            }, {
                "key": "product-back",
                "label": "Product",
                "value": ticket_data.ticket.product_name()
            }, {
                "key": "product-org-back",
                "label": "Product Organisation",
                "value": ticket_data.ticket.product_org_name()
            }, {
                "key": "ticket-id",
                "label": "Ticket ID",
                "value": str(ticket_data.ticket.ticket_id),
            }, {
                "key": "ticket-org",
                "label": "Ticketing Organisation",
                "value": ticket_data.ticket.ticket_org_name(),
            }, {
                "key": "issued-date",
                "label": "Issued at",
                "dateStyle": "PKDateStyleFull",
                "timeStyle": "PKDateStyleFull",
                "value": issued_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
            }, {
                "key": "issuing-org",
                "label": "Issuing Organisation",
                "value": ticket_data.ticket.kvp_org_name(),
            }, {
                "key": "view-link",
                "label": "More info",
                "attributedValue": f"<a href=\"#\">View ticket</a>",
            }]
        },
        "barcodes": [{
            "format": "PKBarcodeFormatAztec",
            "message": ticket_instance.barcode_data.decode("iso-8859-1"),
            "messageEncoding": "iso-8859-1"
        }],
    }

    for elm in ticket_data.ticket.product_data:
        if isinstance(elm, vdv.ticket.PassengerData):
            pass_json["generic"]["primaryFields"].append({
                "key": "passenger",
                "label": "Passenger",
                "value": f"{elm.forename} {elm.surname}",
                "semantics": {
                    "passengerName": {
                        "familyName": elm.surname,
                        "givenName": elm.forename
                    }
                }
            })
            pass_json["generic"]["secondaryFields"].append({
                "key": "date-of-birth",
                "label": "Date of birth",
                "dateStyle": "PKDateStyleMedium",
                "value": elm.date_of_birth.as_date().strftime("%Y-%m-%dT%H:%M:%SZ"),
            })

    pkp = pkpass.PKPass()
    have_logo = False

    if ticket_data.ticket.product_org_id == 77:
        logo_1x = storages["staticfiles"].open("pass/logo-wt.png", "rb").read()
        pkp.add_file("logo.png", logo_1x)
        logo_2x = storages["staticfiles"].open("pass/logo-wt@2x.png", "rb").read()
        pkp.add_file("logo@2x.png", logo_2x)
        logo_3x = storages["staticfiles"].open("pass/logo-wt@3x.png", "rb").read()
        pkp.add_file("logo@3x.png", logo_3x)
        have_logo = True

    if ticket_obj.ticket_type == models.Ticket.TYPE_DEUTCHLANDTICKET:
        pass_json["description"] = "Deutschlandticket"
        icon_1x = storages["staticfiles"].open("pass/icon-dt.png", "rb").read()
        pkp.add_file("icon.png", icon_1x)
        icon_2x = storages["staticfiles"].open("pass/icon-dt@2x.png", "rb").read()
        pkp.add_file("icon@2x.png", icon_2x)
        icon_3x = storages["staticfiles"].open("pass/icon-dt@3x.png", "rb").read()
        pkp.add_file("icon@3x.png", icon_3x)
        if not have_logo:
            logo_1x = storages["staticfiles"].open("pass/logo-dt.png", "rb").read()
            pkp.add_file("logo.png", logo_1x)
            logo_2x = storages["staticfiles"].open("pass/logo-dt@2x.png", "rb").read()
            pkp.add_file("logo@2x.png", logo_2x)
            logo_3x = storages["staticfiles"].open("pass/logo-dt@3x.png", "rb").read()
            pkp.add_file("logo@3x.png", logo_3x)
    else:
        pass_json["description"] = "VDV Ticket"
        icon_1x = storages["staticfiles"].open("pass/icon.png", "rb").read()
        pkp.add_file("icon.png", icon_1x)
        icon_2x = storages["staticfiles"].open("pass/icon@2x.png", "rb").read()
        pkp.add_file("icon@2x.png", icon_2x)
        icon_3x = storages["staticfiles"].open("pass/icon@3x.png", "rb").read()
        pkp.add_file("icon@3x.png", icon_3x)
        if not have_logo:
            logo_1x = storages["staticfiles"].open("pass/logo.png", "rb").read()
            pkp.add_file("logo.png", logo_1x)
            logo_2x = storages["staticfiles"].open("pass/logo@2x.png", "rb").read()
            pkp.add_file("logo@2x.png", logo_2x)
            logo_3x = storages["staticfiles"].open("pass/logo@3x.png", "rb").read()
            pkp.add_file("logo@3x.png", logo_3x)

    pkp.add_file("pass.json", json.dumps(pass_json).encode("utf-8"))
    pkp.sign()

    response = HttpResponse()
    response['Content-Type'] = "application/vnd.apple.pkpass"
    response['Content-Disposition'] = f'attachment; filename="{ticket_obj.pk}.pkpass"'
    response.write(pkp.get_buffer())
    return response
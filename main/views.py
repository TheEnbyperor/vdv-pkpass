import base64
import json
import typing
import pytz
import dataclasses

from django.shortcuts import render, redirect, get_object_or_404
from django.http import HttpResponse
from django.core.files.storage import storages
from django.conf import settings
from . import forms, models, ticket, pkpass, vdv, aztec, templatetags


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
                image = image_form.cleaned_data["ticket"]
                if image.size > 2 * 1024 * 1024:
                    image_form.add_error("ticket", "The image must be less than 2MB")
                else:
                    image_data = image.read()
                    try:
                        ticket_bytes = aztec.decode(image_data)
                    except aztec.AztecError as e:
                        image_form.add_error("ticket", str(e))
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
            ticket_obj, ticket_created = models.Ticket.objects.get_or_create(id=ticket_pk, defaults={
                "ticket_type": ticket_data.type()
            })
            request.session["ticket_updated"] = True
            request.session["ticket_created"] = ticket_created
            if isinstance(ticket_data, ticket.VDVTicket):
                ticket_obj.vdv_instances.get_or_create(
                    ticket_number=ticket_data.ticket.ticket_id,
                    ticket_org_id=ticket_data.ticket.ticket_org_id,
                    defaults={
                        "validity_start": ticket_data.ticket.validity_start.as_datetime(),
                        "validity_end": ticket_data.ticket.validity_end.as_datetime(),
                        "barcode_data": ticket_bytes,
                        "decoded_data": {
                            "root_ca": dataclasses.asdict(ticket_data.root_ca, dict_factory=to_dict_json),
                            "issuing_ca": dataclasses.asdict(ticket_data.issuing_ca, dict_factory=to_dict_json),
                            "envelope_certificate": dataclasses.asdict(ticket_data.envelope_certificate,
                                                                       dict_factory=to_dict_json),
                            "ticket": base64.b64encode(ticket_data.raw_ticket).decode("ascii"),
                        }
                    }
                )
            elif isinstance(ticket_data, ticket.UICTicket):
                ticket_obj.uic_instances.get_or_create(
                    reference=ticket_data.ticket_id(),
                    distributor_rics=ticket_data.issuing_rics(),
                    defaults={
                        "issuing_time": ticket_data.issuing_time(),
                        "barcode_data": ticket_bytes,
                        "decoded_data": {
                            "envelope": dataclasses.asdict(ticket_data.envelope, dict_factory=to_dict_json),
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
        "ticket_updated": request.session.pop("ticket_updated", False),
        "ticket_created": request.session.pop("ticket_created", False),
    })


def add_pkp_img(pkp, img_name: str, pass_path: str):
    img_name, img_name_ext = img_name.rsplit(".", 1)
    pass_path, pass_path_ext = pass_path.rsplit(".", 1)
    img_1x = storages["staticfiles"].open(f"{img_name}.{img_name_ext}", "rb").read()
    pkp.add_file(f"{pass_path}.{pass_path_ext}", img_1x)
    img_2x = storages["staticfiles"].open(f"{img_name}@2x.{img_name_ext}", "rb").read()
    pkp.add_file(f"{pass_path}@2x.{pass_path_ext}", img_2x)
    img_3x = storages["staticfiles"].open(f"{img_name}@3x.{img_name_ext}", "rb").read()
    pkp.add_file(f"{pass_path}@3x.{pass_path_ext}", img_3x)


def ticket_pkpass(request, pk):
    ticket_obj: models.Ticket = get_object_or_404(models.Ticket, id=pk)
    ticket_instance: models.UICTicketInstance = ticket_obj.uic_instances.first()
    pkp = pkpass.PKPass()
    have_logo = False

    pass_json = {
        "formatVersion": 1,
        "organizationName": settings.PKPASS_CONF["organization_name"],
        "passTypeIdentifier": settings.PKPASS_CONF["pass_type"],
        "teamIdentifier": settings.PKPASS_CONF["team_id"],
        "serialNumber": ticket_obj.pk,
        "sharingProhibited": True,
        "backgroundColor": "rgb(255, 255, 255)",
        "foregroundColor": "rgb(0, 0, 0)",
    }

    if ticket_instance:
        ticket_data: ticket.UICTicket = ticket_instance.as_ticket()
        issued_at = ticket_data.issuing_time().astimezone(pytz.utc)
        issuing_rics = ticket_data.issuing_rics()

        pass_json["description"] = "CIV Ticket"
        pass_json["generic"] = {
            "headerFields": [],
            "primaryFields": [],
            "secondaryFields": [],
            "backFields": []
        }
        pass_json["barcodes"] = [{
            "format": "PKBarcodeFormatAztec",
            "message": bytes(ticket_instance.barcode_data).decode("iso-8859-1"),
            "messageEncoding": "iso-8859-1"
        }]

        if ticket_id := ticket_data.ticket_id():
            pass_json["generic"]["backFields"].append({
                "key": "ticket-id",
                "label": "ticket-id-label",
                "value": ticket_id,
            })

        if issuing_rics in RICS_LOGO:
            add_pkp_img(pkp, RICS_LOGO[issuing_rics], "logo.png")
            have_logo = True

        if distributor := ticket_data.distributor():
            if distributor["url"]:
                pass_json["generic"]["backFields"].append({
                    "key": "issuing-org",
                    "label": "issuing-organisation-label",
                    "value": distributor["full_name"],
                    "attributedValue": f"<a href=\"{distributor['url']}\">{distributor['full_name']}</a>",
                })
            else:
                pass_json["generic"]["backFields"].append({
                    "key": "distributor",
                    "label": "issuing-organisation-label",
                    "value": distributor["full_name"],
                })

        if ticket_data.flex.version == 3:
            if len(ticket_data.flex.data["transportDocument"]) >= 1:
                document = ticket_data.flex.data["transportDocument"][0]["ticket"]
                if document[0] == "openTicket":
                    document = document[1]

                    validity_start = templatetags.rics.rics_valid_from(document, issued_at)
                    validity_end = templatetags.rics.rics_valid_until(document, issued_at)

                    pass_json["expirationDate"] = validity_end.strftime("%Y-%m-%dT%H:%M:%SZ")
                    pass_json["generic"]["secondaryFields"].append({
                        "key": "validity-start",
                        "label": "validity-start-label",
                        "dateStyle": "PKDateStyleMedium",
                        "value": validity_start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    })
                    pass_json["generic"]["secondaryFields"].append({
                        "key": "validity-end",
                        "label": "validity-end-label",
                        "dateStyle": "PKDateStyleMedium",
                        "value": validity_end.strftime("%Y-%m-%dT%H:%M:%SZ"),
                        "changeMessage": "validity-end-change"
                    })
                    pass_json["generic"]["backFields"].append({
                        "key": "validity-start-back",
                        "label": "validity-start-label",
                        "dateStyle": "PKDateStyleFull",
                        "timeStyle": "PKDateStyleFull",
                        "value": validity_start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    })
                    pass_json["generic"]["backFields"].append({
                        "key": "validity-end-back",
                        "label": "validity-end-label",
                        "dateStyle": "PKDateStyleFull",
                        "timeStyle": "PKDateStyleFull",
                        "value": validity_end.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    })

                    if len(document.get("tariffs")) >= 1:
                        tariff = document["tariffs"][0]
                        if "tariffDesc" in tariff:
                            pass_json["generic"]["headerFields"].append({
                                "key": "product",
                                "label": "product-label",
                                "value": tariff["tariffDesc"]
                            })

            if len(ticket_data.flex.data.get("travelerDetail", {}).get("traveler", [])) >= 1:
                passenger = ticket_data.flex.data["travelerDetail"]["traveler"][0]
                dob_year = passenger.get("yearOfBirth", 0)
                dob_month = passenger.get("monthOfBirth", 0)
                dob_day = passenger.get("dayOfBirthInMonth", 0)
                pass_json["generic"]["primaryFields"].append({
                    "key": "passenger",
                    "label": "passenger-label",
                    "value": f"{passenger.get('firstName')}\n{passenger.get('lastName')}",
                    "semantics": {
                        "passengerName": {
                            "familyName": passenger.get('lastName'),
                            "givenName": passenger.get('firstName')
                        }
                    }
                })
                pass_json["generic"]["secondaryFields"].append({
                    "key": "date-of-birth",
                    "label": "date-of-birth-label",
                    "dateStyle": "PKDateStyleMedium",
                    "value": f"{dob_year:04d}-{dob_month:02d}-{dob_day:02d}T00:00:00Z",
                })

        pass_json["generic"]["backFields"].append({
            "key": "issued-date",
            "label": "issued-at-label",
            "dateStyle": "PKDateStyleFull",
            "timeStyle": "PKDateStyleFull",
            "value": issued_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        })
    else:
        ticket_instance: models.VDVTicketInstance = ticket_obj.vdv_instances.first()
        ticket_data: ticket.VDVTicket = ticket_instance.as_ticket()

        validity_start = ticket_data.ticket.validity_start.as_datetime().astimezone(pytz.utc)
        validity_end = ticket_data.ticket.validity_end.as_datetime().astimezone(pytz.utc)
        issued_at = ticket_data.ticket.transaction_time.as_datetime().astimezone(pytz.utc)

        pass_json["expirationDate"] = validity_end.strftime("%Y-%m-%dT%H:%M:%SZ")
        pass_json["description"] = "VDV Ticket"
        pass_json["generic"] = {
            "headerFields": [{
                "key": "product",
                "label": "product-label",
                "value": ticket_data.ticket.product_name()
            }],
            "primaryFields": [],
            "secondaryFields": [{
                "key": "validity-start",
                "label": "validity-start-label",
                "dateStyle": "PKDateStyleMedium",
                "value": validity_start.strftime("%Y-%m-%dT%H:%M:%SZ"),
            }, {
                "key": "validity-end",
                "label": "validity-end-label",
                "dateStyle": "PKDateStyleMedium",
                "value": validity_end.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "changeMessage": "validity-end-change"
            }],
            "backFields": [{
                "key": "validity-start-back",
                "label": "validity-start-label",
                "dateStyle": "PKDateStyleFull",
                "timeStyle": "PKDateStyleFull",
                "value": validity_start.strftime("%Y-%m-%dT%H:%M:%SZ"),
            }, {
                "key": "validity-end-back",
                "label": "validity-end-label",
                "dateStyle": "PKDateStyleFull",
                "timeStyle": "PKDateStyleFull",
                "value": validity_end.strftime("%Y-%m-%dT%H:%M:%SZ"),
            }, {
                "key": "product-back",
                "label": "product-label",
                "value": ticket_data.ticket.product_name()
            }, {
                "key": "product-org-back",
                "label": "product-organisation-label",
                "value": ticket_data.ticket.product_org_name()
            }, {
                "key": "ticket-id",
                "label": "ticket-id-label",
                "value": str(ticket_data.ticket.ticket_id),
            }, {
                "key": "ticket-org",
                "label": "ticketing-organisation-label",
                "value": ticket_data.ticket.ticket_org_name(),
            }, {
                "key": "issued-date",
                "label": "issued-at-label",
                "dateStyle": "PKDateStyleFull",
                "timeStyle": "PKDateStyleFull",
                "value": issued_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
            }, {
                "key": "issuing-org",
                "label": "issuing-organisation-label",
                "value": ticket_data.ticket.kvp_org_name(),
            }]
        }
        pass_json["barcodes"] = [{
            "format": "PKBarcodeFormatAztec",
            "message": bytes(ticket_instance.barcode_data).decode("iso-8859-1"),
            "messageEncoding": "iso-8859-1"
        }]

        for elm in ticket_data.ticket.product_data:
            if isinstance(elm, vdv.ticket.PassengerData):
                pass_json["generic"]["primaryFields"].append({
                    "key": "passenger",
                    "label": "passenger-label",
                    "value": f"{elm.forename}\n{elm.surname}",
                    "semantics": {
                        "passengerName": {
                            "familyName": elm.surname,
                            "givenName": elm.forename
                        }
                    }
                })
                pass_json["generic"]["secondaryFields"].append({
                    "key": "date-of-birth",
                    "label": "date-of-birth-label",
                    "dateStyle": "PKDateStyleMedium",
                    "value": elm.date_of_birth.as_date().strftime("%Y-%m-%dT%H:%M:%SZ"),
                })

        if ticket_data.ticket.product_org_id in VDV_ORG_ID_LOGO:
            add_pkp_img(pkp, VDV_ORG_ID_LOGO[ticket_data.ticket.product_org_id], "logo.png")
            have_logo = True

    pass_json["generic"]["backFields"].append({
        "key": "view-link",
        "label": "more-info-label",
        "attributedValue": f"<a href=\"#\">View ticket</a>",
    })

    for lang, strings in PASS_STRINGS.items():
        pkp.add_file(f"{lang}.lproj/pass.strings", strings.encode("utf-8"))

    if not have_logo:
        add_pkp_img(pkp, "pass/logo.png", "logo.png")

    add_pkp_img(pkp, "pass/icon.png", "icon.png")

    if ticket_obj.ticket_type == models.Ticket.TYPE_DEUTCHLANDTICKET:
        add_pkp_img(pkp, "pass/logo-dt.png", "thumbnail.png")

    pkp.add_file("pass.json", json.dumps(pass_json).encode("utf-8"))
    pkp.sign()

    response = HttpResponse()
    response['Content-Type'] = "application/vnd.apple.pkpass"
    response['Content-Disposition'] = f'attachment; filename="{ticket_obj.pk}.pkpass"'
    response.write(pkp.get_buffer())
    return response


def page_not_found(request, exception):
    return render(request, "main/404.html", {
        "exception": exception,
    }, status=404)


PASS_STRINGS = {
    "en": """
"product-label" = "Product";
"ticket-id-label" = "Ticket ID";
"more-info-label" = "More info";
"product-organisation-label" = "Product Organisation";
"issuing-organisation-label" = "Issuing Organisation";
"ticketing-organisation-label" = "Ticketing Organisation";
"validity-start-label" = "Valid from";
"validity-end-label" = "Valid until";
"validity-end-change" = "Validity extended to %@";
"issued-at-label" = "Issued at";
"passenger-label" = "Passenger";
"date-of-birth-label" = "Date of birth";
""",
    "de": """
"product-label" = "Produkt";
"ticket-id-label" = "Ticket-ID";
"more-info-label" = "Mehr Infos";
"product-organisation-label" = "Produktorganisation";
"issuing-organisation-label" = "Ausstellende Organisation";
"ticketing-organisation-label" = "Ticketverkaufsorganisation";
"validity-start-label" = "Gültig vom";
"validity-end-label" = "Gültig bis";
"validity-end-change" = "Verlängert bis %@";
"issued-at-label" = "Ausgestellt am";
"passenger-label" = "Fahrgast";
"date-of-birth-label" = "Geburtsdatum";
"""
}

RICS_LOGO = {
    1080: "pass/logo-db.png",
}

VDV_ORG_ID_LOGO = {
    77: "pass/logo-wt.png",
}
import base64
import json
import typing
import urllib.parse
import pytz
import dataclasses

from django.shortcuts import render, redirect, get_object_or_404, reverse
from django.http import HttpResponse
from django.core.files.storage import storages
from django.conf import settings
from . import forms, models, ticket, pkpass, vdv, aztec, templatetags, apn


def to_dict_json(elements: typing.List[typing.Tuple[str, typing.Any]]) -> dict:
    def encode_value(v):
        if isinstance(v, bytes) or isinstance(v, bytearray):
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
            ticket_obj, ticket_created = models.Ticket.objects.update_or_create(id=ticket_pk, defaults={
                "ticket_type": ticket_data.type()
            })
            request.session["ticket_updated"] = True
            request.session["ticket_created"] = ticket_created
            if isinstance(ticket_data, ticket.VDVTicket):
                models.VDVTicketInstance.objects.update_or_create(
                    ticket_number=ticket_data.ticket.ticket_id,
                    ticket_org_id=ticket_data.ticket.ticket_org_id,
                    defaults={
                        "ticket": ticket_obj,
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
                models.UICTicketInstance.objects.update_or_create(
                    reference=ticket_data.ticket_id(),
                    distributor_rics=ticket_data.issuing_rics(),
                    defaults={
                        "ticket": ticket_obj,
                        "issuing_time": ticket_data.issuing_time(),
                        "barcode_data": ticket_bytes,
                        "decoded_data": {
                            "envelope": dataclasses.asdict(ticket_data.envelope, dict_factory=to_dict_json),
                        }
                    }
                )
            apn.notify_ticket(ticket_obj)
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
    return make_pkpass(ticket_obj)

def make_pkpass(ticket_obj: models.Ticket):
    ticket_instance: models.UICTicketInstance = ticket_obj.uic_instances.first()
    pkp = pkpass.PKPass()
    have_logo = False

    pass_json = {
        "formatVersion": 1,
        "organizationName": settings.PKPASS_CONF["organization_name"],
        "passTypeIdentifier": settings.PKPASS_CONF["pass_type"],
        "teamIdentifier": settings.PKPASS_CONF["team_id"],
        "serialNumber": ticket_obj.pk,
        "groupingIdentifier": ticket_obj.pk,
        "description": ticket_obj.get_ticket_type_display(),
        "sharingProhibited": True,
        "backgroundColor": "rgb(255, 255, 255)",
        "foregroundColor": "rgb(0, 0, 0)",
        "locations": [],
        "webServiceURL": f"{settings.EXTERNAL_URL_BASE}/api/apple/",
        "authenticationToken": ticket_obj.pkpass_authentication_token
    }

    pass_type = "generic"
    pass_fields = {
        "headerFields": [],
        "primaryFields": [],
        "secondaryFields": [],
        "auxiliaryFields": [],
        "backFields": []
    }

    if ticket_instance:
        ticket_data: ticket.UICTicket = ticket_instance.as_ticket()
        issued_at = ticket_data.issuing_time().astimezone(pytz.utc)
        issuing_rics = ticket_data.issuing_rics()

        pass_json["barcodes"] = [{
            "format": "PKBarcodeFormatAztec",
            "message": bytes(ticket_instance.barcode_data).decode("iso-8859-1"),
            "messageEncoding": "iso-8859-1",
            "altText": ticket_data.ticket_id()
        }]

        if ticket_id := ticket_data.ticket_id():
            pass_fields["backFields"].append({
                "key": "ticket-id",
                "label": "ticket-id-label",
                "value": ticket_id,
                "semantics": {
                    "confirmationNumber": ticket_id
                }
            })

        if issuing_rics in RICS_LOGO:
            add_pkp_img(pkp, RICS_LOGO[issuing_rics], "logo.png")
            have_logo = True

        if ticket_data.flex:
            pass_json["voided"] = not ticket_data.flex.data["issuingDetail"]["activated"]

            if len(ticket_data.flex.data["transportDocument"]) >= 1:
                document_type, document = ticket_data.flex.data["transportDocument"][0]["ticket"]
                if document_type == "openTicket":
                    validity_start = templatetags.rics.rics_valid_from(document, issued_at)
                    validity_end = templatetags.rics.rics_valid_until(document, issued_at)

                    pass_json["expirationDate"] = validity_end.strftime("%Y-%m-%dT%H:%M:%SZ")

                    if "fromStationNum" in document and "toStationNum" in document:
                        pass_type = "boardingPass"
                        pass_fields["transitType"] = "PKTransitTypeTrain"

                        from_station = templatetags.rics.get_station(document["fromStationNum"], document["stationCodeTable"])
                        to_station = templatetags.rics.get_station(document["toStationNum"], document["stationCodeTable"])

                        if "classCode" in document:
                            pass_fields["auxiliaryFields"].append({
                                "key": "class-code",
                                "label": "class-code-label",
                                "value": f"class-code-{document['classCode']}-label",
                            })

                        if from_station:
                            pass_fields["primaryFields"].append({
                                "key": "from-station",
                                "label": "from-station-label",
                                "value": from_station["name"],
                                "semantics": {
                                    "departureLocation": {
                                        "latitude": float(from_station["latitude"]),
                                        "longitude": float(from_station["longitude"]),
                                    },
                                    "departureStationName": from_station["name"]
                                }
                            })
                            pass_json["locations"].append({
                                "latitude": float(from_station["latitude"]),
                                "longitude": float(from_station["longitude"]),
                                "relevantText": from_station["name"]
                            })
                            maps_link = urllib.parse.urlencode({
                                "q": from_station["name"],
                                "ll": f"{from_station['latitude']},{from_station['longitude']}"
                            })
                            pass_fields["backFields"].append({
                                "key": "from-station-back",
                                "label": "from-station-label",
                                "value": from_station["name"],
                                "attributedValue": f"<a href=\"https://maps.apple.com/?{maps_link}\">{from_station['name']}</a>",
                            })
                        elif "fromStationNameUTF8" in document:
                            pass_fields["primaryFields"].append({
                                "key": "from-station",
                                "label": "from-station-label",
                                "value": document["fromStationNameUTF8"],
                                "semantics": {
                                    "departureStationName": document["fromStationNameUTF8"]
                                }
                            })
                        elif "fromStationIA5" in document:
                            pass_fields["primaryFields"].append({
                                "key": "from-station",
                                "label": "from-station-label",
                                "value": document["fromStationIA5"],
                                "semantics": {
                                    "departureStationName": document["fromStationIA5"]
                                }
                            })

                        if to_station:
                            pass_fields["primaryFields"].append({
                                "key": "to-station",
                                "label": "to-station-label",
                                "value": to_station["name"],
                                "semantics": {
                                    "destinationLocation": {
                                        "latitude": float(from_station["latitude"]),
                                        "longitude": float(from_station["longitude"]),
                                    },
                                    "destinationStationName": to_station["name"]
                                }
                            })
                            pass_json["locations"].append({
                                "latitude": float(to_station["latitude"]),
                                "longitude": float(to_station["longitude"]),
                                "relevantText": to_station["name"]
                            })
                            maps_link = urllib.parse.urlencode({
                                "q": to_station["name"],
                                "ll": f"{to_station['latitude']},{to_station['longitude']}"
                            })
                            pass_fields["backFields"].append({
                                "key": "to-station-back",
                                "label": "to-station-label",
                                "value": to_station["name"],
                                "attributedValue": f"<a href=\"https://maps.apple.com/?{maps_link}\">{to_station['name']}</a>",
                            })
                        elif "toStationNameUTF8" in document:
                            pass_fields["primaryFields"].append({
                                "key": "to-station",
                                "label": "to-station-label",
                                "value": document["toStationNameUTF8"],
                                "semantics": {
                                    "destinationStationName": document["toStationNameUTF8"]
                                }
                            })
                        elif "toStationIA5" in document:
                            pass_fields["primaryFields"].append({
                                "key": "to-station",
                                "label": "to-station-label",
                                "value": document["toStationIA5"],
                                "semantics": {
                                    "destinationStationName": document["toStationIA5"]
                                }
                            })

                    if len(document.get("tariffs")) >= 1:
                        tariff = document["tariffs"][0]
                        if "tariffDesc" in tariff:
                            pass_fields["headerFields"].append({
                                "key": "product",
                                "label": "product-label",
                                "value": tariff["tariffDesc"]
                            })

                        for card in tariff.get("reductionCard", []):
                            pass_fields["auxiliaryFields"].append({
                                "key": "reduction-card",
                                "label": "reduction-card-label",
                                "value": card["cardName"]
                            })

                    pass_fields["backFields"].append({
                        "key": "return-included",
                        "label": "return-included-label",
                        "value": "return-included-yes" if document["returnIncluded"] else "return-included-no",
                    })

                    if "productIdIA5" in document:
                        pass_fields["backFields"].append({
                            "key": "product-id",
                            "label": "product-id-label",
                            "value": document["productIdIA5"],
                        })

                    pass_fields["secondaryFields"].append({
                        "key": "validity-start",
                        "label": "validity-start-label",
                        "dateStyle": "PKDateStyleMedium",
                        "timeStyle": "PKDateStyleNone",
                        "value": validity_start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    })
                    pass_fields["secondaryFields"].append({
                        "key": "validity-end",
                        "label": "validity-end-label",
                        "dateStyle": "PKDateStyleMedium",
                        "timeStyle": "PKDateStyleNone",
                        "value": validity_end.strftime("%Y-%m-%dT%H:%M:%SZ"),
                        "changeMessage": "validity-end-change"
                    })
                    pass_fields["backFields"].append({
                        "key": "validity-start-back",
                        "label": "validity-start-label",
                        "dateStyle": "PKDateStyleFull",
                        "timeStyle": "PKDateStyleFull",
                        "value": validity_start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    })
                    pass_fields["backFields"].append({
                        "key": "validity-end-back",
                        "label": "validity-end-label",
                        "dateStyle": "PKDateStyleFull",
                        "timeStyle": "PKDateStyleFull",
                        "value": validity_end.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    })

                    if "validRegionDesc" in document:
                        pass_fields["backFields"].append({
                            "key": "valid-region",
                            "label": "valid-region-label",
                            "value": document["validRegionDesc"],
                        })

                elif document_type == "customerCard":
                    validity_start = templatetags.rics.rics_valid_from_date(document)
                    validity_end = templatetags.rics.rics_valid_until_date(document)

                    if "cardTypeDescr" in document:
                        pass_fields["headerFields"].append({
                            "key": "product",
                            "label": "product-label",
                            "value": document["cardTypeDescr"]
                        })

                    if "cardIdIA5" in document:
                        pass_fields["secondaryFields"].append({
                            "key": "card-id",
                            "label": "card-id-label",
                            "value": document["cardIdIA5"],
                        })
                    elif "cardIdNum" in document:
                        pass_fields["secondaryFields"].append({
                            "key": "card-id",
                            "label": "card-id-label",
                            "value": str(document["cardIdNum"]),
                        })

                    if "classCode" in document:
                        pass_fields["secondaryFields"].append({
                            "key": "class-code",
                            "label": "class-code-label",
                            "value": f"class-code-{document['classCode']}-label",
                        })

                    if validity_start:
                        pass_fields["backFields"].append({
                            "key": "validity-start-back",
                            "label": "validity-start-label",
                            "dateStyle": "PKDateStyleFull",
                            "timeStyle": "PKDateStyleNone",
                            "value": validity_start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                        })
                    if validity_end:
                        pass_json["expirationDate"] = validity_end.strftime("%Y-%m-%dT%H:%M:%SZ")
                        pass_fields["backFields"].append({
                            "key": "validity-end-back",
                            "label": "validity-end-label",
                            "dateStyle": "PKDateStyleFull",
                            "timeStyle": "PKDateStyleNone",
                            "value": validity_end.strftime("%Y-%m-%dT%H:%M:%SZ"),
                        })

                elif document_type == "pass":
                    if document["passType"] == 1:
                        product_name = "Eurail Global Pass"
                    elif document["passType"] == 2:
                        product_name = "Interrail Global Pass"
                    elif document["passType"] == 3:
                        product_name = "Interrail One Country Pass"
                    elif document["passType"] == 4:
                        product_name = "Eurail One Country Pass"
                    elif document["passType"] == 5:
                        product_name = "Eurail/Interrail Emergency ticket"
                    else:
                        product_name = f"Pass type {document['passType']}"

                    pass_fields["headerFields"].append({
                        "key": "product",
                        "label": "product-label",
                        "value": product_name
                    })

            if len(ticket_data.flex.data.get("travelerDetail", {}).get("traveler", [])) >= 1:
                passenger = ticket_data.flex.data["travelerDetail"]["traveler"][0]
                dob_year = passenger.get("yearOfBirth", 0)
                dob_month = passenger.get("monthOfBirth", 0)
                dob_day = passenger.get("dayOfBirthInMonth", 0)
                first_name = passenger.get('firstName', "").strip()
                last_name = passenger.get('lastName', "").strip()

                field_data = {
                    "key": "passenger",
                    "label": "passenger-label",
                    "value": f"{first_name}\n{last_name}" if pass_type == "generic" else f"{first_name} {last_name}",
                    "semantics": {
                        "passengerName": {
                            "familyName": last_name,
                            "givenName": first_name,
                        }
                    }
                }
                if pass_type == "generic":
                    pass_fields["primaryFields"].append(field_data)
                else:
                    pass_fields["auxiliaryFields"].append(field_data)

                if dob_year != 0 and dob_month != 0 and dob_day != 0:
                    pass_fields["secondaryFields"].append({
                        "key": "date-of-birth",
                        "label": "date-of-birth-label",
                        "dateStyle": "PKDateStyleMedium",
                        "value": f"{dob_year:04d}-{dob_month:02d}-{dob_day:02d}T00:00:00Z",
                    })
                elif dob_year != 0 and dob_month != 0:
                    pass_fields["secondaryFields"].append({
                        "key": "month-of-birth",
                        "label": "month-of-birth-label",
                        "value": f"{dob_month:02d}.{dob_year:04d}",
                    })
                elif dob_year != 0:
                    pass_fields["secondaryFields"].append({
                        "key": "year-of-birth",
                        "label": "year-of-birth-label",
                        "value": f"{dob_year:04d}",
                    })

                if "countryOfResidence" in passenger:
                    pass_fields["secondaryFields"].append({
                        "key": "country-of-residence",
                        "label": "country-of-residence-label",
                        "value": templatetags.rics.get_country(passenger["countryOfResidence"]),
                    })

                if "passportId" in passenger:
                    pass_fields["secondaryFields"].append({
                        "key": "passport-number",
                        "label": "passport-number-label",
                        "value": passenger["passportId"],
                    })

        if distributor := ticket_data.distributor():
            pass_json["organizationName"] = distributor["full_name"]
            if distributor["url"]:
                pass_fields["backFields"].append({
                    "key": "issuing-org",
                    "label": "issuing-organisation-label",
                    "value": distributor["full_name"],
                    "attributedValue": f"<a href=\"{distributor['url']}\">{distributor['full_name']}</a>",
                })
            else:
                pass_fields["backFields"].append({
                    "key": "distributor",
                    "label": "issuing-organisation-label",
                    "value": distributor["full_name"],
                })

        pass_fields["backFields"].append({
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
        pass_fields = {
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
        pass_json["organizationName"] = ticket_data.ticket.kvp_org_name()
        pass_json["barcodes"] = [{
            "format": "PKBarcodeFormatAztec",
            "message": bytes(ticket_instance.barcode_data).decode("iso-8859-1"),
            "messageEncoding": "iso-8859-1",
            "altText": str(ticket_data.ticket.ticket_id),
        }]

        for elm in ticket_data.ticket.product_data:
            if isinstance(elm, vdv.ticket.PassengerData):
                pass_fields["primaryFields"].append({
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
                pass_fields["secondaryFields"].append({
                    "key": "date-of-birth",
                    "label": "date-of-birth-label",
                    "dateStyle": "PKDateStyleMedium",
                    "value": elm.date_of_birth.as_date().strftime("%Y-%m-%dT%H:%M:%SZ"),
                })

        if ticket_data.ticket.product_org_id in VDV_ORG_ID_LOGO:
            add_pkp_img(pkp, VDV_ORG_ID_LOGO[ticket_data.ticket.product_org_id], "logo.png")
            have_logo = True
        elif ticket_data.ticket.product_org_id == 3000 and ticket_data.ticket.ticket_org_id in VDV_ORG_ID_LOGO:
            add_pkp_img(pkp, VDV_ORG_ID_LOGO[ticket_data.ticket.ticket_org_id], "logo.png")
            have_logo = True

    ticket_url = reverse('ticket', kwargs={"pk": ticket_obj.pk})
    pass_fields["backFields"].append({
        "key": "view-link",
        "label": "more-info-label",
        "value": "",
        "attributedValue": f"<a href=\"{settings.EXTERNAL_URL_BASE}{ticket_url}\">View ticket</a>",
    })

    pass_json[pass_type] = pass_fields

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
"card-id-label" = "Card ID";
"more-info-label" = "More info";
"product-organisation-label" = "Product Organisation";
"issuing-organisation-label" = "Issuing Organisation";
"ticketing-organisation-label" = "Ticketing Organisation";
"validity-start-label" = "Valid from";
"validity-end-label" = "Valid until";
"validity-end-change" = "Validity extended to %@";
"issued-at-label" = "Issued at";
"passenger-label" = "Passenger";
"class-code-label" = "Class";
"class-code-first-label" = "1st";
"class-code-second-label" = "2nd";
"reduction-card-label" = "Discount card";
"date-of-birth-label" = "Date of birth";
"month-of-birth-label" = "Birth month";
"year-of-birth-label" = "Birth year";
"country-of-residence-label" = "Country of residence";
"passport-number-label" = "Passport number";
"from-station-label" = "From";
"to-station-label" = "To";
"product-id-label" = "Ticket type";
"valid-region-label" = "Validity";
"return-included-label" = "Return included";
"return-included-yes" = "Yes";
"return-included-no" = "No";
""",
    "de": """
"product-label" = "Produkt";
"ticket-id-label" = "Ticket-ID";
"card-id-label" = "Kartennummer";
"more-info-label" = "Mehr Infos";
"product-organisation-label" = "Produktorganisation";
"issuing-organisation-label" = "Ausstellende Organisation";
"ticketing-organisation-label" = "Ticketverkaufsorganisation";
"validity-start-label" = "Gültig vom";
"validity-end-label" = "Gültig bis";
"validity-end-change" = "Verlängert bis %@";
"issued-at-label" = "Ausgestellt am";
"passenger-label" = "Fahrgast";
"class-code-label" = "Klasse";
"class-code-first-label" = "1.";
"class-code-second-label" = "2.";
"reduction-card-label" = "Bahncard";
"date-of-birth-label" = "Geburtsdatum";
"month-of-birth-label" = "Geburtsmonat";
"year-of-birth-label" = "Geburtsjahr";
"country-of-residence-label" = "Land des Wohnsitzes";
"passport-number-label" = "Passnummer";
"from-station-label" = "Von";
"to-station-label" = "Nach";
"product-id-label" = "Tickettyp";
"valid-region-label" = "Gültigkeit";
"return-included-label" = "Rückfahrt inklusive";
"return-included-yes" = "Ja";
"return-included-no" = "Nein";
"""
}

RICS_LOGO = {
    1080: "pass/logo-db.png",
    1088: "pass/logo-sncb.png",
    1181: "pass/logo-oebb.png",
    1184: "pass/logo-ns.png",
    1186: "pass/logo-dsb.png",
    1251: "pass/logo-pkp-ic.png",
    9901: "pass/logo-interrail.png",
}

VDV_ORG_ID_LOGO = {
    36: "pass/logo-rmv.png",
    77: "pass/logo-wt.png",
    102: "pass/logo-vrs.png",
    103: "pass/logo-swb.png",
    6234: "pass/logo-vvs.png",
    6310: "pass/logo-svv.png",
    6496: "pass/logo-naldo.png",
}
import datetime
import json
import urllib

import pytz
import pymupdf
import io
import typing
import copy
import niquests
import idna
import urllib3.util
from PIL import Image, ImageOps
from django.utils import timezone
from django.shortcuts import render, redirect, get_object_or_404, reverse
from django.http import HttpResponse
from django.core.files.storage import storages
from django.conf import settings
from django.core.files.storage import default_storage
from django.contrib import messages
from main import forms, models, ticket, pkpass, vdv, aztec, templatetags, apn, gwallet, rsp, uic, ssb, swisspass, cal, \
    bahnbonus, iata

NSR_DAY_UPGRADES = ('Fietskaart', 'Toeslag 2-1 Dag', 'Dagkaart Hond')


def get_client_ip(request):
    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded_for:
        ip = x_forwarded_for.split(',')[0]
    else:
        ip = request.META.get('REMOTE_ADDR')
    return ip


def idna_encode_url(url: str):
    parts = urllib3.util.parse_url(url)
    host = idna.encode(parts.host).decode()
    new_url = urllib3.util.Url(parts.scheme, parts.auth, host, parts.port, parts.path, parts.query, parts.fragment)
    return new_url.url


def process_tickets(request, tickets):
    if tickets:
        ticket_ids = []
        errors = []
        for tb in tickets:
            try:
                ticket_obj, ticket_created = ticket.update_from_barcode(
                    tb, request.user.account if request.user.is_authenticated else None,
                    force_update=True
                )
            except ticket.TicketError as e:
                errors.append({
                    "title": e.title,
                    "message": e.message,
                    "exception": e.exception,
                    "ticket_contents": tb.hex()
                })
            else:
                request.session["ticket_updated"] = True
                request.session["ticket_created"] = ticket_created
                ticket_ids.append(ticket_obj.id)

                headers = dict(request.headers)
                headers.pop("Cookie", None)
                headers.pop("Authorization", None)
                models.AccessLogEntry.objects.create(
                    ticket=ticket_obj,
                    action=models.AccessLogEntry.ACTION_UPLOAD,
                    remote_ip=get_client_ip(request),
                    headers=headers,
                    account=request.user.account if request.user.is_authenticated else None,
                )

        if ticket_ids:
            if len(ticket_ids) > 1:
                messages.info(request, f"{len(ticket_ids) - 1} other tickets have been added to your account")
            return ticket_ids[0], None
        elif errors:
            return None, errors[0]

    return None, None


def index(request):
    ticket_bytes = None
    tickets = []

    if request.method == "POST":
        if request.POST.get("type") == "scan":
            try:
                ticket_bytes = bytes.fromhex(request.POST.get("ticket_hex"))
            except ValueError:
                pass

            image_form = forms.TicketUploadForm()
        elif request.POST.get("type") == "text":
            try:
                ticket_bytes = bytes.fromhex(request.POST.get("ticket_text"))
            except ValueError:
                ticket_bytes = request.POST.get("ticket_text").encode("utf-8")

            image_form = forms.TicketUploadForm()
        else:
            image_form = forms.TicketUploadForm(request.POST, request.FILES)
            if image_form.is_valid():
                ticket_file = image_form.cleaned_data["ticket"]
                if ticket_file.size > 16 * 1024 * 1024:
                    image_form.add_error("ticket", "The ticket must be less than 16MB")
                else:
                    if ticket_file.content_type != "application/pdf":
                        try:
                            ticket_bytes = aztec.decode(ticket_file.read())
                        except aztec.AztecError as e:
                            image_form.add_error("ticket", str(e))
                    else:
                        try:
                            pdf = pymupdf.open(stream=ticket_file.read(), filetype=ticket_file.content_type)
                        except RuntimeError as e:
                            image_form.add_error("ticket", f"Error opening PDF: {e}")
                        else:
                            for page in pdf:
                                img_bytes = page.get_pixmap(dpi=300).tobytes()
                                try:
                                    tickets.extend(aztec.decode_multiple(img_bytes, scan_speed="slow"))
                                except aztec.AztecError:
                                    continue

                            if not tickets:
                                image_form.add_error("ticket", f"Failed to find any Aztec codes in the PDF")

    else:
        image_form = forms.TicketUploadForm()

    if not tickets and ticket_bytes:
        tickets = [ticket_bytes]

    ticket_id, error = process_tickets(request, tickets)
    if ticket_id:
        return redirect('ticket', pk=ticket_id)

    return render(request, "main/index.html", {
        "image_form": image_form,
        "error": error,
    })


def view_ticket(request, pk):
    ticket_obj = get_object_or_404(models.Ticket, id=pk)
    ticket_url = f"{settings.EXTERNAL_URL_BASE}{reverse('ticket', args=[ticket_obj.id])}"
    gwallet_url = gwallet.create_jwt_link(ticket_obj)

    is_saarvv = False
    is_sbahn_berlin = False
    is_db_abo = False
    active_instance = ticket_obj.active_instance
    if isinstance(active_instance, models.VDVTicketInstance):
        if active_instance.ticket_org_id == 6310:
            is_saarvv = True
        elif active_instance.ticket_org_id == 6061:
            is_db_abo = True
        elif active_instance.ticket_org_id == 6135:
            is_sbahn_berlin = True
    elif isinstance(active_instance, models.UICTicketInstance):
        if active_instance.distributor_rics in (80, 1080):
            is_db_abo = True

    has_saarvv = ticket_obj.account and ticket_obj.account.is_saarvv_authenticated()
    has_sbahn_berlin = ticket_obj.account and ticket_obj.account.is_sbahn_berlin_authenticated()
    has_db_abo = (ticket_obj.account and ticket_obj.account.is_db_authenticated()) or ticket_obj.db_subscription

    photo_upload_forms = {}
    if rsp_obj := ticket_obj.rsp_instances.first():
        td = rsp_obj.as_ticket  # type: ticket.RSPTicket
        if isinstance(td.data, rsp.RailcardData):
            photo_upload_forms["first"] = {
                "name": td.data.passenger_1_name(),
            }
            if name := ticket_obj.photos.get("first"):
                photo_upload_forms["first"]["current"] = default_storage.url(name)
            if td.data.has_passenger_2():
                photo_upload_forms["second"] = {
                    "name": td.data.passenger_2_name(),
                    "current": ticket_obj.photos.get("second"),
                }

    if request.method == "POST":
        if "photo-upload" in request.POST:
            pi = request.POST["photo-upload"]
            if pi in photo_upload_forms:
                if "photo" in request.FILES:
                    file = request.FILES["photo"]
                    if file.size > 16 * 1024 * 1024:
                        photo_upload_forms[pi]["error"] = "The photo must be less than 16MB"
                    elif file.content_type not in ("image/jpeg", "image/png"):
                        photo_upload_forms[pi]["error"] = "The photo must be a JPEG or PNG"
                    else:
                        file_name = default_storage.save(file.name, file)
                        ticket_obj.photos[pi] = file_name
                        ticket_obj.save()
                        apn.notify_ticket.delay(ticket_obj.pk)
                        gwallet.sync_ticket.delay(ticket_obj.pk)

    return render(request, "main/ticket.html", {
        "ticket": ticket_obj,
        "ticket_url": ticket_url,
        "ticket_updated": request.session.pop("ticket_updated", False),
        "ticket_created": request.session.pop("ticket_created", False),
        "gwallet_url": gwallet_url,
        "ical_url": reverse("ticket_ics", args=(ticket_obj.id,)) if cal.supports_calendar(ticket_obj) else None,
        "photo_upload_forms": photo_upload_forms,
        "is_saarvv": is_saarvv,
        "has_saarvv": has_saarvv,
        "is_sbahn_berlin": is_sbahn_berlin,
        "has_sbahn_berlin": has_sbahn_berlin,
        "is_db_abo": is_db_abo,
        "has_db_abo": has_db_abo,
    })


def edit_ticket(request, pk):
    ticket_obj = get_object_or_404(models.Ticket, id=pk)

    can_edit = not ticket_obj.account or (
            request.user.is_authenticated and ticket_obj.account == request.user.account
    )
    if not can_edit:
        return render(request, "main/ticket_edit.html", {
            "ticket": ticket_obj,
            "can_edit": False,
        })

    if request.method == "POST":
        form = forms.TicketEditForm(request.POST, initial={
            "name": ticket_obj.label,
        })

        if form.is_valid():
            ticket_obj.label = form.cleaned_data["name"]
            ticket_obj.save()
            return redirect("ticket", ticket_obj.pk)
    else:
        form = forms.TicketEditForm(initial={
            "name": ticket_obj.label,
        })

    return render(request, "main/ticket_edit.html", {
        "ticket": ticket_obj,
        "form": form,
        "can_edit": True
    })


def delete_ticket(request, pk):
    ticket_obj = get_object_or_404(models.Ticket, id=pk)

    can_delete = not ticket_obj.account or (
            request.user.is_authenticated and ticket_obj.account == request.user.account
    )

    if request.method == "POST" and can_delete:
        if request.POST.get("confirm") == "yes":
            ticket_obj.delete()
            return redirect("index")

    return render(request, "main/ticket_delete.html", {
        "ticket": ticket_obj,
        "can_delete": can_delete,
    })


def pass_photo_thumbnail(ticket_obj: "models.Ticket", size, padding):
    out = Image.new("RGBA", size, (0, 0, 0, 0))
    images = []
    for k in ("first", "second"):
        if img := ticket_obj.photos.get(k):
            with default_storage.open(img) as f:
                i = Image.open(f)
                ImageOps.exif_transpose(i, in_place=True)
                i.thumbnail(out.size, Image.Resampling.LANCZOS)
                images.append(i)

    total_width = sum((i.width + padding) for i in images)
    x = (out.width // 2) - (total_width // 2)
    for i in images:
        out.paste(i, (x + (padding // 2), (out.height - i.height) // 2))
        x += i.width + (padding // 2)

    return out


def pass_photo_banner(request, pk):
    ticket_obj = get_object_or_404(models.Ticket, id=pk)
    out = pass_photo_thumbnail(ticket_obj, (1000, 500), 50)
    out_bytes = io.BytesIO()
    out.save(out_bytes, format='PNG')
    return HttpResponse(out_bytes.getvalue(), content_type="image/png")


def add_pkp_img(pkp, img_name: str, pass_path: str):
    img_name, img_name_ext = img_name.rsplit(".", 1)
    pass_path, pass_path_ext = pass_path.rsplit(".", 1)
    storage = storages["staticfiles"]
    with storage.open(f"{img_name}.{img_name_ext}", "rb") as f:
        img_1x = f.read()
        pkp.add_file(f"{pass_path}.{pass_path_ext}", img_1x)
    try:
        with storage.open(f"{img_name}@2x.{img_name_ext}", "rb") as f:
            img_2x = f.read()
            pkp.add_file(f"{pass_path}@2x.{pass_path_ext}", img_2x)
    except FileNotFoundError:
        pass
    try:
        with storages["staticfiles"].open(f"{img_name}@3x.{img_name_ext}", "rb") as f:
            img_3x = f.read()
            pkp.add_file(f"{pass_path}@3x.{pass_path_ext}", img_3x)
    except FileNotFoundError:
        pass


def ticket_pkpass(request, pk):
    ticket_obj: models.Ticket = get_object_or_404(models.Ticket, id=pk)

    headers = dict(request.headers)
    headers.pop("Cookie", None)
    headers.pop("Authorization", None)
    models.AccessLogEntry.objects.create(
        ticket=ticket_obj,
        action=models.AccessLogEntry.ACTION_DOWNLOAD_PKPASS,
        remote_ip=get_client_ip(request),
        headers=headers,
        account=request.user.account if request.user.is_authenticated else None,
    )

    return make_pkpass(ticket_obj)


def make_pkpass_file(ticket_obj: "models.Ticket", part: typing.Optional[str] = None):
    pkp = pkpass.PKPass()
    have_logo = False
    have_icon = False
    is_dark_bg = False

    pass_json = {
        "formatVersion": 1,
        "organizationName": settings.PKPASS_CONF["organization_name"],
        "passTypeIdentifier": settings.PKPASS_CONF["pass_type"],
        "teamIdentifier": settings.PKPASS_CONF["team_id"],
        "serialNumber": ticket_obj.pk,
        "groupingIdentifier": ticket_obj.pk,
        "description": ticket_obj.label if ticket_obj.label else ticket_obj.get_ticket_type_display(),
        "backgroundColor": "rgb(255, 255, 255)",
        "suppressStripShine": True,
        "suppressHeaderDarkening": True,
        "labelColor": "rgb(75, 75, 75)",
        "foregroundColor": "rgb(0, 0, 0)",
        "locations": [{
            "longitude": 0,
            "latitude": 0,
            "relevantText": "Null-island"
        }],
        "relevantDates": [],
        "webServiceURL": idna_encode_url(f"{settings.EXTERNAL_URL_BASE}/api/apple"),
        "authenticationToken": ticket_obj.pkpass_authentication_token,
        "semantics": {},
        "associatedStoreIdentifiers": [6743959567],
        "voided": False,
        "beacons": [{
            "proximityUUID": "00000000-0000-0000-7F00-000000000000",
            "relevantText": "iBeacon go brrrr",
        }],
    }

    pass_type = "generic"
    pass_fields = {
        "headerFields": [],
        "primaryFields": [],
        "secondaryFields": [],
        "auxiliaryFields": [],
        "backFields": []
    }
    has_return = False
    return_pass_fields = {
        "headerFields": [],
        "primaryFields": [],
        "secondaryFields": [],
        "auxiliaryFields": [],
        "backFields": []
    }
    return_pass_type = "generic"
    return_pass_json = None

    ticket_instance = ticket_obj.active_instance

    if isinstance(ticket_instance, models.UICTicketInstance):
        ticket_data: ticket.UICTicket = ticket_instance.as_ticket
        if t := ticket_data.issuing_time():
            issued_at = t.astimezone(pytz.utc)
        else:
            issued_at = None
        issuing_rics = ticket_data.issuing_rics()

        if issuing_rics in KNOWN_VALID_SIGNATURE_RICS and (
                (ticket_data.envelope and not ticket_data.envelope.verify_signature()) or
                (ticket_data.dosipas_envelope and not ticket_data.dosipas_envelope.verify_level_1_signature())
        ):
            pass_json["voided"] = True
        elif ticket_data.dtvg_revoked():
            pass_json["voided"] = True

        if issuing_rics == 1184:
            # NSI occasionally issues domestic tickets. If the signature key ID is not
            # known to be used by tickets issued using benerail, set issuing_rics to 1084
            # as a fallback
            if ticket_data.envelope.signature_key_id not in (18,):
                issuing_rics = 1084

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
        if issuing_rics in RICS_BG:
            pass_json["backgroundColor"] = RICS_BG[issuing_rics]
        if issuing_rics in RICS_DARK_BG:
            is_dark_bg = True
        if issuing_rics in RICS_FG:
            pass_json["foregroundColor"] = RICS_FG[issuing_rics]
        if issuing_rics in RICS_FG_SECONDARY:
            pass_json["labelColor"] = RICS_FG_SECONDARY[issuing_rics]

        parsed_layout = None
        if ticket_data.layout and ticket_data.layout.standard in ("RCT2", "RTC2"):
            parser = uic.rct2_parse.RCT2Parser()
            parser.read(ticket_data.layout)
            parsed_layout = parser.parse(issuing_rics)

        if ticket_data.flex:
            issued_at = ticket_data.flex.issuing_time()
            if not ticket_data.flex.data["issuingDetail"]["activated"]:
                pass_json["voided"] = True

            if ticket_data.flex.data["issuingDetail"].get("issuerName") in UIC_NAME_LOGO:
                add_pkp_img(pkp, UIC_NAME_LOGO[ticket_data.flex.data["issuingDetail"]["issuerName"]], "logo.png")
                have_logo = True

            if not have_logo and "issuerNum" in ticket_data.flex.data["issuingDetail"]:
                issuing_rics = ticket_data.flex.data["issuingDetail"]["issuerNum"]
                if issuing_rics in RICS_LOGO:
                    add_pkp_img(pkp, RICS_LOGO[issuing_rics], "logo.png")
                    have_logo = True
                if issuing_rics in RICS_BG:
                    pass_json["backgroundColor"] = RICS_BG[issuing_rics]
                if issuing_rics in RICS_FG:
                    pass_json["foregroundColor"] = RICS_FG[issuing_rics]
                if issuing_rics in RICS_FG_SECONDARY:
                    pass_json["labelColor"] = RICS_FG_SECONDARY[issuing_rics]

            if len(ticket_data.flex.data["transportDocument"]) >= 1:
                ticket_document = next(map(
                    lambda d: d["ticket"][1],
                    filter(
                        lambda d: d["ticket"][0] == "openTicket", ticket_data.flex.data["transportDocument"]
                    ),
                ), None)
                reservation_document = next(map(
                    lambda d: d["ticket"][1],
                    filter(
                        lambda d: d["ticket"][0] == "reservation", ticket_data.flex.data["transportDocument"]
                    ),
                ), None)
                customer_card_document = next(map(
                    lambda d: d["ticket"][1],
                    filter(
                        lambda d: d["ticket"][0] == "customerCard", ticket_data.flex.data["transportDocument"]
                    ),
                ), None)
                pass_document = next(map(
                    lambda d: d["ticket"][1],
                    filter(
                        lambda d: d["ticket"][0] == "pass", ticket_data.flex.data["transportDocument"]
                    ),
                ), None)
                delay_conf_document = next(map(
                    lambda d: d["ticket"][1],
                    filter(
                        lambda d: d["ticket"][0] == "delayConfirmation", ticket_data.flex.data["transportDocument"]
                    ),
                ), None)
                if ticket_document or reservation_document:
                    if ticket_document:
                        validity_start = templatetags.rics.rics_valid_from(ticket_document, issued_at)
                        validity_end = templatetags.rics.rics_valid_until(ticket_document, issued_at)

                        pass_json["expirationDate"] = validity_end.strftime("%Y-%m-%dT%H:%M:%SZ")

                        if "validRegion" in ticket_document:
                            train_links = list(map(
                                lambda l: l[1],
                                filter(lambda l: l[0] == "trainLink", ticket_document["validRegion"])
                            ))
                            via_stations = list(map(
                                lambda l: l[1],
                                filter(lambda l: l[0] == "viaStations", ticket_document["validRegion"])
                            ))
                        else:
                            train_links = None
                            via_stations = None

                        if "fromStationNum" in ticket_document and "toStationNum" in ticket_document or via_stations:
                            pass_type = "boardingPass"
                            pass_fields["transitType"] = "PKTransitTypeTrain"

                            from_station = None
                            to_station = None

                            if "fromStationNum" in ticket_document:
                                from_station = templatetags.rics.get_station(
                                    ticket_document["fromStationNum"], ticket_document)
                            if "toStationNum" in ticket_document:
                                to_station = templatetags.rics.get_station(
                                    ticket_document["toStationNum"], ticket_document)

                            if via_stations and via_stations[0].get("route"):
                                route_start = via_stations[0]["route"][0]
                                route_end = via_stations[0]["route"][-1]

                                if not from_station:
                                    from_station = templatetags.rics.get_station(
                                        route_start["stationNum"], ticket_document)

                                if not to_station:
                                    to_station = templatetags.rics.get_station(
                                        route_end["stationNum"], ticket_document)

                            if "classCode" in ticket_document and ticket_document["classCode"] != "notApplicable":
                                pass_fields["auxiliaryFields"].append({
                                    "key": "class-code",
                                    "label": "class-code-label",
                                    "value": f"class-code-{ticket_document['classCode']}-label",
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
                            elif "fromStationNameUTF8" in ticket_document:
                                pass_fields["primaryFields"].append({
                                    "key": "from-station",
                                    "label": "from-station-label",
                                    "value": ticket_document["fromStationNameUTF8"],
                                    "semantics": {
                                        "departureStationName": ticket_document["fromStationNameUTF8"]
                                    }
                                })
                            elif "fromStationIA5" in ticket_document:
                                pass_fields["primaryFields"].append({
                                    "key": "from-station",
                                    "label": "from-station-label",
                                    "value": ticket_document["fromStationIA5"],
                                    "semantics": {
                                        "departureStationName": ticket_document["fromStationIA5"]
                                    }
                                })

                            if to_station:
                                pass_fields["primaryFields"].append({
                                    "key": "to-station",
                                    "label": "to-station-label",
                                    "value": to_station["name"],
                                    "semantics": {
                                        "destinationLocation": {
                                            "latitude": float(to_station["latitude"]),
                                            "longitude": float(to_station["longitude"]),
                                        },
                                        "destinationStationName": to_station["name"]
                                    }
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
                            elif "toStationNameUTF8" in ticket_document:
                                pass_fields["primaryFields"].append({
                                    "key": "to-station",
                                    "label": "to-station-label",
                                    "value": ticket_document["toStationNameUTF8"],
                                    "semantics": {
                                        "destinationStationName": ticket_document["toStationNameUTF8"]
                                    }
                                })
                            elif "toStationIA5" in ticket_document:
                                pass_fields["primaryFields"].append({
                                    "key": "to-station",
                                    "label": "to-station-label",
                                    "value": ticket_document["toStationIA5"],
                                    "semantics": {
                                        "destinationStationName": ticket_document["toStationIA5"]
                                    }
                                })
                        else:
                            if "classCode" in ticket_document and ticket_document["classCode"] != "notApplicable":
                                pass_fields["auxiliaryFields"].append({
                                    "key": "class-code",
                                    "label": "class-code-label",
                                    "value": f"class-code-{ticket_document['classCode']}-label",
                                })

                        has_product_name = False
                        if len(ticket_document.get("tariffs", [])) >= 1:
                            tariff = ticket_document["tariffs"][0]
                            if "tariffDesc" in tariff:
                                has_product_name = True
                                pass_fields["headerFields"].append({
                                    "key": "product",
                                    "label": "product-label",
                                    "value": tariff["tariffDesc"]
                                })
                                pass_fields["backFields"].append({
                                    "key": "product-back",
                                    "label": "product-label",
                                    "value": tariff["tariffDesc"],
                                })

                            reduction_cards = ", ".join(
                                list(map(lambda c: c["cardName"], tariff.get("reductionCard", []))))
                            if reduction_cards:
                                pass_fields["auxiliaryFields"].append({
                                    "key": f"reduction-card",
                                    "label": "reduction-card-label",
                                    "value": reduction_cards,
                                })

                        pass_fields["backFields"].append({
                            "key": "return-included",
                            "label": "return-included-label",
                            "value": "yes-label" if \
                                ticket_document["returnIncluded"] else "no-label",
                        })

                        if "productIdIA5" in ticket_document:
                            if not has_product_name:
                                pass_fields["headerFields"].append({
                                    "key": "product",
                                    "label": "product-label",
                                    "value": ticket_document["productIdIA5"],
                                })
                                has_product_name = True

                            pass_fields["backFields"].append({
                                "key": "product-id",
                                "label": "product-id-label",
                                "value": ticket_document["productIdIA5"],
                            })

                        if "infoText" in ticket_document:
                            if not has_product_name:
                                pass_fields["headerFields"].append({
                                    "key": "info-text",
                                    "value": ticket_document["infoText"],
                                })

                            pass_fields["backFields"].append({
                                "key": "info-text",
                                "label": "info-label",
                                "value": ticket_document["infoText"],
                            })

                        f = "secondaryFields" if pass_type == "boardingPass" else "auxiliaryFields"
                        pass_fields[f].append({
                            "key": "validity-start",
                            "label": "validity-start-label",
                            "dateStyle": "PKDateStyleMedium",
                            "timeStyle": "PKDateStyleShort",
                            "value": validity_start.isoformat() if validity_start.tzinfo else validity_start.strftime(
                                "%Y-%m-%dT%H:%M:%SZ"),
                            "ignoresTimeZone": True,
                        })
                        pass_fields[f].append({
                            "key": "validity-end",
                            "label": "validity-end-label",
                            "dateStyle": "PKDateStyleMedium",
                            "timeStyle": "PKDateStyleShort",
                            "value": validity_end.isoformat() if validity_end.tzinfo else validity_end.strftime(
                                "%Y-%m-%dT%H:%M:%SZ"),
                            "changeMessage": "validity-end-change",
                            "ignoresTimeZone": True,
                        })

                        if "validRegionDesc" in ticket_document:
                            pass_fields["backFields"].append({
                                "key": "valid-region",
                                "label": "valid-region-label",
                                "value": ticket_document["validRegionDesc"].replace("<", "&lt;").replace(">", "&gt;"),
                            })

                        if train_links and not reservation_document:
                            departure_time = templatetags.rics.rics_departure_time(train_links[0], issued_at)
                            train_number = ", ".join(
                                list(dict.fromkeys(
                                    [l.get("trainIA5") or str(l.get("trainNum")) for l in train_links])))
                            pass_fields[f] = list(filter(
                                lambda e: e["key"] not in ("validity-start", "validity-end"),
                                pass_fields[f]
                            ))
                            if from_station and "time_zone" in from_station and departure_time.tzinfo:
                                departure_time = departure_time.astimezone(pytz.timezone(from_station["time_zone"]))
                            departure_time_str = departure_time.isoformat() if departure_time.tzinfo else departure_time.strftime("%Y-%m-%dT%H:%M:%SZ")
                            pass_json["relevantDates"].append({"date": departure_time_str})
                            pass_fields["headerFields"] = [{
                                "key": "train-number",
                                "label": "train-number-label",
                                "value": train_number,
                                "semantics": {
                                    "vehicleNumber": train_number
                                }
                            }, {
                                "key": "departure-date",
                                "label": "departure-date-label",
                                "value": departure_time_str,
                                "dateStyle": "PKDateStyleShort",
                                "timeStyle": "PKDateStyleNone",
                                "ignoresTimeZone": True,
                            }]
                            pass_fields["secondaryFields"].append({
                                "key": "departure-time",
                                "label": "departure-time-label",
                                "value": departure_time_str,
                                "dateStyle": "PKDateStyleNone",
                                "timeStyle": "PKDateStyleShort",
                                "ignoresTimeZone": True,
                                "semantics": {
                                    "originalDepartureDate": departure_time_str,
                                }
                            })

                        if "returnDescription" in ticket_document:
                            return_document = ticket_document["returnDescription"]
                            has_return = True
                            return_pass_json = copy.deepcopy(pass_json)
                            return_pass_json["locations"] = []

                            return_pass_fields["headerFields"] = []
                            return_pass_fields["auxiliaryFields"].extend(pass_fields["auxiliaryFields"])
                            return_pass_fields["secondaryFields"].extend(
                                filter(lambda f: f["key"] != "departure-time", pass_fields["secondaryFields"]))
                            return_pass_fields["backFields"].extend(filter(
                                lambda f: f["key"] not in ("valid-region", "to-station-back", "from-station-back"),
                                pass_fields["backFields"]
                            ))

                            if "fromStationNum" in return_document and "toStationNum" in return_document:
                                return_pass_type = "boardingPass"
                                return_pass_fields["transitType"] = "PKTransitTypeTrain"

                                from_station = templatetags.rics.get_station(return_document["fromStationNum"],
                                                                             ticket_document)
                                to_station = templatetags.rics.get_station(return_document["toStationNum"],
                                                                           ticket_document)

                                if from_station:
                                    return_pass_fields["primaryFields"].append({
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
                                    return_pass_json["locations"].append({
                                        "latitude": float(from_station["latitude"]),
                                        "longitude": float(from_station["longitude"]),
                                        "relevantText": from_station["name"]
                                    })
                                    maps_link = urllib.parse.urlencode({
                                        "q": from_station["name"],
                                        "ll": f"{from_station['latitude']},{from_station['longitude']}"
                                    })
                                    return_pass_fields["backFields"].append({
                                        "key": "from-station-back",
                                        "label": "from-station-label",
                                        "value": from_station["name"],
                                        "attributedValue": f"<a href=\"https://maps.apple.com/?{maps_link}\">{from_station['name']}</a>",
                                    })
                                elif "fromStationNameUTF8" in return_document:
                                    return_pass_fields["primaryFields"].append({
                                        "key": "from-station",
                                        "label": "from-station-label",
                                        "value": return_document["fromStationNameUTF8"],
                                        "semantics": {
                                            "departureStationName": return_document["fromStationNameUTF8"]
                                        }
                                    })
                                elif "fromStationIA5" in return_document:
                                    return_pass_fields["primaryFields"].append({
                                        "key": "from-station",
                                        "label": "from-station-label",
                                        "value": return_document["fromStationIA5"],
                                        "semantics": {
                                            "departureStationName": return_document["fromStationIA5"]
                                        }
                                    })

                                if to_station:
                                    return_pass_fields["primaryFields"].append({
                                        "key": "to-station",
                                        "label": "to-station-label",
                                        "value": to_station["name"],
                                        "semantics": {
                                            "destinationLocation": {
                                                "latitude": float(to_station["latitude"]),
                                                "longitude": float(to_station["longitude"]),
                                            },
                                            "destinationStationName": to_station["name"]
                                        }
                                    })
                                    return_pass_json["locations"].append({
                                        "latitude": float(to_station["latitude"]),
                                        "longitude": float(to_station["longitude"]),
                                        "relevantText": to_station["name"]
                                    })
                                    maps_link = urllib.parse.urlencode({
                                        "q": to_station["name"],
                                        "ll": f"{to_station['latitude']},{to_station['longitude']}"
                                    })
                                    return_pass_fields["backFields"].append({
                                        "key": "to-station-back",
                                        "label": "to-station-label",
                                        "value": to_station["name"],
                                        "attributedValue": f"<a href=\"https://maps.apple.com/?{maps_link}\">{to_station['name']}</a>",
                                    })
                                elif "toStationNameUTF8" in return_document:
                                    return_pass_fields["primaryFields"].append({
                                        "key": "to-station",
                                        "label": "to-station-label",
                                        "value": return_document["toStationNameUTF8"],
                                        "semantics": {
                                            "destinationStationName": return_document["toStationNameUTF8"]
                                        }
                                    })
                                elif "toStationIA5" in return_document:
                                    return_pass_fields["primaryFields"].append({
                                        "key": "to-station",
                                        "label": "to-station-label",
                                        "value": return_document["toStationIA5"],
                                        "semantics": {
                                            "destinationStationName": return_document["toStationIA5"]
                                        }
                                    })

                            if "validReturnRegionDesc" in return_document:
                                return_pass_fields["backFields"].append({
                                    "key": "valid-region",
                                    "label": "valid-region-label",
                                    "value": return_document["validReturnRegionDesc"],
                                })

                            if "validReturnRegion" in return_document and return_document["validReturnRegion"][0][
                                0] == "trainLink":
                                train_link = return_document["validReturnRegion"][0][1]
                                departure_time = templatetags.rics.rics_departure_time(train_link, issued_at)
                                train_number = train_link.get("trainIA5") or str(train_link.get("trainNum"))
                                if from_station and "time_zone" in from_station and departure_time.tzinfo:
                                    departure_time = departure_time.astimezone(pytz.timezone(from_station["time_zone"]))
                                departure_time_str = departure_time.isoformat() if departure_time.tzinfo else departure_time.strftime(
                                    "%Y-%m-%dT%H:%M:%SZ")
                                return_pass_json["relevantDates"].append({"date": departure_time_str})
                                return_pass_fields["headerFields"] = [{
                                    "key": "train-number",
                                    "label": "train-number-label",
                                    "value": train_number,
                                    "semantics": {
                                        "vehicleNumber": train_number
                                    }
                                }, {
                                    "key": "departure-date",
                                    "label": "departure-date-label",
                                    "value": departure_time_str,
                                    "dateStyle": "PKDateStyleShort",
                                    "timeStyle": "PKDateStyleNone",
                                    "ignoresTimeZone": True,
                                }]
                                return_pass_fields["secondaryFields"].append({
                                    "key": "departure-time",
                                    "label": "departure-time-label",
                                    "value": departure_time_str,
                                    "dateStyle": "PKDateStyleNone",
                                    "timeStyle": "PKDateStyleShort",
                                    "ignoresTimeZone": True,
                                    "semantics": {
                                        "originalDepartureDate": departure_time_str,
                                    }
                                })

                        pass_fields["backFields"].append({
                            "key": "validity-start-back",
                            "label": "validity-start-label",
                            "dateStyle": "PKDateStyleFull",
                            "timeStyle": "PKDateStyleFull",
                            "value": validity_start.isoformat() if validity_start.tzinfo else validity_start.strftime(
                                "%Y-%m-%dT%H:%M:%SZ"),
                        })
                        return_pass_fields["backFields"].append({
                            "key": "validity-start-back",
                            "label": "validity-start-label",
                            "dateStyle": "PKDateStyleFull",
                            "timeStyle": "PKDateStyleFull",
                            "value": validity_start.isoformat() if validity_start.tzinfo else validity_start.strftime(
                                "%Y-%m-%dT%H:%M:%SZ"),
                        })
                        pass_fields["backFields"].append({
                            "key": "validity-end-back",
                            "label": "validity-end-label",
                            "dateStyle": "PKDateStyleFull",
                            "timeStyle": "PKDateStyleFull",
                            "value": validity_end.isoformat() if validity_end.tzinfo else validity_end.strftime(
                                "%Y-%m-%dT%H:%M:%SZ"),
                        })
                        return_pass_fields["backFields"].append({
                            "key": "validity-end-back",
                            "label": "validity-end-label",
                            "dateStyle": "PKDateStyleFull",
                            "timeStyle": "PKDateStyleFull",
                            "value": validity_end.isoformat() if validity_end.tzinfo else validity_end.strftime(
                                "%Y-%m-%dT%H:%M:%SZ"),
                        })

                    if reservation_document:
                        pass_type = "boardingPass"
                        pass_fields["transitType"] = "PKTransitTypeTrain"

                        departure_time = templatetags.rics.rics_departure_time(reservation_document, issued_at)
                        arrival_time = templatetags.rics.rics_arrival_time(reservation_document, issued_at)
                        pass_json["relevantDates"].append({
                            "startDate": departure_time.isoformat(),
                            "endDate": arrival_time.isoformat(),
                        })
                        if not ticket_document:
                            pass_json["expirationDate"] = arrival_time.isoformat()

                            if "fromStationNum" in reservation_document:
                                from_station = templatetags.rics.get_station(
                                    reservation_document["fromStationNum"], reservation_document
                                )
                            else:
                                from_station = None

                            if "toStationNum" in reservation_document:
                                to_station = templatetags.rics.get_station(
                                    reservation_document["toStationNum"], reservation_document
                                )
                            else:
                                to_station = None

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
                                        "departureStationName": from_station["name"],
                                        "originalDepartureDate": departure_time.strftime("%Y-%m-%dT%H:%M:%SZ"),
                                    }
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
                            elif "fromStationNameUTF8" in reservation_document:
                                pass_fields["primaryFields"].append({
                                    "key": "from-station",
                                    "label": "from-station-label",
                                    "value": reservation_document["fromStationNameUTF8"],
                                    "semantics": {
                                        "departureStationName": reservation_document["fromStationNameUTF8"],
                                        "originalDepartureDate": departure_time.strftime("%Y-%m-%dT%H:%M:%SZ"),
                                    }
                                })
                            elif "fromStationIA5" in reservation_document:
                                pass_fields["primaryFields"].append({
                                    "key": "from-station",
                                    "label": "from-station-label",
                                    "value": reservation_document["fromStationIA5"],
                                    "semantics": {
                                        "departureStationName": reservation_document["fromStationIA5"],
                                        "originalDepartureDate": departure_time.strftime("%Y-%m-%dT%H:%M:%SZ"),
                                    }
                                })

                            if to_station:
                                pass_fields["primaryFields"].append({
                                    "key": "to-station",
                                    "label": "to-station-label",
                                    "value": to_station["name"],
                                    "semantics": {
                                        "destinationLocation": {
                                            "latitude": float(to_station["latitude"]),
                                            "longitude": float(to_station["longitude"]),
                                        },
                                        "destinationStationName": to_station["name"],
                                        "originalArrivalDate": arrival_time.strftime("%Y-%m-%dT%H:%M:%SZ"),
                                    }
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
                            elif "toStationNameUTF8" in reservation_document:
                                pass_fields["primaryFields"].append({
                                    "key": "to-station",
                                    "label": "to-station-label",
                                    "value": reservation_document["toStationNameUTF8"],
                                    "semantics": {
                                        "destinationStationName": reservation_document["toStationNameUTF8"],
                                        "originalArrivalDate": arrival_time.strftime("%Y-%m-%dT%H:%M:%SZ"),
                                    }
                                })
                            elif "toStationIA5" in reservation_document:
                                pass_fields["primaryFields"].append({
                                    "key": "to-station",
                                    "label": "to-station-label",
                                    "value": reservation_document["toStationIA5"],
                                    "semantics": {
                                        "destinationStationName": reservation_document["toStationIA5"],
                                        "originalArrivalDate": arrival_time.strftime("%Y-%m-%dT%H:%M:%SZ"),
                                    }
                                })

                            if "classCode" in reservation_document and reservation_document[
                                "classCode"] != "notApplicable":
                                pass_fields["auxiliaryFields"].append({
                                    "key": "class-code",
                                    "label": "class-code-label",
                                    "value": f"class-code-{reservation_document['classCode']}-label",
                                })

                        if "places" in reservation_document:
                            if "coach" in reservation_document["places"]:
                                pass_fields["auxiliaryFields"].append({
                                    "key": f"reservation-coach",
                                    "label": "coach-number-label",
                                    "value": reservation_document["places"]["coach"],
                                })
                            if "placeString" in reservation_document["places"]:
                                pass_fields["auxiliaryFields"].append({
                                    "key": f"reservation-seat",
                                    "label": "seat-number-label",
                                    "value": reservation_document["places"]["placeString"],
                                })
                            elif "placeNum" in reservation_document["places"]:
                                pass_fields["auxiliaryFields"].append({
                                    "key": f"reservation-seat",
                                    "label": "seat-number-label",
                                    "value": ", ".join(list(map(str, reservation_document["places"]["placeNum"]))),
                                })

                        pass_fields["secondaryFields"] = []

                        if from_station and "time_zone" in from_station and departure_time.tzinfo:
                            departure_time = departure_time.astimezone(pytz.timezone(from_station["time_zone"]))
                        if to_station and "time_zone" in to_station and arrival_time.tzinfo:
                            arrival_time = arrival_time.astimezone(pytz.timezone(to_station["time_zone"]))

                        one_day_ticket = departure_time.date() == arrival_time.date()
                        f = "secondaryFields" if pass_type == "boardingPass" else "auxiliaryFields"
                        pass_fields[f] = list(filter(
                            lambda e: e["key"] not in ("validity-start", "validity-end"),
                            pass_fields[f]
                        ))
                        pass_fields["secondaryFields"].append({
                            "key": "departure-time",
                            "label": "departure-time-label",
                            "dateStyle": "PKDateStyleNone" if one_day_ticket else "PKDateStyleMedium",
                            "timeStyle": "PKDateStyleShort",
                            "value": departure_time.isoformat() if departure_time.tzinfo else departure_time.strftime(
                                "%Y-%m-%dT%H:%M:%SZ"),
                            "ignoresTimeZone": True
                        })
                        pass_fields["secondaryFields"].append({
                            "key": "arrival-time",
                            "label": "arrival-time-label",
                            "dateStyle": "PKDateStyleNone" if one_day_ticket else "PKDateStyleMedium",
                            "timeStyle": "PKDateStyleShort",
                            "value": arrival_time.isoformat() if arrival_time.tzinfo else arrival_time.strftime(
                                "%Y-%m-%dT%H:%M:%SZ"),
                            "ignoresTimeZone": True
                        })
                        if one_day_ticket:
                            pass_fields["headerFields"].append({
                                "key": "departure-date",
                                "label": "departure-date-label",
                                "dateStyle": "PKDateStyleMedium",
                                "timeStyle": "PKDateStyleNone",
                                "value": departure_time.isoformat() if departure_time.tzinfo else departure_time.strftime(
                                "%Y-%m-%dT%H:%M:%SZ"),
                                "ignoresTimeZone": True
                            })
                        pass_fields["backFields"].append({
                            "key": "departure-time-back",
                            "label": "departure-time-label",
                            "dateStyle": "PKDateStyleFull",
                            "timeStyle": "PKDateStyleFull",
                            "value": departure_time.isoformat() if departure_time.tzinfo else departure_time.strftime(
                                "%Y-%m-%dT%H:%M:%SZ"),
                            "ignoresTimeZone": True
                        })
                        pass_fields["backFields"].append({
                            "key": "arrival-time-back",
                            "label": "arrival-time-label",
                            "dateStyle": "PKDateStyleFull",
                            "timeStyle": "PKDateStyleFull",
                            "value": arrival_time.isoformat() if arrival_time.tzinfo else arrival_time.strftime(
                                "%Y-%m-%dT%H:%M:%SZ"),
                            "ignoresTimeZone": True
                        })

                        if not ticket_document:
                            for i, carrier_id in enumerate(reservation_document.get("carrierNum", [])):
                                if carrier := uic.rics.get_rics(carrier_id):
                                    if carrier["url"]:
                                        pass_fields["backFields"].append({
                                            "key": f"carrier-{i}",
                                            "label": "carrier-label",
                                            "value": carrier["full_name"],
                                            "attributedValue": f"<a href=\"{carrier['url']}\">{carrier['full_name']}</a>",
                                        })
                                    else:
                                        pass_fields["backFields"].append({
                                            "key": f"carrier-{i}",
                                            "label": "carrier-label",
                                            "value": carrier["full_name"],
                                        })
                                else:
                                    pass_fields["backFields"].append({
                                        "key": f"carrier-{i}",
                                        "label": "carrier-label",
                                        "value": str(carrier_id),
                                    })

                        train_number = reservation_document.get("trainIA5") or reservation_document.get("trainNum")
                        if train_number:
                            pass_fields["headerFields"].append({
                                "key": "train-number",
                                "label": "train-number-label",
                                "value": str(train_number),
                                "semantics": {
                                    "vehicleNumber": str(train_number)
                                }
                            })

                        if "referenceNum" in reservation_document:
                            pass_fields["backFields"].append({
                                "key": "reference-num",
                                "label": "reference-num-label",
                                "value": str(reservation_document["referenceNum"])
                            })

                elif customer_card_document:
                    validity_start = templatetags.rics.rics_valid_from_date(customer_card_document)
                    validity_end = templatetags.rics.rics_valid_until_date(customer_card_document)

                    if "cardTypeDescr" in customer_card_document:
                        pass_fields["backFields"].append({
                            "key": "product-back",
                            "label": "product-label",
                            "value": customer_card_document["cardTypeDescr"]
                        })

                        if customer_card_document["cardTypeDescr"] in BC_STRIP_IMG:
                            pass_type = "storeCard"
                            add_pkp_img(pkp, BC_STRIP_IMG[customer_card_document["cardTypeDescr"]], "strip.png")

                        pass_fields["headerFields"].append({
                            "key": "product",
                            "label": "product-label",
                            "value": customer_card_document["cardTypeDescr"]
                        })

                    if "cardIdIA5" in customer_card_document:
                        pass_fields["secondaryFields"].append({
                            "key": "card-id",
                            "label": "card-id-label",
                            "value": customer_card_document["cardIdIA5"],
                        })
                    elif "cardIdNum" in customer_card_document:
                        pass_fields["secondaryFields"].append({
                            "key": "card-id",
                            "label": "card-id-label",
                            "value": str(customer_card_document["cardIdNum"]),
                        })

                    if "classCode" in customer_card_document and customer_card_document["classCode"] != "notApplicable":
                        pass_fields["secondaryFields"].append({
                            "key": "class-code",
                            "label": "class-code-label",
                            "value": f"class-code-{customer_card_document['classCode']}-label",
                        })

                    if validity_end:
                        pass_json["expirationDate"] = validity_end.strftime("%Y-%m-%dT%H:%M:%SZ")
                        pass_fields["auxiliaryFields"].append({
                            "key": "validity-end",
                            "label": "validity-end-label",
                            "dateStyle": "PKDateStyleMedium",
                            "timeStyle": "PKDateStyleNone",
                            "value": validity_end.strftime("%Y-%m-%dT%H:%M:%SZ"),
                            "ignoresTimeZone": True,
                            "changeMessage": "validity-end-change"
                        })
                        pass_fields["backFields"].append({
                            "key": "validity-end-back",
                            "label": "validity-end-label",
                            "dateStyle": "PKDateStyleFull",
                            "timeStyle": "PKDateStyleNone",
                            "value": validity_end.strftime("%Y-%m-%dT%H:%M:%SZ"),
                            "ignoresTimeZone": True
                        })

                    if validity_start:
                        pass_fields["auxiliaryFields"].append({
                            "key": "validity-start",
                            "label": "validity-start-label",
                            "dateStyle": "PKDateStyleMedium",
                            "timeStyle": "PKDateStyleNone",
                            "value": validity_start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                            "ignoresTimeZone": True
                        })
                        pass_fields["backFields"].append({
                            "key": "validity-start-back",
                            "label": "validity-start-label",
                            "dateStyle": "PKDateStyleFull",
                            "timeStyle": "PKDateStyleNone",
                            "value": validity_start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                            "ignoresTimeZone": True
                        })

                elif pass_document:
                    validity_start = templatetags.rics.rics_valid_from(pass_document, issued_at)
                    validity_end = templatetags.rics.rics_valid_until(pass_document, issued_at)

                    pass_json["expirationDate"] = validity_end.strftime("%Y-%m-%dT%H:%M:%SZ")

                    if "passType" in pass_document:
                        if pass_document["passType"] == 1:
                            product_name = "Eurail Global Pass"
                        elif pass_document["passType"] == 2:
                            product_name = "Interrail Global Pass"
                        elif pass_document["passType"] == 3:
                            product_name = "Interrail One Country Pass"
                        elif pass_document["passType"] == 4:
                            product_name = "Eurail One Country Pass"
                        elif pass_document["passType"] == 5:
                            product_name = "Eurail/Interrail Emergency ticket"
                        else:
                            product_name = f"Pass type {pass_document['passType']}"
                    elif "passDescription" in pass_document:
                        product_name = pass_document["passDescription"]
                    else:
                        product_name = None

                    if product_name:
                        pass_fields["headerFields"].append({
                            "key": "product",
                            "label": "product-label",
                            "value": product_name
                        })
                        pass_fields["backFields"].append({
                            "key": "product-back",
                            "label": "product-label",
                            "value": product_name,
                        })

                    if "classCode" in pass_document and pass_document["classCode"] != "notApplicable":
                        pass_fields["auxiliaryFields"].append({
                            "key": "class-code",
                            "label": "class-code-label",
                            "value": f"class-code-{pass_document['classCode']}-label",
                        })

                    if validity_start.date() == validity_end.date() and validity_start.time() == datetime.time(0, 0,
                                                                                                               0) and validity_end.time() == datetime.time(
                        23, 59, 59):
                        pass_fields["auxiliaryFields"].append({
                            "key": "validity-on",
                            "label": "validity-on-label",
                            "dateStyle": "PKDateStyleMedium",
                            "timeStyle": "PKDateStyleNone",
                            "value": validity_start.isoformat() if validity_start.tzinfo else validity_start.strftime(
                                "%Y-%m-%dT%H:%M:%SZ"),
                            "ignoresTimeZone": True
                        })
                    else:
                        pass_fields["auxiliaryFields"].append({
                            "key": "validity-start",
                            "label": "validity-start-label",
                            "dateStyle": "PKDateStyleMedium",
                            "timeStyle": "PKDateStyleMedium" if validity_start.time() != datetime.time(0, 0,
                                                                                                       0) else "PKDateStyleNone",
                            "value": validity_start.isoformat() if validity_start.tzinfo else validity_start.strftime(
                                "%Y-%m-%dT%H:%M:%SZ"),
                            "ignoresTimeZone": True
                        })
                        pass_fields["auxiliaryFields"].append({
                            "key": "validity-end",
                            "label": "validity-end-label",
                            "dateStyle": "PKDateStyleMedium",
                            "timeStyle": "PKDateStyleMedium" if validity_end.time() != datetime.time(23, 59,
                                                                                                     59) else "PKDateStyleNone",
                            "value": validity_end.isoformat() if validity_end.tzinfo else validity_end.strftime(
                                "%Y-%m-%dT%H:%M:%SZ"),
                            "changeMessage": "validity-end-change",
                            "ignoresTimeZone": True
                        })

                    pass_fields["backFields"].append({
                        "key": "validity-start-back",
                        "label": "validity-start-label",
                        "dateStyle": "PKDateStyleFull",
                        "timeStyle": "PKDateStyleFull",
                        "value": validity_start.isoformat() if validity_start.tzinfo else validity_start.strftime(
                            "%Y-%m-%dT%H:%M:%SZ"),
                        "ignoresTimeZone": True
                    })
                    pass_fields["backFields"].append({
                        "key": "validity-end-back",
                        "label": "validity-end-label",
                        "dateStyle": "PKDateStyleFull",
                        "timeStyle": "PKDateStyleFull",
                        "value": validity_end.isoformat() if validity_end.tzinfo else validity_end.strftime(
                            "%Y-%m-%dT%H:%M:%SZ"),
                        "ignoresTimeZone": True
                    })

                elif delay_conf_document:
                    pass_fields["headerFields"].append({
                        "key": "delay-conf",
                        "value": "delay-conf-label"
                    })

                    train_num = delay_conf_document.get("trainIA5") or delay_conf_document.get("trainNum")
                    if train_num:
                        pass_fields["auxiliaryFields"].append({
                            "key": "train-number",
                            "label": "train-number-label",
                            "value": str(train_num),
                            "semantics": {
                                "vehicleNumber": str(train_num)
                            }
                        })

                    if departure_time := templatetags.rics.rics_delay_departure_time(delay_conf_document):
                        departure_time_str = departure_time.isoformat() if departure_time.tzinfo else \
                            departure_time.strftime("%Y-%m-%dT%H:%M:%SZ")
                        pass_fields["auxiliaryFields"].append({
                            "key": "time",
                            "label": "original-time-label",
                            "value": departure_time_str,
                            "dateStyle": "PKDateStyleShort",
                            "timeStyle": "PKDateStyleShort",
                            "ignoresTimeZone": True
                        })

                    if "stationNum" in delay_conf_document:
                        station = templatetags.rics.get_station(delay_conf_document["stationNum"], ticket_document)
                        if station:
                            pass_fields["secondaryFields"].append({
                                "key": "station",
                                "label": "station-label",
                                "value": station["name"],
                            })
                            maps_link = urllib.parse.urlencode({
                                "q": station["name"],
                                "ll": f"{station['latitude']},{station['longitude']}"
                            })
                            pass_fields["backFields"].append({
                                "key": "station-back",
                                "label": "station-label",
                                "value": station["name"],
                                "attributedValue": f"<a href=\"https://maps.apple.com/?{maps_link}\">{station['name']}</a>",
                            })
                        elif "stationIA5" in delay_conf_document:
                            pass_fields["secondaryFields"].append({
                                "key": "station",
                                "label": "station-label",
                                "value": delay_conf_document["stationIA5"],
                            })

                    if delay_conf_document["trainCancelled"]:
                        pass_fields["secondaryFields"].append({
                            "key": "train-cancelled",
                            "value": "train-cancelled-label"
                        })
                    else:
                        pass_fields["secondaryFields"].append({
                            "key": "train-delay",
                            "label": "train-delay-label",
                            "value": f"{delay_conf_document['delay']} min"
                        })

                    if "infoText" in delay_conf_document:
                        pass_fields["backFields"].append({
                            "key": "info-text",
                            "label": "info-label",
                            "value": delay_conf_document["infoText"],
                        })

                    for i, t in enumerate(delay_conf_document.get("affectedTickets", [])):
                        pass_fields["backFields"].append({
                            "key": f"affected-ticket-{i}",
                            "label": f"for-ticket-{t['ticketType']}-label",
                            "value": t.get("issuerPNR", "")
                        })

            if len(ticket_data.flex.data.get("travelerDetail", {}).get("traveler", [])) >= 1:
                passenger = ticket_data.flex.data["travelerDetail"]["traveler"][0]
                first_name = passenger.get('firstName', "").strip()
                last_name = passenger.get('lastName', "").strip()

                if first_name or last_name:
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
                        return_pass_fields["primaryFields"].append(field_data)
                    elif pass_type == "storeCard":
                        pass_fields["secondaryFields"].append(field_data)
                        return_pass_fields["secondaryFields"].append(field_data)
                    else:
                        pass_fields["auxiliaryFields"].append(field_data)
                        return_pass_fields["auxiliaryFields"].append(field_data)

                dob = templatetags.rics.rics_traveler_dob(passenger)
                if dob:
                    dob = datetime.datetime.combine(dob, datetime.time.min)
                    dob_field = {
                        "key": "date-of-birth",
                        "label": "date-of-birth-label",
                        "dateStyle": "PKDateStyleMedium",
                        "value": dob.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    }
                    if pass_type == "boardingPass":
                        pass_fields["auxiliaryFields"].append(dob_field)
                        return_pass_fields["auxiliaryFields"].append(dob_field)
                    else:
                        pass_fields["secondaryFields"].append(dob_field)
                        return_pass_fields["secondaryFields"].append(dob_field)
                else:
                    dob_year = passenger.get("yearOfBirth", 0)
                    dob_month = passenger.get("monthOfBirth", 0)
                    if dob_year != 0 and dob_month != 0:
                        if pass_type == "boardingPass":
                            pass_fields["auxiliaryFields"].append({
                                "key": "month-of-birth",
                                "label": "month-of-birth-label",
                                "value": f"{dob_month:02d}.{dob_year:04d}",
                            })
                            return_pass_fields["auxiliaryFields"].append({
                                "key": "month-of-birth",
                                "label": "month-of-birth-label",
                                "value": f"{dob_month:02d}.{dob_year:04d}",
                            })
                        else:
                            pass_fields["secondaryFields"].append({
                                "key": "month-of-birth",
                                "label": "month-of-birth-label",
                                "value": f"{dob_month:02d}.{dob_year:04d}",
                            })
                            return_pass_fields["secondaryFields"].append({
                                "key": "month-of-birth",
                                "label": "month-of-birth-label",
                                "value": f"{dob_month:02d}.{dob_year:04d}",
                            })
                    elif dob_year != 0:
                        if pass_type == "boardingPass":
                            pass_fields["auxiliaryFields"].append({
                                "key": "year-of-birth",
                                "label": "year-of-birth-label",
                                "value": f"{dob_year:04d}",
                            })
                            return_pass_fields["auxiliaryFields"].append({
                                "key": "year-of-birth",
                                "label": "year-of-birth-label",
                                "value": f"{dob_year:04d}",
                            })
                        else:
                            pass_fields["secondaryFields"].append({
                                "key": "year-of-birth",
                                "label": "year-of-birth-label",
                                "value": f"{dob_year:04d}",
                            })
                            return_pass_fields["secondaryFields"].append({
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
                    return_pass_fields["secondaryFields"].append({
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
                    return_pass_fields["secondaryFields"].append({
                        "key": "passport-number",
                        "label": "passport-number-label",
                        "value": passenger["passportId"],
                    })

        elif ticket_data.db_bl:
            tz = pytz.timezone("Europe/Berlin")
            if ticket_data.db_bl.product:
                pass_fields["headerFields"].append({
                    "key": "product",
                    "label": "product-label",
                    "value": ticket_data.db_bl.product,
                })
                pass_fields["backFields"].append({
                    "key": "product-back",
                    "label": "product-label",
                    "value": ticket_data.db_bl.product,
                })

            if ticket_data.db_bl.from_station_uic and ticket_data.db_bl.to_station_uic:
                pass_type = "boardingPass"
                pass_fields["transitType"] = "PKTransitTypeTrain"

                from_station = templatetags.rics.get_station(ticket_data.db_bl.from_station_uic, "db")
                to_station = templatetags.rics.get_station(ticket_data.db_bl.to_station_uic, "db")

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
                elif ticket_data.db_bl.from_station_name:
                    pass_fields["primaryFields"].append({
                        "key": "from-station",
                        "label": "from-station-label",
                        "value": ticket_data.db_bl.from_station_name,
                        "semantics": {
                            "departureStationName": ticket_data.db_bl.from_station_name
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
                elif ticket_data.db_bl.to_station_name:
                    pass_fields["primaryFields"].append({
                        "key": "to-station",
                        "label": "to-station-label",
                        "value": ticket_data.db_bl.to_station_name,
                        "semantics": {
                            "destinationStationName": ticket_data.db_bl.to_station_name
                        }
                    })
            elif parsed_layout and len(parsed_layout.trips) and \
                    (parsed_layout.trips[0].departure_station or parsed_layout.trips[0].arrival_station):
                pass_type = "boardingPass"
                pass_fields["transitType"] = "PKTransitTypeTrain"

                pass_fields["primaryFields"].append({
                    "key": "from-station",
                    "label": "from-station-label",
                    "value": parsed_layout.trips[0].departure_station,
                    "semantics": {
                        "departureStationName": parsed_layout.trips[0].departure_station
                    }
                })
                pass_fields["primaryFields"].append({
                    "key": "to-station",
                    "label": "to-station-label",
                    "value": parsed_layout.trips[0].arrival_station,
                    "semantics": {
                        "departureStationName": parsed_layout.trips[0].arrival_station,
                    }
                })

            if ticket_data.db_bl.service_class:
                if pass_type == "boardingPass":
                    pass_fields["auxiliaryFields"].append({
                        "key": "class-code",
                        "label": "class-code-label",
                        "value": f"class-code-{ticket_data.db_bl.service_class}-label",
                    })
                else:
                    pass_fields["headerFields"].append({
                        "key": "class-code",
                        "label": "class-code-label",
                        "value": f"class-code-{ticket_data.db_bl.service_class}-label",
                    })
            elif parsed_layout and parsed_layout.travel_class:
                if pass_type == "boardingPass":
                    pass_fields["auxiliaryFields"].append({
                        "key": "class-code",
                        "label": "class-code-label",
                        "value": parsed_layout.travel_class,
                    })
                else:
                    pass_fields["headerFields"].append({
                        "key": "class-code",
                        "label": "class-code-label",
                        "value": parsed_layout.travel_class,
                    })

            if ticket_data.db_bl.validity_start or ticket_data.db_bl.validity_end:
                if ticket_data.db_bl.validity_start:
                    validity_start = tz.localize(
                        datetime.datetime.combine(ticket_data.db_bl.validity_start, datetime.time.min)) \
                        .astimezone(pytz.utc)
                else:
                    validity_start = None
                if ticket_data.db_bl.validity_end:
                    validity_end = tz.localize(
                        datetime.datetime.combine(ticket_data.db_bl.validity_end, datetime.time.max)) \
                        .astimezone(pytz.utc)
                else:
                    validity_end = None

                if validity_start and validity_end:
                    pass_json["relevantDates"].append({
                        "startDate": validity_start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                        "endDate": validity_end.strftime("%Y-%m-%dT%H:%M:%SZ")
                    })
                elif validity_start:
                    pass_json["relevantDates"].append({
                        "date": validity_start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    })

                if validity_start:
                    pass_fields["secondaryFields"].append({
                        "key": "validity-start",
                        "label": "validity-start-label",
                        "dateStyle": "PKDateStyleMedium",
                        "timeStyle": "PKDateStyleNone",
                        "value": validity_start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    })
                    pass_fields["backFields"].append({
                        "key": "validity-start-back",
                        "label": "validity-start-label",
                        "dateStyle": "PKDateStyleFull",
                        "timeStyle": "PKDateStyleFull",
                        "value": validity_start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    })

                if validity_end:
                    pass_json["expirationDate"] = validity_end.strftime("%Y-%m-%dT%H:%M:%SZ")
                    pass_fields["secondaryFields"].append({
                        "key": "validity-end",
                        "label": "validity-end-label",
                        "dateStyle": "PKDateStyleMedium",
                        "timeStyle": "PKDateStyleNone",
                        "value": validity_end.strftime("%Y-%m-%dT%H:%M:%SZ"),
                        "changeMessage": "validity-end-change"
                    })
                    pass_fields["backFields"].append({
                        "key": "validity-end-back",
                        "label": "validity-end-label",
                        "dateStyle": "PKDateStyleFull",
                        "timeStyle": "PKDateStyleFull",
                        "value": validity_end.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    })

            if ticket_data.db_bl.route:
                pass_fields["backFields"].append({
                    "key": "valid-region",
                    "label": "valid-region-label",
                    "value": ticket_data.db_bl.route,
                })
            elif parsed_layout and parsed_layout.train_data:
                pass_fields["backFields"].append({
                    "key": "train-data",
                    "label": "train-number-label",
                    "value": parsed_layout.train_data.replace("<", "&lt;").replace(">", "&gt;"),
                })

            if ticket_data.db_bl.traveller_forename or ticket_data.db_bl.traveller_surname:
                field_data = {
                    "key": "passenger",
                    "label": "passenger-label",
                    "value": f"{ticket_data.db_bl.traveller_forename}\n{ticket_data.db_bl.traveller_surname}"
                    if pass_type == "generic" else
                    f"{ticket_data.db_bl.traveller_forename} {ticket_data.db_bl.traveller_surname}",
                    "semantics": {
                        "passengerName": {
                            "familyName": ticket_data.db_bl.traveller_surname,
                            "givenName": ticket_data.db_bl.traveller_forename,
                        }
                    }
                }
                if pass_type == "generic":
                    pass_fields["primaryFields"].append(field_data)
                else:
                    pass_fields["auxiliaryFields"].append(field_data)

        elif ticket_data.cd_ut:
            pass_type = "boardingPass"
            pass_fields["transitType"] = "PKTransitTypeTrain"

            if ticket_data.cd_ut.pnr:
                pass_json["barcodes"][0]["altText"] = ticket_data.cd_ut.pnr

            from_station = None
            to_station = None

            if ticket_data.cd_ut.route_uic:
                from_station = templatetags.rics.get_station(ticket_data.cd_ut.route_uic[0], "uic")
                to_station = templatetags.rics.get_station(ticket_data.cd_ut.route_uic[-1], "uic")
            elif ticket_data.cd_ut.origin_uic or ticket_data.cd_ut.destination_uic:
                if ticket_data.cd_ut.origin_uic:
                    from_station = templatetags.rics.get_station(ticket_data.cd_ut.origin_uic, "uic")
                if ticket_data.cd_ut.destination_uic:
                    to_station = templatetags.rics.get_station(ticket_data.cd_ut.destination_uic, "uic")

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
            elif parsed_layout and parsed_layout.trips[0].departure_station:
                pass_fields["primaryFields"].append({
                    "key": "from-station",
                    "label": "from-station-label",
                    "value": parsed_layout.trips[0].departure_station,
                    "semantics": {
                        "departureStationName": parsed_layout.trips[0].departure_station
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
            elif parsed_layout and parsed_layout.trips[0].arrival_station:
                pass_fields["primaryFields"].append({
                    "key": "to-station",
                    "label": "to-station-label",
                    "value": parsed_layout.trips[0].arrival_station,
                    "semantics": {
                        "destinationStationName": parsed_layout.trips[0].arrival_station
                    }
                })

            if parsed_layout and parsed_layout.travel_class:
                pass_fields["auxiliaryFields"].append({
                    "key": "class-code",
                    "label": "class-code-label",
                    "value": parsed_layout.travel_class,
                })

            for res in ticket_data.cd_ut.reservations:
                pass_fields["headerFields"].append({
                    "key": "train-number",
                    "label": "train-number-label",
                    "value": res.train,
                })
                pass_fields["auxiliaryFields"].append({
                    "key": f"reservation-coach",
                    "label": "coach-number-label",
                    "value": res.carriage,
                })
                pass_fields["auxiliaryFields"].append({
                    "key": f"reservation-seat",
                    "label": "seat-number-label",
                    "value": res.seat,
                })

            if ticket_data.cd_ut.validity_start and ticket_data.cd_ut.validity_end:
                pass_json["relevantDates"].append({
                    "startDate": ticket_data.cd_ut.validity_start.astimezone(pytz.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "endDate": ticket_data.cd_ut.validity_end.astimezone(pytz.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                })
            elif ticket_data.cd_ut.validity_start:
                pass_json["relevantDates"].append({
                    "date": ticket_data.cd_ut.validity_start.astimezone(pytz.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                })

            if ticket_data.cd_ut.validity_start:
                pass_fields["secondaryFields"].append({
                    "key": "validity-start",
                    "label": "validity-start-label",
                    "dateStyle": "PKDateStyleMedium",
                    "timeStyle": "PKDateStyleShort",
                    "value": ticket_data.cd_ut.validity_start.astimezone(pytz.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                })
                pass_fields["backFields"].append({
                    "key": "validity-start-back",
                    "label": "validity-start-label",
                    "dateStyle": "PKDateStyleFull",
                    "timeStyle": "PKDateStyleFull",
                    "value": ticket_data.cd_ut.validity_start.astimezone(pytz.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                })

            if ticket_data.cd_ut.validity_end:
                pass_json["expirationDate"] = ticket_data.cd_ut.validity_end.astimezone(pytz.utc).strftime(
                    "%Y-%m-%dT%H:%M:%SZ")
                pass_fields["secondaryFields"].append({
                    "key": "validity-end",
                    "label": "validity-end-label",
                    "dateStyle": "PKDateStyleMedium",
                    "timeStyle": "PKDateStyleShort",
                    "value": ticket_data.cd_ut.validity_end.astimezone(pytz.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "changeMessage": "validity-end-change"
                })
                pass_fields["backFields"].append({
                    "key": "validity-end-back",
                    "label": "validity-end-label",
                    "dateStyle": "PKDateStyleFull",
                    "timeStyle": "PKDateStyleFull",
                    "value": ticket_data.cd_ut.validity_end.astimezone(pytz.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                })

            if ticket_data.cd_ut.name:
                pass_fields["auxiliaryFields"].append({
                    "key": "passenger",
                    "label": "passenger-label",
                    "value": ticket_data.cd_ut.name,
                })

        elif ticket_data.oebb_99:
            if ticket_data.oebb_99.validity_end:
                pass_json["expirationDate"] = ticket_data.oebb_99.validity_end.isoformat()

            if parsed_layout and parsed_layout.travel_class:
                pass_fields["auxiliaryFields"].append({
                    "key": "class-code",
                    "label": "class-code-label",
                    "value": parsed_layout.travel_class,
                })

            one_day_ticket = ticket_data.oebb_99.validity_start.date() == ticket_data.oebb_99.validity_end.date() \
                if ticket_data.oebb_99.validity_start and ticket_data.oebb_99.validity_end else False
            if one_day_ticket:
                pass_fields["headerFields"].append({
                    "key": "departure-date",
                    "label": "departure-date-label",
                    "dateStyle": "PKDateStyleMedium",
                    "timeStyle": "PKDateStyleNone",
                    "value": ticket_data.oebb_99.validity_start.isoformat(),
                    "ignoresTimeZone": True
                })

            if ticket_data.oebb_99.trains:
                if ticket_data.oebb_99.validity_start and ticket_data.oebb_99.validity_end:
                    pass_json["relevantDates"].append({
                        "startDate": ticket_data.oebb_99.validity_start.isoformat(),
                        "endDate": ticket_data.oebb_99.validity_end.isoformat(),
                    })
                elif ticket_data.oebb_99.validity_start:
                    pass_json["relevantDates"].append({
                        "date": ticket_data.oebb_99.validity_start.isoformat(),
                    })

                train_number = ", ".join(list(map(lambda t: str(t.train_number), ticket_data.oebb_99.trains)))
                pass_fields["headerFields"].append({
                    "key": "train-number",
                    "label": "train-number-label",
                    "value": train_number,
                    "semantics": {
                        "vehicleNumber": train_number,
                    }
                })
                if len(ticket_data.oebb_99.trains) == 1 and ticket_data.oebb_99.trains[0].carriage_number:
                    pass_fields["auxiliaryFields"].append({
                        "key": "coach-number",
                        "label": "coach-number-label",
                        "value": str(ticket_data.oebb_99.trains[0].carriage_number),
                    })
                if ticket_data.oebb_99.validity_start:
                    pass_fields["secondaryFields"].append({
                        "key": "departure-time",
                        "label": "departure-time-label",
                        "dateStyle": "PKDateStyleNone" if one_day_ticket else "PKDateStyleMedium",
                        "timeStyle": "PKDateStyleShort",
                        "value": ticket_data.oebb_99.validity_start.isoformat(),
                        "ignoresTimeZone": True,
                    })
                if ticket_data.oebb_99.validity_end:
                    pass_fields["secondaryFields"].append({
                        "key": "arrival-time",
                        "label": "arrival-time-label",
                        "dateStyle": "PKDateStyleNone" if one_day_ticket else "PKDateStyleMedium",
                        "timeStyle": "PKDateStyleShort",
                        "value": ticket_data.oebb_99.validity_end.isoformat(),
                        "ignoresTimeZone": True,
                    })
            elif not one_day_ticket:
                if ticket_data.oebb_99.validity_start:
                    pass_fields["secondaryFields"].append({
                        "key": "validity-start",
                        "label": "validity-start-label",
                        "dateStyle": "PKDateStyleMedium",
                        "timeStyle": "PKDateStyleNone",
                        "value": ticket_data.oebb_99.validity_start.isoformat(),
                        "ignoresTimeZone": True,
                    })
                if ticket_data.oebb_99.validity_end:
                    pass_fields["secondaryFields"].append({
                        "key": "validity-end",
                        "label": "validity-end-label",
                        "dateStyle": "PKDateStyleMedium",
                        "timeStyle": "PKDateStyleNone",
                        "value": ticket_data.oebb_99.validity_end.isoformat(),
                        "changeMessage": "validity-end-change",
                        "ignoresTimeZone": True,
                    })

            if parsed_layout and parsed_layout.document_type:
                pass_fields["backFields"].append({
                    "key": "document-type",
                    "label": "product-label",
                    "value": parsed_layout.document_type,
                })

            if ticket_data.oebb_99.validity_start:
                pass_fields["backFields"].append({
                    "key": "validity-start-back",
                    "label": "validity-start-label",
                    "dateStyle": "PKDateStyleFull",
                    "timeStyle": "PKDateStyleFull",
                    "value": ticket_data.oebb_99.validity_start.isoformat(),
                    "ignoresTimeZone": True,
                })
            if ticket_data.oebb_99.validity_end:
                pass_fields["backFields"].append({
                    "key": "validity-end-back",
                    "label": "validity-end-label",
                    "dateStyle": "PKDateStyleFull",
                    "timeStyle": "PKDateStyleFull",
                    "value": ticket_data.oebb_99.validity_end.isoformat(),
                    "ignoresTimeZone": True,
                })

            if parsed_layout and parsed_layout.traveller:
                pass_fields["backFields"].append({
                    "key": "traveller",
                    "label": "passenger-label",
                    "value": parsed_layout.traveller,
                })

            if parsed_layout and parsed_layout.train_data:
                pass_fields["backFields"].append({
                    "key": "train-data",
                    "label": "train-number-label",
                    "value": parsed_layout.train_data,
                })

            if parsed_layout and parsed_layout.extra:
                pass_fields["backFields"].append({
                    "key": "extra-data",
                    "label": "other-data-label",
                    "value": parsed_layout.extra,
                })

            if parsed_layout and parsed_layout.operator_rics:
                if carrier := uic.rics.get_rics(parsed_layout.operator_rics):
                    if carrier["url"]:
                        pass_fields["backFields"].append({
                            "key": "carrier",
                            "label": "carrier-label",
                            "value": carrier["full_name"],
                            "attributedValue": f"<a href=\"{carrier['url']}\">{carrier['full_name']}</a>",
                        })
                    else:
                        pass_fields["backFields"].append({
                            "key": "carrier",
                            "label": "carrier-label",
                            "value": carrier["full_name"],
                        })
                else:
                    pass_fields["backFields"].append({
                        "key": "carrier",
                        "label": "carrier-label",
                        "value": parsed_layout.operator_rics,
                    })

            if parsed_layout and parsed_layout.price:
                pass_fields["backFields"].append({
                    "key": "price",
                    "label": "price-label",
                    "value": parsed_layout.price,
                })

            if parsed_layout and parsed_layout.conditions:
                pass_fields["backFields"].append({
                    "key": "conditions",
                    "label": "product-conditions-label",
                    "value": parsed_layout.conditions,
                })

            if parsed_layout and parsed_layout.trips:
                pass_type = "boardingPass"
                pass_fields["transitType"] = "PKTransitTypeTrain"

                pass_fields["primaryFields"].append({
                    "key": "from-station",
                    "label": "from-station-label",
                    "value": parsed_layout.trips[0].departure_station,
                    "semantics": {
                        "departureStationName": parsed_layout.trips[0].departure_station
                    }
                })
                pass_fields["primaryFields"].append({
                    "key": "to-station",
                    "label": "to-station-label",
                    "value": parsed_layout.trips[0].arrival_station,
                    "semantics": {
                        "departureStationName": parsed_layout.trips[0].arrival_station,
                    }
                })

        elif ticket_data.bravo:
            if ticket_data.bravo.valid_to:
                pass_json["expirationDate"] = ticket_data.bravo.valid_to.isoformat()

            if parsed_layout and parsed_layout.travel_class:
                pass_fields["auxiliaryFields"].append({
                    "key": "class-code",
                    "label": "class-code-label",
                    "value": parsed_layout.travel_class,
                })

            if ticket_data.bravo.valid_from:
                pass_fields["secondaryFields"].append({
                    "key": "validity-start",
                    "label": "validity-start-label",
                    "dateStyle": "PKDateStyleMedium",
                    "timeStyle": "PKDateStyleShort",
                    "value": ticket_data.bravo.valid_from.isoformat(),
                    "ignoresTimeZone": True,
                })
            if ticket_data.bravo.valid_to:
                pass_fields["secondaryFields"].append({
                    "key": "validity-end",
                    "label": "validity-end-label",
                    "dateStyle": "PKDateStyleMedium",
                    "timeStyle": "PKDateStyleShort",
                    "value": ticket_data.bravo.valid_to.isoformat(),
                    "changeMessage": "validity-end-change",
                    "ignoresTimeZone": True,
                })

        elif ticket_data.dt_ti or ticket_data.dt_pa:
            if ticket_data.dt_ti:
                if ticket_data.dt_ti.product_name:
                    pass_fields["headerFields"].append({
                        "key": "product",
                        "label": "product-label",
                        "value": ticket_data.dt_ti.product_name,
                    })
                    pass_fields["backFields"].append({
                        "key": "product-back",
                        "label": "product-label",
                        "value": ticket_data.dt_ti.product_name,
                    })

                if ticket_data.dt_ti.validity_start:
                    pass_fields["auxiliaryFields"].append({
                        "key": "validity-start",
                        "label": "validity-start-label",
                        "dateStyle": "PKDateStyleMedium",
                        "timeStyle": "PKDateStyleShort",
                        "value": ticket_data.dt_ti.validity_start.isoformat(),
                        "ignoresTimeZone": True,
                    })
                    pass_fields["backFields"].append({
                        "key": "validity-start-back",
                        "label": "validity-start-label",
                        "dateStyle": "PKDateStyleFull",
                        "timeStyle": "PKDateStyleFull",
                        "value": ticket_data.dt_ti.validity_start.isoformat(),
                        "ignoresTimeZone": True,
                    })

                if ticket_data.dt_ti.validity_end:
                    pass_json["expirationDate"] = ticket_data.dt_ti.validity_end.isoformat()
                    pass_fields["auxiliaryFields"].append({
                        "key": "validity-end",
                        "label": "validity-end-label",
                        "dateStyle": "PKDateStyleMedium",
                        "timeStyle": "PKDateStyleShort",
                        "value": ticket_data.dt_ti.validity_end.isoformat(),
                        "changeMessage": "validity-end-change",
                        "ignoresTimeZone": True
                    })
                    pass_fields["backFields"].append({
                        "key": "validity-end-back",
                        "label": "validity-end-label",
                        "dateStyle": "PKDateStyleFull",
                        "timeStyle": "PKDateStyleFull",
                        "value": ticket_data.dt_ti.validity_end.isoformat(),
                        "ignoresTimeZone": True,
                    })

            if ticket_data.dt_pa and ticket_data.dt_pa.passenger_name:
                pass_fields["primaryFields"].append({
                    "key": "passenger",
                    "label": "passenger-label",
                    "value": ticket_data.dt_pa.passenger_name,
                })

        elif ticket_data.vor_fi or ticket_data.vor_vd:
            if ticket_data.vor_fi:
                pass_fields["auxiliaryFields"].append({
                    "key": "validity-start",
                    "label": "validity-start-label",
                    "dateStyle": "PKDateStyleMedium",
                    "timeStyle": "PKDateStyleShort",
                    "value": ticket_data.vor_fi.validity_start.astimezone(pytz.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                })
                pass_fields["backFields"].append({
                    "key": "validity-start-back",
                    "label": "validity-start-label",
                    "dateStyle": "PKDateStyleFull",
                    "timeStyle": "PKDateStyleFull",
                    "value": ticket_data.vor_fi.validity_start.astimezone(pytz.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                })

                if ticket_data.vor_fi.validity_end:
                    pass_json["expirationDate"] = ticket_data.vor_fi.validity_end.astimezone(pytz.utc).strftime(
                        "%Y-%m-%dT%H:%M:%SZ")
                    pass_fields["auxiliaryFields"].append({
                        "key": "validity-end",
                        "label": "validity-end-label",
                        "dateStyle": "PKDateStyleMedium",
                        "timeStyle": "PKDateStyleShort",
                        "value": ticket_data.vor_fi.validity_end.astimezone(pytz.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                        "changeMessage": "validity-end-change"
                    })
                    pass_fields["backFields"].append({
                        "key": "validity-end-back",
                        "label": "validity-end-label",
                        "dateStyle": "PKDateStyleFull",
                        "timeStyle": "PKDateStyleFull",
                        "value": ticket_data.vor_fi.validity_end.astimezone(pytz.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    })

            if ticket_data.vor_vd:
                if ticket_data.vor_vd.forename and ticket_data.vor_vd.surname:
                    name_value = f"{ticket_data.vor_vd.forename}\n{ticket_data.vor_vd.surname}"
                elif ticket_data.vor_vd.forename:
                    name_value = ticket_data.vor_vd.forename
                elif ticket_data.vor_vd.surname:
                    name_value = ticket_data.vor_vd.surname
                else:
                    name_value = ""
                if name_value:
                    pass_fields["primaryFields"].append({
                        "key": "passenger",
                        "label": "passenger-label",
                        "value": name_value,
                        "semantics": {
                            "passengerName": {
                                "familyName": ticket_data.vor_vd.surname,
                                "givenName": ticket_data.vor_vd.forename
                            }
                        }
                    })
                if ticket_data.vor_vd.date_of_birth:
                    pass_fields["secondaryFields"].append({
                        "key": "date-of-birth",
                        "label": "date-of-birth-label",
                        "dateStyle": "PKDateStyleMedium",
                        "value": ticket_data.vor_vd.date_of_birth.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    })

        elif ticket_data.st01:
            tz = pytz.timezone("Europe/Berlin")

            pass_fields["headerFields"].append({
                "key": "product",
                "label": "product-label",
                "value": ticket_data.st01.ticket_type,
            })
            pass_fields["backFields"].append({
                "key": "product-back",
                "label": "product-label",
                "value": ticket_data.st01.ticket_type,
            })

            pass_fields["primaryFields"].append({
                "key": "passenger",
                "label": "passenger-label",
                "value": ticket_data.st01.passenger_name,
            })

            if ticket_data.st01.passenger_dob:
                pass_fields["secondaryFields"].append({
                    "key": "date-of-birth",
                    "label": "date-of-birth-label",
                    "dateStyle": "PKDateStyleMedium",
                    "value": ticket_data.st01.passenger_dob.strftime("%Y-%m-%dT%H:%M:%SZ"),
                })

            if ticket_data.st01.validity:
                pass_fields["secondaryFields"].append({
                    "key": "valid-region",
                    "label": "valid-region-label",
                    "value": ticket_data.st01.validity,
                })

            if ticket_data.st01.valid_from:
                validity_start = tz.localize(datetime.datetime.combine(ticket_data.st01.valid_from, datetime.time.min))
                pass_fields["auxiliaryFields"].append({
                    "key": "validity-start",
                    "label": "validity-start-label",
                    "dateStyle": "PKDateStyleMedium",
                    "timeStyle": "PKDateStyleNone",
                    "value": validity_start.isoformat(),
                    "ignoresTimeZone": True,
                })
                pass_fields["backFields"].append({
                    "key": "validity-start-back",
                    "label": "validity-start-label",
                    "dateStyle": "PKDateStyleFull",
                    "timeStyle": "PKDateStyleFull",
                    "value": validity_start.isoformat(),
                    "ignoresTimeZone": True,
                })

            if ticket_data.st01.valid_to:
                validity_end = tz.localize(datetime.datetime.combine(ticket_data.st01.valid_to, datetime.time.max))
                pass_json["expirationDate"] = validity_end.isoformat()
                pass_fields["auxiliaryFields"].append({
                    "key": "validity-end",
                    "label": "validity-end-label",
                    "dateStyle": "PKDateStyleMedium",
                    "timeStyle": "PKDateStyleNone",
                    "value": validity_end.isoformat(),
                    "changeMessage": "validity-end-change",
                    "ignoresTimeZone": True
                })
                pass_fields["backFields"].append({
                    "key": "validity-end-back",
                    "label": "validity-end-label",
                    "dateStyle": "PKDateStyleFull",
                    "timeStyle": "PKDateStyleFull",
                    "value": validity_end.isoformat(),
                    "ignoresTimeZone": True,
                })

        elif ticket_data.db_plai:
            pass_fields["headerFields"].append({
                "key": "product",
                "label": "product-label",
                "value": ticket_data.db_plai.ticket_type
            })
            pass_fields["backFields"].append({
                "key": "product-back",
                "label": "product-label",
                "value": ticket_data.db_plai.ticket_type,
            })

            pass_fields["backFields"].append({
                "key": "conditions",
                "label": "product-conditions-label",
                "value": ticket_data.db_plai.conditions,
            })

            pass_fields["primaryFields"].append({
                "key": "passenger",
                "label": "passenger-label",
                "value": ticket_data.db_plai.customer_name
            })

            if ticket_data.db_plai.customer_dob:
                pass_fields["secondaryFields"].append({
                    "key": "date-of-birth",
                    "label": "date-of-birth-label",
                    "dateStyle": "PKDateStyleMedium",
                    "value": ticket_data.db_plai.customer_dob.strftime("%Y-%m-%dT%H:%M:%SZ"),
                })

            if ticket_data.db_plai.valid_from:
                pass_fields["auxiliaryFields"].append({
                    "key": "validity-start",
                    "label": "validity-start-label",
                    "dateStyle": "PKDateStyleMedium",
                    "timeStyle": "PKDateStyleShort",
                    "value": ticket_data.db_plai.valid_from.isoformat(),
                    "ignoresTimeZone": True,
                })
                pass_fields["backFields"].append({
                    "key": "validity-start-back",
                    "label": "validity-start-label",
                    "dateStyle": "PKDateStyleFull",
                    "timeStyle": "PKDateStyleFull",
                    "value": ticket_data.db_plai.valid_to.isoformat(),
                    "ignoresTimeZone": True,
                })

            if ticket_data.db_plai.valid_to:
                pass_json["expirationDate"] = ticket_data.db_plai.valid_to.isoformat()
                pass_fields["auxiliaryFields"].append({
                    "key": "validity-end",
                    "label": "validity-end-label",
                    "dateStyle": "PKDateStyleMedium",
                    "timeStyle": "PKDateStyleShort",
                    "value": ticket_data.db_plai.valid_to.isoformat(),
                    "changeMessage": "validity-end-change",
                    "ignoresTimeZone": True
                })
                pass_fields["backFields"].append({
                    "key": "validity-end-back",
                    "label": "validity-end-label",
                    "dateStyle": "PKDateStyleFull",
                    "timeStyle": "PKDateStyleFull",
                    "value": ticket_data.db_plai.valid_to.isoformat(),
                    "ignoresTimeZone": True,
                })

            if ticket_data.db_plai.booked_at:
                pass_fields["backFields"].append({
                    "key": "issued-at",
                    "label": "issued-at-label",
                    "dateStyle": "PKDateStyleFull",
                    "timeStyle": "PKDateStyleFull",
                    "value": ticket_data.db_plai.booked_at.isoformat(),
                    "ignoresTimeZone": True,
                })

            pass_fields["secondaryFields"].append({
                "key": "product-organisation",
                "label": "product-organisation-label",
                "value": ticket_data.db_plai.tariff
            })
            pass_fields["backFields"].append({
                "key": "product-organisation-back",
                "label": "product-organisation-label",
                "value": ticket_data.db_plai.tariff
            })

        elif parsed_layout:
            if parsed_layout.trips:
                if parsed_layout.operator_rics == 1084 and parsed_layout.document_type.startswith(NSR_DAY_UPGRADES):
                    # NS tickets that allow some entitlement for a day (i.e. first class, bicycle, dog, ICD)
                    # irrespective of itinerary, display these as storecards as instead they will be
                    # displayed as a trip from 'any station in NL' to 'any station in NL'
                    # which isn't very helpful
                    pass_type = "storeCard"

                    pass_fields["headerFields"].append({
                        "key": "document-type",
                        "label": "product-label",
                        "value": parsed_layout.document_type,
                    })

                    pass_fields["secondaryFields"].append({
                        "key": "validity-on",
                        "label": "validity-on-label",
                        "dateStyle": "PKDateStyleMedium",
                        "value": parsed_layout.trips[0].departure.strftime("%Y-%m-%dT00:00:00Z"),
                    })
                    pass_json["relevantDates"].append({
                        "startDate": parsed_layout.trips[0].departure.strftime("%Y-%m-%dT%H:%M:%SZ"),
                        "endDate": parsed_layout.trips[0].arrival.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    })
                    expiration_date = parsed_layout.trips[0].arrival + datetime.timedelta(days=1, hours=4)
                    pass_json["expirationDate"] = expiration_date.strftime("%Y-%m-%dT%H:%M:%SZ")

                elif parsed_layout.trips[0].departure_station or parsed_layout.trips[0].arrival_station:
                    pass_type = "boardingPass"
                    pass_fields["transitType"] = "PKTransitTypeTrain"

                    pass_fields["primaryFields"].append({
                        "key": "from-station",
                        "label": "from-station-label",
                        "value": parsed_layout.trips[0].departure_station,
                        "semantics": {
                            "departureStationName": parsed_layout.trips[0].departure_station
                        }
                    })
                    pass_fields["primaryFields"].append({
                        "key": "to-station",
                        "label": "to-station-label",
                        "value": parsed_layout.trips[0].arrival_station,
                        "semantics": {
                            "departureStationName": parsed_layout.trips[0].arrival_station,
                        }
                    })

                    if parsed_layout.trips[0].departure:
                        if isinstance(parsed_layout.trips[0].departure, datetime.datetime):
                            pass_fields["secondaryFields"].append({
                                "key": "departure-time",
                                "label": "departure-time-label",
                                "dateStyle": "PKDateStyleMedium",
                                "timeStyle": "PKDateStyleShort",
                                "value": parsed_layout.trips[0].departure.strftime("%Y-%m-%dT%H:%M:%SZ"),
                                "ignoresTimeZone": True
                            })
                            pass_json["relevantDates"].append({
                                "date": parsed_layout.trips[0].departure.strftime("%Y-%m-%dT%H:%M:%SZ"),
                            })
                        elif isinstance(parsed_layout.trips[0].departure, datetime.date):
                            pass_fields["secondaryFields"].append({
                                "key": "validity-start",
                                "label": "validity-start-label",
                                "dateStyle": "PKDateStyleMedium",
                                "timeStyle": "PKDateStyleNone",
                                "value": parsed_layout.trips[0].departure.strftime("%Y-%m-%dT00:00:00Z"),
                                "ignoresTimeZone": True
                            })
                            pass_json["relevantDates"].append({
                                "date": parsed_layout.trips[0].departure.strftime("%Y-%m-%dT00:00:00Z")
                            })
                    elif parsed_layout.trips[0].departure_time:
                        pass_fields["secondaryFields"].append({
                            "key": "departure-time",
                            "label": "departure-time-label",
                            "value": f"{parsed_layout.trips[0].departure_date} {parsed_layout.trips[0].departure_time}",
                        })
                    elif parsed_layout.trips[0].departure_date:
                        pass_fields["secondaryFields"].append({
                            "key": "valid-from",
                            "label": "validity-start-label",
                            "value": parsed_layout.trips[0].departure_date
                        })

                    if parsed_layout.trips[0].arrival:
                        if isinstance(parsed_layout.trips[0].arrival, datetime.datetime):
                            pass_fields["secondaryFields"].append({
                                "key": "arrival-time",
                                "label": "arrival-time-label",
                                "dateStyle": "PKDateStyleMedium",
                                "timeStyle": "PKDateStyleShort",
                                "value": parsed_layout.trips[0].arrival.strftime("%Y-%m-%dT%H:%M:%SZ"),
                                "ignoresTimeZone": True
                            })
                            pass_json["expirationDate"] = parsed_layout.trips[0].arrival.strftime("%Y-%m-%dT%H:%M:%SZ")
                        elif isinstance(parsed_layout.trips[0].arrival, datetime.date):
                            if "expirationDate" not in pass_json:
                                pass_json["expirationDate"] = parsed_layout.trips[0].arrival.strftime(
                                    "%Y-%m-%dT23:59:59Z")
                            pass_fields["secondaryFields"].append({
                                "key": "validity-end",
                                "label": "validity-end-label",
                                "dateStyle": "PKDateStyleMedium",
                                "timeStyle": "PKDateStyleNone",
                                "value": parsed_layout.trips[0].arrival.strftime("%Y-%m-%dT23:59:59Z"),
                                "ignoresTimeZone": True
                            })
                    elif parsed_layout.trips[0].arrival_time:
                        pass_fields["secondaryFields"].append({
                            "key": "arrival-time",
                            "label": "arrival-time-label",
                            "value": f"{parsed_layout.trips[0].arrival_date} {parsed_layout.trips[0].arrival_time}",
                        })
                    elif parsed_layout.trips[0].arrival_date:
                        pass_fields["secondaryFields"].append({
                            "key": "valid-until",
                            "label": "validity-end-label",
                            "value": parsed_layout.trips[0].arrival_date
                        })

                else:
                    if parsed_layout.trips[0].departure:
                        if isinstance(parsed_layout.trips[0].arrival, datetime.datetime):
                            pass_fields["auxiliaryFields"].append({
                                "key": "validity-start",
                                "label": "validity-start-label",
                                "dateStyle": "PKDateStyleMedium",
                                "timeStyle": "PKDateStyleShort",
                                "value": parsed_layout.trips[0].departure.strftime("%Y-%m-%dT%H:%M:%SZ"),
                                "ignoresTimeZone": True
                            })
                        elif isinstance(parsed_layout.trips[0].arrival, datetime.date):
                            pass_fields["auxiliaryFields"].append({
                                "key": "validity-start",
                                "label": "validity-start-label",
                                "dateStyle": "PKDateStyleMedium",
                                "timeStyle": "PKDateStyleNone",
                                "value": parsed_layout.trips[0].departure.strftime("%Y-%m-%dT00:00:00Z"),
                                "ignoresTimeZone": True
                            })
                    elif parsed_layout.trips[0].departure_time:
                        pass_fields["auxiliaryFields"].append({
                            "key": "validity-start",
                            "label": "validity-start-label",
                            "value": f"{parsed_layout.trips[0].departure_date} {parsed_layout.trips[0].departure_time}",
                        })
                    elif parsed_layout.trips[0].departure_date:
                        pass_fields["auxiliaryFields"].append({
                            "key": "validity-start",
                            "label": "validity-start-label",
                            "value": parsed_layout.trips[0].departure_date
                        })

                    if parsed_layout.trips[0].arrival:
                        if isinstance(parsed_layout.trips[0].arrival, datetime.datetime):
                            if "expirationDate" not in pass_json:
                                pass_json["expirationDate"] = parsed_layout.trips[0].arrival.astimezone(
                                    pytz.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                            pass_fields["auxiliaryFields"].append({
                                "key": "validity-end",
                                "label": "validity-end-label",
                                "dateStyle": "PKDateStyleMedium",
                                "timeStyle": "PKDateStyleShort",
                                "value": parsed_layout.trips[0].arrival.strftime("%Y-%m-%dT%H:%M:%SZ"),
                                "ignoresTimeZone": True
                            })
                        elif isinstance(parsed_layout.trips[0].arrival, datetime.date):
                            if "expirationDate" not in pass_json:
                                pass_json["expirationDate"] = parsed_layout.trips[0].arrival.strftime(
                                    "%Y-%m-%dT23:59:59Z")
                            pass_fields["auxiliaryFields"].append({
                                "key": "validity-end",
                                "label": "validity-end-label",
                                "dateStyle": "PKDateStyleMedium",
                                "timeStyle": "PKDateStyleNone",
                                "value": parsed_layout.trips[0].arrival.strftime("%Y-%m-%dT23:59:59Z"),
                                "ignoresTimeZone": True
                            })
                    elif parsed_layout.trips[0].arrival_time:
                        pass_fields["auxiliaryFields"].append({
                            "key": "validity-end",
                            "label": "validity-end-label",
                            "value": f"{parsed_layout.trips[0].arrival_date} {parsed_layout.trips[0].arrival_time}",
                        })
                    elif parsed_layout.trips[0].arrival_date:
                        pass_fields["auxiliaryFields"].append({
                            "key": "validity-end",
                            "label": "validity-end-label",
                            "value": parsed_layout.trips[0].arrival_date
                        })

            if parsed_layout.travel_class and parsed_layout.travel_class != "0":
                if pass_type == "boardingPass":
                    pass_fields["auxiliaryFields"].append({
                        "key": "class-code",
                        "label": "class-code-label",
                        "value": parsed_layout.travel_class,
                    })
                else:
                    if not (parsed_layout.operator_rics == 1084 and parsed_layout.travel_class == 'n'):
                        # NS bike tickets (and other supplement adj things I assume?) set their class as 'n'
                        # needlessly filling up scarce header space, so do not display if that's the case
                        pass_fields["auxiliaryFields"].append({
                            "key": "class-code",
                            "label": "class-code-label",
                            "value": parsed_layout.travel_class,
                        })

            if parsed_layout.document_type:
                pass_fields["backFields"].append({
                    "key": "document-type",
                    "label": "product-label",
                    "value": parsed_layout.document_type,
                })

            if len(parsed_layout.discount_cards):
                pass_fields["auxiliaryFields"].append({
                    "key": "discount",
                    "label": "reduction-card-label",
                    "value": ', '.join(parsed_layout.discount_cards)
                })

            if parsed_layout.passenger_name:
                if pass_fields["primaryFields"] and parsed_layout.operator_rics not in (1084, 1184):
                    # passenger name can be on primaryFields if we know the parsing of the RCT2 ticket to be reliable
                    # (this is the case for at least SNCB internationaal and NSI)
                    pass_fields["auxiliaryFields"].append({
                        "key": "passenger",
                        "label": "passenger-label",
                        "value": parsed_layout.passenger_name,
                    })
                else:
                    pass_fields["primaryFields"].append({
                        "key": "passenger",
                        "label": "passenger-label",
                        "value": parsed_layout.passenger_name,
                    })

            if parsed_layout.traveller:
                pass_fields["backFields"].append({
                    "key": "traveller",
                    "label": "passenger-label",
                    "value": parsed_layout.traveller,
                })

            if parsed_layout.date_of_birth:
                pass_fields["secondaryFields"].append({
                    "key": "date-of-birth",
                    "label": "date-of-birth-label",
                    "dateStyle": "PKDateStyleMedium",
                    "value": parsed_layout.date_of_birth.strftime("%Y-%m-%dT%H:%M:%SZ"),
                })

            if parsed_layout.train_data:
                pass_fields["backFields"].append({
                    "key": "train-data",
                    "label": "train-number-label",
                    "value": parsed_layout.train_data.replace("<", "%lt;"),
                })

            if parsed_layout.extra:
                if pass_fields["secondaryFields"]:
                    pass_fields["backFields"].append({
                        "key": "extra-data",
                        "label": "other-data-label",
                        "value": parsed_layout.extra.replace("<", "%lt;"),
                    })
                else:
                    pass_fields["secondaryFields"].append({
                        "key": "extra-data",
                        "value": parsed_layout.extra.replace("<", "%lt;"),
                    })

            if parsed_layout.operator_rics:
                if carrier := uic.rics.get_rics(parsed_layout.operator_rics):
                    if carrier["url"]:
                        pass_fields["backFields"].append({
                            "key": "carrier",
                            "label": "carrier-label",
                            "value": carrier["full_name"],
                            "attributedValue": f"<a href=\"{carrier['url']}\">{carrier['full_name']}</a>",
                        })
                    else:
                        pass_fields["backFields"].append({
                            "key": "carrier",
                            "label": "carrier-label",
                            "value": carrier["full_name"],
                        })
                else:
                    pass_fields["backFields"].append({
                        "key": "carrier",
                        "label": "carrier-label",
                        "value": parsed_layout.operator_rics,
                    })

            if parsed_layout.price:
                pass_fields["backFields"].append({
                    "key": "price",
                    "label": "price-label",
                    "value": parsed_layout.price,
                })

            if parsed_layout.conditions:
                pass_fields["backFields"].append({
                    "key": "conditions",
                    "label": "product-conditions-label",
                    "value": parsed_layout.conditions.replace("<", "%lt;"),
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
                return_pass_fields["backFields"].append({
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
                return_pass_fields["backFields"].append({
                    "key": "distributor",
                    "label": "issuing-organisation-label",
                    "value": distributor["full_name"],
                })

        if issued_at:
            pass_fields["backFields"].append({
                "key": "issued-date",
                "label": "issued-at-label",
                "dateStyle": "PKDateStyleFull",
                "timeStyle": "PKDateStyleFull",
                "value": issued_at.isoformat(),
            })
            return_pass_fields["backFields"].append({
                "key": "issued-date",
                "label": "issued-at-label",
                "dateStyle": "PKDateStyleFull",
                "timeStyle": "PKDateStyleFull",
                "value": issued_at.isoformat(),
            })
    elif isinstance(ticket_instance, models.VDVTicketInstance):
        ticket_data: ticket.VDVTicket = ticket_instance.as_ticket

        validity_start = ticket_data.ticket.validity_start.as_datetime()
        validity_end = ticket_data.ticket.validity_end.as_datetime()
        issued_at = ticket_data.ticket.transaction_time.as_datetime()

        pass_json["expirationDate"] = validity_end.isoformat()
        pass_json["voided"] = ticket_data.revoked_status() is not None
        pass_fields = {
            "headerFields": [],
            "primaryFields": [],
            "secondaryFields": [],
            "auxiliaryFields": [{
                "key": "validity-start",
                "label": "validity-start-label",
                "dateStyle": "PKDateStyleMedium",
                "timeStyle": "PKDateStyleShort",
                "value": validity_start.isoformat(),
            }, {
                "key": "validity-end",
                "label": "validity-end-label",
                "dateStyle": "PKDateStyleMedium",
                "timeStyle": "PKDateStyleShort",
                "value": validity_end.isoformat(),
                "changeMessage": "validity-end-change"
            }],
            "backFields": [{
                "key": "validity-start-back",
                "label": "validity-start-label",
                "dateStyle": "PKDateStyleFull",
                "timeStyle": "PKDateStyleFull",
                "value": validity_start.isoformat(),
            }, {
                "key": "validity-end-back",
                "label": "validity-end-label",
                "dateStyle": "PKDateStyleFull",
                "timeStyle": "PKDateStyleFull",
                "value": validity_end.isoformat(),
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
                "value": issued_at.isoformat(),
            }, {
                "key": "issuing-org",
                "label": "issuing-organisation-label",
                "value": ticket_data.ticket.kvp_org_name(),
            }]
        }
        pass_json["organizationName"] = ticket_data.ticket.kvp_org_name()

        if validity_start.date() == validity_end.date():
            pass_json["relevantDates"].append({
                "startDate": validity_start.isoformat(),
                "endDate": validity_end.isoformat(),
            })

        barcode_data = ticket_data.motics.application_data if ticket_data.motics else ticket_instance.barcode_data
        pass_json["barcodes"] = [{
            "format": "PKBarcodeFormatAztec",
            "message": bytes(barcode_data).decode("iso-8859-1"),
            "messageEncoding": "iso-8859-1",
            "altText": str(ticket_data.ticket.ticket_id),
        }]

        for elm in ticket_data.ticket.product_data:
            if isinstance(elm, vdv.ticket.PassengerData):
                if elm.forename and elm.surname:
                    name_value = f"{elm.forename}\n{elm.surname}"
                elif elm.forename:
                    name_value = elm.forename
                elif elm.surname:
                    name_value = elm.surname
                else:
                    name_value = ""
                if name_value:
                    pass_fields["primaryFields"].append({
                        "key": "passenger",
                        "label": "passenger-label",
                        "value": name_value,
                        "semantics": {
                            "passengerName": {
                                "familyName": elm.surname,
                                "givenName": elm.forename
                            }
                        }
                    })
                if elm.date_of_birth:
                    pass_fields["secondaryFields"].append({
                        "key": "date-of-birth",
                        "label": "date-of-birth-label",
                        "dateStyle": "PKDateStyleMedium",
                        "value": elm.date_of_birth.as_date().strftime("%Y-%m-%dT%H:%M:%SZ"),
                    })
            elif isinstance(elm, vdv.ticket.SpatialValidity):
                if elm.organization_id == 6100:  # Verkehrsverbund Berlin Brandenburg
                    if elm.area == 1200 or 1200 in elm.validity_ids:
                        add_pkp_img(pkp, "pass/berlin-ab.png", "thumbnail.png")
                    elif elm.area == 1202 or 1202 in elm.validity_ids:
                        add_pkp_img(pkp, "pass/berlin-abc.png", "thumbnail.png")

                if elm.variant == "D":
                    validity = ", ".join(elm.tariff_point_names())
                    pass_fields["secondaryFields"].append({
                        "key": "valid-in",
                        "label": "valid-region-label",
                        "value": validity,
                    })
                elif elm.variant == "E":
                    if elm.start_station:
                        pass_fields["secondaryFields"].append({
                            "key": "valid-from",
                            "label": "from-station-label",
                            "value": elm.start_station_name()
                        })
                    if elm.area:
                        pass_fields["secondaryFields"].append({
                            "key": "valid-in",
                            "label": "valid-region-label",
                            "value": elm.area_name(),
                        })
                elif elm.variant == "B":
                    if elm.area:
                        # edgecase for KVV, they do spatial validity in NRWweit tickets (and D-Ticket?) oddly
                        if elm.tariff_points[0] == elm.tariff_points[1] == elm.area:
                            pass_fields["secondaryFields"].append({
                                "key": "valid-in",
                                "label": "valid-region-label",
                                "value": elm.area_name(),
                            })
            elif isinstance(elm, vdv.ticket.IdentificationMedium):
                if elm.id_type == 84:
                    pass_fields["secondaryFields"].append({
                        "key": "telephone-number",
                        "label": "telephone-number-label",
                        "value": elm.international_phone_number() if elm.phone_number else elm.id_number
                    })
            elif isinstance(elm, vdv.ticket.RMVProductData):
                pass_fields["secondaryFields"].append({
                    "key": "origin",
                    "label": "from-station-label",
                    "value": elm.start_tariff_point_name()
                })
                pass_fields["secondaryFields"].append({
                    "key": "destination",
                    "label": "to-station-label",
                    "value": elm.end_tariff_point_name()
                })

                if elm.passenger_data.forename and elm.passenger_data.surname:
                    name_value = f"{elm.passenger_data.forename}\n{elm.passenger_data.surname}"
                elif elm.passenger_data.forename:
                    name_value = elm.passenger_data.forename
                elif elm.passenger_data.surname:
                    name_value = elm.passenger_data.surname
                else:
                    name_value = ""
                if name_value:
                    pass_fields["primaryFields"].append({
                        "key": "passenger",
                        "label": "passenger-label",
                        "value": name_value,
                        "semantics": {
                            "passengerName": {
                                "familyName": elm.passenger_data.surname,
                                "givenName": elm.passenger_data.forename
                            }
                        }
                    })
                if elm.passenger_data.date_of_birth:
                    pass_fields["secondaryFields"].append({
                        "key": "date-of-birth",
                        "label": "date-of-birth-label",
                        "dateStyle": "PKDateStyleMedium",
                        "value": elm.passenger_data.date_of_birth.as_date().strftime("%Y-%m-%dT%H:%M:%SZ"),
                    })

        if pass_fields["primaryFields"]:
            pass_fields["headerFields"].append({
                "key": "product",
                "label": "product-label",
                "value": ticket_data.ticket.product_name()
            })
        else:
            pass_fields["primaryFields"].append({
                "key": "product",
                "label": "product-label",
                "value": ticket_data.ticket.product_name()
            })

        org_id = (
            ticket_data.ticket.ticket_org_id if ticket_data.ticket.ticket_org_id in VDV_ORG_ID_LOGO
            else ticket_data.ticket.product_org_id
        ) if ticket_data.ticket.product_org_id not in (3000, 5000) else ticket_data.ticket.ticket_org_id
        if org_id in VDV_ORG_ID_LOGO:
            add_pkp_img(pkp, VDV_ORG_ID_LOGO[org_id], "logo.png")
            have_logo = True
        if org_id in VDV_ORG_ID_BG:
            pass_json["backgroundColor"] = VDV_ORG_ID_BG[org_id]
        if org_id in VDV_ORG_ID_FG:
            pass_json["foregroundColor"] = VDV_ORG_ID_FG[org_id]
        if org_id in VDV_ORG_ID_FG_SECONDARY:
            pass_json["labelColor"] = VDV_ORG_ID_FG_SECONDARY[org_id]
    elif isinstance(ticket_instance, models.RSPTicketInstance):
        ticket_data: ticket.RSPTicket = ticket_instance.as_ticket

        pass_json["barcodes"] = [{
            "format": "PKBarcodeFormatAztec",
            "message": bytes(ticket_instance.barcode_data).decode("iso-8859-1"),
            "messageEncoding": "iso-8859-1",
            "altText": f"{ticket_instance.issuer_id}-{ticket_instance.reference}",
        }]
        pass_json["organizationName"] = ticket_data.issuer_name()
        pass_json["backgroundColor"] = "#fff6e9"

        add_pkp_img(pkp, "pass/logo-nr.png", "icon.png")
        have_icon = True

        def format_text(text):
            return text.replace("<p>", "").replace("</p>", "\n\n").replace('title=""', "").strip()

        if isinstance(ticket_data.data, rsp.TicketData):
            validity_start = ticket_data.data.validity_start_time()
            validity_end = ticket_data.data.validity_end_time()

            show_time = False
            if ticket_data.data.depart_time == rsp.data.DepartureTime.SpecificDeparture:
                show_time = True
            elif ticket_data.data.depart_time == rsp.data.DepartureTime.NotSet and validity_start.time() != datetime.time(
                    0, 0, 0):
                show_time = True

            if show_time:
                pass_json["relevantDates"].append({
                    "startDate": validity_start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "endDate": validity_end.strftime("%Y-%m-%dT%H:%M:%SZ")
                })
            pass_json["expirationDate"] = validity_end.strftime("%Y-%m-%dT%H:%M:%SZ")
            pass_fields = {
                "transitType": "PKTransitTypeTrain",
                "headerFields": [{
                    "key": "departure-time",
                    "label": "departure-time-label",
                    "value": validity_start.isoformat(),
                    "dateStyle": "PKDateStyleShort",
                    "timeStyle": "PKDateStyleShort" if show_time else "PKDateStyleNone",
                    "ignoresTimeZone": True,
                }],
                "primaryFields": [],
                "auxiliaryFields": [{
                    "key": "travel-class",
                    "label": "class-code-label",
                    "value": "class-code-second-label" if ticket_data.data.standard_class else "class-code-first-label",
                }],
                "secondaryFields": [],
                "backFields": [],
            }

            if ticket_data.data.depart_time != rsp.data.DepartureTime.SpecificDeparture and ticket_data.data.limited_duration_code:
                pass_fields["secondaryFields"].append({
                    "key": "validity-start",
                    "label": "validity-start-label",
                    "dateStyle": "PKDateStyleMedium",
                    "timeStyle": "PKDateStyleShort",
                    "value": validity_start.isoformat(),
                    "ignoresTimeZone": True,
                })
                pass_fields["secondaryFields"].append({
                    "key": "validity-end",
                    "label": "validity-end-label",
                    "dateStyle": "PKDateStyleMedium",
                    "timeStyle": "PKDateStyleShort",
                    "value": validity_end.isoformat(),
                    "changeMessage": "validity-end-change",
                    "ignoresTimeZone": True,
                })

            if ticket_data.data.origin_nlc == "Z036" and ticket_data.data.destination_nlc == "Z036":
                pass_fields["primaryFields"].append({
                    "key": "product",
                    "label": "product-label",
                    "value": "Scotrail\nTap & Pay"
                })
            elif ticket_data.data.origin_nlc == "I668" and ticket_data.data.destination_nlc == "I668":
                add_pkp_img(pkp, "pass/britrail.png", "logo.png")
                have_logo = True
                pass_json["backgroundColor"] = "#6dbde4"
                pass_fields["primaryFields"].append({
                    "key": "product",
                    "label": "product-label",
                    "value": "BritRail Pass"
                })
            elif ticket_data.data.fare_label == "PBD":
                to_station = rsp.locations.get_station_by_nlc(ticket_data.data.destination_nlc)
                pass_fields["primaryFields"].append({
                    "key": "product",
                    "label": "product-label",
                    "value": to_station["NLCDESC"],
                })
                add_pkp_img(pkp, "pass/plusbus.png", "thumbnail.png")
            else:
                pass_type = "boardingPass"
                if from_station := rsp.ticket_data.get_station_by_nlc(ticket_data.data.origin_nlc):
                    pass_fields["primaryFields"].append({
                        "key": "from-station",
                        "label": "from-station-label",
                        "value": from_station.crs_code,
                        "semantics": {
                            "departureLocation": {
                                "latitude": float(from_station.latitude),
                                "longitude": float(from_station.longitude),
                            },
                            "departureStationName": from_station.name,
                        }
                    })
                    maps_link = urllib.parse.urlencode({
                        "q": from_station.name,
                        "ll": f"{from_station.latitude},{from_station.longitude}"
                    })
                    pass_fields["backFields"].append({
                        "key": "from-station-back",
                        "label": "from-station-label",
                        "value": from_station.name,
                        "attributedValue": f"<a href=\"https://maps.apple.com/?{maps_link}\">{from_station.name}</a>",
                    })
                elif from_station := rsp.locations.get_station_by_nlc(ticket_data.data.origin_nlc):
                    if "3ALPHA" in from_station:
                        name = from_station["3ALPHA"]
                    else:
                        name = from_station["NLCDESC"]
                    pass_fields["primaryFields"].append({
                        "key": "from-station",
                        "label": "from-station-label",
                        "value": name,
                        "semantics": {
                            "departureStationName": from_station["NLCDESC"]
                        }
                    })
                    pass_fields["backFields"].append({
                        "key": "from-station-back",
                        "label": "from-station-label",
                        "value": from_station["NLCDESC"]
                    })

                if to_station := rsp.ticket_data.get_station_by_nlc(ticket_data.data.destination_nlc):
                    pass_fields["primaryFields"].append({
                        "key": "to-station",
                        "label": "to-station-label",
                        "value": to_station.crs_code,
                        "semantics": {
                            "departureLocation": {
                                "latitude": float(to_station.latitude),
                                "longitude": float(to_station.longitude),
                            },
                            "departureStationName": to_station.name,
                        }
                    })
                    maps_link = urllib.parse.urlencode({
                        "q": to_station.name,
                        "ll": f"{to_station.latitude},{to_station.longitude}"
                    })
                    pass_fields["backFields"].append({
                        "key": "to-station-back",
                        "label": "to-station-label",
                        "value": to_station.name,
                        "attributedValue": f"<a href=\"https://maps.apple.com/?{maps_link}\">{to_station.name}</a>",
                    })
                elif to_station := rsp.locations.get_station_by_nlc(ticket_data.data.destination_nlc):
                    if "3ALPHA" in to_station:
                        name = to_station["3ALPHA"]
                    else:
                        name = to_station["NLCDESC"]
                    pass_fields["primaryFields"].append({
                        "key": "to-station",
                        "label": "to-station-label",
                        "value": name,
                        "semantics": {
                            "departureStationName": to_station["NLCDESC"]
                        }
                    })
                    pass_fields["backFields"].append({
                        "key": "to-station-back",
                        "label": "to-station-label",
                        "value": to_station["NLCDESC"]
                    })

            pass_fields["backFields"].append({
                "key": "return-included",
                "label": "return-included-label",
                "value": "yes-label" if ticket_data.data.bidirectional else "no-label",
            })
            pass_fields["backFields"].append({
                "key": "issuing-org",
                "label": "issuing-organisation-label",
                "value": ticket_data.issuer_name(),
            })

            if ticket_data.data.purchase_data:
                pass_fields["backFields"].extend([{
                    "key": "ticket-id",
                    "label": "ticket-id-label",
                    "value": ticket_data.data.purchase_data.purchase_reference or ticket_data.ticket_ref,
                }, {
                    "key": "issued-date",
                    "label": "issued-at-label",
                    "dateStyle": "PKDateStyleFull",
                    "timeStyle": "PKDateStyleFull",
                    "value": ticket_data.data.purchase_data.purchase_time().isoformat(),
                }, {
                    "key": "price",
                    "label": "price-label",
                    "value": ticket_data.data.purchase_data.price_str()
                }])

            if discount_data := rsp.ticket_data.get_discount_by_id(ticket_data.data.discount_code):
                if discount_data["description"]:
                    pass_fields["secondaryFields"].append({
                        "key": "discount",
                        "label": "reduction-card-label",
                        "value": discount_data["description"]
                    })

            for i, reservation in enumerate(ticket_data.data.reservations):
                pass_fields["secondaryFields"].append({
                    "key": f"reservation-{i}-service",
                    "label": "train-number-label",
                    "value": reservation.service_id,
                })
                if reservation.coach:
                    pass_fields["auxiliaryFields"].append({
                        "key": f"reservation-{i}-coach",
                        "label": "coach-number-label",
                        "value": reservation.coach,
                    })
                if reservation.seat:
                    pass_fields["auxiliaryFields"].append({
                        "key": f"reservation-{i}-seat",
                        "label": "seat-number-label",
                        "value": reservation.seat,
                    })

            if route_data := rsp.ticket_data.get_route_by_id(ticket_data.data.route_code):
                pass_fields["auxiliaryFields"].append({
                    "key": "route",
                    "label": "route-label",
                    "value": route_data["cc_desc"]
                })
                pass_fields["backFields"].append({
                    "key": "route-description",
                    "label": "route-label",
                    "value": route_data["atb_desc"]
                })
                if route_data["all_included_crs"]:
                    stations = []
                    for crs in route_data["all_included_crs"]:
                        if station := rsp.ticket_data.get_station_by_crs(crs):
                            if station.latitude and station.longitude:
                                maps_link = urllib.parse.urlencode({
                                    "q": station.name,
                                    "ll": f"{station.latitude},{station.longitude}"
                                })
                                stations.append(f"<a href=\"https://maps.apple.com/?{maps_link}\">{station.name}</a>")
                            else:
                                stations.append(station.name)
                        else:
                            stations.append(crs)
                    pass_fields["backFields"].append({
                        "key": "all-included-crs",
                        "label": "travel-via-all-label",
                        "value": "\n".join(stations)
                    })
                if route_data["any_included_crs"]:
                    stations = []
                    for crs in route_data["any_included_crs"]:
                        if station := rsp.ticket_data.get_station_by_crs(crs):
                            if station.latitude and station.longitude:
                                maps_link = urllib.parse.urlencode({
                                    "q": station.name,
                                    "ll": f"{station.latitude},{station.longitude}"
                                })
                                stations.append(f"<a href=\"https://maps.apple.com/?{maps_link}\">{station.name}</a>")
                            else:
                                stations.append(station.name)
                        else:
                            stations.append(crs)
                    pass_fields["backFields"].append({
                        "key": "any-included-crs",
                        "label": "travel-via-any-label",
                        "value": "\n".join(stations)
                    })
                if route_data["excluded_crs"]:
                    stations = []
                    for crs in route_data["excluded_crs"]:
                        if station := rsp.ticket_data.get_station_by_crs(crs):
                            if station.latitude and station.longitude:
                                maps_link = urllib.parse.urlencode({
                                    "q": station.name,
                                    "ll": f"{station.latitude},{station.longitude}"
                                })
                                stations.append(f"<a href=\"https://maps.apple.com/?{maps_link}\">{station.name}</a>")
                            else:
                                stations.append(station.name)
                        else:
                            stations.append(crs)
                    pass_fields["backFields"].append({
                        "key": "excluded-crs",
                        "label": "travel-via-excl-label",
                        "value": "\n".join(stations)
                    })
                if route_data["included_tocs"]:
                    tocs = []
                    for toc in route_data["included_tocs"]:
                        if toc_data := rsp.ticket_data.get_toc_by_id(toc):
                            tocs.append(toc_data["name"])
                        else:
                            tocs.append(toc)
                    pass_fields["backFields"].append({
                        "key": "included-toc",
                        "label": "travel-inc-toc-label",
                        "value": "\n".join(tocs)
                    })
                if route_data["excluded_tocs"]:
                    tocs = []
                    for toc in route_data["excluded_tocs"]:
                        if toc_data := rsp.ticket_data.get_toc_by_id(toc):
                            tocs.append(toc_data["name"])
                        else:
                            tocs.append(toc)
                    pass_fields["backFields"].append({
                        "key": "excluded-toc",
                        "label": "travel-excl-toc-label",
                        "value": "\n".join(tocs)
                    })

            if ticket_type := rsp.ticket_data.get_ticket_type(ticket_data.data.fare_label):
                pass_fields["secondaryFields"].append({
                    "key": "product",
                    "label": "product-label",
                    "value": ticket_type["name"],
                })

            if ticket_conditions := rsp.ticket_data.get_ticket_conditions(ticket_data.data.fare_label):
                if ticket_conditions.validity:
                    if ticket_conditions.validity.day_outward:
                        pass_fields["backFields"].append({
                            "key": "product-validity-outward-date",
                            "label": "product-validity-outward-date-label",
                            "value": "",
                            "attributedValue": format_text(ticket_conditions.validity.day_outward),
                        })
                    if ticket_conditions.validity.time_outward:
                        pass_fields["backFields"].append({
                            "key": "product-validity-outward-time",
                            "label": "product-validity-outward-time-label",
                            "value": "",
                            "attributedValue": format_text(ticket_conditions.validity.time_outward),
                        })
                    if ticket_conditions.validity.day_return:
                        pass_fields["backFields"].append({
                            "key": "product-validity-return-date",
                            "label": "product-validity-return-date-label",
                            "value": "",
                            "attributedValue": format_text(ticket_conditions.validity.day_return),
                        })
                    if ticket_conditions.validity.time_return:
                        pass_fields["backFields"].append({
                            "key": "product-validity-return-time",
                            "label": "product-validity-return-time-label",
                            "value": "",
                            "attributedValue": format_text(ticket_conditions.validity.time_return),
                        })
                if ticket_conditions.break_of_journey:
                    if ticket_conditions.break_of_journey.outward_note:
                        pass_fields["backFields"].append({
                            "key": "product-break-of-journey-outward",
                            "label": "product-break-of-journey-outward-label",
                            "attributedValue": format_text(ticket_conditions.break_of_journey.outward_note),
                        })
                    if ticket_conditions.break_of_journey.return_note:
                        pass_fields["backFields"].append({
                            "key": "product-break-of-journey-return",
                            "label": "product-break-of-journey-return-label",
                            "attributedValue": format_text(ticket_conditions.break_of_journey.return_note),
                        })
                if ticket_conditions.conditions:
                    pass_fields["backFields"].append({
                        "key": "product-conditions",
                        "label": "product-conditions-label",
                        "attributedValue": format_text(ticket_conditions.conditions),
                    })
                if ticket_conditions.changes_to_travel_plans:
                    pass_fields["backFields"].append({
                        "key": "product-changes",
                        "label": "product-changes-label",
                        "attributedValue": format_text(ticket_conditions.changes_to_travel_plans),
                    })
                if ticket_conditions.refunds:
                    pass_fields["backFields"].append({
                        "key": "product-refunds",
                        "label": "product-refunds-label",
                        "attributedValue": format_text(ticket_conditions.refunds),
                    })

            if not have_logo:
                if ticket_data.issuer_id in RSP_ORG_LOGO:
                    add_pkp_img(pkp, RSP_ORG_LOGO[ticket_data.issuer_id], "logo.png")
                    have_logo = True
                else:
                    add_pkp_img(pkp, "pass/logo-nr.png", "logo.png")
                    have_logo = True

            if ticket_data.issuer_id in RSP_ORG_BG:
                pass_json["backgroundColor"] = RSP_ORG_BG[ticket_data.issuer_id]
            if ticket_data.issuer_id in RSP_ORG_FG:
                pass_json["foregroundColor"] = RSP_ORG_FG[ticket_data.issuer_id]
            if ticket_data.issuer_id in RSP_ORG_FG_SECONDARY:
                pass_json["labelColor"] = RSP_ORG_FG_SECONDARY[ticket_data.issuer_id]

        elif isinstance(ticket_data.data, rsp.Type11):
            validity_start = ticket_data.data.validity_start_time()
            validity_end = ticket_data.data.validity_end_time()

            pass_json["expirationDate"] = validity_end.strftime("%Y-%m-%dT%H:%M:%SZ")
            pass_fields = {
                "transitType": "PKTransitTypeTrain",
                "headerFields": [{
                    "key": "departure-time",
                    "label": "departure-time-label",
                    "value": validity_start.isoformat(),
                    "dateStyle": "PKDateStyleShort",
                    "timeStyle": "PKDateStyleNone",
                    "ignoresTimeZone": True,
                }],
                "primaryFields": [],
                "auxiliaryFields": [{
                    "key": "travel-class",
                    "label": "class-code-label",
                    "value": "class-code-second-label" if ticket_data.data.standard_class else "class-code-first-label",
                }],
                "secondaryFields": [],
                "backFields": [],
            }

            if ticket_data.data.ticket_type == "PBD":
                to_station = rsp.locations.get_station_by_nlc(ticket_data.data.destination_nlc)
                pass_fields["primaryFields"].append({
                    "key": "product",
                    "label": "product-label",
                    "value": to_station["NLCDESC"],
                })
                add_pkp_img(pkp, "pass/plusbus.png", "thumbnail.png")
            else:
                pass_type = "boardingPass"
                if from_station := rsp.ticket_data.get_station_by_nlc(ticket_data.data.origin_nlc):
                    pass_fields["primaryFields"].append({
                        "key": "from-station",
                        "label": "from-station-label",
                        "value": from_station.crs_code,
                        "semantics": {
                            "departureLocation": {
                                "latitude": float(from_station.latitude),
                                "longitude": float(from_station.longitude),
                            },
                            "departureStationName": from_station.name,
                        }
                    })
                    maps_link = urllib.parse.urlencode({
                        "q": from_station.name,
                        "ll": f"{from_station.latitude},{from_station.longitude}"
                    })
                    pass_fields["backFields"].append({
                        "key": "from-station-back",
                        "label": "from-station-label",
                        "value": from_station.name,
                        "attributedValue": f"<a href=\"https://maps.apple.com/?{maps_link}\">{from_station.name}</a>",
                    })
                elif from_station := rsp.locations.get_station_by_nlc(ticket_data.data.origin_nlc):
                    if "3ALPHA" in from_station:
                        name = from_station["3ALPHA"]
                    else:
                        name = from_station["NLCDESC"]
                    pass_fields["primaryFields"].append({
                        "key": "from-station",
                        "label": "from-station-label",
                        "value": name,
                        "semantics": {
                            "departureStationName": from_station["NLCDESC"]
                        }
                    })
                    pass_fields["backFields"].append({
                        "key": "from-station-back",
                        "label": "from-station-label",
                        "value": from_station["NLCDESC"]
                    })

                if to_station := rsp.ticket_data.get_station_by_nlc(ticket_data.data.destination_nlc):
                    pass_fields["primaryFields"].append({
                        "key": "to-station",
                        "label": "to-station-label",
                        "value": to_station.crs_code,
                        "semantics": {
                            "departureLocation": {
                                "latitude": float(to_station.latitude),
                                "longitude": float(to_station.longitude),
                            },
                            "departureStationName": to_station.name,
                        }
                    })
                    maps_link = urllib.parse.urlencode({
                        "q": to_station.name,
                        "ll": f"{to_station.latitude},{to_station.longitude}"
                    })
                    pass_fields["backFields"].append({
                        "key": "to-station-back",
                        "label": "to-station-label",
                        "value": to_station.name,
                        "attributedValue": f"<a href=\"https://maps.apple.com/?{maps_link}\">{to_station.name}</a>",
                    })
                elif to_station := rsp.locations.get_station_by_nlc(ticket_data.data.destination_nlc):
                    if "3ALPHA" in to_station:
                        name = to_station["3ALPHA"]
                    else:
                        name = to_station["NLCDESC"]
                    pass_fields["primaryFields"].append({
                        "key": "to-station",
                        "label": "to-station-label",
                        "value": name,
                        "semantics": {
                            "departureStationName": to_station["NLCDESC"]
                        }
                    })
                    pass_fields["backFields"].append({
                        "key": "to-station-back",
                        "label": "to-station-label",
                        "value": to_station["NLCDESC"]
                    })

            pass_fields["backFields"].append({
                "key": "issuing-org",
                "label": "issuing-organisation-label",
                "value": ticket_data.issuer_name(),
            })

            if ticket_type := rsp.ticket_data.get_ticket_type(ticket_data.data.ticket_type):
                pass_fields["auxiliaryFields"].append({
                    "key": "product",
                    "label": "product-label",
                    "value": ticket_type["name"],
                })

            if ticket_conditions := rsp.ticket_data.get_ticket_conditions(ticket_data.data.ticket_type):
                if ticket_conditions.validity:
                    if ticket_conditions.validity.day_outward:
                        pass_fields["backFields"].append({
                            "key": "product-validity-outward-date",
                            "label": "product-validity-outward-date-label",
                            "value": "",
                            "attributedValue": format_text(ticket_conditions.validity.day_outward),
                        })
                    if ticket_conditions.validity.time_outward:
                        pass_fields["backFields"].append({
                            "key": "product-validity-outward-time",
                            "label": "product-validity-outward-time-label",
                            "value": "",
                            "attributedValue": format_text(ticket_conditions.validity.time_outward),
                        })
                    if ticket_conditions.validity.day_return:
                        pass_fields["backFields"].append({
                            "key": "product-validity-return-date",
                            "label": "product-validity-return-date-label",
                            "value": "",
                            "attributedValue": format_text(ticket_conditions.validity.day_return),
                        })
                    if ticket_conditions.validity.time_return:
                        pass_fields["backFields"].append({
                            "key": "product-validity-return-time",
                            "label": "product-validity-return-time-label",
                            "value": "",
                            "attributedValue": format_text(ticket_conditions.validity.time_return),
                        })
                if ticket_conditions.break_of_journey:
                    if ticket_conditions.break_of_journey.outward_note:
                        pass_fields["backFields"].append({
                            "key": "product-break-of-journey-outward",
                            "label": "product-break-of-journey-outward-label",
                            "attributedValue": format_text(ticket_conditions.break_of_journey.outward_note),
                        })
                    if ticket_conditions.break_of_journey.return_note:
                        pass_fields["backFields"].append({
                            "key": "product-break-of-journey-return",
                            "label": "product-break-of-journey-return-label",
                            "attributedValue": format_text(ticket_conditions.break_of_journey.return_note),
                        })
                if ticket_conditions.conditions:
                    pass_fields["backFields"].append({
                        "key": "product-conditions",
                        "label": "product-conditions-label",
                        "attributedValue": format_text(ticket_conditions.conditions),
                    })
                if ticket_conditions.changes_to_travel_plans:
                    pass_fields["backFields"].append({
                        "key": "product-changes",
                        "label": "product-changes-label",
                        "attributedValue": format_text(ticket_conditions.changes_to_travel_plans),
                    })
                if ticket_conditions.refunds:
                    pass_fields["backFields"].append({
                        "key": "product-refunds",
                        "label": "product-refunds-label",
                        "attributedValue": format_text(ticket_conditions.refunds),
                    })

            if not have_logo:
                if ticket_data.issuer_id in RSP_ORG_LOGO:
                    add_pkp_img(pkp, RSP_ORG_LOGO[ticket_data.issuer_id], "logo.png")
                    have_logo = True
                else:
                    add_pkp_img(pkp, "pass/logo-nr.png", "logo.png")
                    have_logo = True

            if ticket_data.issuer_id in RSP_ORG_BG:
                pass_json["backgroundColor"] = RSP_ORG_BG[ticket_data.issuer_id]
            if ticket_data.issuer_id in RSP_ORG_FG:
                pass_json["foregroundColor"] = RSP_ORG_FG[ticket_data.issuer_id]
            if ticket_data.issuer_id in RSP_ORG_FG_SECONDARY:
                pass_json["labelColor"] = RSP_ORG_FG_SECONDARY[ticket_data.issuer_id]

        elif isinstance(ticket_data.data, rsp.RailcardData):
            validity_start = ticket_data.data.validity_start_time()
            validity_end = ticket_data.data.validity_end_time()
            pass_json["organizationName"] = ticket_data.data.issuer_name()
            if colour := ticket_data.data.background_colour():
                pass_json["backgroundColor"] = colour
                pass_json["foregroundColor"] = "rgb(255, 255, 255)"
                pass_json["labelColor"] = "rgb(205, 205, 205)"

            pass_json["expirationDate"] = validity_end.strftime("%Y-%m-%dT%H:%M:%SZ")
            pass_fields = {
                "headerFields": [{
                    "key": "product",
                    "label": "product-label",
                    "value": ticket_data.data.railcard_type_name(),
                }],
                "primaryFields": [{
                    "key": "passenger",
                    "label": "passenger-label",
                    "value": f"{ticket_data.data.passenger_1_forename}\n{ticket_data.data.passenger_1_surname}",
                    "semantics": {
                        "passengerName": {
                            "familyName": ticket_data.data.passenger_1_surname,
                            "givenName": ticket_data.data.passenger_1_forename,
                        }
                    }
                }],
                "secondaryFields": [{
                    "key": "railcard-number",
                    "label": "railcard-number",
                    "value": ticket_data.data.railcard_number,
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
                    "key": "issuing-org",
                    "label": "issuing-organisation-label",
                    "value": ticket_data.data.issuer_name(),
                }, {
                    "key": "ticket-id",
                    "label": "ticket-id-label",
                    "value": str(ticket_data.data.ticket_reference),
                }, {
                    "key": "issued-date",
                    "label": "issued-at-label",
                    "dateStyle": "PKDateStyleFull",
                    "timeStyle": "PKDateStyleFull",
                    "value": ticket_data.data.purchase_time().strftime("%Y-%m-%dT%H:%M:%SZ"),
                }]
            }

            if ticket_data.data.has_passenger_2():
                pass_fields["primaryFields"].append({
                    "key": "passenger-2",
                    "label": "passenger-2-label",
                    "value": ticket_data.data.passenger_2_name(),
                    "semantics": {
                        "passengerName": {
                            "familyName": ticket_data.data.passenger_2_surname,
                            "givenName": ticket_data.data.passenger_2_forename,
                        }
                    }
                })

            thumb = pass_photo_thumbnail(ticket_obj, (270, 270), 20)
            out_3x = io.BytesIO()
            thumb.save(out_3x, format="PNG")
            out_2x = io.BytesIO()
            thumb.resize((180, 180)).save(out_2x, format="PNG")
            out_1x = io.BytesIO()
            thumb.resize((90, 90)).save(out_1x, format="PNG")
            pkp.add_file(f"thumbnail@3x.png", out_3x.getvalue())
            pkp.add_file(f"thumbnail@2x.png", out_2x.getvalue())
            pkp.add_file(f"thumbnail.png", out_1x.getvalue())

            add_pkp_img(pkp, "pass/logo-nr.png", "logo.png")
            have_logo = True

    elif isinstance(ticket_instance, models.TS2Instance):
        ticket_data: ticket.TS2Ticket = ticket_instance.as_ticket

        if ticket_data.envelope.issuer_rics in KNOWN_VALID_SIGNATURE_RICS and not ticket_data.envelope.verify_signature():
            pass_json["voided"] = True

        pass_json["barcodes"] = [{
            "format": "PKBarcodeFormatAztec",
            "message": bytes(ticket_instance.barcode_data).decode("iso-8859-1"),
            "messageEncoding": "iso-8859-1",
            "altText": ticket_data.data.vvt_ticket_id,
        }]

        pass_json["expirationDate"] = datetime.datetime.combine(ticket_data.data.valid_to, datetime.time.max) \
            .strftime("%Y-%m-%dT%H:%M:%SZ")
        pass_fields = {
            "primaryFields": [{
                "key": "passenger",
                "label": "passenger-label",
                "value": f"{ticket_data.data.customer_forename}\n{ticket_data.data.customer_surname}",
                "semantics": {
                    "passengerName": {
                        "familyName": ticket_data.data.customer_surname,
                        "givenName": ticket_data.data.customer_forename,
                    }
                }
            }],
            "secondaryFields": [{
                "key": "validity-start",
                "label": "validity-start-label",
                "dateStyle": "PKDateStyleMedium",
                "value": ticket_data.data.valid_from.strftime("%Y-%m-%dT%H:%M:%SZ"),
            }, {
                "key": "validity-end",
                "label": "validity-end-label",
                "dateStyle": "PKDateStyleMedium",
                "value": ticket_data.data.valid_to.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "changeMessage": "validity-end-change"
            }],
            "auxiliaryFields": [{
                "key": "date-of-birth",
                "label": "date-of-birth-label",
                "dateStyle": "PKDateStyleMedium",
                "value": ticket_data.data.customer_dob.strftime("%Y-%m-%dT%H:%M:%SZ"),
            }],
            "backFields": [{
                "key": "ticket-id",
                "label": "ticket-id-label",
                "value": str(ticket_data.data.ticket_id),
            }]
        }

        add_pkp_img(pkp, "pass/logo-vvt.png", "logo.png")
        have_logo = True
        pass_json["backgroundColor"] = "#E5F2FB"
        pass_json["foregroundColor"] = "#000000"
        pass_json["labelColor"] = "#D9001A"

    elif isinstance(ticket_instance, models.SNCFTicketInstance):
        ticket_data: ticket.SNCFTicket = ticket_instance.as_ticket

        pass_type = "boardingPass"

        from_station = templatetags.rics.get_station(ticket_data.data.departure_station, "sncf")
        to_station = templatetags.rics.get_station(ticket_data.data.arrival_station, "sncf")

        tz = pytz.timezone('Europe/Paris')
        now = timezone.now()
        travel_date = ticket_data.data.travel_date.replace(year=now.year)
        if travel_date < now.date():
            travel_date = travel_date.replace(year=travel_date.year + 1)
        travel_datetime = tz.localize(datetime.datetime.combine(travel_date, datetime.time.min)) \
            .astimezone(pytz.utc)
        pass_json["relevantDates"].append({
            "startDate": travel_datetime.strftime("%Y-%m-%dT00:00:00Z"),
            "endDate": travel_datetime.strftime("%Y-%m-%dT23:59:59Z"),
        })

        pass_json["locations"].append({
            "latitude": float(from_station["latitude"]),
            "longitude": float(from_station["longitude"]),
            "relevantText": from_station["name"]
        })
        pass_json["locations"].append({
            "latitude": float(to_station["latitude"]),
            "longitude": float(to_station["longitude"]),
            "relevantText": to_station["name"]
        })
        from_station_maps_link = urllib.parse.urlencode({
            "q": from_station["name"],
            "ll": f"{from_station['latitude']},{from_station['longitude']}"
        })
        to_station_maps_link = urllib.parse.urlencode({
            "q": to_station["name"],
            "ll": f"{to_station['latitude']},{to_station['longitude']}"
        })

        pass_fields = {
            "transitType": "PKTransitTypeTrain",
            "headerFields": [{
                "key": "travel-date",
                "label": "departure-date-label",
                "value": ticket_data.data.travel_date.strftime("%b %d"),
            }],
            "primaryFields": [{
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
            }, {
                "key": "to-station",
                "label": "to-station-label",
                "value": to_station["name"],
                "semantics": {
                    "destinationLocation": {
                        "latitude": float(to_station["latitude"]),
                        "longitude": float(to_station["longitude"]),
                    },
                    "destinationStationName": to_station["name"]
                }
            }],
            "auxiliaryFields": [{
                "key": "passenger",
                "label": "passenger-label",
                "value": f"{ticket_data.data.traveler_forename} {ticket_data.data.traveler_surname}",
                "semantics": {
                    "passengerName": {
                        "familyName": ticket_data.data.traveler_surname,
                        "givenName": ticket_data.data.traveler_forename,
                    }
                }
            }, {
                "key": "date-of-birth",
                "label": "date-of-birth-label",
                "dateStyle": "PKDateStyleMedium",
                "value": ticket_data.data.traveler_dob.strftime("%Y-%m-%dT%H:%M:%SZ"),
            }],
            "secondaryFields": [{
                "key": "class-code",
                "label": "class-code-label",
                "value": f"class-code-{ticket_data.data.travel_class}-label",
            }, {
                "key": "train-number",
                "label": "train-number-label",
                "value": ticket_data.data.train_number
            }],
            "backFields": [{
                "key": "from-station-back",
                "label": "from-station-label",
                "value": from_station["name"],
                "attributedValue": f"<a href=\"https://maps.apple.com/?{from_station_maps_link}\">{from_station['name']}</a>",
            }, {
                "key": "to-station-back",
                "label": "to-station-label",
                "value": to_station["name"],
                "attributedValue": f"<a href=\"https://maps.apple.com/?{to_station_maps_link}\">{to_station['name']}</a>",
            }, {
                "key": "ticket-id",
                "label": "ticket-id-label",
                "value": str(ticket_data.data.ticket_number),
            }]
        }
        pass_json["organizationName"] = "SNCF"
        pass_json["barcodes"] = [{
            "format": "PKBarcodeFormatAztec",
            "message": bytes(ticket_instance.barcode_data).decode("iso-8859-1"),
            "messageEncoding": "iso-8859-1",
            "altText": str(ticket_data.data.pnr)
        }]
        add_pkp_img(pkp, "pass/logo-sncf.png", "logo.png")
        have_logo = True

    elif isinstance(ticket_instance, models.ELBTicketInstance):
        ticket_data: ticket.ELBTicket = ticket_instance.as_ticket
        validity_end = ticket_data.data.validity_end_time()
        departure_date = ticket_data.data.departure_time()
        pass_type = "boardingPass"
        from_station = templatetags.rics.get_station(ticket_data.data.departure_station, "sncf")
        if not from_station:
            from_station = templatetags.rics.get_station(ticket_data.data.departure_station, "benerail")
        to_station = templatetags.rics.get_station(ticket_data.data.arrival_station, "sncf")
        if not to_station:
            to_station = templatetags.rics.get_station(ticket_data.data.arrival_station, "benerail")

        gtfs_trip = ticket_data.data.gtfs_trip()

        pass_json["expirationDate"] = validity_end.strftime("%Y-%m-%dT%H:%M:%SZ")
        pass_json["relevantDates"].append({
            "startDate": departure_date.strftime("%Y-%m-%dT00:00:00Z"),
            "endDate": departure_date.strftime("%Y-%m-%dT23:59:59Z"),
        })

        pass_fields = {
            "transitType": "PKTransitTypeTrain",
            "headerFields": [{
                "key": "departure-date",
                "label": "departure-date-label",
                "value": departure_date.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "dateStyle": "PKDateStyleMedium",
            }],
            "primaryFields": [],
            "secondaryFields": [{
                "key": "class-code",
                "label": "class-code-label",
                "value": f"class-code-{ticket_data.data.travel_class}-label",
            }],
            "auxiliaryFields": [{
                "key": "train-number",
                "label": "train-number-label",
                "value": ticket_data.data.train_number,
            }, {
                "key": "coach-number",
                "label": "coach-number-label",
                "value": ticket_data.data.coach_number,
            }, {
                "key": "seat-number",
                "label": "seat-number-label",
                "value": ticket_data.data.seat_number,
            }],
            "backFields": []
        }

        if from_station:
            from_station_maps_link = urllib.parse.urlencode({
                "q": from_station["name"],
                "ll": f"{from_station['latitude']},{from_station['longitude']}"
            })
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
            pass_fields["backFields"].append({
                "key": "from-station-back",
                "label": "from-station-label",
                "value": from_station["name"],
                "attributedValue": f"<a href=\"https://maps.apple.com/?{from_station_maps_link}\">{from_station['name']}</a>",
            })
            if gtfs_trip:
                if departure_stop := next(filter(lambda s: s.uic_station_id == from_station["uic"], gtfs_trip.stops),
                                          None):
                    departure_time_str = departure_stop.departure.isoformat()
                    pass_fields["secondaryFields"].append({
                        "key": "departure-time",
                        "label": "departure-time-label",
                        "value": departure_time_str,
                        "dateStyle": "PKDateStyleNone",
                        "timeStyle": "PKDateStyleShort",
                        "ignoresTimeZone": True,
                        "semantics": {
                            "originalDepartureDate": departure_time_str,
                        }
                    })
        else:
            pass_fields["primaryFields"].append({
                "key": "from-station",
                "label": "from-station-label",
                "value": ticket_data.data.departure_station,
            })

        if to_station:
            to_station_maps_link = urllib.parse.urlencode({
                "q": to_station["name"],
                "ll": f"{to_station['latitude']},{to_station['longitude']}"
            })
            pass_fields["primaryFields"].append({
                "key": "to-station",
                "label": "to-station-label",
                "value": to_station["name"],
                "semantics": {
                    "destinationLocation": {
                        "latitude": float(to_station["latitude"]),
                        "longitude": float(to_station["longitude"]),
                    },
                    "destinationStationName": to_station["name"]
                }
            })
            pass_fields["backFields"].append({
                "key": "to-station-back",
                "label": "to-station-label",
                "value": to_station["name"],
                "attributedValue": f"<a href=\"https://maps.apple.com/?{to_station_maps_link}\">{to_station['name']}</a>",
            })
            if gtfs_trip:
                if arrival_stop := next(filter(lambda s: s.uic_station_id == to_station["uic"], gtfs_trip.stops), None):
                    arrival_time_str = arrival_stop.arrival.isoformat()
                    pass_fields["secondaryFields"].append({
                        "key": "arrival-time",
                        "label": "arrival-time-label",
                        "value": arrival_time_str,
                        "dateStyle": "PKDateStyleNone",
                        "timeStyle": "PKDateStyleShort",
                        "ignoresTimeZone": True,
                        "semantics": {
                            "originalArrivalDate": arrival_time_str,
                        }
                    })
        else:
            pass_fields["primaryFields"].append({
                "key": "to-station",
                "label": "to-station-label",
                "value": ticket_data.data.arrival_station,
            })

        pass_fields["backFields"].append({
            "key": "ticket-id",
            "label": "ticket-id-label",
            "value": str(ticket_data.data.booking_number),
        })

        pass_json["barcodes"] = [{
            "format": "PKBarcodeFormatAztec",
            "message": bytes(ticket_instance.barcode_data).decode("iso-8859-1"),
            "messageEncoding": "iso-8859-1",
            "altText": f"{ticket_data.data.pnr} {ticket_data.data.sequence_number}"
        }]
        add_pkp_img(pkp, "pass/logo-eurostar.png", "logo.png")
        have_logo = True

    elif isinstance(ticket_instance, models.SSBTicketInstance):
        ticket_data: ticket.SSBTicket = ticket_instance.as_ticket

        if ticket_data.envelope.issuer_rics in KNOWN_VALID_SIGNATURE_RICS and not ticket_data.envelope.verify_signature():
            pass_json["voided"] = True

        pass_json["barcodes"] = [{
            "format": "PKBarcodeFormatAztec",
            "message": bytes(ticket_instance.barcode_data).decode("iso-8859-1"),
            "messageEncoding": "iso-8859-1",
            "altText": ticket_data.data.pnr
        }]

        if isinstance(ticket_data.data, ssb.IntegratedReservationTicket):
            pass_type = "boardingPass"
            pass_fields["transitType"] = "PKTransitTypeTrain"

            pass_fields["backFields"].append({
                "key": "ticket-id",
                "label": "ticket-id-label",
                "value": ticket_data.data.pnr,
                "semantics": {
                    "confirmationNumber": ticket_data.data.pnr,
                }
            })

            from_station = ticket_data.data.departure_station.station()
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
            elif ticket_data.data.departure_station.type == "name":
                pass_fields["primaryFields"].append({
                    "key": "from-station",
                    "label": "from-station-label",
                    "value": ticket_data.data.departure_station.id,
                    "semantics": {
                        "departureStationName": ticket_data.data.departure_station.id
                    }
                })

            to_station = ticket_data.data.arrival_station.station()
            if to_station:
                pass_fields["primaryFields"].append({
                    "key": "to-station",
                    "label": "to-station-label",
                    "value": to_station["name"],
                    "semantics": {
                        "departureLocation": {
                            "latitude": float(to_station["latitude"]),
                            "longitude": float(to_station["longitude"]),
                        },
                        "departureStationName": to_station["name"]
                    }
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
            elif ticket_data.data.arrival_station.type == "name":
                pass_fields["primaryFields"].append({
                    "key": "to-station",
                    "label": "to-station-label",
                    "value": ticket_data.data.arrival_station.id,
                    "semantics": {
                        "departureStationName": ticket_data.data.arrival_station.id,
                    }
                })

            if ticket_data.data.travel_class:
                pass_fields["auxiliaryFields"].append({
                    "key": "class-code",
                    "label": "class-code-label",
                    "value": f"class-code-{ticket_data.data.travel_class}-label",
                })

            if ticket_data.data.train_number:
                pass_fields["headerFields"].append({
                    "key": "train-number",
                    "label": "train-number-label",
                    "value": ticket_data.data.train_number,
                    "semantics": {
                        "vehicleNumber": ticket_data.data.train_number,
                    }
                })

            if ticket_data.data.coach_number:
                pass_fields["auxiliaryFields"].append({
                    "key": "coach-number",
                    "label": "coach-number-label",
                    "value": ticket_data.data.coach_number,
                })

            if ticket_data.data.seat_number:
                pass_fields["auxiliaryFields"].append({
                    "key": "seat-number",
                    "label": "seat-number-label",
                    "value": ticket_data.data.seat_number,
                })

            if from_station and from_station.get("time_zone"):
                tz = pytz.timezone(from_station["time_zone"])
            else:
                tz = pytz.utc

            pass_json["relevantDates"].append({
                "date": tz.localize(ticket_data.data.departure).isoformat()
            })

            pass_fields["secondaryFields"].append({
                "key": "departure-time",
                "label": "departure-time-label",
                "dateStyle": "PKDateStyleMedium",
                "timeStyle": "PKDateStyleShort",
                "value": f"{ticket_data.data.departure.isoformat()}Z",
                "ignoresTimeZone": True
            })
            pass_fields["backFields"].append({
                "key": "issued-date",
                "label": "issued-at-label",
                "dateStyle": "PKDateStyleFull",
                "timeStyle": "PKDateStyleNone",
                "value": f"{ticket_data.data.issuing_date.isoformat()}T00:00:00Z",
                "ignoresTimeZone": True
            })

            if ticket_data.data.extra_text:
                pass_fields["backFields"].append({
                    "key": "extra-date",
                    "label": "other-data-label",
                    "value": ticket_data.data.extra_text,
                })

            if ticket_data.data.sncb_data:
                if ticket_data.data.sncb_data.product_name:
                    pass_fields["headerFields"].append({
                        "key": "product-name",
                        "label": "product-label",
                        "value": ticket_data.data.sncb_data.product_name
                    })
                pass_fields["backFields"].append({
                    "key": "product-code",
                    "label": "product-label",
                    "value": ticket_data.data.sncb_data.product_code
                })
                if ticket_data.data.sncb_data.forename:
                    pass_fields["auxiliaryFields"].append({
                        "key": "passenger",
                        "label": "passenger-label",
                        "value": f"{ticket_data.data.sncb_data.forename} {ticket_data.data.sncb_data.surname}",
                        "semantics": {
                            "passengerName": {
                                "familyName": ticket_data.data.sncb_data.surname,
                                "givenName": ticket_data.data.sncb_data.forename,
                            }
                        }
                    })

        elif isinstance(ticket_data.data, ssb.NonReservationTicket):
            pass_type = "boardingPass"
            pass_fields["transitType"] = "PKTransitTypeTrain"

            pass_fields["backFields"].append({
                "key": "ticket-id",
                "label": "ticket-id-label",
                "value": ticket_data.data.pnr,
                "semantics": {
                    "confirmationNumber": ticket_data.data.pnr,
                }
            })

            from_station = ticket_data.data.departure_station.station()
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
            elif ticket_data.data.departure_station.type == "name":
                pass_fields["primaryFields"].append({
                    "key": "from-station",
                    "label": "from-station-label",
                    "value": ticket_data.data.departure_station.id,
                    "semantics": {
                        "departureStationName": ticket_data.data.departure_station.id,
                    }
                })

            to_station = ticket_data.data.arrival_station.station()
            if to_station:
                pass_fields["primaryFields"].append({
                    "key": "to-station",
                    "label": "to-station-label",
                    "value": to_station["name"],
                    "semantics": {
                        "departureLocation": {
                            "latitude": float(to_station["latitude"]),
                            "longitude": float(to_station["longitude"]),
                        },
                        "departureStationName": to_station["name"]
                    }
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
            elif ticket_data.data.arrival_station.type == "name":
                pass_fields["primaryFields"].append({
                    "key": "to-station",
                    "label": "to-station-label",
                    "value": ticket_data.data.arrival_station.id,
                    "semantics": {
                        "departureStationName": ticket_data.data.arrival_station.id,
                    }
                })

            if ticket_data.data.travel_class:
                pass_fields["auxiliaryFields"].append({
                    "key": "class-code",
                    "label": "class-code-label",
                    "value": f"class-code-{ticket_data.data.travel_class}-label",
                })

            validity_end = datetime.datetime.combine(ticket_data.data.validity_end, datetime.time.max)
            if to_station and to_station.get("time_zone"):
                tz = pytz.timezone(to_station["time_zone"])
            else:
                tz = pytz.utc

            validity_end += datetime.timedelta(hours=3)
            pass_json["expirationDate"] = tz.localize(validity_end).isoformat()

            pass_fields["secondaryFields"].append({
                "key": "validity-start",
                "label": "validity-start-label",
                "dateStyle": "PKDateStyleMedium",
                "timeStyle": "PKDateStyleNone",
                "value": f"{ticket_data.data.validity_start.isoformat()}T00:00:00Z",
                "ignoresTimeZone": True
            })
            pass_fields["secondaryFields"].append({
                "key": "validity-end",
                "label": "validity-end-label",
                "dateStyle": "PKDateStyleMedium",
                "timeStyle": "PKDateStyleNone",
                "value": f"{ticket_data.data.validity_end.isoformat()}T00:00:00Z",
                "ignoresTimeZone": True
            })
            pass_fields["backFields"].append({
                "key": "issued-date",
                "label": "issued-at-label",
                "dateStyle": "PKDateStyleFull",
                "timeStyle": "PKDateStyleNone",
                "value": f"{ticket_data.data.issuing_date.isoformat()}T00:00:00Z",
                "ignoresTimeZone": True
            })

            if ticket_data.data.extra_text:
                if ticket_data.data.extra_text == "GATING_ONLY":
                    pass_json["voided"] = False
                    pass_fields["headerFields"].append({
                        "key": "card-name",
                        "label": "product-label",
                        "value": "Keycard",
                    })

                pass_fields["backFields"].append({
                    "key": "extra-date",
                    "label": "other-data-label",
                    "value": ticket_data.data.extra_text,
                })

            if ticket_data.data.sncb_data:
                if ticket_data.data.sncb_data.product_name:
                    pass_fields["headerFields"].append({
                        "key": "product-name",
                        "label": "product-label",
                        "value": ticket_data.data.sncb_data.product_name
                    })
                pass_fields["backFields"].append({
                    "key": "product-code",
                    "label": "product-label",
                    "value": ticket_data.data.sncb_data.product_code
                })
                if ticket_data.data.sncb_data.forename:
                    pass_fields["auxiliaryFields"].append({
                        "key": "passenger",
                        "label": "passenger-label",
                        "value": f"{ticket_data.data.sncb_data.forename} {ticket_data.data.sncb_data.surname}",
                        "semantics": {
                            "passengerName": {
                                "familyName": ticket_data.data.sncb_data.surname,
                                "givenName": ticket_data.data.sncb_data.forename,
                            }
                        }
                    })

        elif isinstance(ticket_data.data, ssb.GroupTicket):
            pass_type = "boardingPass"
            pass_fields["transitType"] = "PKTransitTypeTrain"

            pass_fields["backFields"].append({
                "key": "ticket-id",
                "label": "ticket-id-label",
                "value": ticket_data.data.pnr,
                "semantics": {
                    "confirmationNumber": ticket_data.data.pnr,
                }
            })

            pass_fields["headerFields"].append({
                "key": "group-ticket",
                "value": "group-ticket-label"
            })

            from_station = ticket_data.data.departure_station.station()
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
            elif ticket_data.data.departure_station.type == "name":
                pass_fields["primaryFields"].append({
                    "key": "from-station",
                    "label": "from-station-label",
                    "value": ticket_data.data.departure_station.id,
                    "semantics": {
                        "departureStationName": ticket_data.data.departure_station.id,
                    }
                })

            to_station = ticket_data.data.arrival_station.station()
            if to_station:
                pass_fields["primaryFields"].append({
                    "key": "to-station",
                    "label": "to-station-label",
                    "value": to_station["name"],
                    "semantics": {
                        "departureLocation": {
                            "latitude": float(to_station["latitude"]),
                            "longitude": float(to_station["longitude"]),
                        },
                        "departureStationName": to_station["name"]
                    }
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
            elif ticket_data.data.arrival_station.type == "name":
                pass_fields["primaryFields"].append({
                    "key": "to-station",
                    "label": "to-station-label",
                    "value": ticket_data.data.arrival_station.id,
                    "semantics": {
                        "departureStationName": ticket_data.data.arrival_station.id
                    }
                })

            if ticket_data.data.travel_class:
                pass_fields["auxiliaryFields"].append({
                    "key": "class-code",
                    "label": "class-code-label",
                    "value": f"class-code-{ticket_data.data.travel_class}-label",
                })

            if ticket_data.data.group_leader:
                pass_fields["secondaryFields"].append({
                    "key": "group-leader",
                    "label": "group-leader-label",
                    "value": ticket_data.data.group_leader,
                })

            validity_end = datetime.datetime.combine(ticket_data.data.validity_end, datetime.time.max)
            if to_station and to_station.get("time_zone"):
                tz = pytz.timezone(to_station["time_zone"])
            else:
                tz = pytz.utc

            validity_end += datetime.timedelta(hours=3)
            pass_json["expirationDate"] = tz.localize(validity_end).isoformat()

            pass_fields["secondaryFields"].append({
                "key": "validity-start",
                "label": "validity-start-label",
                "dateStyle": "PKDateStyleMedium",
                "timeStyle": "PKDateStyleNone",
                "value": f"{ticket_data.data.validity_start.isoformat()}T00:00:00Z",
                "ignoresTimeZone": True
            })
            pass_fields["secondaryFields"].append({
                "key": "validity-end",
                "label": "validity-end-label",
                "dateStyle": "PKDateStyleMedium",
                "timeStyle": "PKDateStyleNone",
                "value": f"{ticket_data.data.validity_end.isoformat()}T00:00:00Z",
                "ignoresTimeZone": True
            })
            pass_fields["backFields"].append({
                "key": "issued-date",
                "label": "issued-at-label",
                "dateStyle": "PKDateStyleFull",
                "timeStyle": "PKDateStyleNone",
                "value": f"{ticket_data.data.issuing_date.isoformat()}T00:00:00Z",
                "ignoresTimeZone": True
            })

            if ticket_data.data.extra_text:
                pass_fields["backFields"].append({
                    "key": "extra-date",
                    "label": "other-data-label",
                    "value": ticket_data.data.extra_text,
                })

        elif isinstance(ticket_data.data, ssb.ns_keycard.Keycard):
            pass_fields["headerFields"].append({
                "key": "card-name",
                "label": "product-label",
                "value": "Keycard",
            })

            if ticket_data.data.station_uic:
                station = templatetags.rics.get_station(ticket_data.data.station_uic, "uic")
                pass_fields["primaryFields"].append({
                    "key": "station",
                    "label": "station-label",
                    "value": station["name"],
                })

            pass_fields["secondaryFields"].append({
                "key": "card-id",
                "label": "card-id-label",
                "value": ticket_data.data.card_id,
            })
            pass_fields["secondaryFields"].append({
                "key": "validity-start",
                "label": "validity-start-label",
                "dateStyle": "PKDateStyleMedium",
                "timeStyle": "PKDateStyleNone",
                "value": f"{ticket_data.data.validity_start.isoformat()}T00:00:00Z",
                "ignoresTimeZone": True
            })
            pass_fields["secondaryFields"].append({
                "key": "validity-end",
                "label": "validity-end-label",
                "dateStyle": "PKDateStyleMedium",
                "timeStyle": "PKDateStyleNone",
                "value": f"{ticket_data.data.validity_end.isoformat()}T00:00:00Z",
                "ignoresTimeZone": True
            })
            pass_fields["backFields"].append({
                "key": "issued-date",
                "label": "issued-at-label",
                "dateStyle": "PKDateStyleFull",
                "timeStyle": "PKDateStyleNone",
                "value": f"{ticket_data.data.issuing_date.isoformat()}T00:00:00Z",
                "ignoresTimeZone": True
            })

            if ticket_data.data.extra_text:
                pass_fields["backFields"].append({
                    "key": "extra-date",
                    "label": "other-data-label",
                    "value": ticket_data.data.extra_text,
                })

        elif isinstance(ticket_data.data, ssb.sz.Ticket):
            pass_json["expirationDate"] = ticket_data.data.valid_to.astimezone(pytz.utc).isoformat()

            pass_fields["primaryFields"].append({
                "key": "card-name",
                "label": "product-label",
                "value": ticket_data.data.product_str()
            })

            pass_fields["secondaryFields"].append({
                "key": "validity-start",
                "label": "validity-start-label",
                "dateStyle": "PKDateStyleMedium",
                "timeStyle": "PKDateStyleShort",
                "value": ticket_data.data.valid_from.isoformat()
            })
            pass_fields["secondaryFields"].append({
                "key": "validity-end",
                "label": "validity-end-label",
                "dateStyle": "PKDateStyleMedium",
                "timeStyle": "PKDateStyleShort",
                "value": ticket_data.data.valid_to.isoformat()
            })

            class_code = ticket_data.data.travel_class()
            if class_code and class_code != "notApplicable":
                pass_fields["secondaryFields"].append({
                    "key": "class-code",
                    "label": "class-code-label",
                    "value": f"class-code-{class_code}-label",
                })

            pass_fields["backFields"].append({
                "key": "price",
                "label": "price-label",
                "value": ticket_data.data.price_str()
            })

        if distributor := ticket_data.envelope.issuer():
            pass_json["organizationName"] = distributor["full_name"]
            if distributor["url"]:
                pass_fields["backFields"].append({
                    "key": "issuing-org",
                    "label": "issuing-organisation-label",
                    "value": distributor["full_name"],
                    "attributedValue": f"<a href=\"{distributor['url']}\">{distributor['full_name']}</a>",
                })
                return_pass_fields["backFields"].append({
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
                return_pass_fields["backFields"].append({
                    "key": "distributor",
                    "label": "issuing-organisation-label",
                    "value": distributor["full_name"],
                })

        if ticket_data.envelope.issuer_rics in RICS_LOGO:
            add_pkp_img(pkp, RICS_LOGO[ticket_data.envelope.issuer_rics], "logo.png")
            have_logo = True
        if ticket_data.envelope.issuer_rics in RICS_BG:
            pass_json["backgroundColor"] = RICS_BG[ticket_data.envelope.issuer_rics]
        if ticket_data.envelope.issuer_rics in RICS_FG:
            pass_json["foregroundColor"] = RICS_FG[ticket_data.envelope.issuer_rics]
        if ticket_data.envelope.issuer_rics in RICS_FG_SECONDARY:
            pass_json["labelColor"] = RICS_FG_SECONDARY[ticket_data.envelope.issuer_rics]

    elif isinstance(ticket_instance, models.SSB1TicketInstance):
        ticket_data: ticket.SSB1Ticket = ticket_instance.as_ticket

        pass_json["barcodes"] = [{
            "format": "PKBarcodeFormatAztec",
            "message": bytes(ticket_instance.barcode_data).decode("iso-8859-1"),
            "messageEncoding": "iso-8859-1",
            "altText": ticket_data.ticket.pnr
        }]

        pass_type = "boardingPass"
        pass_fields["transitType"] = "PKTransitTypeTrain"

        pass_fields["backFields"].append({
            "key": "ticket-id",
            "label": "ticket-id-label",
            "value": ticket_data.ticket.pnr,
            "semantics": {
                "confirmationNumber": ticket_data.ticket.pnr,
            }
        })

        if ticket_data.ticket.departure_station_number:
            from_station = templatetags.rics.get_station(ticket_data.ticket.departure_station_number, "uic")
        elif ticket_data.ticket.departure_station_name:
            from_station = templatetags.rics.get_station(ticket_data.ticket.departure_station_name, "finland")
        else:
            from_station = None

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

        if ticket_data.ticket.arrival_station_number:
            to_station = templatetags.rics.get_station(ticket_data.ticket.arrival_station_number, "uic")
        elif ticket_data.ticket.arrival_station_name:
            to_station = templatetags.rics.get_station(ticket_data.ticket.arrival_station_name, "finland")
        else:
            to_station = None

        if to_station:
            pass_fields["primaryFields"].append({
                "key": "to-station",
                "label": "to-station-label",
                "value": to_station["name"],
                "semantics": {
                    "departureLocation": {
                        "latitude": float(to_station["latitude"]),
                        "longitude": float(to_station["longitude"]),
                    },
                    "departureStationName": to_station["name"]
                }
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

        if ticket_data.ticket.travel_class:
            pass_fields["auxiliaryFields"].append({
                "key": "class-code",
                "label": "class-code-label",
                "value": f"class-code-{ticket_data.ticket.travel_class}-label",
            })

        if ticket_data.ticket.train_number:
            pass_fields["headerFields"].append({
                "key": "train-number",
                "label": "train-number-label",
                "value": str(ticket_data.ticket.train_number),
                "semantics": {
                    "vehicleNumber": str(ticket_data.ticket.train_number),
                }
            })

        if ticket_data.ticket.coach_number:
            pass_fields["auxiliaryFields"].append({
                "key": "coach-number",
                "label": "coach-number-label",
                "value": str(ticket_data.ticket.coach_number),
            })

        if ticket_data.ticket.seat:
            pass_fields["auxiliaryFields"].append({
                "key": "seat-number",
                "label": "seat-number-label",
                "value": str(ticket_data.ticket.seat),
            })

        if ticket_data.ticket.departure_time:
            pass_fields["secondaryFields"].append({
                "key": "departure-time",
                "label": "departure-time-label",
                "value": f"{ticket_data.ticket.valid_from.day:02d}.{ticket_data.ticket.valid_from.month:02d} "
                         f"{ticket_data.ticket.departure_time.hour:02d}:{ticket_data.ticket.departure_time.minute:02d}"
            })
        else:
            pass_fields["secondaryFields"].append({
                "key": "validity-start",
                "label": "validity-start-label",
                "value": f"{ticket_data.ticket.valid_from.day:02d}.{ticket_data.ticket.valid_from.month:02d}"
            })
            pass_fields["secondaryFields"].append({
                "key": "validity-end",
                "label": "validity-end-label",
                "value": f"{ticket_data.ticket.valid_until.day:02d}.{ticket_data.ticket.valid_until.month:02d}"
            })

        if distributor := ticket_data.ticket.issuer():
            pass_json["organizationName"] = distributor["full_name"]
            if distributor["url"]:
                pass_fields["backFields"].append({
                    "key": "issuing-org",
                    "label": "issuing-organisation-label",
                    "value": distributor["full_name"],
                    "attributedValue": f"<a href=\"{distributor['url']}\">{distributor['full_name']}</a>",
                })
                return_pass_fields["backFields"].append({
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
                return_pass_fields["backFields"].append({
                    "key": "distributor",
                    "label": "issuing-organisation-label",
                    "value": distributor["full_name"],
                })

        if ticket_data.ticket.issuer_rics in RICS_LOGO:
            add_pkp_img(pkp, RICS_LOGO[ticket_data.ticket.issuer_rics], "logo.png")
            have_logo = True
        if ticket_data.ticket.issuer_rics in RICS_BG:
            pass_json["backgroundColor"] = RICS_BG[ticket_data.ticket.issuer_rics]
        if ticket_data.ticket.issuer_rics in RICS_FG:
            pass_json["foregroundColor"] = RICS_FG[ticket_data.ticket.issuer_rics]
        if ticket_data.ticket.issuer_rics in RICS_FG_SECONDARY:
            pass_json["labelColor"] = RICS_FG_SECONDARY[ticket_data.ticket.issuer_rics]

    elif isinstance(ticket_instance, models.HZPPTicketInstance):
        ticket_data: ticket.HZPPTicket = ticket_instance.as_ticket

        pass_type = "boardingPass"
        pass_fields["transitType"] = "PKTransitTypeTrain"
        pass_json["backgroundColor"] = "rgb(239, 239, 239)"
        pass_json["labelColor"] = "rgb(255, 51, 51)"
        pass_json["foregroundColor"] = "rgb(5, 80, 160)"

        pass_json["organizationName"] = "HŽPP"
        pass_json["barcodes"] = [{
            "format": "PKBarcodeFormatAztec",
            "message": bytes(ticket_instance.barcode_data).decode("iso-8859-1"),
            "messageEncoding": "iso-8859-1",
            "altText": str(ticket_data.data.ticket_number)
        }]

        pass_json["relevantDates"].append({
            "startDate": ticket_data.data.valid_from.astimezone(pytz.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "endDate": ticket_data.data.valid_until.astimezone(pytz.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        })
        pass_json["expirationDate"] = ticket_data.data.valid_until.astimezone(pytz.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        if len(ticket_data.data.journey_segments) > 1:
            has_return = True
            return_pass_json = copy.deepcopy(pass_json)
            return_pass_type = "boardingPass"
            return_pass_fields["transitType"] = "PKTransitTypeTrain"

        pass_fields["backFields"].append({
            "key": "ticket-id",
            "label": "ticket-id-label",
            "value": str(ticket_data.data.ticket_number),
        })
        pass_fields["secondaryFields"].append({
            "key": "validity-start",
            "label": "validity-start-label",
            "dateStyle": "PKDateStyleMedium",
            "timeStyle": "PKDateStyleShort",
            "value": ticket_data.data.valid_from.astimezone(pytz.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        })
        pass_fields["backFields"].append({
            "key": "validity-start-back",
            "label": "validity-start-label",
            "dateStyle": "PKDateStyleFull",
            "timeStyle": "PKDateStyleFull",
            "value": ticket_data.data.valid_from.astimezone(pytz.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        })
        pass_fields["secondaryFields"].append({
            "key": "validity-end",
            "label": "validity-end-label",
            "dateStyle": "PKDateStyleMedium",
            "timeStyle": "PKDateStyleShort",
            "value": ticket_data.data.valid_until.astimezone(pytz.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        })
        pass_fields["backFields"].append({
            "key": "validity-end-back",
            "label": "validity-end-label",
            "dateStyle": "PKDateStyleFull",
            "timeStyle": "PKDateStyleFull",
            "value": ticket_data.data.valid_until.astimezone(pytz.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        })
        if ticket_data.data.customer:
            pass_fields["auxiliaryFields"].append({
                "key": "passenger",
                "label": "passenger-label",
                "value": ticket_data.data.customer.name
            })
        if has_return:
            return_pass_fields["backFields"].append({
                "key": "ticket-id",
                "label": "ticket-id-label",
                "value": str(ticket_data.data.ticket_number),
            })
            return_pass_fields["secondaryFields"].append({
                "key": "validity-start",
                "label": "validity-start-label",
                "dateStyle": "PKDateStyleMedium",
                "timeStyle": "PKDateStyleShort",
                "value": ticket_data.data.valid_from.astimezone(pytz.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            })
            return_pass_fields["backFields"].append({
                "key": "validity-start-back",
                "label": "validity-start-label",
                "dateStyle": "PKDateStyleFull",
                "timeStyle": "PKDateStyleFull",
                "value": ticket_data.data.valid_from.astimezone(pytz.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            })
            return_pass_fields["secondaryFields"].append({
                "key": "validity-end",
                "label": "validity-end-label",
                "dateStyle": "PKDateStyleMedium",
                "timeStyle": "PKDateStyleShort",
                "value": ticket_data.data.valid_until.astimezone(pytz.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            })
            return_pass_fields["backFields"].append({
                "key": "validity-end-back",
                "label": "validity-end-label",
                "dateStyle": "PKDateStyleFull",
                "timeStyle": "PKDateStyleFull",
                "value": ticket_data.data.valid_until.astimezone(pytz.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            })
            if ticket_data.data.customer:
                return_pass_fields["auxiliaryFields"].append({
                    "key": "passenger",
                    "label": "passenger-label",
                    "value": ticket_data.data.customer.name
                })

        from_station = templatetags.rics.get_station(ticket_data.data.journey_segments[0].origin_station, "uic")
        to_station = templatetags.rics.get_station(ticket_data.data.journey_segments[0].destination_station, "uic")
        from_station_maps_link = urllib.parse.urlencode({
            "q": from_station["name"],
            "ll": f"{from_station['latitude']},{from_station['longitude']}"
        })
        to_station_maps_link = urllib.parse.urlencode({
            "q": to_station["name"],
            "ll": f"{to_station['latitude']},{to_station['longitude']}"
        })

        pass_fields["primaryFields"] = [{
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
        }, {
            "key": "to-station",
            "label": "to-station-label",
            "value": to_station["name"],
            "semantics": {
                "destinationLocation": {
                    "latitude": float(to_station["latitude"]),
                    "longitude": float(to_station["longitude"]),
                },
                "destinationStationName": to_station["name"]
            }
        }]
        pass_fields["headerFields"] = [{
            "key": "class-code",
            "label": "class-code-label",
            "value": f"class-code-{ticket_data.data.journey_segments[0].travel_class}-label",
        }]
        pass_fields["backFields"].append({
            "key": "from-station-back",
            "label": "from-station-label",
            "value": from_station["name"],
            "attributedValue": f"<a href=\"https://maps.apple.com/?{from_station_maps_link}\">{from_station['name']}</a>",
        })
        pass_fields["backFields"].append({
            "key": "to-station-back",
            "label": "to-station-label",
            "value": to_station["name"],
            "attributedValue": f"<a href=\"https://maps.apple.com/?{to_station_maps_link}\">{to_station['name']}</a>",
        })

        if has_return:
            from_station = templatetags.rics.get_station(ticket_data.data.journey_segments[1].origin_station, "uic")
            to_station = templatetags.rics.get_station(ticket_data.data.journey_segments[1].destination_station, "uic")
            from_station_maps_link = urllib.parse.urlencode({
                "q": from_station["name"],
                "ll": f"{from_station['latitude']},{from_station['longitude']}"
            })
            to_station_maps_link = urllib.parse.urlencode({
                "q": to_station["name"],
                "ll": f"{to_station['latitude']},{to_station['longitude']}"
            })
            return_pass_fields["primaryFields"] = [{
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
            }, {
                "key": "to-station",
                "label": "to-station-label",
                "value": to_station["name"],
                "semantics": {
                    "destinationLocation": {
                        "latitude": float(to_station["latitude"]),
                        "longitude": float(to_station["longitude"]),
                    },
                    "destinationStationName": to_station["name"]
                }
            }]
            return_pass_fields["headerFields"] = [{
                "key": "class-code",
                "label": "class-code-label",
                "value": f"class-code-{ticket_data.data.journey_segments[1].travel_class}-label",
            }]
            return_pass_fields["backFields"].append({
                "key": "from-station-back",
                "label": "from-station-label",
                "value": from_station["name"],
                "attributedValue": f"<a href=\"https://maps.apple.com/?{from_station_maps_link}\">{from_station['name']}</a>",
            })
            return_pass_fields["backFields"].append({
                "key": "to-station-back",
                "label": "to-station-label",
                "value": to_station["name"],
                "attributedValue": f"<a href=\"https://maps.apple.com/?{to_station_maps_link}\">{to_station['name']}</a>",
            })

        add_pkp_img(pkp, "pass/logo-hzpp.png", "logo.png")
        have_logo = True
    elif isinstance(ticket_instance, models.SwissPassTicketInstance):
        ticket_data: ticket.SwissPassTicket = ticket_instance.as_ticket

        pass_json["barcodes"] = [{
            "format": "PKBarcodeFormatQR",
            "message": bytes(ticket_instance.barcode_data).decode("iso-8859-1"),
            "messageEncoding": "iso-8859-1",
            "altText": str(ticket_data.data.ticket.ticket_data.ticket_id),
        }]

        pass_fields["backFields"].append({
            "key": "ticket-id",
            "label": "ticket-id-label",
            "value": str(ticket_data.data.ticket.ticket_data.ticket_id),
            "semantics": {
                "confirmationNumber": str(ticket_data.data.ticket.ticket_data.ticket_id),
            }
        })

        if ticket_data.data.ticket.ticket_data.transport:
            train_number = ", ".join([t.journey_number for t in ticket_data.data.ticket.ticket_data.transport])
            pass_fields["headerFields"].append({
                "key": "train-number",
                "label": "train-number-label",
                "value": train_number,
                "semantics": {
                    "vehicleNumber": train_number
                }
            })
        else:
            pass_fields["headerFields"].append({
                "key": "product",
                "label": "product-label",
                "value": ticket_data.data.ticket.ticket_data.tariff.product.name
            })

        pass_fields["backFields"].append({
            "key": "product-back",
            "label": "product-label",
            "value": ticket_data.data.ticket.ticket_data.tariff.product.name
        })

        if ticket_data.data.ticket.ticket_data.tariff.departure_station or \
                ticket_data.data.ticket.ticket_data.tariff.arrival_station:
            pass_type = "boardingPass"
            pass_fields["transitType"] = "PKTransitTypeTrain"

        if ticket_data.data.ticket.ticket_data.tariff.departure_station:
            pass_fields["primaryFields"].append({
                "key": "from-station",
                "label": "from-station-label",
                "value": ticket_data.data.ticket.ticket_data.tariff.departure_station,
                "semantics": {
                    "departureStationName": ticket_data.data.ticket.ticket_data.tariff.departure_station
                }
            })

        if ticket_data.data.ticket.ticket_data.tariff.arrival_station:
            pass_fields["primaryFields"].append({
                "key": "to-station",
                "label": "to-station-label",
                "value": ticket_data.data.ticket.ticket_data.tariff.arrival_station,
                "semantics": {
                    "arrivalStationName": ticket_data.data.ticket.ticket_data.tariff.arrival_station
                }
            })

        one_day_ticket = ticket_data.data.valid_from.date() == ticket_data.data.valid_until.date()
        pass_fields["secondaryFields"].append({
            "key": "validity-start",
            "label": "validity-start-label",
            "dateStyle": "PKDateStyleMedium",
            "timeStyle": "PKDateStyleMedium" if one_day_ticket else "PKDateStyleNone",
            "value": ticket_data.data.valid_from.isoformat(),
        })
        pass_fields["backFields"].append({
            "key": "validity-start-back",
            "label": "validity-start-label",
            "dateStyle": "PKDateStyleFull",
            "timeStyle": "PKDateStyleFull",
            "value": ticket_data.data.valid_from.isoformat(),
        })
        pass_fields["secondaryFields"].append({
            "key": "validity-end",
            "label": "validity-end-label",
            "dateStyle": "PKDateStyleMedium",
            "timeStyle": "PKDateStyleMedium" if one_day_ticket else "PKDateStyleNone",
            "value": ticket_data.data.valid_until.isoformat(),
        })
        pass_fields["backFields"].append({
            "key": "validity-end-back",
            "label": "validity-end-label",
            "dateStyle": "PKDateStyleFull",
            "timeStyle": "PKDateStyleFull",
            "value": ticket_data.data.valid_until.isoformat(),
        })

        pass_json["expirationDate"] = ticket_data.data.valid_until.isoformat()

        if ticket_data.data.ticket.ticket_data.tariff.route:
            pass_fields["auxiliaryFields"].append({
                "key": "route",
                "label": "route-label",
                "value": ", ".join(ticket_data.data.ticket.ticket_data.tariff.route)
            })

        if ticket_data.data.ticket.ticket_data.tariff.travel_class:
            pass_fields["auxiliaryFields"].append({
                "key": "class-code",
                "label": "class-code-label",
                "value": f"class-code-{ticket_data.data.ticket.ticket_data.tariff.travel_class}-label"
            })

        if ticket_data.data.ticket.ticket_data.HasField("traveler"):
            if pass_type == "boardingPass":
                name_value = (f"{ticket_data.data.ticket.ticket_data.traveler.forename} "
                              f"{ticket_data.data.ticket.ticket_data.traveler.surname}").strip()
                if name_value:
                    pass_fields["auxiliaryFields"].append({
                        "key": "passenger",
                        "label": "passenger-label",
                        "value": name_value,
                        "semantics": {
                            "passengerName": {
                                "familyName": ticket_data.data.ticket.ticket_data.traveler.surname,
                                "givenName": ticket_data.data.ticket.ticket_data.traveler.forename,
                            }
                        }
                    })
            else:
                name_value = (f"{ticket_data.data.ticket.ticket_data.traveler.forename}\n"
                              f"{ticket_data.data.ticket.ticket_data.traveler.surname}").strip()
                if name_value:
                    pass_fields["primaryFields"].append({
                        "key": "passenger",
                        "label": "passenger-label",
                        "value": name_value,
                        "semantics": {
                            "passengerName": {
                                "familyName": ticket_data.data.ticket.ticket_data.traveler.surname,
                                "givenName": ticket_data.data.ticket.ticket_data.traveler.forename,
                            }
                        }
                    })
            if ticket_data.data.ticket.ticket_data.traveler.birthday and ticket_data.data.traveler_birthday:
                if pass_type == "boardingPass":
                    pass_fields["secondaryFields"].append({
                        "key": "date-of-birth",
                        "label": "date-of-birth-label",
                        "dateStyle": "PKDateStyleMedium",
                        "value": ticket_data.data.traveler_birthday.isoformat(),
                    })
                else:
                    pass_fields["auxiliaryFields"].append({
                        "key": "date-of-birth",
                        "label": "date-of-birth-label",
                        "dateStyle": "PKDateStyleMedium",
                        "value": ticket_data.data.traveler_birthday.isoformat(),
                    })

        pass_fields["backFields"].append({
            "key": "issued-date",
            "label": "issued-at-label",
            "dateStyle": "PKDateStyleFull",
            "timeStyle": "PKDateStyleFull",
            "value": ticket_data.data.issuing_time.isoformat(),
        })

        if ticket_data.data.ticket.ticket_data.tariff.product_number:
            pass_fields["backFields"].append({
                "key": "article-number",
                "label": "article-number-label",
                "value": str(ticket_data.data.ticket.ticket_data.tariff.product_number)
            })

        if ticket_data.data.ticket.ticket_data.HasField("payment"):
            pass_fields["backFields"].append({
                "key": "price",
                "label": "price-label",
                "value": f"{ticket_data.data.ticket.ticket_data.payment.price} "
                         f"{ticket_data.data.ticket.ticket_data.payment.currency}"
            })

        if distributor := ticket_data.data.issuer():
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

        if seller := swisspass.org_id.get_org(ticket_data.data.ticket.ticket_data.sale.issuing_org):
            pass_fields["backFields"].append({
                "key": "ticket-org",
                "label": "ticketing-organisation-label",
                "value": f"{seller['short_name']} - {seller['name']}"
            })

        issuer_id = ticket_data.data.ticket.ticket_data.sale.issuing_org
        if issuer_id in SWISSPASS_LOGO:
            add_pkp_img(pkp, SWISSPASS_LOGO[issuer_id], "logo.png")
            have_logo = True
        if issuer_id in SWISSPASS_BG:
            pass_json["backgroundColor"] = SWISSPASS_BG[issuer_id]
        if issuer_id in SWISSPASS_FG:
            pass_json["foregroundColor"] = SWISSPASS_FG[issuer_id]
        if issuer_id in SWISSPASS_FG_SECONDARY:
            pass_json["labelColor"] = SWISSPASS_FG_SECONDARY[issuer_id]

    elif isinstance(ticket_instance, models.IATATicketInstance):
        ticket_data: ticket.IATATicket = ticket_instance.as_ticket

        pass_json["barcodes"] = [{
            "format": "PKBarcodeFormatAztec",
            "message": bytes(ticket_instance.barcode_data).decode("iso-8859-1"),
            "messageEncoding": "iso-8859-1",
            "altText": f"{ticket_data.data.pnr} SEQ {ticket_data.data.sequence}",
        }]
        pass_json["preferredStyleSchemes"] = ["semanticBoardingPass", "boardingPass"]

        pass_type = "boardingPass"
        pass_fields["transitType"] = "PKTransitTypeAir"

        pass_fields["auxiliaryFields"].append({
            "key": "passenger",
            "label": "passenger-label",
            "value": f"{ticket_data.data.header.passenger_surname}, {ticket_data.data.header.passenger_forename}",
            "semantics": {
                "passengerName": {
                    "familyName": ticket_data.data.header.passenger_surname,
                    "givenName": ticket_data.data.header.passenger_forename,
                }
            }
        })

        if ticket_data.data.conditional:
            if issuer := templatetags.iata.get_iata_airline_code(ticket_data.data.conditional.issuer):
                pass_fields["backFields"].append({
                    "key": "issuing-org",
                    "label": "issuing-organisation-label",
                    "value": issuer["Airline_Name"]
                })

        if ticket_data.data.legs:
            leg = ticket_data.data.legs[0]

            pass_json["semantics"]["airlinePassengerCapabilities"] = []
            pass_json["semantics"]["departureAirportSecurityPrograms"] = []
            if leg.date:
                pass_json["semantics"]["originalDepartureDate"] = f"{leg.date.isoformat()}T00:00:00Z"
            pass_json["semantics"]["airlineCode"] = leg.operating_carrier
            pass_json["semantics"]["flightNumber"] = int(leg.flight_number)

            pass_fields["headerFields"].append({
                "key": "flight-number",
                "label": "flight-number-label",
                "value": f"{leg.operating_carrier}{leg.flight_number}",
            })

            pass_fields["auxiliaryFields"].append({
                "key": "seat-number",
                "label": "seat-number-label",
                "value": leg.seat
            })

            pass_json["locations"] = []

            departure = templatetags.iata.get_iata_airport_code(leg.from_code)
            pass_fields["primaryFields"].append({
                "key": "from-station",
                "label": "from-station-label",
                "value": leg.from_code
            })
            pass_json["semantics"]["departureAirportCode"] = leg.from_code
            if departure:
                pass_json["semantics"]["departureAirportName"] = departure["name"]
                pass_json["semantics"]["departureLocation"] = {
                    "latitude": float(departure["latitude"]),
                    "longitude": float(departure["longitude"]),
                }
                pass_json["semantics"]["departureAirportTimeZone"] = departure["tz_name"]
                pass_json["semantics"]["departureCityName"] = departure["city"]
                pass_json["locations"].append({
                    "latitude": float(departure["latitude"]),
                    "longitude": float(departure["longitude"]),
                    "relevantText": departure["name"]
                })
                maps_link = urllib.parse.urlencode({
                    "q": departure["name"],
                    "ll": f"{departure['latitude']},{departure['longitude']}"
                })
                pass_fields["backFields"].append({
                    "key": "to-station-back",
                    "label": "to-station-label",
                    "value": departure["name"],
                    "attributedValue": f"<a href=\"https://maps.apple.com/?{maps_link}\">{departure['name']}</a>",
                })

            arrival = templatetags.iata.get_iata_airport_code(leg.to_code)
            pass_fields["primaryFields"].append({
                "key": "to-station",
                "label": "to-station-label",
                "value": leg.to_code,
            })
            pass_json["semantics"]["destinationAirportCode"] = leg.to_code
            if arrival:
                pass_json["semantics"]["destinationAirportName"] = arrival["name"]
                pass_json["semantics"]["destinationLocation"] = {
                    "latitude": float(arrival["latitude"]),
                    "longitude": float(arrival["longitude"]),
                }
                pass_json["semantics"]["destinationAirportTimeZone"] = arrival["tz_name"]
                pass_json["semantics"]["destinationCityName"] = arrival["city"]
                pass_json["locations"].append({
                    "latitude": float(arrival["latitude"]),
                    "longitude": float(arrival["longitude"]),
                    "relevantText": arrival["name"]
                })
                maps_link = urllib.parse.urlencode({
                    "q": departure["name"],
                    "ll": f"{arrival['latitude']},{arrival['longitude']}"
                })
                pass_fields["backFields"].append({
                    "key": "from-station-back",
                    "label": "from-station-label",
                    "value": arrival["name"],
                    "attributedValue": f"<a href=\"https://maps.apple.com/?{maps_link}\">{arrival['name']}</a>",
                })

            if c := leg.compartment.type_key:
                pass_fields["secondaryFields"].append({
                    "key": "compartment",
                    "label": "class-code-label",
                    "value": f"compartment-code-{c}-label",
                })
                pass_json["semantics"]["ticketFareClass"] = f"compartment-code-{c}-label"

            if leg.leg_conditional and leg.leg_conditional.marketing_carrier:
                airline = templatetags.iata.get_iata_airline_code(leg.operating_carrier)
            else:
                airline = templatetags.iata.get_iata_airline_code(leg.operating_carrier)

            if airline:
                pass_json["logoText"] = airline["Airline_Name"]
                r = niquests.get(f"https://airlabs.co/img/airline/m/{airline['IATA_Code']}.png")
                if r.ok:
                    pkp.add_file("logo.png", r.content)
                    have_logo = True

                if airline["IATA_Code"] in IATA_BG:
                    pass_json["backgroundColor"] = IATA_BG[airline["IATA_Code"]]
                if airline["IATA_Code"] in IATA_FG:
                    pass_json["foregroundColor"] = IATA_FG[airline["IATA_Code"]]
                if airline["IATA_Code"] in IATA_FG_SECONDARY:
                    pass_json["labelColor"] = IATA_FG_SECONDARY[airline["IATA_Code"]]

            if leg.leg_conditional:
                if leg.leg_conditional.fast_track is not None:
                    pass_fields["secondaryFields"].append({
                        "key": "fast-track",
                        "label": "fast-track-label",
                        "value": "yes-label" if leg.leg_conditional.fast_track else "no-label"
                    })
                    if leg.leg_conditional.fast_track:
                        pass_json["semantics"]["airlinePassengerCapabilities"].append(
                            "PKAirlinePassengerCapabilityPriorityBoarding")

                if leg.leg_conditional.selectee == iata.conditional.Selectee.TSAPre:
                    pass_json["semantics"]["departureAirportSecurityPrograms"].append(
                        "PKAirportSecurityProgramTSAPreCheck")
                    add_pkp_img(pkp, "pass/tsa-pre.png", "footer.png")

                if leg.leg_conditional.frequent_flyer_number:
                    if airline := templatetags.iata.get_iata_airline_code(
                            leg.leg_conditional.frequent_flyer_designator):
                        pass_fields["backFields"].append({
                            "key": "frequent-flyer",
                            "label": "frequent-flyer-label",
                            "value": f"{airline['Airline_Name']}\n{leg.leg_conditional.frequent_flyer_number}",
                        })
                    else:
                        pass_fields["backFields"].append({
                            "key": "frequent-flyer",
                            "label": "frequent-flyer-label",
                            "value": f"{leg.leg_conditional.frequent_flyer_designator} {leg.leg_conditional.frequent_flyer_number}",
                        })

    elif isinstance(ticket_instance, models.BahnBonusInstance):
        ticket_data: ticket.BahnBonusCode = ticket_instance.as_ticket

        if ticket_data.data.valid_from:
            validity_start = datetime.datetime.combine(ticket_data.data.valid_from, datetime.time.min)
        else:
            validity_start = None
        if ticket_data.data.valid_until:
            validity_end = datetime.datetime.combine(ticket_data.data.valid_until, datetime.time.max)
        else:
            validity_end = None

        pass_json["barcodes"] = [{
            "format": "PKBarcodeFormatAztec",
            "message": bytes(ticket_instance.barcode_data).decode("iso-8859-1"),
            "messageEncoding": "iso-8859-1",
        }]
        pass_json["backgroundColor"] = "#3c414b"
        pass_json["foregroundColor"] = "#ffffff"
        pass_json["labelColor"] = "#ffffff"

        add_pkp_img(pkp, "pass/logo-db.png", "logo.png")
        have_logo = True

        product = ticket_data.data.product()
        if product:
            pass_fields["primaryFields"].append({
                "key": "product",
                "value": product.name
            })

            if product.strip_image:
                add_pkp_img(pkp, product.strip_image, "strip.png")

            if product.strip_colour:
                pass_json["stripColor"] = product.strip_colour

        if ticket_data.data.product_id == bahnbonus.products.BAHNBONUS:
            pass_type = "storeCard"

            pass_fields["auxiliaryFields"].append({
                "key": "card-id",
                "label": "card-id-label",
                "value": ticket_data.data.barcode_id
            })
        else:
            pass_type = "coupon"

        if validity_start:
            pass_fields["secondaryFields"].append({
                "key": "validity-start",
                "label": "validity-start-label",
                "dateStyle": "PKDateStyleMedium",
                "timeStyle": "PKDateStyleNone",
                "value": validity_start.strftime("%Y-%m-%dT%H:%M:%SZ"),
            })

        if validity_end:
            pass_json["expirationDate"] = validity_end.strftime("%Y-%m-%dT%H:%M:%SZ")
            pass_fields["secondaryFields"].append({
                "key": "validity-end",
                "label": "validity-end-label",
                "dateStyle": "PKDateStyleMedium",
                "timeStyle": "PKDateStyleNone",
                "value": validity_end.strftime("%Y-%m-%dT%H:%M:%SZ"),
            })

    elif isinstance(ticket_instance, models.SNCBTrainPlusInstance):
        ticket_data: ticket.SNCBTrainPlus = ticket_instance.as_ticket

        pass_json["barcodes"] = [{
            "format": "PKBarcodeFormatQR",
            "message": bytes(ticket_instance.barcode_data).decode("utf-8"),
            "messageEncoding": "utf-8",
        }]
        pass_json["backgroundColor"] = "#000000"
        pass_json["foregroundColor"] = "#ffffff"
        pass_json["labelColor"] = "#8c8c8c"

        add_pkp_img(pkp, "pass/logo-sncb.png", "logo.png")
        have_logo = True

        pass_fields["primaryFields"].append({
            "key": "card-id",
            "label": "card-id-label",
            "value": ticket_data.data.card_number,
        })
        pass_fields["headerFields"].append({
            "key": "product",
            "value": "Train+"
        })
        pass_type = "storeCard"

    elif isinstance(ticket_instance, models.ADIFInstance):
        ticket_data: ticket.ADIFTicket = ticket_instance.as_ticket

        if ticket_data.issuer_id in RICS_LOGO:
            add_pkp_img(pkp, RICS_LOGO[ticket_data.issuer_id], "logo.png")
            have_logo = True
        if ticket_data.issuer_id in RICS_BG:
            pass_json["backgroundColor"] = RICS_BG[ticket_data.issuer_id]
        if ticket_data.issuer_id in RICS_DARK_BG:
            is_dark_bg = True
        if ticket_data.issuer_id in RICS_FG:
            pass_json["foregroundColor"] = RICS_FG[ticket_data.issuer_id]
        if ticket_data.issuer_id in RICS_FG_SECONDARY:
            pass_json["labelColor"] = RICS_FG_SECONDARY[ticket_data.issuer_id]

        pass_type = "boardingPass"

        from_station = templatetags.rics.get_station(ticket_data.data.origin_station, "uic")
        to_station = templatetags.rics.get_station(ticket_data.data.destination_station, "uic")

        tz = pytz.timezone('Europe/Madrid')
        travel_datetime = tz.localize(ticket_data.data.departure)
        pass_json["relevantDates"].append({
            "date": travel_datetime.isoformat()
        })

        if from_station:
            pass_json["locations"].append({
                "latitude": float(from_station["latitude"]),
                "longitude": float(from_station["longitude"]),
                "relevantText": from_station["name"]
            })
        if to_station:
            pass_json["locations"].append({
                "latitude": float(to_station["latitude"]),
                "longitude": float(to_station["longitude"]),
                "relevantText": to_station["name"]
            })

        pass_fields = {
            "transitType": "PKTransitTypeTrain",
            "headerFields": [{
                "key": "train-number",
                "label": "train-number-label",
                "value": str(ticket_data.data.train_number),
                "semantics": {
                    "vehicleNumber": str(ticket_data.data.train_number)
                }
            }],
            "primaryFields": [{
                "key": "from-station",
                "label": "from-station-label",
                "value": from_station["name"] if from_station else "",
                "semantics": {
                    "departureLocation": {
                        "latitude": float(from_station["latitude"]),
                        "longitude": float(from_station["longitude"]),
                    },
                    "departureStationName": from_station["name"]
                } if from_station else {},
            }, {
                "key": "to-station",
                "label": "to-station-label",
                "value": to_station["name"] if to_station else "",
                "semantics": {
                    "destinationLocation": {
                        "latitude": float(to_station["latitude"]),
                        "longitude": float(to_station["longitude"]),
                    },
                    "destinationStationName": to_station["name"]
                } if to_station else {},
            }],
            "auxiliaryFields": [],
            "secondaryFields": [{
                "key": "departure-time",
                "label": "departure-time-label",
                "dateStyle": "PKDateStyleMedium",
                "timeStyle": "PKDateStyleShort",
                "value": travel_datetime.isoformat(),
                "ignoresTimeZone": True,
            }],
            "backFields": [{
                "key": "ticket-id",
                "label": "ticket-id-label",
                "value": str(ticket_data.ticket_ref),
            }]
        }
        pass_json["barcodes"] = [{
            "format": "PKBarcodeFormatAztec",
            "message": bytes(ticket_instance.barcode_data).decode("iso-8859-1"),
            "messageEncoding": "iso-8859-1",
        }]

        if from_station:
            from_station_maps_link = urllib.parse.urlencode({
                "q": from_station["name"],
                "ll": f"{from_station['latitude']},{from_station['longitude']}"
            })
            pass_fields["backFields"].append({
                "key": "from-station-back",
                "label": "from-station-label",
                "value": from_station["name"],
                "attributedValue": f"<a href=\"https://maps.apple.com/?{from_station_maps_link}\">{from_station['name']}</a>",
            })
        if to_station:
            to_station_maps_link = urllib.parse.urlencode({
                "q": to_station["name"],
                "ll": f"{to_station['latitude']},{to_station['longitude']}"
            })
            pass_fields["backFields"].append({
                "key": "to-station-back",
                "label": "to-station-label",
                "value": to_station["name"],
                "attributedValue": f"<a href=\"https://maps.apple.com/?{to_station_maps_link}\">{to_station['name']}</a>",
            })

        if ticket_data.data.carriage:
            pass_fields["auxiliaryFields"].append({
                "key": f"reservation-coach",
                "label": "coach-number-label",
                "value": str(ticket_data.data.carriage)
            })
        if ticket_data.data.seat:
            pass_fields["auxiliaryFields"].append({
                "key": f"reservation-seat",
                "label": "seat-number-label",
                "value": ticket_data.data.seat
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
                return_pass_fields["backFields"].append({
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
                return_pass_fields["backFields"].append({
                    "key": "distributor",
                    "label": "issuing-organisation-label",
                    "value": distributor["full_name"],
                })

    elif isinstance(ticket_instance, models.MavInstance):
        ticket_data: ticket.MavTicket = ticket_instance.as_ticket

        pass_json["barcodes"] = [{
            "format": "PKBarcodeFormatAztec",
            "message": bytes(ticket_instance.barcode_data).decode("iso-8859-1"),
            "messageEncoding": "iso-8859-1",
            "altText": ticket_data.envelope.ticket_id
        }]
        pass_fields["backFields"].append({
            "key": "ticket-id",
            "label": "ticket-id-label",
            "value": ticket_data.envelope.ticket_id,
            "semantics": {
                "confirmationNumber": ticket_data.envelope.ticket_id
            }
        })
        pass_fields["backFields"].append({
            "key": "issued-date",
            "label": "issued-at-label",
            "dateStyle": "PKDateStyleFull",
            "timeStyle": "PKDateStyleNone",
            "value": ticket_data.data.issued_at.isoformat(),
            "ignoresTimeZone": True
        })
        pass_fields["backFields"].append({
            "key": "price",
            "label": "price-label",
            "value": ticket_data.data.price_str
        })

        if ticket_data.data.trip_data:
            pass_type = "boardingPass"
            pass_fields["transitType"] = "PKTransitTypeTrain"

            pass_json["relevantDates"].append({
                "startDate": ticket_data.data.trip_data.valid_from.isoformat(),
                "endDate": ticket_data.data.trip_data.valid_until.isoformat()
            })
            pass_json["expirationDate"] = ticket_data.data.trip_data.valid_until.isoformat()

            from_station = templatetags.rics.get_station(ticket_data.data.trip_data.origin, "mav")
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

            to_station = templatetags.rics.get_station(ticket_data.data.trip_data.destination, "mav")
            if to_station:
                pass_fields["primaryFields"].append({
                    "key": "to-station",
                    "label": "to-station-label",
                    "value": to_station["name"],
                    "semantics": {
                        "departureLocation": {
                            "latitude": float(to_station["latitude"]),
                            "longitude": float(to_station["longitude"]),
                        },
                        "departureStationName": to_station["name"]
                    }
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

            pass_fields["auxiliaryFields"].append({
                "key": "class-code",
                "label": "class-code-label",
                "value": f"class-code-{ticket_data.data.trip_data.travel_class}-label",
            })

            pass_fields["secondaryFields"].append({
                "key": "validity-start",
                "label": "validity-start-label",
                "dateStyle": "PKDateStyleMedium",
                "timeStyle": "PKDateStyleShort",
                "value": ticket_data.data.trip_data.valid_from.isoformat(),
                "ignoresTimeZone": True
            })
            pass_fields["secondaryFields"].append({
                "key": "validity-end",
                "label": "validity-end-label",
                "dateStyle": "PKDateStyleMedium",
                "timeStyle": "PKDateStyleShort",
                "value": ticket_data.data.trip_data.valid_until.isoformat(),
                "ignoresTimeZone": True
            })

        if ticket_data.data.passenger_data:
            if ticket_data.data.passenger_data.name:
                pass_fields["auxiliaryFields"].append({
                    "key": "passenger",
                    "label": "passenger-label",
                    "value": ticket_data.data.passenger_data.name,
                })
            if ticket_data.data.passenger_data.dob:
                pass_fields["auxiliaryFields"].append({
                    "key": "date-of-birth",
                    "label": "date-of-birth-label",
                    "dateStyle": "PKDateStyleMedium",
                    "value": f"{ticket_data.data.passenger_data.dob.isoformat()}T00:00:00Z",
                    "ignoresTimeZone": True
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
                return_pass_fields["backFields"].append({
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
                return_pass_fields["backFields"].append({
                    "key": "distributor",
                    "label": "issuing-organisation-label",
                    "value": distributor["full_name"],
                })

        if ticket_data.envelope.issuer_rics in RICS_LOGO:
            add_pkp_img(pkp, RICS_LOGO[ticket_data.envelope.issuer_rics], "logo.png")
            have_logo = True
        if ticket_data.envelope.issuer_rics in RICS_BG:
            pass_json["backgroundColor"] = RICS_BG[ticket_data.envelope.issuer_rics]
        if ticket_data.envelope.issuer_rics in RICS_FG:
            pass_json["foregroundColor"] = RICS_FG[ticket_data.envelope.issuer_rics]
        if ticket_data.envelope.issuer_rics in RICS_FG_SECONDARY:
            pass_json["labelColor"] = RICS_FG_SECONDARY[ticket_data.envelope.issuer_rics]

    ticket_url = reverse('ticket', kwargs={"pk": ticket_obj.pk})
    pass_fields["backFields"].append({
        "key": "view-link",
        "label": "more-info-label",
        "value": "",
        "attributedValue": f"<a href=\"{idna_encode_url(settings.EXTERNAL_URL_BASE)}{ticket_url}\">View ticket</a>",
    })
    return_pass_fields["backFields"].append({
        "key": "view-link",
        "label": "more-info-label",
        "value": "",
        "attributedValue": f"<a href=\"{idna_encode_url(settings.EXTERNAL_URL_BASE)}{ticket_url}\">View ticket</a>",
    })

    pass_json[pass_type] = pass_fields
    if return_pass_json:
        return_pass_json[return_pass_type] = return_pass_fields

    for lang, strings in PASS_STRINGS.items():
        pkp.add_file(f"{lang}.lproj/pass.strings", strings.encode("utf-8"))

    if not have_logo:
        add_pkp_img(pkp, "pass/logo.png", "logo.png")

    if not have_icon:
        add_pkp_img(pkp, "pass/icon.png", "icon.png")

    if ticket_obj.ticket_type == models.Ticket.TYPE_DEUTCHLANDTICKET:
        if is_dark_bg:
            add_pkp_img(pkp, "pass/logo-dt-white.png", "thumbnail.png")
        else:
            add_pkp_img(pkp, "pass/logo-dt.png", "thumbnail.png")

    public_id = ticket_obj.public_id()
    if has_return:
        pass_json["serialNumber"] = f'{pass_json["serialNumber"]}:outbound'
        return_pass_json["serialNumber"] = f'{return_pass_json["serialNumber"]}:return'

        if part == "outbound":
            pkp.add_file("pass.json", json.dumps(pass_json).encode("utf-8"))
            pkp.sign()

            return f"{public_id}_outbound.pkpass", [
                (f"{public_id}_outbound.pkpass", pkp.get_buffer())
            ]
        elif part == "return":
            pkp.add_file("pass.json", json.dumps(return_pass_json).encode("utf-8"))
            pkp.sign()

            return f"{public_id}_return.pkpass", [
                (f"{public_id}_return.pkpass", pkp.get_buffer())
            ]
        else:
            pkp.add_file("pass.json", json.dumps(pass_json).encode("utf-8"))
            pkp.sign()
            buf1 = pkp.get_buffer()

            pkp.add_file("pass.json", json.dumps(return_pass_json).encode("utf-8"))
            pkp.sign()
            buf2 = pkp.get_buffer()

            return f"{public_id}.pkpasses", [
                (f"{public_id}_outbound.pkpass", buf1),
                (f"{public_id}_return.pkpass", buf2)
            ]
    else:
        pkp.add_file("pass.json", json.dumps(pass_json).encode("utf-8"))
        pkp.sign()

        return f"{public_id}.pkpass", [
            (f"{public_id}.pkpass", pkp.get_buffer())
        ]


def make_pkpass(ticket_obj: "models.Ticket", part: typing.Optional[str] = None):
    file_name, files = make_pkpass_file(ticket_obj, part)

    if len(files) == 1:
        response = HttpResponse()
        response['Content-Type'] = "application/vnd.apple.pkpass"
        response['Content-Disposition'] = f'attachment; filename="{file_name}"'
        response.write(files[0][1])
        return response
    else:
        multi_pass = pkpass.MultiPKPass()
        for _, file_contents in files:
            multi_pass.add_pkpass(file_contents)
        response = HttpResponse()
        response['Content-Type'] = "application/vnd.apple.pkpasses"
        response['Content-Disposition'] = f'attachment; filename="{file_name}"'
        response.write(multi_pass.get_buffer())
        return response


PASS_STRINGS = {
    "en": """
"product-label" = "Product";
"ticket-id-label" = "Ticket ID";
"card-id-label" = "Card ID";
"info-label" = "Info";
"more-info-label" = "More info";
"product-organisation-label" = "Product Organisation";
"issuing-organisation-label" = "Issuing Organisation";
"ticketing-organisation-label" = "Ticketing Organisation";
"validity-on-label" = "Valid on";
"validity-start-label" = "Valid from";
"validity-end-label" = "Valid until";
"validity-end-change" = "Validity extended to %@";
"issued-at-label" = "Issued at";
"passenger-label" = "Passenger";
"class-code-label" = "Class";
"class-code-first-label" = "1st";
"class-code-1-label" = "1st";
"class-code-second-label" = "2nd";
"class-code-2-label" = "2nd";
"reduction-card-label" = "Discount card";
"date-of-birth-label" = "Date of birth";
"month-of-birth-label" = "Birth month";
"year-of-birth-label" = "Birth year";
"country-of-residence-label" = "Country of residence";
"passport-number-label" = "Passport number";
"from-station-label" = "From";
"to-station-label" = "To";
"station-label" = "Station";
"product-id-label" = "Ticket type";
"valid-region-label" = "Validity";
"return-included-label" = "Return included";
"yes-label" = "Yes";
"no-label" = "No";
"railcard-number" = "Railcard number";
"departure-date-label" = "Departure date";
"departure-time-label" = "Departure";
"arrival-time-label" = "Arrival";
"train-number-label" = "Train";
"flight-number-label" = "Flight";
"coach-number-label" = "Coach";
"seat-number-label" = "Seat";
"price-label" = "Price";
"price-level-label" = "Price level";
"product-validity-outward-date-label" = "Outward validity - date";
"product-validity-outward-time-label" = "Outward validity - time";
"product-validity-return-date-label" = "Return validity - date";
"product-validity-return-time-label" = "Return validity - time";
"product-break-of-journey-outward-label" = "Break of journey - outward";
"product-break-of-journey-return-label" = "Break of journey - return";
"product-conditions-label" = "Conditions";
"product-changes-label" = "Changes to travel plans";
"product-refunds-label" = "Refunds";
"carrier-label" = "Carrier";
"reference-num-label" = "Reference number";
"other-data-label" = "Other data";
"travel-via-all-label" = "Travel must be made via all of";
"travel-via-any-label" = "Travel must be made via any of";
"travel-via-excl-label" = "Travel must not be made via";
"travel-inc-toc-label" = "Travel must include a service operated by";
"travel-excl-toc-label" = "Travel must not include a service operated by";
"route-label" = "Route";
"group-ticket-label" = "Group ticket";
"group-leader-label" = "Group leader";
"article-number-label" = "Article number";
"telephone-number-label" = "Telephone";
"frequent-flyer-label" = "Frequent Flyer Number";
"compartment-code-supersonic-label" = "Supersonic";
"compartment-code-first-class-label" = "First Class";
"compartment-code-premium-business-label" = "Premium Business";
"compartment-code-business-label" = "Business";
"compartment-code-premium-economy-label" = "Premium Economy";
"compartment-code-economy-label" = "Economy";
"fast-track-label" = "Fast-track";
"delay-conf-label" = "Delay";
"train-delay-label" = "Delayed";
"train-cancelled-label" = "Cancelled";
"original-time-label" = "Original time";
"for-ticket-openTicket-label" = "For ticket no.";
"for-ticket-pass-label" = "For pass no.";
"for-ticket-reservation-label" = "For reservation no.";
""",
    "cy": """
"product-label" = "Cynnyrch";
"ticket-id-label" = "Rhif Tocyn";
"card-id-label" = "Rhif Cerdyn";
"info-label" = "Gwybodaeth";
"more-info-label" = "Gwybodaeth ychwanegol";
"product-organisation-label" = "Cynnyrchgwni";
"issuing-organisation-label" = "Cwni dyddori";
"ticketing-organisation-label" = "Cwni tocynnu";
"validity-or-label" = "Dilys ar y";
"validity-start-label" = "Dilys o'r";
"validity-end-label" = "Dilys tan";
"validity-end-change" = "Wedi ystynnedu tan %@";
"issued-at-label" = "Dyddorwyd am";
"passenger-label" = "Teithiwr";
"class-code-label" = "Dosbarth";
"class-code-first-label" = "1af";
"class-code-1-label" = "1af";
"class-code-second-label" = "2ail";
"class-code-2-label" = "2ail";
"reduction-card-label" = "Cerdyn gostwng";
"date-of-birth-label" = "Dyddiad genni";
"month-of-birth-label" = "Mîs genni";
"year-of-birth-label" = "Blwyddyn genni";
"country-of-residence-label" = "Gwlad breswylio";
"passport-number-label" = "Rhif Pasbort";
"from-station-label" = "O";
"to-station-label" = "I";
"station-label" = "Gorsaf";
"product-id-label" = "Math tocyn";
"valid-region-label" = "Dilysrwydd";
"return-included-label" = "Gyda dychweldaith?";
"yes-label" = "Iê";
"no-label" = "Na";
"railcard-number" = "Rhif Railcard";
"departure-date-label" = "Dyddiad teithio";
"departure-time-label" = "Ymadawiad";
"arrival-time-label" = "Cyrrhaeddiad";
"train-number-label" = "Trên";
"flight-number-label" = "Awyren";
"coach-number-label" = "Cerbyd";
"seat-number-label" = "Sedd";
"price-label" = "Pris";
"price-level-label" = "Lefel pris";
"product-validity-outward-date-label" = "Dilysrwydd alladaith - dyddiad";
"product-validity-outward-time-label" = "Dilysrwydd alladaith - amser";
"product-validity-return-date-label" = "Dilysrwydd dychweldaith - dyddiad";
"product-validity-return-time-label" = "Dilysrwydd dychweldaith - amser";
"product-break-of-journey-outward-label" = "Toriad taith - allandaith";
"product-break-of-journey-return-label" = "Toriad taith - dychweldaith";
"product-conditions-label" = "Telerau";
"product-changes-label" = "Newidiadau i gynlluniau teithio";
"product-refunds-label" = "Ad-daliadau";
"carrier-label" = "Cludwr";
"reference-num-label" = "Cyfeirnod";
"other-data-label" = "Data arall";
"travel-via-all-label" = "Rhaid teithio trwy pob un o";
"travel-via-any-label" = "Rhaid teithio twry un o";
"travel-via-excl-label" = "Ni allai teithio trwy";
"travel-inc-toc-label" = "Rhaid i'r daith cynnwys gwasanaeth";
"travel-excl-toc-label" = "Ni all y daith cynnwys gwasanaeth";
"route-label" = "Llwybr";
"group-ticket-label" = "Tocyn grwp";
"group-leader-label" = "Prifdeithiwr";
"article-number-label" = "Rhif articl";
"telephone-number-label" = "Rhif ffôn";
"frequent-flyer-label" = "Rhif Cwsmer Aml";
"compartment-code-supersonic-label" = "Uwchsonig";
"compartment-code-first-class-label" = "Dosbarth Cyntaf";
"compartment-code-premium-business-label" = "Busnes Premiwm";
"compartment-code-business-label" = "Busnes";
"compartment-code-premium-economy-label" = "Economi Premiwm";
"compartment-code-economy-label" = "Economi";
"fast-track-label" = "Llywbr carlam";
"delay-conf-label" = "Oediad";
"train-delay-label" = "Wedi'i ohirio gan";
"train-cancelled-label" = "Wedi'i ganslo";
"original-time-label" = "Amer gwreiddiol";
"for-ticket-openTicket-label" = "Ar gyfer tocyn rhif";
"for-ticket-pass-label" = "Ar gyfer pás rhif";
"for-ticket-reservation-label" = "Ar gyfer archeb rhif";
""",
    "de": """
"product-label" = "Produkt";
"ticket-id-label" = "Ticket-ID";
"card-id-label" = "Kartennummer";
"info-label" = "Info";
"more-info-label" = "Mehr Infos";
"product-organisation-label" = "Produktorganisation";
"issuing-organisation-label" = "Ausstellende Organisation";
"ticketing-organisation-label" = "Ticketverkaufsorganisation";
"validity-on-label" = "Gültig am";
"validity-start-label" = "Gültig vom";
"validity-end-label" = "Gültig bis";
"validity-end-change" = "Verlängert bis %@";
"issued-at-label" = "Ausgestellt am";
"passenger-label" = "Fahrgast";
"class-code-label" = "Klasse";
"class-code-first-label" = "1.";
"class-code-1-label" = "1.";
"class-code-second-label" = "2.";
"class-code-2-label" = "2.";
"reduction-card-label" = "Ermäßigung";
"date-of-birth-label" = "Geburtsdatum";
"month-of-birth-label" = "Geburtsmonat";
"year-of-birth-label" = "Geburtsjahr";
"country-of-residence-label" = "Wohnsitzland";
"passport-number-label" = "Passnummer";
"from-station-label" = "Von";
"to-station-label" = "Nach";
"station-label" = "Bahnhof";
"product-id-label" = "Tickettyp";
"valid-region-label" = "Gültigkeit";
"return-included-label" = "Rückfahrt inklusive";
"yes-label" = "Ja";
"no-label" = "Nein";
"railcard-number" = "Railcard-Nummer";
"departure-date-label" = "Datum";
"departure-time-label" = "Abfahrt";
"arrival-time-label" = "Ankunft";
"train-number-label" = "Zug nr.";
"flight-number-label" = "Flug nr.";
"coach-number-label" = "Waggon";
"seat-number-label" = "Sitzpl.";
"price-label" = "Preis";
"price-level-label" = "Preisstufe";
"product-validity-outward-date-label" = "Hinfahrt Gültigkeit - Datum";
"product-validity-outward-time-label" = "Hinfahrt Gültigkeit - Zeit";
"product-validity-return-date-label" = "Ruckfahrt Gültigkeit - Datum";
"product-validity-return-time-label" = "Ruckfahrt Gültigkeit - Zeit";
"product-break-of-journey-outward-label" = "Reisepause - Hinfahrt";
"product-break-of-journey-return-label" = "Reisepause - Ruckfahrt";
"product-conditions-label" = "Bedingungen";
"product-changes-label" = "Änderungen";
"product-refunds-label" = "Erstattungen";
"carrier-label" = "Verkehrsbetrieb";
"reference-num-label" = "Referenznummer";
"other-data-label" = "Andere Daten";
"travel-via-all-label" = "Gültige Fahrt nur über alle";
"travel-via-any-label" = "Gültige Fahrt nur über mindestens eine";
"travel-via-excl-label" = "Gültige Fahrt nich über";
"travel-inc-toc-label" = "Gültige Fahrt nur mit einen Dienst von";
"travel-excl-toc-label" = "Gültige Fahrt nicht mit Dienste von";
"route-label" = "Route";
"group-ticket-label" = "Gruppenkarte";
"group-leader-label" = "Hauptfahrgast";
"article-number-label" = "Artikel-nr.";
"telephone-number-label" = "Telefonnr.";
"frequent-flyer-label" = "Vielfliegerprogramm-nr.";
"compartment-code-supersonic-label" = "Überschall";
"compartment-code-first-class-label" = "Erste-Klasse";
"compartment-code-premium-business-label" = "Premium-Business";
"compartment-code-business-label" = "Business";
"compartment-code-premium-economy-label" = "Premium-Economy";
"compartment-code-economy-label" = "Economy";
"fast-track-label" = "Schelleinsteig";
"delay-conf-label" = "Verspätung";
"train-delay-label" = "Verspätet";
"train-cancelled-label" = "Fällt aus";
"original-time-label" = "Ursprüngliche Zeit";
"for-ticket-openTicket-label" = "Für Fahrkarte Nr.";
"for-ticket-pass-label" = "Für Pass Nr.";
"for-ticket-reservation-label" = "Für Resevierung Nr.";
""",
    "nl": """
"product-label" = "Product";
"ticket-id-label" = "Ticket-ID";
"card-id-label" = "Kaart-ID";
"info-label" = "Info";
"more-info-label" = "Meer info";
"product-organisation-label" = "Productorganisatie";
"issuing-organisation-label" = "Uitgevende Organisatie";
"ticketing-organisation-label" = "Ticketorganisatie";
"validity-on-label" = "Geldig op";
"validity-start-label" = "Geldig van";
"validity-end-label" = "Geldig tot";
"validity-end-change" = "Geldigheid verlengd tot %@";
"issued-at-label" = "Uitgegeven om";
"passenger-label" = "Reiziger";
"class-code-label" = "Klasse";
"class-code-first-label" = "1e";
"class-code-1-label" = "1e";
"class-code-second-label" = "2e";
"class-code-2-label" = "2e";
"reduction-card-label" = "Kortingskaart";
"date-of-birth-label" = "Geboortedatum";
"month-of-birth-label" = "Geboortemaand";
"year-of-birth-label" = "Geboortejaar";
"country-of-residence-label" = "Woonachtig in land";
"passport-number-label" = "Paspoortnummer";
"from-station-label" = "Van";
"to-station-label" = "Naar";
"station-label" = "Station";
"product-id-label" = "Tickettype";
"valid-region-label" = "Geldigheid";
"return-included-label" = "Inclusief retour";
"yes-label" = "Ja";
"no-label" = "Nee";
"railcard-number" = "Railcard-nummer";
"departure-date-label" = "Vertrekdatum";
"departure-time-label" = "Vertrek";
"arrival-time-label" = "Aankomst";
"train-number-label" = "Trein";
"flight-number-label" = "Vlucht";
"coach-number-label" = "Rijtuig";
"seat-number-label" = "Stoel";
"price-label" = "Prijs";
"price-level-label" = "Prijsniveau";
"product-validity-outward-date-label" = "Geldigheid heenrit - datum";
"product-validity-outward-time-label" = "Geldigheid heenrit - tijd";
"product-validity-return-date-label" = "Geldigheid terugrit - datum";
"product-validity-return-time-label" = "Geldigheid terugrit - tijd";
"product-break-of-journey-outward-label" = "Ritonderbreking - heenrit";
"product-break-of-journey-return-label" = "Ritonderbreking - terugrit";
"product-conditions-label" = "Voorwaarden";
"product-changes-label" = "Wijzigingen aan het reisplan";
"product-refunds-label" = "Restitutie";
"carrier-label" = "Vervoerder";
"reference-num-label" = "Referentienummer";
"other-data-label" = "Andere gegevens";
"travel-via-all-label" = "Reis moet gemaakt worden via alle van";
"travel-via-any-label" = "Reis moet gemaakt worden via tenminste één van";
"travel-via-excl-label" = "Reis mag niet gemaakt worden via";
"travel-inc-toc-label" = "Reis moet vervoer bevatten uitgevoerd door";
"travel-excl-toc-label" = "Reis mag geen vervoer bevatten uitgevoerd door";
"route-label" = "Route";
"group-ticket-label" = "Groepsticket";
"group-leader-label" = "Groepsleider";
"article-number-label" = "Artikelnummer";
"frequent-flyer-label" = "Frequentflyerprogramma";
"""
}

RICS_LOGO = {
    10: "pass/logo-vr.png",
    60: "pass/logo-ir.png",
    80: "pass/logo-db.png",
    83: "pass/logo-trenitalia.png",
    1071: "pass/logo-renfe.png",
    1073: "pass/logo-hellenic.png",
    1080: "pass/logo-db.png",
    1088: "pass/logo-sncb.png",
    1084: "pass/logo-ns.png",
    1154: "pass/logo-cd.png",
    1155: "pass/logo-mav.png",
    1156: "pass/logo-zssk.png",
    1174: "pass/logo-sj.png",
    1178: "pass/logo-hzpp.png",
    1179: "pass/logo-szpp.png",
    1180: "pass/logo-db.png",
    1181: "pass/logo-oebb.png",
    1182: "pass/logo-cfl.png",
    1183: "pass/logo-trenitalia.png",
    1184: "pass/logo-nsi.png",
    1186: "pass/logo-dsb.png",
    1187: "pass/logo-sncf.png",
    1188: "pass/logo-sncb.png",
    1251: "pass/logo-pkp-ic.png",
    3018: "pass/logo-thalys.png",
    3076: "pass/logo-transdev.png",
    3153: "pass/logo-wl.png",
    3229: "pass/logo-rnv.png",
    3243: "pass/logo-ustra.png",
    3252: "pass/logo-kd.png",
    3268: "pass/logo-graz.png",
    3306: "pass/logo-vor.png",
    3316: "pass/logo-avg.png",
    3429: "pass/logo-salzburg.png",
    3453: "pass/logo-mvb.png",
    3497: "pass/logo-rvv.png",
    3509: "pass/logo-ret.png",
    3591: "pass/logo-akn.png",
    3602: "pass/logo-vvv.png",
    3606: "pass/logo-qbuzz.png",
    3626: "pass/logo-gvb.png",
    3634: "pass/logo-dtvg.png",
    3697: "pass/logo-cendis.png",
    3703: "pass/logo-grand-est.png",
    3780: "pass/logo-ouigo.png",
    3906: "pass/logo-mdv.png",
    5008: "pass/logo-vrn.png",
    5173: "pass/logo-nasa.png",
    5177: "pass/logo-fribus.png",
    5188: "pass/logo-es.png",
    5197: "pass/logo-avv.png",
    5211: "pass/logo-vetter.png",
    5217: "pass/logo-bremerhaven.png",
    5245: "pass/logo-wvv.png",
    8999: "pass/logo-connexxion.png",
    9901: "pass/logo-interrail.png",
}

RICS_BG = {
    1084: "#ffc917",
    1154: "#00a0dc",
    1174: "#05aa3b",
    1184: "#1b1a68",
    3018: "#af1634",
    3453: "#018e4a",
    3497: "#14181a",
    3602: "#004d66",
    3626: "#0863b5",
    3634: "#343a40",
    3697: "#0f173e",
    3703: "#191998",
    3780: "#00aedf",
    5188: "#40002c",
    8999: "#0b828e",
}

RICS_DARK_BG = (3634,)

RICS_FG = {
    10: "rgb(51, 51, 51)",
    1084: "rgb(7, 7, 33)",
    1073: "#27509b",
    1154: "#ffffff",
    1174: "#ffffff",
    1184: "rgb(223, 223, 200)",
    1187: "#282828",
    3018: "#ffffff",
    3429: "#444444",
    3453: "#ffffff",
    3497: "#ffffff",
    3602: "#ffffff",
    3606: "rgb(0, 70, 84)",
    3626: "#ffffff",
    3634: "#ffffff",
    3697: "#ffffff",
    3703: "#ffffff",
    3780: "#ffffff",
    5188: "#ffffff",
    8999: "#ffffff",
}

RICS_FG_SECONDARY = {
    10: "rgb(0, 161, 73)",
    60: "rgb(61, 165, 53)",
    83: "rgb(0, 106, 106)",
    1071: "#830065",
    1073: "#bd0d2e",
    1084: "rgb(32, 32, 55)",
    1154: "#ffffff",
    1174: "#ffffff",
    1181: "#e33c3e",
    1183: "rgb(0, 106, 106)",
    1184: "#fec917",
    1187: "#6e1b6e",
    3018: "#ffffff",
    3153: "rgb(227, 0, 21)",
    3243: "rgb(120, 180, 30)",
    3268: "rgb(67, 165, 0)",
    3306: "rgb(128, 204, 40)",
    3429: "#007da3",
    3453: "#ffffff",
    3497: "#96c87d",
    3602: "#69AB98",
    3606: "rgb(247, 147, 48)",
    3626: "#575757",
    3634: "#fbc02d",
    3697: "rgb(110, 193, 228)",
    3703: "#ffc94f",
    3780: "#e3006a",
    5188: "rgb(255, 54, 0)",
    5245: "rgb(211, 2, 64)",
    8999: "rgb(226, 1, 112)",
}

UIC_NAME_LOGO = {
    "BMK": "pass/logo-kt.png",
}

VDV_ORG_ID_LOGO = {
    35: "pass/logo-hvv.png",
    36: "pass/logo-rmv.png",
    49: "pass/logo-swk.png",
    57: "pass/logo-dsw.png",
    70: "pass/logo-vrr.png",
    77: "pass/logo-wt.png",
    102: "pass/logo-vrs.png",
    103: "pass/logo-swb.png",
    6003: "pass/logo-kvsh.png",
    6055: "pass/logo-mdv.png",
    6060: "pass/logo-vvo.png",
    6072: "pass/logo-avv2.png",
    6073: "pass/logo-aseag.png",
    6074: "pass/logo-vgn.png",
    6096: "pass/logo-vmt.png",
    6100: "pass/logo-vbb.png",
    6150: "pass/logo-hnv.png",
    6212: "pass/logo-vrs.png",
    6234: "pass/logo-vvs.png",
    6292: "pass/logo-mvg.png",
    6310: "pass/logo-svv.png",
    6377: "pass/logo-db.png",
    6395: "pass/logo-ssw.png",
    6425: "pass/logo-db.png",
    6441: "pass/logo-kvg.png",
    6478: "pass/logo-bw.png",
    6491: "pass/logo-rnv.png",
    6496: "pass/logo-naldo.png",
    6517: "pass/logo-rnn.png",
    6538: "pass/logo-vrm.png",
    6613: "pass/logo-arriva.png",
    6665: "pass/logo-rsag.png",
    6671: "pass/logo-ewse.png",
    6691: "pass/logo-vgi.png",
    6861: "pass/logo-nst.png",
}

VDV_ORG_ID_BG = {
    6517: "rgb(0, 79, 159)"
}

VDV_ORG_ID_FG = {
    6072: "rgb(67, 67, 67)",
    6517: "rgb(255, 255, 255)",
    6538: "#2743a0",
}

VDV_ORG_ID_FG_SECONDARY = {
    49: "rgb(226, 1, 26)",
    6072: "rgb(181, 9, 127)",
    6310: "rgb(12, 156, 58)",
    6517: "rgb(255, 204, 2)",
    6691: "rgb(0, 56, 116)",
    6538: "#00aaa8",
}

SWISSPASS_LOGO = {
    11: "pass/logo-sbb.png",
    490: "pass/logo-zvv.png",
    801: "pass/logo-postbus.png",
}

SWISSPASS_BG = {
    11: "rgb(246, 246, 246)",
    490: "rgb(255, 255, 255)",
    801: "rgb(255, 204, 0)",
}

SWISSPASS_FG = {
    11: "rgb(33, 33, 33)",
    490: "rgb(99, 99, 99)",
    801: "rgb(0, 0, 0)",
}

SWISSPASS_FG_SECONDARY = {
    11: "rgb(255, 0, 0)",
    490: "rgb(4, 121, 204)",
    801: "rgb(255, 0, 0)",
}

RSP_ORG_LOGO = {
    "TT": "pass/logo-tt.png",
    "CS": "pass/logo-cs.png",
    "RE": "pass/logo-re.png",
    "R5": "pass/logo-re.png",
    "ZS": "pass/logo-sf.png",
}

RSP_ORG_BG = {
    "ZS": "#040309",
}

RSP_ORG_FG = {
    "ZS": "#ffffff",
}

RSP_ORG_FG_SECONDARY = {
    "ZS": "#00d973",
}

IATA_BG = {
    "FR": "#173c8a"
}

IATA_FG = {
    "FR": "#ffffff"
}

IATA_FG_SECONDARY = {
    "FR": "#f1ca33"
}

BC_STRIP_IMG = {
    "BahnCard 50 Herbst Aktion 2024 (1. Klasse)": "bahncard/AKTIONSBAHNCARD501KLASSE.png",
    "BahnCard 50 Herbst Aktion 2024 (2. Klasse)": "bahncard/AKTIONSBAHNCARD502KLASSE.png",
    "My BahnCard 50 Aktion Frühjahr 2025 (1. Kl.)": "bahncard/AKTIONSMYBAHNCARD501KLASSE.png",
    "My BahnCard 50 Aktion Frühjahr 2025 (2. Kl.)": "bahncard/AKTIONSMYBAHNCARD502KLASSE.png",
    "BahnCard 25 Aktion Frühjahr 2025 (2. Kl.)": "bahncard/AKTIONSBAHNCARD252KLASSE.png",
    "BahnCard 25 Aktion Frühjahr 2025 (1. Kl.)": "bahncard/AKTIONSBAHNCARD251KLASSE.png",
    "My BahnCard 25 (1. Klasse)": "bahncard/MYBAHNCARD251KLASSE.png",
    "My BahnCard 25 (2. Klasse)": "bahncard/MYBAHNCARD252KLASSE.png",
    "My BahnCard 50 (1. Klasse)": "bahncard/MYBAHNCARD501KLASSE.png",
    "My BahnCard 50 (2. Klasse)": "bahncard/MYBAHNCARD502KLASSE.png",
    "BahnCard 25 (1. Klasse)": "bahncard/BAHNCARD251KLASSE.png",
    "BahnCard 25 (2. Klasse)": "bahncard/BAHNCARD252KLASSE.png",
    "BahnCard 50 (1. Klasse)": "bahncard/BAHNCARD501KLASSE.png",
    "BahnCard 50 (2. Klasse)": "bahncard/BAHNCARD502KLASSE.png",
    "Jugend BahnCard 25": "bahncard/JUGENDBAHNCARD25BAHN.png",
    "Senioren BahnCard 25 (1. Klasse)": "bahncard/BAHNCARD251KLASSE.png",
    "Senioren BahnCard 25 (2. Klasse)": "bahncard/BAHNCARD252KLASSE.png",
}

KNOWN_VALID_SIGNATURE_RICS = [
    80,  # Deutsche Bahn AG
    1080,  # Deutsche Bahn AG
    1181,  # ÖBB Personenverkehr AG
    2080,  # DB Regio AG Business Unit Bus
    3076,  # Transdev GmbH
    3135,  # HanseCom GmbH
    3243,  # üstra Hannoversche Verkehrsbetriebe AG
    3316,  # AVG Augsburger Verkehrsgesellschaft mbH
    3348,  # Verkehrsverbund Bremen/Niedersachsen GmbH (VBN)
    3497,  # Regensburger Verkehrsverbund GmbH
    3565,  # SWT Stadtwerke Trier Verkehrs-GmbH
    3591,  # AKN Eisenbahn AG
    3634,  # Deutschlandtarifverbund GmbH
    3703,  # Region Grand Est
    3823,  # KMG - Klagenfurt Mobil GmbH
    3834,  # Verkehrsverbund Region Braunschweig GmbH
    3841,  # Hanseatische Eisenbahn GmbH
    3906,  # Mitteldeutscher Verkehrsverbund GmbH
    3940,  # Aachener Verkehrsverbund GmbH
    3966,  # FlixTrain GmbH
    5008,  # Verkehrsverbund Rhein-Neckar GmbH
    5046,  # Verkehrsverbund Steiermark GmbH
    5062,  # Dessauer Verkehrs GmbH
    5143,  # AMCON Software GmbH
    5167,  # Stadtwerke Viernheim GmbH
    5170,  # Aktiv Bus Flensburg GmbH
    5172,  # Koblenzer Verkehrsbetriebe GmbH
    5173,  # Nahverkehrsservice Sachsen-Anhalt GmbH
    5197,  # Augsburger Verkehrs- und Tarifverbund GmbH
    5203,  # Verwaltungsgesellschaft des OPNV Sommerda mbH
    5211,  # Vetter GmbH Omnibus- und Mietwagenbetrieb
    5217,  # Verkehrsgesellschaft Bremerhaven AG
    5218,  # Verkehr und Wasser GmbH
    5245,  # Würzburger Straßenbahn GmbH
    5294,  # bConn GmbH
    5379,  # Mentz GmbH
    5398,  # Verkehrsverbund Kärnten GmbH
    5651,  # Autobus Oberbayern GmbH
    5671,  # Edzards Reisen GmbH & Co. KG
    5679,  # Husmann Reisen GmbH
    5682,  # EBR Busreisen GmbH
    5684,  # Taxi Lehner
    5685,  # Ludwig Meier GmbH
    9002,  # DB Vertrieb GmbH
    9901,  # Eurail B.V.
    9902,  # Eurail Group G.I.E. management
]

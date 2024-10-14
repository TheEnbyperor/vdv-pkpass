import json
import logging
import datetime
import pytz
from django.utils import timezone
from django.http import HttpResponse
from django.conf import settings
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import condition
from main import models, views

logger = logging.Logger(__name__)


def check_pass_auth(f):
    def wrapper(request, *, pass_type_id, serial_number, **kwargs):
        if "Authorization" not in request.headers:
            return HttpResponse(status=401)

        auth_header = request.headers["Authorization"]
        if not auth_header.startswith("ApplePass "):
            return HttpResponse(status=401)

        auth_token = auth_header[10:]

        if pass_type_id != settings.PKPASS_CONF["pass_type"]:
            return HttpResponse(status=404)

        ticket_obj: models.Ticket = models.Ticket.objects.get(id=serial_number)
        if not ticket_obj:
            return HttpResponse(status=404)

        if ticket_obj.pkpass_authentication_token != auth_token:
            return HttpResponse(status=401)

        return f(request, ticket_obj=ticket_obj, **kwargs)

    return wrapper


def ticket_updated_date(_request, pass_type_id, serial_number):
    if pass_type_id != settings.PKPASS_CONF["pass_type"]:
        return
    ticket_obj: models.Ticket = models.Ticket.objects.get(id=serial_number)
    if not ticket_obj:
        return
    return ticket_obj.last_updated

@csrf_exempt
def pass_status(request, device_id, pass_type_id):
    device_obj = models.AppleDevice.objects.get(device_id=device_id)
    if not device_obj:
        return HttpResponse(status=204)

    if pass_type_id != settings.PKPASS_CONF["pass_type"]:
        return HttpResponse(status=204)

    last_updated = request.GET.get("passesUpdatedSince")
    if last_updated:
        try:
            last_updated = datetime.datetime.fromtimestamp(int(last_updated), pytz.utc)
        except ValueError:
            return HttpResponse(status=400)

    regs = device_obj.registrations.all()
    if last_updated:
        regs = regs.filter(ticket__last_updated__gt=last_updated)

    tickets = [reg.ticket for reg in regs]
    new_last_updated = max(
        (ticket.last_updated for ticket in tickets),
        default=datetime.datetime.now(pytz.utc)
    )

    return HttpResponse(status=200, content_type="application/json", content=json.dumps({
        "lastUpdated": str(int(new_last_updated.astimezone(pytz.utc).timestamp()) + 1),
        "serialNumbers": [str(ticket.id) for ticket in tickets]
    }))


@csrf_exempt
@check_pass_auth
def registration(request, device_id, ticket_obj):
    if request.method == "POST":
        if request.content_type != "application/json":
            return HttpResponse(status=415)

        try:
            data = json.loads(request.body)
        except ValueError:
            return HttpResponse(status=400)

        if "pushToken" not in data or not data["pushToken"] or not isinstance(data["pushToken"], str):
            return HttpResponse(status=400)

        device_obj, _ = models.AppleDevice.objects.update_or_create(
            device_id=device_id,
            defaults={
                "push_token": data["pushToken"],
            }
        )
        models.AppleRegistration.objects.update_or_create(
            device=device_obj,
            ticket=ticket_obj
        )

        return HttpResponse(status=200)
    elif request.method == "DELETE":
        device_obj = models.AppleDevice.objects.get(device_id=device_id)
        if not device_obj:
            return HttpResponse(status=200)

        models.AppleRegistration.objects.filter(
            device=device_obj,
            ticket=ticket_obj
        ).delete()

        if device_obj.registrations.count() == 0:
            device_obj.delete()

        return HttpResponse(status=200)
    else:
        return HttpResponse(status=405)


@csrf_exempt
@condition(last_modified_func=ticket_updated_date)
@check_pass_auth
def pass_document(_, ticket_obj):
    return views.passes.make_pkpass(ticket_obj)


@csrf_exempt
def log(request):
    if request.method != "POST":
        return HttpResponse(status=405)

    if request.content_type != "application/json":
        return HttpResponse(status=415)

    try:
        data = json.loads(request.body)
    except ValueError:
        return HttpResponse(status=400)

    if "logs" not in data:
        return HttpResponse(status=400)

    for log_entry in data["logs"]:
        logger.warning(log_entry)

    return HttpResponse(status=200)
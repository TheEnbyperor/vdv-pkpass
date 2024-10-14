import json
import base64
import binascii
from django.http import HttpResponse
from django.views.decorators.csrf import csrf_exempt
from .. import ticket


@csrf_exempt
def upload_aztec(request):
    if request.method != 'POST':
        return HttpResponse(status=405)

    if request.content_type != 'application/json':
        return HttpResponse(status=400)

    try:
        data = json.loads(request.body)
    except ValueError:
        return HttpResponse(status=400)

    if "barcode_data" not in data:
        return HttpResponse(status=400)

    try:
        barcode_data = base64.b64decode(data["barcode_data"])
    except binascii.Error:
        return HttpResponse(status=400)

    try:
        ticket_obj = ticket.update_from_subscription_barcode(barcode_data, account=None)
    except ticket.TicketError as e:
        return HttpResponse(json.dumps({
            "title": e.title,
            "message": e.message,
            "exception": e.exception,
        }), status=422, content_type="application/json")

    return HttpResponse(json.dumps({
        "ticket_id": ticket_obj.id,
        "access_token": ticket_obj.pkpass_authentication_token
    }), content_type="application/json")

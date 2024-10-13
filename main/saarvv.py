import niquests
import secrets
import hashlib
import json
import datetime
import hmac
import urllib3.util
import base64
import logging
from Crypto.Cipher import AES
from django.core.files.storage import storages
from . import models, aztec, ticket, apn

logger = logging.getLogger(__name__)

VERSION = "3.10.17"
COMMIT_HASH = "h183ab339"
EOS_INSTANCE = None


def get_device_id():
    device_id = secrets.token_hex(16)
    return hashlib.sha1(device_id.encode()).hexdigest()


def get_eos_instance():
    global EOS_INSTANCE

    if EOS_INSTANCE:
        return EOS_INSTANCE

    with storages["staticfiles"].open("saarvv/parsed_licenses.lcs", "rb") as f:
        encrypted_license = f.read()

    encryption_key = hashlib.sha512(f"saarvv{COMMIT_HASH}".encode("utf-8")).hexdigest()[:16].encode("utf-8")
    cipher = AES.new(encryption_key, AES.MODE_CBC, iv=bytes(16))
    decrypted_license = cipher.decrypt(encrypted_license)
    eos_license = json.loads(decrypted_license)
    eos_instance = eos_license["instances"][0]
    EOS_INSTANCE = eos_instance
    return eos_instance


def sign_request(request: niquests.PreparedRequest, device_id: str) -> niquests.PreparedRequest:
    eos = get_eos_instance()

    request.headers["User-Agent"] = (f"{eos['clientName']}/{VERSION}/{eos['mobileServiceAPIVersion']}/"
                                     f"{eos['identifier']} (VDV PKPass q@magicalcodewit.ch)")
    request.headers["X-Eos-Date"] = datetime.datetime.now(datetime.UTC).strftime('%a, %d %b %Y %H:%M:%S GMT')
    request.headers["Device-Identifier"] = device_id

    mac_key = eos["applicationKey"].encode("utf-8")
    mac1 = hmac.new(mac_key, request.body, "sha512").hexdigest()
    scheme, auth, host, port, path, query, fragment = urllib3.util.parse_url(request.url)
    default_port = 443 if scheme == "https" else 80
    mac2_msg = f"{mac1}|{host}|{port or default_port}|{path}"
    if query:
        mac2_msg += f"?{query}"
    x_eos_date = request.headers.get("X-Eos-Date", "")
    content_type = request.headers.get("Content-Type", "")
    authorization = request.headers.get("Authorization", "")
    x_eos_anonymous = request.headers.get("X-TICKeos-Anonymous", "")
    x_eos_sso = request.headers.get("X-TICKeos-SSO", "")
    user_agent = request.headers.get("User-Agent", "")
    mac2_msg += f"|{x_eos_date}|{content_type}|{authorization}|{x_eos_anonymous}|{x_eos_sso}|{user_agent}"
    mac2 = hmac.new(mac_key, mac2_msg.encode("utf-8"), "sha512").hexdigest()
    request.headers["X-Api-Signature"] = mac2

    return request


def update_all():
    for account in models.Account.objects.filter(saarvv_device_id__isnull=False):
        update_saarvv_tickets(account)

        for t in account.saarvv_tickets.all():
            apn.notify_ticket_if_renewed(t)


def update_saarvv_tickets(account: models.Account):
    if not account.saarvv_token or not account.saarvv_device_id:
        return

    r = niquests.post(f"https://saarvv.tickeos.de/index.php/mobileService/sync", json={}, hooks={
        "pre_request": [lambda req: sign_request(req, account.saarvv_device_id)],
    }, headers={
        "Authorization": account.saarvv_token
    })
    if r.status_code != 200:
        logger.error(f"Failed to update SaarVV {account.saarvv_device_id}: {r.text}")
    data = r.json()

    r = niquests.post(f"https://saarvv.tickeos.de/index.php/mobileService/ticket", json={
        "details": True,
        "tickets": data["tickets"],
        "provide_aztec_content": False,
        "parameters": False,
    }, hooks={
        "pre_request": [lambda req: sign_request(req, account.saarvv_device_id)],
    }, headers={
        "Authorization": account.saarvv_token
    })
    if r.status_code != 200:
        logger.error(f"Failed to update SaarVV {account.saarvv_device_id}: {r.text}")
    data = r.json()
    for t in data["tickets"].values():
        template = json.loads(t["template"])
        barcode_img = base64.b64decode(template["content"]["images"]["aztec_barcode"])
        barcode_data = aztec.decode(barcode_img)

        try:
            ticket_obj = ticket.update_from_subscription_barcode(barcode_data, account=account)
            ticket_obj.saarvv_account = account
            ticket_obj.save()
        except ticket.TicketError as e:
            logger.error("Error decoding barcode ticket: %s", e)
            continue

    logger.info(f"Successfully updated SaarVV {account.saarvv_device_id}")
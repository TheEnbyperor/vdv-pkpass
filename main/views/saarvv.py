import hashlib
import niquests
import datetime
import json
import hmac
import urllib3.util
import secrets
import typing
from Crypto.Cipher import AES
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.files.storage import storages
from django.shortcuts import render, redirect
from .. import forms

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


def login(username: str, password: str) -> typing.Optional[typing.Tuple[str, str]]:
    device_id = get_device_id()
    r = niquests.post(f"https://saarvv.tickeos.de/index.php/mobileService/login", json={
        "credentials": {
            "password": password,
            "username": username,
        }
    }, hooks={
        "pre_request": [lambda r: sign_request(r, device_id)],
    })
    if r.status_code != 200:
        return None
    auth_data = r.json()
    access_token_data = next(filter(lambda t: t["name"] == "tickeos_access_token", auth_data["authorization_types"]), None)
    if not access_token_data:
        return None
    access_token = "{} {}".format(access_token_data["header"]["type"], access_token_data["header"]["value"])
    return access_token, device_id


@login_required
def saarvv_login(request):
    if request.method == "POST":
        form = forms.SaarVVLoginForm(request.POST)
        if form.is_valid():
            token = login(form.cleaned_data["username"], form.cleaned_data["password"])
            if not token:
                messages.error(request, "Login failed")
            else:
                messages.success(request, "Login successful")
                token, device_id = token
                request.user.account.saarvv_token = token
                request.user.account.saarvv_device_id = device_id
                request.user.account.save()
                return redirect("account")
    else:
        form = forms.SaarVVLoginForm()

    return render(request, "main/account/saarvv_login.html", {
        "form": form,
    })


@login_required
def saarvv_logout(request):
    request.user.account.saarvv_token = None
    request.user.account.saarvv_device_id = None
    request.user.account.save()
    messages.add_message(request, messages.SUCCESS, "Successfully logged out")
    return redirect("account")
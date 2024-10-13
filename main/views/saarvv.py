import niquests
import typing
import datetime
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect
from .. import forms, saarvv


def login(username: str, password: str) -> typing.Optional[typing.Tuple[str, str]]:
    device_id = saarvv.get_device_id()
    r = niquests.post(f"https://saarvv.tickeos.de/index.php/mobileService/login", json={
        "credentials": {
            "password": password,
            "username": username,
        }
    }, hooks={
        "pre_request": [lambda req: saarvv.sign_request(req, device_id)],
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
                saarvv.update_saarvv_tickets(request.user.account)
                return redirect("saarvv_account")
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


def map_customer_field(f):
    if f["content"]["type"] == "choice":
        return next(filter(lambda c: c["key"] == f["content"]["default"], f["content"]["choices"]))["value"]
    elif f["content"]["type"] == "text":
        return f["content"].get("default")
    elif f["content"]["type"] == "date":
        return datetime.date.fromisoformat(f["content"]["default"])


@login_required
def saarvv_account(request):
    if not request.user.account.saarvv_token:
        return redirect("saarvv_login")

    r = niquests.post(f"https://saarvv.tickeos.de/index.php/mobileService/customer/fields", json={}, hooks={
        "pre_request": [lambda req: saarvv.sign_request(req, request.user.account.saarvv_device_id)],
    }, headers={
        "Authorization": request.user.account.saarvv_token
    })
    r.raise_for_status()
    data = r.json()

    fields = {f["name"]: map_customer_field(f) for b in data["layout_blocks"] for f in b["fields"]}

    return render(request, "main/account/saarvv.html", {
        "fields": fields,
        "tickets": request.user.account.saarvv_tickets,
    })
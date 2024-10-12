import secrets
import base64
import hashlib
import urllib.parse
import binascii
import niquests
import jwt
import datetime
from django.utils import timezone
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect

DB_ISSUER = "https://accounts.bahn.de/auth/realms/db"
DB_AUTH_URL = "https://accounts.bahn.de/auth/realms/db/protocol/openid-connect/auth"
DB_TOKEN_URL = "https://accounts.bahn.de/auth/realms/db/protocol/openid-connect/token"
DB_CERTS_URL = "https://accounts.bahn.de/auth/realms/db/protocol/openid-connect/certs"
DB_CLIENT_ID = "kf_mobile"
DB_REDIRECT_URI = "dbnav://dbnavigator.bahn.de/auth"

def get_db_token(user):
    now = timezone.now()
    if user.account.db_token and user.account.db_token_expires_at and \
            user.account.db_token_expires_at > now + datetime.timedelta(minutes=5):
        return user.account.db_token
    elif user.account.db_refresh_token and user.account.db_refresh_token_expires_at and user.account.db_refresh_token_expires_at > now:
        r = niquests.post(DB_TOKEN_URL, data={
            "grant_type": "refresh_token",
            "client_id": DB_CLIENT_ID,
            "refresh_token": user.account.db_refresh_token,
        }, headers={
            "User-Agent": "VDV PKPass q@magicalcodewit.ch"
        })
        if r.status_code != 200:
            user.account.db_refresh_token = None
            user.account.db_refresh_token_expires_at = None
            user.account.save()
            return None

        data = r.json()
        user.account.db_token = data["access_token"]
        user.account.db_token_expires_at = now + datetime.timedelta(seconds=data["expires_in"])
        user.account.db_refresh_token = data["refresh_token"]
        user.account.db_refresh_token_expires_at = now + datetime.timedelta(seconds=data["refresh_expires_in"])
        user.account.save()
        return user.account.db_token
    else:
        return None

@login_required
def db_login(request):
    return render(request, "main/account/db_login.html", {})


@login_required
def db_logout(request):
    request.user.account.db_token = None
    request.user.account.db_token_expires_at = None
    request.user.account.db_refresh_token = None
    request.user.account.db_refresh_token_expires_at = None
    request.user.account.save()
    messages.add_message(request, messages.SUCCESS, "Successfully logged out")
    return redirect("account")


@login_required
def db_login_start(request):
    code_verifier = secrets.token_hex(32)
    session_state = secrets.token_hex(32)
    request.session["db_code_verifier"] = code_verifier
    request.session["db_session_state"] = session_state
    code_challenge = base64.urlsafe_b64encode(
        hashlib.sha256(code_verifier.encode()).digest()
    ).decode().replace("=", "")
    params = urllib.parse.urlencode({
        "client_id": DB_CLIENT_ID,
        "redirect_uri": DB_REDIRECT_URI,
        "response_type": "code",
        "code_challenge_method": "S256",
        "code_challenge": code_challenge,
        "state": session_state,
        "scope": "offline_access",
    })
    return redirect(f"{DB_AUTH_URL}?{params}")


@login_required
def db_login_callback(request):
    if "url" not in request.GET or \
            "db_code_verifier" not in request.session or \
            "db_session_state" not in request.session:
        return db_login_start(request)

    try:
        url = base64.urlsafe_b64decode(request.GET["url"]).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError):
        messages.error(request, "Invalid login response")
        return redirect('db_login')

    response_url = urllib.parse.urlparse(url)

    if response_url.scheme != "dbnav":
        messages.error(request, "Invalid login response")
        return redirect('db_login')

    if response_url.netloc != "dbnavigator.bahn.de":
        messages.error(request, "Invalid login response")
        return redirect('db_login')

    if response_url.path != "/auth":
        messages.error(request, "Invalid login response")
        return redirect('db_login')

    response_params = urllib.parse.parse_qs(response_url.query)

    if "error" in response_params:
        messages.error(request, f"Login error - {response_params.get('error_description', '')}")
        return redirect('db_login')

    code = response_params.get("code", [""])[0]
    db_session_state = response_params.get("session_state", [""])[0]
    code_verifier = request.session.pop("db_code_verifier")
    session_state = request.session.pop("db_session_state")

    if response_params.get("state", [""])[0] != session_state:
        messages.error(request, "Invalid login response")
        return redirect('db_login')

    r = niquests.post(DB_TOKEN_URL, data={
        "grant_type": "authorization_code",
        "client_id": DB_CLIENT_ID,
        "redirect_uri": DB_REDIRECT_URI,
        "code": code,
        "code_verifier": code_verifier,
    }, headers={
        "User-Agent": "VDV PKPass q@magicalcodewit.ch"
    })
    data = r.json()
    if r.status_code != 200:
        messages.error(request, f"Login failed - {data.get('error_description', '')}")
        return redirect('db_login')

    if data.get("session_state") != db_session_state:
        messages.error(request, "Invalid login response")
        return redirect('db_login')

    auth_token = data.get("access_token", None)
    auth_token_expires_at = timezone.now() + datetime.timedelta(seconds=data.get("expires_in", 0))
    refresh_token = data.get("refresh_token", None)
    refresh_token_expires_at = timezone.now() + datetime.timedelta(seconds=data.get("refresh_expires_in", 0))

    jwks_client = jwt.PyJWKClient(DB_CERTS_URL)
    header = jwt.get_unverified_header(auth_token)
    key = jwks_client.get_signing_key(header["kid"]).key
    try:
        auth_data = jwt.decode(
            auth_token, key, [header["alg"]],
            issuer=DB_ISSUER,
            options={
                "verify_aud": False
            },
            leeway=60,
        )
    except jwt.DecodeError as e:
        messages.error(request, f"Invalid token - {e}")
        return redirect('db_login')

    if auth_data["session_state"] != db_session_state:
        messages.error(request, "Invalid login response")
        return redirect('db_login')


    request.user.account.db_token = auth_token
    request.user.account.db_token_expires_at = auth_token_expires_at
    request.user.account.db_refresh_token = refresh_token
    request.user.account.db_refresh_token_expires_at = refresh_token_expires_at
    request.user.account.db_account_id = auth_data.get("kundenkontoid")
    request.user.account.save()

    return redirect('account')
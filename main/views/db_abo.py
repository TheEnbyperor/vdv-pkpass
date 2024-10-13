import niquests
import datetime
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect, get_object_or_404
from .. import forms, models, db_abo


@login_required
def view_db_abo(request):
    subscriptions = models.DBSubscription.objects.filter(account=request.user.account)

    return render(request, "main/account/db_abo.html", {
        "subscriptions": subscriptions
    })

@login_required
def new_abo(request):
    already_present = False
    if request.method == "POST":
        form = forms.DBAboForm(request.POST)
        if form.is_valid():
            if request.POST.get("action") == "remove":
                r = niquests.post("https://dig-aboprod.noncd.db.de/aboticket/changedevice", json={
                    "abonummer": form.cleaned_data["subscription_number"],
                    "nachname": form.cleaned_data["surname"],
                }, headers={
                    "X-User-Agent": "com.deutschebahn.abo.navigatorV2.modul"
                })
                if r.status_code == 200:
                    messages.success(request, f"Removal request was successful.")
                else:
                    messages.error(request, f"Error occurred with removal request")
            else:
                r = niquests.get("https://dig-aboprod.noncd.db.de/aboticket", params={
                    "abonummer": form.cleaned_data["subscription_number"],
                    "nachname": form.cleaned_data["surname"],
                }, headers={
                    "X-User-Agent": "com.deutschebahn.abo.navigatorV2.modul"
                })
                if r.status_code == 404:
                    messages.add_message(request, messages.ERROR, "Subscription not found")
                elif r.status_code == 401:
                    already_present = True
                elif r.status_code != 200:
                    messages.add_message(request, messages.ERROR, "Unknown error")
                else:
                    data = r.json()
                    device_token = data["deviceToken"]
                    refresh_at = datetime.datetime.fromisoformat(data["refreshDatum"])
                    abo, _ = models.DBSubscription.objects.update_or_create(device_token=device_token, defaults={
                        "refresh_at": refresh_at,
                        "info": data["ticketHuelle"],
                        "account": request.user.account,
                    })
                    db_abo.update_abo_tickets(abo)
                    return redirect("db_abo")
    else:
        form = forms.DBAboForm()

    return render(request, "main/account/db_abo_new.html", {
        "form": form,
        "already_present": already_present,
    })


@login_required
def delete_abo(request, abo_id):
    subscription = get_object_or_404(models.DBSubscription, pk=abo_id)
    if subscription.account != request.user.account:
        return redirect("db_abo")

    if request.method == "POST" and request.POST.get("action") == "remove":
        r = niquests.post("https://dig-aboprod.noncd.db.de/aboticket/logout", json={
            "deviceToken": subscription.device_token,
        }, headers={
            "X-User-Agent": "com.deutschebahn.abo.navigatorV2.modul"
        })
        if r.status_code == 200:
            messages.success(request, "Removal request was successful.")
            subscription.delete()
        else:
            messages.error(request, "Error occurred with removal request")
        return redirect("db_abo")

    return render(request, "main/account/db_abo_delete.html", {})
from django.shortcuts import render
from . import apple_api, passes, account, db, saarvv


def page_not_found(request, exception):
    return render(request, "main/404.html", {
        "exception": exception,
    }, status=404)
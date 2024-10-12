from django.contrib import admin
from . import models


class VDVTicketInstanceInline(admin.StackedInline):
    extra = 0
    model = models.VDVTicketInstance


class UICTicketInstanceInline(admin.StackedInline):
    extra = 0
    model = models.UICTicketInstance


class AppleRegistrationInline(admin.StackedInline):
    extra = 0
    model = models.AppleRegistration
    readonly_fields = [
        "device",
        "ticket",
    ]


@admin.register(models.Ticket)
class TicketAdmin(admin.ModelAdmin):
    readonly_fields = [
        "id",
        "pkpass_authentication_token",
        "last_updated",
    ]
    inlines = [
        VDVTicketInstanceInline,
        UICTicketInstanceInline,
        AppleRegistrationInline,
    ]
    view_on_site = True
    list_display = [
        "id",
        "ticket_type",
        "last_updated"
    ]
    date_hierarchy = "last_updated"
    list_filter = [
        "ticket_type",
    ]
    search_fields = ["id"]


@admin.register(models.AppleDevice)
class AppleDeviceAdmin(admin.ModelAdmin):
    readonly_fields = [
        "device_id",
        "push_token",
    ]
    inlines = [
        AppleRegistrationInline,
    ]


@admin.register(models.Account)
class AccountAdmin(admin.ModelAdmin):
    readonly_fields = [
        "db_token"
    ]


@admin.register(models.DBSubscription)
class DBSubscriptionAdmin(admin.ModelAdmin):
    readonly_fields = [
        "device_token"
    ]
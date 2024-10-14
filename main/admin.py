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


class TicketAccountInline(admin.StackedInline):
    extra = 0
    model = models.Ticket
    fk_name = "account"
    readonly_fields = [
        "pkpass_authentication_token",
        "last_updated",
    ]


class TicketDBSubscriptionInline(admin.StackedInline):
    extra = 0
    model = models.Ticket
    fk_name = "db_subscription"
    readonly_fields = [
        "pkpass_authentication_token",
        "last_updated",
    ]


class DBSubscriptionInline(admin.StackedInline):
    extra = 0
    model = models.DBSubscription
    readonly_fields = [
        "device_token"
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
        "user",
        "db_token",
        "db_token_expires_at",
        "db_refresh_token",
        "db_token_expires_at",
        "saarvv_token",
        "saarvv_device_id",
    ]
    inlines = [
        TicketAccountInline,
        DBSubscriptionInline,
    ]


@admin.register(models.DBSubscription)
class DBSubscriptionAdmin(admin.ModelAdmin):
    readonly_fields = [
        "device_token"
    ]
    inlines = [
        TicketDBSubscriptionInline
    ]
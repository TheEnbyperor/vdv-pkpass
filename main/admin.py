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
    ]
    inlines = [
        VDVTicketInstanceInline,
        UICTicketInstanceInline,
        AppleRegistrationInline,
    ]

@admin.register(models.AppleDevice)
class AppleDeviceAdmin(admin.ModelAdmin):
    readonly_fields = [
        "device_id",
        "push_token",
    ]
    inlines = [
        AppleRegistrationInline,
    ]
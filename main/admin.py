from django.contrib import admin
from . import models

class VDVTicketInstanceInline(admin.StackedInline):
    extra = 0
    model = models.VDVTicketInstance

class UICTicketInstanceInline(admin.StackedInline):
    extra = 0
    model = models.UICTicketInstance

@admin.register(models.Ticket)
class TicketAdmin(admin.ModelAdmin):
    readonly_fields = [
        "id",
        "pkpass_authentication_token",
    ]
    inlines = [
        VDVTicketInstanceInline,
        UICTicketInstanceInline,
    ]

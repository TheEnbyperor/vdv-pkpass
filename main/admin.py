from django.contrib import admin
from . import models

class TicketInstanceInline(admin.StackedInline):
    model = models.TicketInstance

@admin.register(models.Ticket)
class TicketAdmin(admin.ModelAdmin):
    readonly_fields = [
        "pkpass_authentication_token",
    ]
    inlines = [
        TicketInstanceInline,
    ]

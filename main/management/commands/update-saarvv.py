from django.core.management.base import BaseCommand
import main.saarvv


class Command(BaseCommand):
    help = "Update SaarVV tickets"

    def handle(self, *args, **options):
        main.saarvv.update_all()

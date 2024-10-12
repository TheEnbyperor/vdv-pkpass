from django.core.management.base import BaseCommand
import main.db_abo


class Command(BaseCommand):
    help = "Update DB subscription tickets"

    def handle(self, *args, **options):
        main.db_abo.update_all()

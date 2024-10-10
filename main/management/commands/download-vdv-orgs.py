from django.core.management.base import BaseCommand
import django.core.files.storage
import niquests
import json


class Command(BaseCommand):
    help = "Download VDV organisation names"

    def handle(self, *args, **options):
        storage = django.core.files.storage.storages["vdv-certs"]

        r = niquests.get(
            "https://pro.eticket.app/api/organisations/all",
            auth=("eticket-app-pro", "VDV-K3rn4ppl!kat1on"),
            headers={
                "User-Agent": "VDV PKPass Generator (magicalcodewit.ch)",
            },
        )
        r.raise_for_status()
        data = r.json()

        out = {
            "orgs": [],
            "vdv_ids": {},
            "vdv_test_ids": {},
        }

        for org in data["data"]:
            org_pos = len(out["orgs"])
            out["orgs"].append(org)
            if org.get("org_type") == "VDV":
                out["vdv_ids"][org["id"]] = org_pos
                out["vdv_test_ids"][org["test_id"]] = org_pos

        with storage.open("orgs.json", "w") as f:
            json.dump(out, f)

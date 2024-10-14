from django.core.management.base import BaseCommand
import django.core.files.storage
import niquests
import csv
import datetime
import json


class Command(BaseCommand):
    help = "Download VDV certificates from LDAP"

    def handle(self, *args, **options):
        uic_storage = django.core.files.storage.storages["uic-data"]

        rics_codes_r = niquests.get("https://teleref.era.europa.eu/Download_CompanycodesExcel.aspx", headers={
            "User-Agent": "VDV PKPass Generator (magicalcodewit.ch)",
        })
        rics_codes_r.raise_for_status()
        rics_codes = csv.DictReader(rics_codes_r.text.splitlines(), delimiter="\t")

        out = {}
        for row in rics_codes:
            out[int(row["Company Code"])] = {
                "short_name": row["Short Name"],
                "full_name": row["Name"],
                "country": row["Country"],
                "add_date": datetime.datetime.strptime(row["Add Date"], "%d-%m-%y").date().isoformat(),
                "modify_date": datetime.datetime.strptime(row["Mod Date"], "%d-%m-%y").date().isoformat()
                    if row["Mod Date"] else None,
                "start_validity": datetime.datetime.strptime(row["Start Validity"], "%d-%m-%y").date().isoformat(),
                "end_validity": datetime.datetime.strptime(row["End Validity"], "%d-%m-%y").date().isoformat()
                    if row["End Validity"] else None,
                "type": {
                    "freight": row["Freight"] == "x",
                    "passenger": row["Passenger"] == "x",
                    "infrastructure": row["Infrastructure"] == "x",
                    "other": row["Other Company"] == "x",
                },
                "url": row["URL"] if row["URL"] else None,
            }

        with uic_storage.open("rics_codes.json", "w") as f:
            json.dump(out, f)

        stations_r = niquests.get("https://github.com/trainline-eu/stations/raw/refs/heads/master/stations.csv", headers={
            "User-Agent": "VDV PKPass Generator (magicalcodewit.ch)",
        })
        stations_r.raise_for_status()
        stations = csv.DictReader(stations_r.text.splitlines(), delimiter=";")

        out = {
            "stations": [],
            "uic_codes": {},
            "db_ids": {}
        }
        for row in stations:
            station = {}
            for k, v in row.items():
                if v == "t":
                    station[k] = True
                elif v == "f":
                    station[k] = False
                elif v:
                    station[k] = v
            out["stations"].append(station)
            if row["uic"]:
                out["uic_codes"][row["uic"]] = len(out["stations"]) - 1
            if row["db_id"]:
                out["db_ids"][row["db_id"]] = len(out["stations"]) - 1

        with uic_storage.open("stations.json", "w") as f:
            json.dump(out, f)

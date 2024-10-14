import typing
import django.core.files.storage
import json

STATIONS = None

def get_stations_list() -> typing.Dict[str, typing.Any]:
    global STATIONS

    if STATIONS:
        return STATIONS

    uic_storage = django.core.files.storage.storages["uic-data"]
    with uic_storage.open("stations.json", "r") as f:
        STATIONS = json.load(f)

    return STATIONS


def get_station_by_uic(code) -> typing.Optional[dict]:
    if i := get_stations_list()["uic_codes"].get(str(code)):
        return get_stations_list()["stations"][i]


def get_station_by_db(code) -> typing.Optional[dict]:
    if i := get_stations_list()["db_ids"].get(str(code)):
        return get_stations_list()["stations"][i]
import typing
import django.core.files.storage
import json

RICS = None

def get_rics_list() -> typing.Dict[str, dict]:
    global RICS

    if RICS:
        return RICS

    uic_storage = django.core.files.storage.storages["uic-data"]
    with uic_storage.open("rics_codes.json", "r") as f:
        RICS = json.loads(f.read())

    return RICS


def get_rics(code: int) -> typing.Optional[dict]:
    return get_rics_list().get(str(code))
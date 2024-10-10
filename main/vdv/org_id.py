import typing
import django.core.files.storage
import json

ORG_IDS = None

def get_org_ids_list() -> typing.Dict[str, dict]:
    global ORG_IDS

    if ORG_IDS:
        return ORG_IDS

    storage = django.core.files.storage.storages["vdv-certs"]
    with storage.open("orgs.json", "r") as f:
        ORG_IDS = json.loads(f.read())

    return ORG_IDS


def get_org(code: int) -> typing.Tuple[typing.Optional[dict], bool]:
    org_list = get_org_ids_list()
    if org_pos := org_list["vdv_ids"].get(str(code)):
        return org_list["orgs"][org_pos], False
    elif org_pos := org_list["vdv_test_ids"].get(str(code)):
        return org_list["orgs"][org_pos], True
    return None, False
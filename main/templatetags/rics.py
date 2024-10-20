import datetime
import pytz
import typing
import iso3166
from django import template
from .. import uic

register = template.Library()

@register.filter(name="rics")
def get_rics_code(value):
    if not value:
        return None
    return uic.rics.get_rics(int(value))

@register.filter(name="get_station")
def get_station(value, code_type: str):
    if not value:
        return None

    if code_type == "stationUIC":
        return uic.stations.get_station_by_uic(value)
    elif code_type == "db":
        return uic.stations.get_station_by_db(value)

@register.filter(name="iso3166")
def get_country(value):
    return iso3166.countries.get(value).name

@register.filter(name="uic_country")
def get_country_uic(value):
    if value == 10:
        return "FR"
    elif value == 20:
        return "RU"
    elif value == 21:
        return "BY"
    elif value == 22:
        return "UA"
    elif value == 23:
        return "MD"
    elif value == 24:
        return "LT"
    elif value == 25:
        return "LV"
    elif value == 26:
        return "EE"
    elif value == 27:
        return "KZ"
    elif value == 28:
        return "GE"
    elif value == 29:
        return "UZ"
    elif value == 30:
        return "KP"
    elif value == 31:
        return "MN"
    elif value == 32:
        return "VN"
    elif value == 33:
        return "CN"
    elif value == 34:
        return "LA"
    elif value == 383:
        return "XK"
    elif value == 40:
        return "CU"
    elif value == 41:
        return "AL"
    elif value == 42:
        return "JP"
    elif value in (44, 49, 50):
        return "BA"
    elif value == 51:
        return "PL"
    elif value == 52:
        return "BG"
    elif value == 53:
        return "RO"
    elif value == 54:
        return "CZ"
    elif value == 55:
        return "HU"
    elif value == 56:
        return "SK"
    elif value == 57:
        return "AZ"
    elif value == 58:
        return "AM"
    elif value == 59:
        return "KG"
    elif value == 60:
        return "IE"
    elif value == 61:
        return "KR"
    elif value == 62:
        return "ME"
    elif value == 63:
        return "MK"
    elif value == 64:
        return "TJ"
    elif value == 65:
        return "TM"
    elif value == 66:
        return "AF"
    elif value == 70:
        return "GB"
    elif value == 71:
        return "ES"
    elif value == 72:
        return "RS"
    elif value == 73:
        return "GR"
    elif value == 74:
        return "SE"
    elif value == 75:
        return "TR"
    elif value == 76:
        return "NO"
    elif value == 78:
        return "HR"
    elif value == 79:
        return "SI"
    elif value == 80:
        return "DE"
    elif value == 81:
        return "AT"
    elif value == 82:
        return "LU"
    elif value == 83:
        return "IT"
    elif value == 84:
        return "NL"
    elif value == 85:
        return "CH"
    elif value == 86:
        return "DK"
    elif value == 87:
        return "FR"
    elif value == 88:
        return "BE"
    elif value == 89:
        return "TZ"
    elif value == 90:
        return "EG"
    elif value == 91:
        return "TN"
    elif value == 92:
        return "DZ"
    elif value == 93:
        return "MA"
    elif value == 94:
        return "PT"
    elif value == 95:
        return "IL"
    elif value == 96:
        return "IR"
    elif value == 97:
        return "SY"
    elif value == 98:
        return "LB"
    elif value == 99:
        return "IQ"

@register.filter(name="rics_already_newlined")
def ics_already_newlined(value):
    return "\n" in value

@register.filter(name="rics_traveler_dob")
def rics_traveler_dob(value):
    if "yearOfBirth" in value or "monthOfBirth" in value or "dayOfBirthInMonth" in value or "dayOfBirth" in value:
        if "dayOfBirth" in value:
            birthdate = datetime.date(value.get("yearOfBirth", 0), 1, 1)
            birthdate += datetime.timedelta(days=value["dayOfBirth"]-1)
            return birthdate
        else:
            return datetime.date(
                value.get("yearOfBirth", 0),
                value.get("monthOfBirth", 1),
                value.get("dayOfBirthInMonth", 1),
            )

@register.filter(name="rics_unicode")
def rics_unicode(value):
    return value.decode("utf-8", "replace")

@register.filter(name="rics_valid_from")
def rics_valid_from(value, issuing_time: typing.Optional[datetime.datetime]=None):
    if issuing_time:
        issuing_time = datetime.datetime.combine(issuing_time.date(), datetime.time.min)
        issuing_time += datetime.timedelta(days=value["validFromDay"], minutes=value.get("validFromTime", 0))
    else:
        issuing_time = datetime.datetime(value["validFromYear"], 1, 1, 0, 0, 0)
        issuing_time += datetime.timedelta(days=value["validFromDay"]-1, minutes=value.get("validFromTime", 0))
    if "validFromUTCOffset" in value:
        issuing_time += datetime.timedelta(minutes=15 * value["validFromUTCOffset"])
        issuing_time = issuing_time.replace(tzinfo=pytz.utc)
    return issuing_time

@register.filter(name="rics_valid_from_date")
def rics_valid_from_date(value):
    valid_time = datetime.datetime(value["validFromYear"], 1, 1, 0, 0, 0)
    valid_time += datetime.timedelta(days=value["validFromDay"]-1)
    return valid_time

@register.filter(name="rics_valid_until")
def rics_valid_until(value, issuing_time: typing.Optional[datetime.datetime]=None):
    valid_from = rics_valid_from(value, issuing_time)
    if "validUntilYear" in value:
        valid_from = valid_from.replace(
            year=valid_from.year + value["validUntilYear"],
        )
    valid_from += datetime.timedelta(days=value["validUntilDay"], minutes=value.get("validUntilTime", 0))
    if "validUntilUTCOffset" in value:
        valid_from += datetime.timedelta(minutes=15 * value["validUntilUTCOffset"])
        valid_from = valid_from.replace(tzinfo=pytz.utc)
    elif "validFromUTCOffset" in value:
        valid_from += datetime.timedelta(minutes=15 * value["validFromUTCOffset"])
        valid_from = valid_from.replace(tzinfo=pytz.utc)
    return valid_from


@register.filter(name="rics_valid_until_date")
def rics_valid_until_date(value):
    valid_from = rics_valid_from_date(value).replace(day=1, month=1)
    if "validUntilYear" in value:
        valid_from = valid_from.replace(
            year=valid_from.year + value["validUntilYear"],
        )
    valid_from += datetime.timedelta(days=value["validUntilDay"]-1)
    return valid_from

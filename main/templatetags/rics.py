import datetime
import pytz
from django import template
from .. import uic

register = template.Library()

@register.filter(name="rics")
def get_rics_code(value):
    return uic.rics.get_rics(int(value))

@register.filter(name="rics_traveler_dob")
def get_rics_code(value):
    if "yearOfBirth" in value or "monthOfBirth" in value or "dayOfBirthInMonth" in value:
        return datetime.date(
            value.get("yearOfBirth", 0),
            value.get("monthOfBirth", 1),
            value.get("dayOfBirthInMonth", 1),
        )

@register.filter(name="rics_valid_from")
def rics_valid_from(value, issuing_time: datetime.datetime):
    issuing_time = datetime.datetime.combine(issuing_time.date(), datetime.time.min)
    issuing_time += datetime.timedelta(days=value["validFromDay"], minutes=value.get("validFromTime", 0))
    if "validFromUTCOffset" in value:
        issuing_time -= datetime.timedelta(minutes=15 * value["validFromUTCOffset"])
        issuing_time = issuing_time.replace(tzinfo=pytz.utc)
    return issuing_time

@register.filter(name="rics_valid_until")
def rics_valid_until(value, issuing_time: datetime.datetime):
    valid_from = rics_valid_from(value, issuing_time)
    valid_from += datetime.timedelta(days=value["validUntilDay"], minutes=value.get("validUntilTime", 0))
    if "validUntilUTCOffset" in value:
        valid_from -= datetime.timedelta(minutes=15 * value["validUntilUTCOffset"])
        valid_from = valid_from.replace(tzinfo=pytz.utc)
    return valid_from
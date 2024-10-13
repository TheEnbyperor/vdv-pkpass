import datetime
import pytz
import typing
import iso3166
from django import template
from .. import uic

register = template.Library()


@register.filter(name="plai_width")
def plai_width(fields: typing.List) -> int:
    return max([f.column + f.width for f in fields])


@register.filter(name="plai_height")
def plai_height(fields: typing.List) -> int:
    return max([f.line + f.height for f in fields])
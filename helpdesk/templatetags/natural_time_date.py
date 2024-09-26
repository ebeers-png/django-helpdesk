from django import template
from django.contrib.humanize.templatetags.humanize import naturaltime
from django.utils.translation import gettext as _
from django.template import defaultfilters
from datetime import date, datetime

register = template.Library()

@register.filter
def naturaltimedate(value, arg=None):
    """
    For date values that are tomorrow, today or yesterday compared to
    present day return representing string. Otherwise, return a string
    formatted according to settings.DATE_FORMAT.
    """
    tzinfo = getattr(value, 'tzinfo', None)
    original_value = value
    try:
        value = date(value.year, value.month, value.day)
    except AttributeError:
        # Passed value wasn't a date object
        return value
    today = datetime.now(tzinfo).date()
    delta = value - today
    if delta.days == 0:
        return naturaltime(original_value)
    elif delta.days == 1:
        return _('tomorrow')
    elif delta.days == -1:
        return _('yesterday')
    return defaultfilters.date(original_value, arg)
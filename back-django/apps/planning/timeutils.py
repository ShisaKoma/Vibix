from datetime import datetime
from zoneinfo import ZoneInfo

from django.core.exceptions import ValidationError


def local_datetime(raw, zone):
    """Refuse les heures locales inexistantes ou ambiguës plutôt que choisir un pli."""
    try:
        value = datetime.fromisoformat(raw)
        if value.tzinfo is not None or not 1900 <= value.year <= 2100:
            raise ValueError
        first = value.replace(tzinfo=ZoneInfo(zone), fold=0)
        second = value.replace(tzinfo=ZoneInfo(zone), fold=1)
        if first.utcoffset() != second.utcoffset():
            raise ValueError
        return first
    except (ValueError, TypeError) as error:
        raise ValidationError(
            "Choisissez une heure locale valide et non ambiguë dans ce fuseau."
        ) from error

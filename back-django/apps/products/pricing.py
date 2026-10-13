"""Projection tarifaire explicite : aucun taux légal ni conversion de devise."""

from decimal import ROUND_HALF_UP, Decimal

from django.core.exceptions import ValidationError
from django.utils import timezone

CENT = Decimal("0.01")


def money(value):
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def validate_currency(value):
    if len(value) != 3 or not value.isascii() or not value.isalpha() or value != value.upper():
        raise ValidationError("Indiquez un code devise de trois lettres majuscules.")


def amounts(amount, basis, tax_rate):
    """La base est certaine ; l'autre montant reste inconnu sans taxe explicite."""
    if amount is None:
        return {"net": None, "gross": None}
    amount = money(amount)
    factor = Decimal("1") + tax_rate / Decimal("100") if tax_rate is not None else None
    if basis == "HT":
        return {"net": amount, "gross": money(amount * factor) if factor is not None else None}
    return {"net": money(amount / factor) if factor is not None else None, "gross": amount}


def strings(values):
    return {
        key: format(value, ".2f") if value is not None else None for key, value in values.items()
    }


def public_tariffs(variant):
    return variant.tariffs.filter(is_active=True, is_public=True, archived_at__isnull=True)


def tariff_data(tariff, now=None):
    now = now or timezone.now()
    value = tariff.amount if not tariff.on_request else None
    promotion = None
    if (
        value is not None
        and tariff.discount_enabled
        and tariff.discount_starts_at <= now < tariff.discount_ends_at
    ):
        reduction = (
            tariff.discount_value
            if tariff.discount_kind == "amount"
            else value * tariff.discount_value / Decimal("100")
        )
        value = money(value - reduction)
        promotion = {
            "kind": tariff.discount_kind,
            "value": str(tariff.discount_value),
            "starts_at": tariff.discount_starts_at.isoformat(),
            "ends_at": tariff.discount_ends_at.isoformat(),
        }
    return {
        "id": tariff.pk,
        "label": tariff.label,
        "unit": tariff.unit,
        "quantity": str(tariff.quantity),
        "currency": tariff.currency,
        "basis": tariff.basis,
        "on_request": tariff.on_request,
        "amount": format(value, ".2f") if value is not None else None,
        "tax_rate": str(tariff.tax_rate) if tariff.tax_rate is not None else None,
        "amounts": strings(amounts(value, tariff.basis, tariff.tax_rate)),
        "unit_amount": format(money(value / tariff.quantity), ".2f") if value is not None else None,
        "normal_amount": format(tariff.amount, ".2f") if tariff.amount is not None else None,
        "promotion": promotion,
        "terms": tariff.terms,
    }

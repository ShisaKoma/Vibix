"""Services transactionnels de stock. Tous les parcours verrouillent d'abord le site."""

import hashlib
import json
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.audit.models import AuditEvent
from apps.tenancy.models import Site
from apps.tenancy.permissions import has_site_permission

from .stock_models import (
    MAX_INVENTORY_LINES,
    MAX_QUANTITY,
    StockBalance,
    StockInventory,
    StockInventoryLine,
    StockItem,
    StockLocation,
    StockMovement,
    StockOperation,
)


class StockConflict(ValidationError):
    pass


def lock_site(site_id, actor, permission):
    site = Site.objects.select_for_update().exclude(pk=1).get(pk=site_id, is_active=True)
    if not all(
        has_site_permission(actor, site, f"products.{code}")
        for code in (
            "view_product",
            "view_stock",
            permission,
        )
    ):
        raise ValidationError("Vous ne disposez plus des droits requis sur ce site.")
    return site


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def audit(site, actor, instance, action):
    entity = f"products.{instance._meta.model_name}"
    AuditEvent.objects.create(
        site=site,
        actor=actor,
        action=f"{entity}.{action}",
        entity_type=entity,
        entity_id=str(instance.pk),
    )


def validate_quantity(quantity, *, allow_zero=False):
    if not isinstance(quantity, Decimal) or not quantity.is_finite():
        raise ValidationError("Indiquez une quantité décimale finie.")
    if quantity < 0 or (not allow_zero and quantity == 0) or quantity > MAX_QUANTITY:
        raise ValidationError("La quantité est hors des limites autorisées.")
    if quantity != quantity.quantize(Decimal("0.0001")):
        raise ValidationError("La quantité accepte au plus quatre décimales.")


def snapshot(balance):
    variant = balance.item.variant
    return {
        "product": variant.product.title,
        "product_reference": variant.product.reference,
        "variant": variant.title,
        "variant_reference": variant.reference,
        "location": balance.location.name,
        "location_code": balance.location.code,
        "unit": balance.item.unit,
    }


def change_balance(operation, balance, delta):
    before = balance.quantity
    after = before + delta
    validate_quantity(after, allow_zero=True)
    if not delta:
        return
    balance.quantity, balance.version = after, balance.version + 1
    balance.full_clean()
    balance.save(update_fields=["quantity", "version", "updated_at"])
    row = StockMovement(
        operation=operation,
        balance=balance,
        delta=delta,
        before=before,
        after=after,
        snapshot=snapshot(balance),
    )
    row.full_clean()
    row.save()


def balances_for(site):
    return StockBalance.objects.filter(item__site=site).select_related(
        "item__variant__product",
        "location__parent__parent",
    )


@transaction.atomic
def move(
    site_id,
    actor,
    kind,
    item_id,
    source_id,
    destination_id,
    quantity,
    source_version,
    destination_version,
    reason,
    submission_id,
):
    site = lock_site(
        site_id, actor, "transfer_stock" if kind == "transfer" else "record_stock_movement"
    )
    validate_quantity(quantity)
    reason = reason.strip()
    if not reason or len(reason) > 1000:
        raise ValidationError("Renseignez un motif de 1000 caractères maximum.")
    payload = digest(
        {
            "kind": kind,
            "item": item_id,
            "source": source_id,
            "destination": destination_id,
            "quantity": format(quantity, ".4f"),
            "source_version": source_version,
            "destination_version": destination_version,
            "reason": reason,
        }
    )
    previous = StockOperation.objects.filter(site=site, submission_id=submission_id).first()
    if previous:
        if previous.payload_hash != payload:
            raise StockConflict("Cette soumission a déjà servi à une autre opération.")
        return previous
    if kind not in ("entry", "exit", "transfer") or (
        kind == "entry"
        and (source_id or not destination_id)
        or kind == "exit"
        and (destination_id or not source_id)
        or kind == "transfer"
        and (not source_id or not destination_id or source_id == destination_id)
    ):
        raise ValidationError("Choisissez les emplacements requis pour cette opération.")
    StockItem.objects.get(pk=item_id, site=site)
    ids = {pk for pk in (source_id, destination_id) if pk}
    rows = {
        row.location_id: row
        for row in balances_for(site)
        .select_for_update(of=("self",))
        .filter(item_id=item_id, location_id__in=ids)
        .order_by("pk")
    }
    if rows.keys() != ids:
        raise StockConflict("Configurez d'abord ce stock dans les emplacements choisis.")
    for pk, version in ((source_id, source_version), (destination_id, destination_version)):
        if pk and rows[pk].version != version:
            raise StockConflict("Le stock ou son seuil a changé. Rechargez le récapitulatif.")
    if destination_id and not rows[destination_id].location.available:
        raise StockConflict("L'emplacement de destination ou son parent est désactivé.")
    if source_id and rows[source_id].quantity < quantity:
        raise StockConflict(
            "La quantité disponible est insuffisante ; le stock ne peut être négatif."
        )
    if destination_id and rows[destination_id].quantity + quantity > MAX_QUANTITY:
        raise StockConflict("Le stock de destination dépasserait la limite autorisée.")
    operation = StockOperation(
        site=site,
        kind=kind,
        actor=actor,
        reason=reason,
        submission_id=submission_id,
        payload_hash=payload,
    )
    operation.full_clean()
    operation.save()
    if source_id:
        change_balance(operation, rows[source_id], -quantity)
    if destination_id:
        change_balance(operation, rows[destination_id], quantity)
    audit(site, actor, operation, "record")
    return operation


def inventory_rows(inventory):
    rows = list(
        balances_for(inventory.site)
        .select_for_update(of=("self",))
        .filter(location=inventory.location)
        .order_by("pk")[: MAX_INVENTORY_LINES + 1]
    )
    if not rows:
        raise ValidationError(
            "Configurez au moins un stock dans cet emplacement avant l'inventaire."
        )
    if len(rows) > MAX_INVENTORY_LINES:
        raise ValidationError(
            "Un inventaire accepte au plus 500 stocks ; répartissez les emplacements."
        )
    return rows


def capture(inventory, rows):
    existing = {line.balance_id: line for line in inventory.lines.all()}
    # Aucun retrait de ligne : les associations de stock ne se suppriment pas.
    for balance in rows:
        line = existing.get(balance.pk) or StockInventoryLine(inventory=inventory, balance=balance)
        line.expected, line.balance_version, line.counted = balance.quantity, balance.version, None
        line.full_clean()
        line.save()


@transaction.atomic
def open_inventory(site_id, actor, location_id, reason, submission_id):
    site = lock_site(site_id, actor, "manage_stock_inventory")
    previous = StockInventory.objects.filter(site=site, reference=submission_id).first()
    if previous:
        if previous.location_id != location_id or previous.reason != reason:
            raise StockConflict("Cette soumission a déjà servi à un autre inventaire.")
        return previous
    location = StockLocation.objects.select_related("parent__parent").get(
        pk=location_id, site=site, kind="bin"
    )
    if not location.available:
        raise StockConflict("L'emplacement ou son parent est désactivé.")
    if StockInventory.objects.filter(location=location, state="draft").exists():
        raise StockConflict("Un inventaire est déjà ouvert dans cet emplacement.")
    inventory = StockInventory(
        site=site, location=location, reason=reason, reference=submission_id, created_by=actor
    )
    inventory.full_clean()
    inventory.save()
    capture(inventory, inventory_rows(inventory))
    audit(site, actor, inventory, "open")
    return inventory


@transaction.atomic
def count_inventory(site_id, actor, inventory_id, version, counts, expected_ids):
    site = lock_site(site_id, actor, "manage_stock_inventory")
    inventory = StockInventory.objects.select_for_update().get(pk=inventory_id, site=site)
    if inventory.state != "draft" or inventory.version != version:
        raise StockConflict("Cet inventaire a changé ou est clos. Rechargez la page.")
    if len(counts) > 50 or set(counts) != set(expected_ids):
        raise ValidationError("Les lignes soumises ne correspondent pas à cette page.")
    lines = list(inventory.lines.filter(pk__in=expected_ids).select_related("balance__item"))
    if len(lines) != len(counts):
        raise ValidationError("Ligne d'inventaire hors périmètre.")
    for line in lines:
        value = counts[line.pk]
        if value is not None:
            validate_quantity(value, allow_zero=True)
        line.counted = value
        line.full_clean()
        line.save(update_fields=["counted"])
    inventory.version += 1
    inventory.save(update_fields=["version"])
    audit(site, actor, inventory, "count")
    return inventory


@transaction.atomic
def finish_inventory(site_id, actor, inventory_id, action, version):
    permission = "validate_stock_inventory" if action == "validate" else "manage_stock_inventory"
    site = lock_site(site_id, actor, permission)
    inventory = (
        StockInventory.objects.select_for_update()
        .select_related("location")
        .get(pk=inventory_id, site=site)
    )
    if action == "validate" and inventory.state == "validated":
        return inventory
    if action == "cancel" and inventory.state == "cancelled":
        return inventory
    if inventory.state != "draft" or inventory.version != version:
        raise StockConflict("Cet inventaire a changé ou est clos. Rechargez la page.")
    if action == "refresh":
        capture(inventory, inventory_rows(inventory))
    elif action == "cancel":
        inventory.state = "cancelled"
    elif action == "validate":
        balances = {row.pk: row for row in inventory_rows(inventory)}
        lines = list(inventory.lines.select_related("balance__item", "inventory__location"))
        if set(balances) != {line.balance_id for line in lines}:
            raise StockConflict(
                "Les stocks de l'emplacement ont changé. Reprendre l'inventaire est nécessaire."
            )
        for line in lines:
            balance = balances[line.balance_id]
            if line.counted is None:
                raise StockConflict(
                    "Comptez toutes les lignes ; zéro doit être saisi explicitement."
                )
            if balance.version != line.balance_version or balance.quantity != line.expected:
                raise StockConflict(
                    "Des mouvements ou seuils ont changé depuis le début. "
                    "Reprenez l'inventaire et recomptez."
                )
        operation = StockOperation(
            site=site,
            kind="inventory",
            actor=actor,
            reason=inventory.reason,
            inventory=inventory,
            submission_id=inventory.reference,
            payload_hash=digest({"inventory": inventory.pk, "version": version}),
        )
        operation.full_clean()
        operation.save()
        for line in lines:
            change_balance(operation, balances[line.balance_id], line.counted - line.expected)
        audit(site, actor, operation, "record")
        inventory.state = "validated"
    else:
        raise ValidationError("Action d'inventaire inconnue.")
    if inventory.state != "draft":
        inventory.closed_by, inventory.closed_at = actor, timezone.now()
    inventory.version += 1
    inventory.full_clean()
    inventory.save()
    audit(site, actor, inventory, action)
    return inventory

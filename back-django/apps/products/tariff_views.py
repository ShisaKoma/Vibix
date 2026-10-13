from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from apps.cobweb.views import secure_page
from apps.tenancy.permissions import has_site_permission

from .forms import LifecycleForm
from .models import Tariff, Variant
from .tariff_forms import TariffForm, TariffPublicationForm
from .views import (
    audit,
    context,
    current_version,
    product_for,
    save_model_form,
    sections,
    touch_product,
)


def tariff_context(request, site_id, pk, variant_id, action):
    data = context(request, site_id)
    if not all(
        has_site_permission(request.user, data["site"], code)
        for code in ("products.view_tariff", f"products.{action}_tariff")
    ):
        raise Http404
    product = product_for(data["site"], pk)
    variant = get_object_or_404(Variant, product=product, pk=variant_id, archived_at__isnull=True)
    data.update(product=product, variant=variant)
    return data


@secure_page
@login_required
@require_http_methods(["GET", "POST"])
@transaction.atomic
def edit(request, site_id, pk, variant_id, tariff_id=None):
    data = tariff_context(request, site_id, pk, variant_id, "change" if tariff_id else "add")
    variant = data["variant"]
    tariff = (
        get_object_or_404(Tariff, variant=variant, pk=tariff_id, archived_at__isnull=True)
        if tariff_id
        else Tariff(variant=variant)
    )
    form = TariffForm(
        request.POST if request.method == "POST" else None,
        instance=tariff,
        variant=variant,
        can_publish=data["can_publish"],
        can_change_commercial=data["can_view_commercial"] and data["can_change_commercial"],
    )
    status = 200
    if request.method == "POST":
        valid = form.is_valid()
        if not current_version(form, tariff, parent=data["product"]) or str(
            form.data.get("variant_version", "")
        ) != str(variant.version):
            form.add_error(None, "La déclinaison a changé. Rechargez avant de réessayer.")
            status = 409
        elif valid and save_model_form(request, data, form, parent=data["product"]):
            variant.version += 1
            variant.save(update_fields=["version", "updated_at"])
            messages.success(request, "Tarif enregistré.")
            return redirect(
                "products:variant-detail", site_id=site_id, pk=pk, variant_id=variant_id
            )
    data.update(
        form=form,
        heading="Modifier le tarif" if tariff_id else "Ajouter un tarif",
        form_sections=sections(
            form,
            [
                (
                    "Tarif",
                    [
                        "label",
                        "unit",
                        "quantity",
                        "currency",
                        "basis",
                        "amount",
                        "on_request",
                        "tax_rate",
                        "terms",
                        "position",
                    ],
                ),
                (
                    "Promotion",
                    [
                        "discount_kind",
                        "discount_value",
                        "discount_enabled",
                        "discount_starts_at",
                        "discount_ends_at",
                    ],
                ),
                ("Coût privé", ["purchase_amount", "purchase_basis"]),
                ("Disponibilité", ["is_active", "is_public"]),
            ],
        ),
    )
    return render(request, "products/form.html", data, status=status)


@secure_page
@login_required
@require_http_methods(["GET", "POST"])
@transaction.atomic
def lifecycle(request, site_id, pk, variant_id, tariff_id, restore=False):
    data = tariff_context(request, site_id, pk, variant_id, "change" if restore else "delete")
    variant = data["variant"]
    tariff = get_object_or_404(Tariff, variant=variant, pk=tariff_id)
    form = LifecycleForm(
        request.POST if request.method == "POST" else None,
        initial={"version": tariff.version, "parent_version": data["product"].version},
    )
    status = 200
    if request.method == "POST":
        valid = form.is_valid()
        if not current_version(form, tariff, parent=data["product"]):
            status = 409
        elif valid:
            tariff.archived_at = None if restore else timezone.now()
            tariff.is_public = False
            tariff.version += 1
            tariff.save(update_fields=["archived_at", "is_public", "version", "updated_at"])
            audit(request, data["site"], tariff, "restore" if restore else "archive")
            variant.version += 1
            variant.save(update_fields=["version", "updated_at"])
            touch_product(request, data["product"])
            return redirect(
                "products:variant-detail", site_id=site_id, pk=pk, variant_id=variant_id
            )
    data.update(
        form=form,
        heading=f"{'Restaurer' if restore else 'Archiver'} : {tariff.label}",
        form_sections=sections(form, [("Confirmation", ["confirm"])]),
    )
    return render(request, "products/form.html", data, status=status)


@secure_page
@login_required
@require_http_methods(["GET", "POST"])
@transaction.atomic
def publish(request, site_id, pk, variant_id, tariff_id):
    data = context(request, site_id, "publish_product")
    if not has_site_permission(request.user, data["site"], "products.view_tariff"):
        raise Http404
    product = product_for(data["site"], pk)
    variant = get_object_or_404(Variant, product=product, pk=variant_id, archived_at__isnull=True)
    tariff = get_object_or_404(Tariff, variant=variant, pk=tariff_id, archived_at__isnull=True)
    form = TariffPublicationForm(
        request.POST if request.method == "POST" else None, instance=tariff, variant=variant
    )
    status = 200
    if request.method == "POST":
        valid = form.is_valid()
        if not current_version(form, tariff, parent=product) or str(
            form.data.get("variant_version", "")
        ) != str(variant.version):
            form.add_error(None, "La déclinaison a changé. Rechargez-la.")
            status = 409
        elif valid and save_model_form(request, data, form, parent=product):
            variant.version += 1
            variant.save(update_fields=["version", "updated_at"])
            return redirect(
                "products:variant-detail", site_id=site_id, pk=pk, variant_id=variant_id
            )
    data.update(
        product=product,
        variant=variant,
        form=form,
        heading=f"Publication : {tariff.label}",
        form_sections=sections(form, [("Exposition du tarif", ["is_active", "is_public"])]),
    )
    return render(request, "products/form.html", data, status=status)

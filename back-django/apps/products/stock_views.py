from urllib.parse import urlencode
from uuid import uuid4

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.db.models import F, Q, Sum
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_http_methods

from apps.cobweb.views import secure_page
from apps.tenancy.permissions import has_site_permission

from . import stock
from .stock_forms import (
    BalanceForm,
    CountForm,
    InventoryActionForm,
    InventoryCreateForm,
    ItemForm,
    LocationForm,
    MovementForm,
    OperationFilterForm,
    SelectionForm,
    StockFilterForm,
    items,
    locations,
)
from .stock_models import StockBalance, StockInventory, StockItem, StockLocation, StockOperation
from .views import context as product_context
from .views import current_version, save_model_form


def context(request, site_id, action="view_stock"):
    data = product_context(request, site_id, action)
    if not has_site_permission(request.user, data["site"], "products.view_stock"):
        raise Http404
    data.update(
        {
            f"can_{key}": has_site_permission(request.user, data["site"], f"products.{key}")
            for key in (
                "manage_stock",
                "manage_stock_locations",
                "record_stock_movement",
                "transfer_stock",
                "manage_stock_inventory",
                "validate_stock_inventory",
            )
        }
    )
    return data


def error_status(form, error):
    form.add_error(
        None,
        error.messages
        if isinstance(error, ValidationError)
        else "L'opération n'a pas été enregistrée. Rechargez avant de réessayer.",
    )
    return 409 if isinstance(error, (stock.StockConflict, IntegrityError)) else 400


@secure_page
@login_required
@require_GET
def listing(request, site_id):
    data = context(request, site_id)
    form = StockFilterForm(request.GET, site=data["site"])
    rows = stock.balances_for(data["site"])
    low = Q(minimum__isnull=False, quantity__lte=F("minimum"))
    low_count = rows.filter(low).count()
    if form.is_valid():
        value = form.cleaned_data
        if value["q"]:
            rows = rows.filter(
                Q(item__variant__reference__icontains=value["q"])
                | Q(item__variant__title__icontains=value["q"])
                | Q(item__variant__product__reference__icontains=value["q"])
                | Q(item__variant__product__title__icontains=value["q"])
            )
        if value["location"]:
            rows = rows.filter(location=value["location"])
        if value["low"]:
            rows = rows.filter(low)
    else:
        rows = rows.none()
    data.update(
        form=form,
        low_count=low_count,
        page=Paginator(rows, 30).get_page(request.GET.get("page")),
        query=urlencode({k: v for k, v in request.GET.items() if k != "page"}),
    )
    return render(request, "products/stock/list.html", data)


@secure_page
@login_required
@require_http_methods(["GET", "POST"])
@transaction.atomic
def location_edit(request, site_id, pk=None):
    data = context(request, site_id, "manage_stock_locations")
    instance = (
        get_object_or_404(locations(data["site"]), pk=pk)
        if pk
        else StockLocation(site=data["site"])
    )
    form = LocationForm(
        request.POST if request.method == "POST" else None, site=data["site"], instance=instance
    )
    status = 200
    if request.method == "POST":
        valid = form.is_valid()
        if not current_version(form, instance):
            status = 409
        elif valid and save_model_form(request, data, form):
            return redirect("products:stock-locations", site_id=site_id)
    data.update(
        form=form,
        heading="Modifier le lieu" if pk else "Créer un dépôt, une zone ou un emplacement",
    )
    return render(request, "products/stock/form.html", data, status=status)


@secure_page
@login_required
@require_GET
def location_list(request, site_id):
    data = context(request, site_id)
    data.update(page=Paginator(locations(data["site"]), 30).get_page(request.GET.get("page")))
    return render(request, "products/stock/locations.html", data)


@secure_page
@login_required
@require_http_methods(["GET", "POST"])
@transaction.atomic
def item_create(request, site_id):
    data = context(request, site_id, "manage_stock")
    initial = {}
    raw = request.GET.get("variant", "")
    if raw.isascii() and raw.isdigit() and len(raw) <= 18:
        initial["variant"] = int(raw)
    form = ItemForm(
        request.POST if request.method == "POST" else None,
        site=data["site"],
        initial=initial,
        instance=StockItem(site=data["site"]),
    )
    if request.method == "POST" and form.is_valid():
        item = save_model_form(request, data, form)
        if item:
            return redirect("products:stock-item", site_id=site_id, pk=item.pk)
    data.update(form=form, heading="Configurer le stock d'une déclinaison")
    return render(request, "products/stock/form.html", data)


@secure_page
@login_required
@require_GET
def item_list(request, site_id):
    data = context(request, site_id)
    data.update(page=Paginator(items(data["site"]), 30).get_page(request.GET.get("page")))
    return render(request, "products/stock/items.html", data)


@secure_page
@login_required
@require_GET
def item_detail(request, site_id, pk):
    data = context(request, site_id)
    item = get_object_or_404(items(data["site"]), pk=pk)
    rows = stock.balances_for(data["site"]).filter(item=item)
    data.update(
        item=item,
        total=rows.aggregate(total=Sum("quantity"))["total"],
        page=Paginator(rows, 30).get_page(request.GET.get("page")),
    )
    return render(request, "products/stock/item.html", data)


@secure_page
@login_required
@require_http_methods(["GET", "POST"])
@transaction.atomic
def balance_edit(request, site_id, item_id, pk=None):
    data = context(request, site_id, "manage_stock")
    item = get_object_or_404(items(data["site"]), pk=item_id)
    balance = (
        get_object_or_404(stock.balances_for(data["site"]), item=item, pk=pk)
        if pk
        else StockBalance(item=item)
    )
    form = BalanceForm(
        request.POST if request.method == "POST" else None, item=item, instance=balance
    )
    status = 200
    if request.method == "POST":
        valid = form.is_valid()
        if not current_version(form, balance):
            status = 409
        elif valid and save_model_form(request, data, form):
            return redirect("products:stock-item", site_id=site_id, pk=item_id)
    data.update(
        item=item,
        form=form,
        heading="Modifier le seuil" if pk else "Ajouter un emplacement de stock",
    )
    return render(request, "products/stock/form.html", data, status=status)


MOVEMENTS = {
    "entry": "Entrée de stock",
    "exit": "Sortie de stock",
    "transfer": "Transfert de stock",
}


@secure_page
@login_required
@require_http_methods(["GET", "POST"])
@transaction.atomic
def movement(request, site_id, kind):
    if kind not in MOVEMENTS:
        raise Http404
    data = context(
        request, site_id, "transfer_stock" if kind == "transfer" else "record_stock_movement"
    )
    selection = SelectionForm(request.GET or None, site=data["site"], kind=kind)
    data.update(selection=selection, heading=MOVEMENTS[kind], kind=kind)
    status = 200
    if selection.is_valid():
        chosen = selection.cleaned_data
        item = chosen["item"]
        source, destination = chosen.get("source"), chosen.get("destination")
        rows = {
            row.location_id: row
            for row in stock.balances_for(data["site"]).filter(
                item=item, location_id__in=[v.pk for v in (source, destination) if v]
            )
        }
        if any(v and v.pk not in rows for v in (source, destination)):
            selection.add_error(
                None, "Ajoutez d'abord les emplacements à la fiche de stock de cette déclinaison."
            )
        else:
            form = MovementForm(
                request.POST if request.method == "POST" else None,
                initial={
                    "submission_id": uuid4(),
                    "source_version": rows[source.pk].version if source else None,
                    "destination_version": rows[destination.pk].version if destination else None,
                },
            )
            form.fields[
                "quantity"
            ].help_text = f"Quantité en {item.unit}, au plus quatre décimales."
            if request.method == "POST" and form.is_valid():
                value = form.cleaned_data
                try:
                    operation = stock.move(
                        site_id,
                        request.user,
                        kind,
                        item.pk,
                        source.pk if source else None,
                        destination.pk if destination else None,
                        value["quantity"],
                        value["source_version"],
                        value["destination_version"],
                        value["reason"],
                        value["submission_id"],
                    )
                except (ValidationError, IntegrityError) as error:
                    status = error_status(form, error)
                else:
                    messages.success(request, "Mouvement enregistré.")
                    return redirect("products:stock-operation", site_id=site_id, pk=operation.pk)
            data.update(
                form=form,
                item=item,
                source=rows.get(source.pk) if source else None,
                destination=rows.get(destination.pk) if destination else None,
            )
    return render(request, "products/stock/movement.html", data, status=status)


@secure_page
@login_required
@require_GET
def operation_list(request, site_id):
    data = context(request, site_id)
    form = OperationFilterForm(request.GET, site=data["site"])
    rows = StockOperation.objects.filter(site=data["site"]).select_related("actor")
    if form.is_valid():
        value = form.cleaned_data
        filters = {}
        for field, lookup in (
            ("item", "movements__balance__item"),
            ("location", "movements__balance__location"),
            ("kind", "kind"),
        ):
            if value[field]:
                filters[lookup] = value[field]
        rows = rows.filter(**filters).distinct()
    else:
        rows = rows.none()
    data.update(
        form=form,
        page=Paginator(rows, 30).get_page(request.GET.get("page")),
        query=urlencode({key: value for key, value in request.GET.items() if key != "page"}),
    )
    return render(request, "products/stock/operations.html", data)


@secure_page
@login_required
@require_GET
def operation_detail(request, site_id, pk):
    data = context(request, site_id)
    operation = get_object_or_404(
        StockOperation.objects.select_related("actor", "inventory"), site=data["site"], pk=pk
    )
    data.update(
        operation=operation,
        page=Paginator(operation.movements.all(), 50).get_page(request.GET.get("page")),
    )
    return render(request, "products/stock/operation.html", data)


@secure_page
@login_required
@require_GET
def inventory_list(request, site_id):
    data = context(request, site_id)
    rows = StockInventory.objects.filter(site=data["site"]).select_related(
        "location__parent__parent", "created_by"
    )
    data.update(page=Paginator(rows, 30).get_page(request.GET.get("page")))
    return render(request, "products/stock/inventories.html", data)


@secure_page
@login_required
@require_http_methods(["GET", "POST"])
@transaction.atomic
def inventory_create(request, site_id):
    data = context(request, site_id, "manage_stock_inventory")
    form = InventoryCreateForm(
        request.POST if request.method == "POST" else None,
        site=data["site"],
        initial={"submission_id": uuid4()},
    )
    status = 200
    if request.method == "POST" and form.is_valid():
        value = form.cleaned_data
        try:
            inventory = stock.open_inventory(
                site_id, request.user, value["location"].pk, value["reason"], value["submission_id"]
            )
        except (ValidationError, IntegrityError) as error:
            status = error_status(form, error)
        else:
            return redirect("products:stock-inventory", site_id=site_id, pk=inventory.pk)
    data.update(form=form, heading="Ouvrir un inventaire")
    return render(request, "products/stock/form.html", data, status=status)


@secure_page
@login_required
@require_http_methods(["GET", "POST"])
@transaction.atomic
def inventory_detail(request, site_id, pk):
    data = context(
        request, site_id, "manage_stock_inventory" if request.method == "POST" else "view_stock"
    )
    inventory = get_object_or_404(
        StockInventory.objects.select_related(
            "location__parent__parent", "closed_by", "created_by"
        ),
        site=data["site"],
        pk=pk,
    )
    lines = inventory.lines.select_related("balance__item__variant__product")
    page = Paginator(lines, 50).get_page(request.GET.get("page"))
    current_lines = list(page.object_list)
    count_forms = forms.formset_factory(
        CountForm, extra=0, max_num=50, validate_max=True, absolute_max=50
    )
    formset = count_forms(
        request.POST if request.method == "POST" else None,
        initial=[{"line_id": line.pk, "counted": line.counted} for line in current_lines],
    )
    form = InventoryActionForm(
        request.POST if request.method == "POST" else None, initial={"version": inventory.version}
    )
    form.fields["confirm"].label = "Je confirme le comptage de cette page."
    status = 200
    if request.method == "POST" and form.is_valid() and formset.is_valid():
        counts = {value["line_id"]: value["counted"] for value in formset.cleaned_data}
        expected_ids = {line.pk for line in current_lines}
        try:
            if len(counts) != len(formset.cleaned_data):
                raise ValidationError("Une ligne a été soumise plusieurs fois.")
            stock.count_inventory(
                site_id, request.user, pk, form.cleaned_data["version"], counts, expected_ids
            )
        except (ValidationError, IntegrityError) as error:
            status = error_status(form, error)
        else:
            messages.success(request, "Comptage enregistré ; aucune quantité de stock modifiée.")
            return redirect(request.get_full_path())
    changed = lines.filter(~Q(balance_version=F("balance__version"))).exists()
    data.update(
        inventory=inventory,
        page=page,
        form=form,
        formset=formset,
        rows=list(zip(current_lines, formset, strict=False)),
        remaining=lines.filter(counted__isnull=True).count(),
        changed=changed,
    )
    return render(request, "products/stock/inventory.html", data, status=status)


INVENTORY_ACTIONS = {
    "validate": (
        "validate_stock_inventory",
        "Valider l'inventaire",
        "Les écarts seront enregistrés comme mouvements et les quantités comptées deviendront "
        "le stock réel. Toutes les lignes doivent être comptées et le stock doit être inchangé "
        "depuis leur préparation.",
    ),
    "refresh": (
        "manage_stock_inventory",
        "Reprendre l'inventaire",
        "Les quantités attendues seront actualisées. Tous les comptages seront effacés : "
        "recomptez chaque ligne avant validation.",
    ),
    "cancel": (
        "manage_stock_inventory",
        "Annuler l'inventaire",
        "Les comptages seront conservés dans l'historique ; "
        "aucune quantité de stock ne sera modifiée.",
    ),
}


@secure_page
@login_required
@require_http_methods(["GET", "POST"])
@transaction.atomic
def inventory_action(request, site_id, pk, action):
    if action not in INVENTORY_ACTIONS:
        raise Http404
    permission, title, consequences = INVENTORY_ACTIONS[action]
    data = context(request, site_id, permission)
    inventory = get_object_or_404(StockInventory, pk=pk, site=data["site"])
    form = InventoryActionForm(
        request.POST if request.method == "POST" else None, initial={"version": inventory.version}
    )
    status = 200
    if request.method == "POST" and form.is_valid():
        try:
            stock.finish_inventory(site_id, request.user, pk, action, form.cleaned_data["version"])
        except (ValidationError, IntegrityError) as error:
            status = error_status(form, error)
        else:
            return redirect("products:stock-inventory", site_id=site_id, pk=pk)
    data.update(form=form, inventory=inventory, heading=title, consequences=consequences)
    return render(request, "products/stock/form.html", data, status=status)

from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods

from apps.audit.models import AuditEvent
from apps.cobweb.content import rendered_content
from apps.cobweb.publication import descendant_ids
from apps.cobweb.views import secure_page
from apps.tenancy.models import Site
from apps.tenancy.permissions import has_site_permission

from .forms import (
    BrandForm,
    DefinitionForm,
    FeaturesForm,
    ImageForm,
    LifecycleForm,
    ProductForm,
    PublicationForm,
    SearchForm,
    VariantForm,
    VariantPublicationForm,
)
from .models import (
    Brand,
    FeatureDefinition,
    FeatureValue,
    Product,
    ProductImage,
    StockItem,
    Variant,
)


def context(request, site_id, action="view_product"):
    sites = Site.objects.for_user(request.user).exclude(pk=1)
    site = get_object_or_404(sites, pk=site_id)
    if request.method == "POST":
        # for_user() peut utiliser DISTINCT ; PostgreSQL interdit de combiner
        # DISTINCT et FOR UPDATE. Résoudre le périmètre avant le verrou simple.
        site = get_object_or_404(Site.objects.select_for_update(), pk=site.pk, is_active=True)
    for permission in {"products.view_product", f"products.{action}"}:
        if not has_site_permission(request.user, site, permission):
            raise Http404
    permissions = {
        "can_add": "products.add_product",
        "can_change": "products.change_product",
        "can_delete": "products.delete_product",
        "can_restore": "products.restore_product",
        "can_publish": "products.publish_product",
        "can_view_media": "media.view_asset",
        "can_manage_settings": "tenancy.manage_site_settings",
        "can_view_commercial": "products.view_commercial_data",
        "can_change_commercial": "products.change_commercial_data",
        "can_configure_reservations": "products.configure_reservations",
        "can_view_tariffs": "products.view_tariff",
        "can_add_tariff": "products.add_tariff",
        "can_change_tariff": "products.change_tariff",
        "can_delete_tariff": "products.delete_tariff",
        "can_view_bookings": "planning.view_booking",
        "can_view_offers": "planning.view_bookingoffer",
        "can_view_stock": "products.view_stock",
        "can_manage_stock": "products.manage_stock",
    }
    query = request.session.get(f"products_catalog_{site_id}", "")
    return {
        "site": site,
        "products_active": True,
        "return_url": reverse("products:list", args=[site_id]) + (f"?{query}" if query else ""),
        **{
            key: has_site_permission(request.user, site, value)
            for key, value in permissions.items()
        },
    }


def product_for(site, pk, *, trash=False):
    return get_object_or_404(
        Product.objects.select_related(
            "site",
            "category__lookup_list",
            "language__lookup_list",
            "brand",
            "picture",
            "thumbnail",
        ),
        site=site,
        pk=pk,
        deleted_at__isnull=not trash,
    )


def audit(request, site, instance, action):
    entity = f"products.{instance._meta.model_name}"
    AuditEvent.objects.create(
        actor=request.user,
        site=site,
        action=f"{entity}.{action}",
        entity_type=entity,
        entity_id=str(instance.pk),
    )


def touch_product(request, product):
    product.version += 1
    product.save(update_fields=["version", "updated_at"])
    audit(request, product.site, product, "child_update")


def current_version(form, instance, *, parent=None):
    if instance.pk and str(form.data.get("version", "")) != str(instance.version):
        form.add_error(None, "Cette fiche a changé. Rechargez-la avant de réessayer.")
        return False
    if parent and str(form.data.get("parent_version", "")) != str(parent.version):
        form.add_error(None, "Le produit a changé. Rechargez la fiche avant de réessayer.")
        return False
    return True


def save_model_form(request, data, form, *, parent=None):
    """Validation complète et audit dans un savepoint ; le site est déjà verrouillé."""
    try:
        with transaction.atomic():
            instance = form.save(commit=False)
            instance.full_clean()
            if instance.pk:
                instance.version += 1
            instance.save()
            form.save_m2m()
            audit(request, data["site"], instance, "save")
            if parent:
                touch_product(request, parent)
    except ValidationError as error:
        form.add_error(None, error.messages)
        return None
    except IntegrityError:
        form.add_error(
            None, "Une référence ou une adresse identique existe déjà. Rechargez la fiche."
        )
        return None
    return instance


def sections(form, groups):
    return [(title, [form[key] for key in keys if key in form.fields]) for title, keys in groups]


@secure_page
@login_required
@require_GET
def catalog(request, site_id, trash=False):
    data = context(request, site_id)
    form = SearchForm(request.GET, site=data["site"])
    items = Product.objects.filter(site=data["site"], deleted_at__isnull=not trash).select_related(
        "site",
        "category__lookup_list",
        "category",
        "brand",
    )
    if form.is_valid():
        values = form.cleaned_data
        if values["q"]:
            items = items.filter(
                Q(reference__icontains=values["q"])
                | Q(title__icontains=values["q"])
                | Q(summary__icontains=values["q"])
                | Q(content__icontains=values["q"])
            )
        if values["category"]:
            ids = descendant_ids(values["category"])
            items = items.filter(
                Q(category_id__in=ids) | Q(additional_categories__pk__in=ids)
            ).distinct()
        for key in ("brand", "language"):
            if values[key]:
                items = items.filter(**{key: values[key]})
        for key, field in (
            ("active", "is_active"),
            ("featured", "featured"),
            ("is_new", "is_new"),
            ("in_home", "in_home"),
            ("reservable", "is_reservable"),
        ):
            if values[key]:
                items = items.filter(**{field: values[key] == "yes"})
        items = items.order_by(values["order"] or "position", "pk")
        if not trash:
            request.session[f"products_catalog_{site_id}"] = urlencode(
                {
                    key: value
                    for key, value in request.GET.items()
                    if key in form.fields or key == "page"
                }
            )
    else:
        items = items.none()
    query = request.GET.copy()
    query.pop("page", None)
    data.update(
        search_form=form,
        page=Paginator(items, 30).get_page(request.GET.get("page")),
        query=query.urlencode(),
        trash=trash,
    )
    return render(request, "products/list.html", data)


@secure_page
@login_required
@require_GET
def detail(request, site_id, pk):
    data = context(request, site_id)
    product = product_for(data["site"], pk)
    data.update(
        product=product,
        content=rendered_content(product.content),
        variants=product.variants.filter(archived_at__isnull=True)[:100],
        gallery=product.gallery.filter(variant__isnull=True).select_related("asset"),
    )
    return render(request, "products/detail.html", data)


@secure_page
@login_required
@require_GET
def preview(request, site_id, pk):
    data = context(request, site_id)
    product = product_for(data["site"], pk)
    data.update(
        product=product,
        content=rendered_content(product.content),
        preview=True,
        can_change=False,
        can_delete=False,
        variants=product.variants.filter(archived_at__isnull=True)[:100],
        gallery=product.gallery.filter(variant__isnull=True).select_related("asset"),
    )
    response = render(request, "products/detail.html", data)
    response["X-Robots-Tag"] = "noindex, nofollow"
    return response


@secure_page
@login_required
@require_http_methods(["GET", "POST"])
@transaction.atomic
def publish(request, site_id, pk, variant_id=None):
    data = context(request, site_id, "publish_product")
    product = product_for(data["site"], pk)
    instance = (
        get_object_or_404(Variant, product=product, pk=variant_id, archived_at__isnull=True)
        if variant_id
        else product
    )
    form_class = VariantPublicationForm if variant_id else PublicationForm
    form = form_class(request.POST if request.method == "POST" else None, instance=instance)
    status = 200
    if request.method == "POST":
        valid = form.is_valid()
        if not current_version(form, instance, parent=product if variant_id else None):
            status = 409
        elif valid and save_model_form(request, data, form, parent=product if variant_id else None):
            messages.success(request, "Réglages de publication enregistrés.")
            return redirect("products:detail", site_id=site_id, pk=pk)
    data.update(
        form=form,
        product=product,
        heading=f"Publication : {instance.title}",
        form_sections=[("Publication et programmation", list(form.visible_fields()))],
    )
    return render(request, "products/form.html", data, status=status)


@secure_page
@login_required
@require_http_methods(["GET", "POST"])
@transaction.atomic
def edit(request, site_id, pk=None):
    data = context(request, site_id, "change_product" if pk else "add_product")
    product = product_for(data["site"], pk) if pk else Product(site=data["site"])
    form = ProductForm(
        request.POST if request.method == "POST" else None,
        instance=product,
        site=data["site"],
        can_view_media=data["can_view_media"],
        can_configure_reservations=data["can_configure_reservations"],
        can_publish=data["can_publish"],
    )
    status = 200
    if request.method == "POST":
        valid = form.is_valid()
        if not current_version(form, product):
            status = 409
        elif valid and (saved := save_model_form(request, data, form)):
            messages.success(request, "Produit enregistré.")
            return redirect("products:detail", site_id=site_id, pk=saved.pk)
    data.update(
        form=form,
        product=product if pk else None,
        heading="Modifier le produit" if pk else "Nouveau produit",
        form_sections=sections(
            form,
            [
                (
                    "Informations",
                    [
                        "reference",
                        "title",
                        "subtitle",
                        "category",
                        "additional_categories",
                        "language",
                        "brand",
                        "position",
                    ],
                ),
                ("Contenu et conditions", ["summary", "content", "terms"]),
                ("Médias", ["picture", "thumbnail"]),
                ("Référencement", ["slug", "seo_title", "seo_description"]),
                ("Publication et programmation", ["is_online", "timezone", "starts_at", "ends_at"]),
                (
                    "Activation et mise en avant",
                    ["is_active", "featured", "is_new", "in_home", "is_reservable"],
                ),
            ],
        ),
    )
    return render(request, "products/form.html", data, status=status)


@secure_page
@login_required
@require_http_methods(["GET", "POST"])
@transaction.atomic
def lifecycle(request, site_id, pk, restore=False):
    data = context(request, site_id, "restore_product" if restore else "delete_product")
    product = product_for(data["site"], pk, trash=restore)
    form = LifecycleForm(
        request.POST if request.method == "POST" else None, initial={"version": product.version}
    )
    status = 200
    if request.method == "POST":
        valid = form.is_valid()
        if not current_version(form, product):
            status = 409
        elif valid:
            product.deleted_at = None if restore else timezone.now()
            product.is_online = False
            product.version += 1
            product.save(update_fields=["deleted_at", "is_online", "version", "updated_at"])
            audit(request, data["site"], product, "restore" if restore else "trash")
            messages.success(
                request,
                "Produit restauré en brouillon." if restore else "Produit placé en corbeille.",
            )
            return redirect(data["return_url"])
    data.update(
        form=form,
        heading=f"{'Restaurer' if restore else 'Mettre en corbeille'} : {product.title}",
        form_sections=sections(form, [("Confirmation", ["confirm"])]),
    )
    return render(request, "products/form.html", data, status=status)


@secure_page
@login_required
@require_GET
def variants(request, site_id, pk=None):
    data = context(request, site_id)
    items = Variant.objects.filter(
        product__site=data["site"], product__deleted_at__isnull=True
    ).select_related("product")
    if pk:
        data["product"] = product_for(data["site"], pk)
        items = items.filter(product=data["product"])
    q = request.GET.get("q", "")[:200]
    if q:
        items = items.filter(
            Q(title__icontains=q) | Q(reference__icontains=q) | Q(product__title__icontains=q)
        )
    data.update(
        page=Paginator(items, 30).get_page(request.GET.get("page")), q=q, query=urlencode({"q": q})
    )
    return render(request, "products/variants.html", data)


@secure_page
@login_required
@require_GET
def variant_detail(request, site_id, pk, variant_id):
    data = context(request, site_id)
    product = product_for(data["site"], pk)
    variant = get_object_or_404(Variant, product=product, pk=variant_id)
    data.update(
        product=product,
        variant=variant,
        content=rendered_content(variant.content or product.content),
        features=variant.features.select_related("definition").order_by(
            "definition__position", "pk"
        ),
        gallery=variant.gallery.select_related("asset"),
        tariffs=list(variant.tariffs.all()[:100]) if data["can_view_tariffs"] else [],
        stock_item=StockItem.objects.filter(site=data["site"], variant=variant).first()
        if data["can_view_stock"]
        else None,
    )
    return render(request, "products/variant_detail.html", data)


@secure_page
@login_required
@require_http_methods(["GET", "POST"])
@transaction.atomic
def variant_edit(request, site_id, pk, variant_id=None):
    data = context(request, site_id, "change_product")
    product = product_for(data["site"], pk)
    variant = (
        get_object_or_404(Variant, product=product, pk=variant_id, archived_at__isnull=True)
        if variant_id
        else Variant(product=product)
    )
    form = VariantForm(
        request.POST if request.method == "POST" else None,
        instance=variant,
        product=product,
        can_view_media=data["can_view_media"],
        can_view_commercial=data["can_view_commercial"],
        can_change_commercial=data["can_change_commercial"],
        can_publish=data["can_publish"],
    )
    status = 200
    if request.method == "POST":
        valid = form.is_valid()
        if not current_version(form, variant, parent=product):
            status = 409
        elif valid and (saved := save_model_form(request, data, form, parent=product)):
            return redirect("products:variant-detail", site_id=site_id, pk=pk, variant_id=saved.pk)
    data.update(
        form=form,
        product=product,
        heading="Modifier la déclinaison" if variant_id else "Nouvelle déclinaison",
        form_sections=sections(
            form,
            [
                ("Informations", ["reference", "title", "position", "is_active"]),
                ("Contenu et image propres", ["content", "picture"]),
                (
                    "Dimensions et poids",
                    ["length", "width", "height", "dimension_unit", "weight", "weight_unit"],
                ),
                ("Stock descriptif privé", ["stock_quantity", "sold_quantity"]),
                ("Publication de la déclinaison", ["is_online", "starts_at", "ends_at"]),
            ],
        ),
    )
    return render(request, "products/form.html", data, status=status)


@secure_page
@login_required
@require_http_methods(["GET", "POST"])
@transaction.atomic
def features(request, site_id, pk, variant_id):
    data = context(request, site_id, "change_product")
    product = product_for(data["site"], pk)
    variant = get_object_or_404(Variant, product=product, pk=variant_id, archived_at__isnull=True)
    form = FeaturesForm(request.POST if request.method == "POST" else None, variant=variant)
    status = 200
    if request.method == "POST":
        valid = form.is_valid()
        if not current_version(form, variant, parent=product):
            status = 409
        elif valid:
            for definition in form.definitions:
                value = form.cleaned_data[f"feature_{definition.pk}"]
                if not definition.is_active:
                    continue
                if value:
                    feature, _ = FeatureValue.objects.get_or_create(
                        variant=variant, definition=definition, defaults={"value": value}
                    )
                    feature.value = value
                    feature.full_clean()
                    feature.save()
                else:
                    variant.features.filter(definition=definition).delete()
            variant.full_clean()
            variant.version += 1
            variant.save(update_fields=["version"])
            audit(request, data["site"], variant, "features")
            touch_product(request, product)
            return redirect(
                "products:variant-detail", site_id=site_id, pk=pk, variant_id=variant_id
            )
    data.update(
        form=form,
        product=product,
        heading=f"Caractéristiques : {variant.title}",
        form_sections=[("Valeurs", list(form.visible_fields()))],
    )
    return render(request, "products/form.html", data, status=status)


@secure_page
@login_required
@require_http_methods(["GET", "POST"])
@transaction.atomic
def variant_lifecycle(request, site_id, pk, variant_id):
    data = context(request, site_id, "change_product")
    product = product_for(data["site"], pk)
    variant = get_object_or_404(Variant, product=product, pk=variant_id)
    restore = bool(variant.archived_at)
    form = LifecycleForm(
        request.POST if request.method == "POST" else None,
        initial={"version": variant.version, "parent_version": product.version},
    )
    status = 200
    if request.method == "POST":
        valid = form.is_valid()
        if not current_version(form, variant, parent=product):
            status = 409
        elif valid:
            variant.archived_at = None if restore else timezone.now()
            variant.is_online = False
            variant.is_active = False if restore else variant.is_active
            variant.version += 1
            variant.save(update_fields=["archived_at", "is_active", "is_online", "version"])
            audit(request, data["site"], variant, "restore" if restore else "archive")
            touch_product(request, product)
            return redirect("products:variants", site_id=site_id, pk=pk)
    data.update(
        form=form,
        product=product,
        heading=f"{'Restaurer' if restore else 'Archiver'} : {variant.title}",
        form_sections=sections(form, [("Confirmation", ["confirm"])]),
    )
    return render(request, "products/form.html", data, status=status)


REFERENCE_KINDS = {
    "brands": (Brand, BrandForm, "Marques"),
    "definitions": (FeatureDefinition, DefinitionForm, "Caractéristiques"),
}


@secure_page
@login_required
@require_http_methods(["GET", "POST"])
@transaction.atomic
def reference_list(request, site_id, kind, reference_id=None, create=False):
    if kind not in REFERENCE_KINDS:
        raise Http404
    data = context(request, site_id)
    model, form_class, title = REFERENCE_KINDS[kind]
    form = None
    status = 200
    if reference_id or create:
        if not data["can_manage_settings"]:
            raise Http404
        instance = (
            get_object_or_404(model, site=data["site"], pk=reference_id)
            if reference_id
            else model(site=data["site"])
        )
        form = form_class(request.POST if request.method == "POST" else None, instance=instance)
        if request.method == "POST":
            valid = form.is_valid()
            if not current_version(form, instance):
                status = 409
            elif valid and save_model_form(request, data, form):
                return redirect("products:references", site_id=site_id, kind=kind)
        data.update(
            form=form,
            heading=f"{title} : {'modifier' if reference_id else 'ajouter'}",
            form_sections=[("Référentiel", list(form.visible_fields()))],
        )
        return render(request, "products/form.html", data, status=status)
    if request.method == "POST":
        from django.http import HttpResponseNotAllowed

        return HttpResponseNotAllowed(["GET"])
    data.update(
        kind=kind,
        heading=title,
        page=Paginator(model.objects.filter(site=data["site"]), 30).get_page(
            request.GET.get("page")
        ),
    )
    return render(request, "products/references.html", data)


@secure_page
@login_required
@require_http_methods(["GET", "POST"])
@transaction.atomic
def gallery(request, site_id, pk, variant_id=None, image_id=None, remove=False):
    data = context(request, site_id, "change_product")
    if not data["can_view_media"]:
        raise Http404
    product = product_for(data["site"], pk)
    variant = (
        get_object_or_404(Variant, product=product, pk=variant_id, archived_at__isnull=True)
        if variant_id
        else None
    )
    image = (
        get_object_or_404(ProductImage, product=product, variant=variant, pk=image_id)
        if image_id
        else ProductImage(product=product, variant=variant)
    )
    form = (
        LifecycleForm(
            request.POST if request.method == "POST" else None,
            initial={"version": product.version, "parent_version": product.version},
        )
        if remove
        else ImageForm(
            request.POST if request.method == "POST" else None, instance=image, product=product
        )
    )
    status = 200
    if request.method == "POST":
        valid = form.is_valid()
        if str(form.data.get("parent_version", "")) != str(product.version):
            form.add_error(None, "Le produit a changé. Rechargez la galerie.")
            status = 409
        elif valid:
            from django.conf import settings

            limit = settings.PRODUCTS_GALLERY_LIMIT
            if (
                not remove
                and not image.pk
                and product.gallery.filter(variant=variant).count() >= limit
            ):
                form.add_error(None, f"La galerie est limitée à {limit} images.")
            elif remove:
                audit(request, data["site"], image, "detach")
                image.delete()
                touch_product(request, product)
                return redirect("products:detail", site_id=site_id, pk=pk)
            else:
                image = form.save(commit=False)
                image.full_clean()
                image.save()
                audit(request, data["site"], image, "save")
                touch_product(request, product)
                if variant:
                    return redirect(
                        "products:variant-detail", site_id=site_id, pk=pk, variant_id=variant.pk
                    )
                return redirect("products:detail", site_id=site_id, pk=pk)
    data.update(
        form=form,
        product=product,
        heading="Retirer l’image de la galerie" if remove else "Image de la galerie",
        form_sections=[("Galerie", list(form.visible_fields()))],
    )
    return render(request, "products/form.html", data, status=status)

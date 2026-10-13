from django.urls import path

from . import api, stock_views, tariff_views, views

app_name = "products"
base = "sites/<int:site_id>/products/"
urlpatterns = [
    path(base + "stocks/", stock_views.listing, name="stock-list"),
    path(base + "stocks/locations/", stock_views.location_list, name="stock-locations"),
    path(base + "stocks/locations/new/", stock_views.location_edit, name="stock-location-create"),
    path(
        base + "stocks/locations/<int:pk>/", stock_views.location_edit, name="stock-location-edit"
    ),
    path(base + "stocks/items/", stock_views.item_list, name="stock-items"),
    path(base + "stocks/items/new/", stock_views.item_create, name="stock-item-create"),
    path(base + "stocks/items/<int:pk>/", stock_views.item_detail, name="stock-item"),
    path(
        base + "stocks/items/<int:item_id>/locations/new/",
        stock_views.balance_edit,
        name="stock-balance-create",
    ),
    path(
        base + "stocks/items/<int:item_id>/locations/<int:pk>/",
        stock_views.balance_edit,
        name="stock-balance-edit",
    ),
    path(base + "stocks/movements/", stock_views.operation_list, name="stock-operations"),
    path(base + "stocks/movements/<int:pk>/", stock_views.operation_detail, name="stock-operation"),
    path(base + "stocks/movements/new/<str:kind>/", stock_views.movement, name="stock-movement"),
    path(base + "stocks/inventories/", stock_views.inventory_list, name="stock-inventories"),
    path(
        base + "stocks/inventories/new/",
        stock_views.inventory_create,
        name="stock-inventory-create",
    ),
    path(
        base + "stocks/inventories/<int:pk>/", stock_views.inventory_detail, name="stock-inventory"
    ),
    path(
        base + "stocks/inventories/<int:pk>/<str:action>/",
        stock_views.inventory_action,
        name="stock-inventory-action",
    ),
    path(
        base + "<int:pk>/variants/<int:variant_id>/tariffs/<int:tariff_id>/publication/",
        tariff_views.publish,
        name="tariff-publish",
    ),
    path(
        base + "<int:pk>/variants/<int:variant_id>/tariffs/new/",
        tariff_views.edit,
        name="tariff-create",
    ),
    path(
        base + "<int:pk>/variants/<int:variant_id>/tariffs/<int:tariff_id>/",
        tariff_views.edit,
        name="tariff-edit",
    ),
    path(
        base + "<int:pk>/variants/<int:variant_id>/tariffs/<int:tariff_id>/archive/",
        tariff_views.lifecycle,
        name="tariff-archive",
    ),
    path(
        base + "<int:pk>/variants/<int:variant_id>/tariffs/<int:tariff_id>/restore/",
        tariff_views.lifecycle,
        {"restore": True},
        name="tariff-restore",
    ),
    path(
        "api/v1/public/sites/<int:site_id>/products/<int:pk>/variants/<int:variant_id>/tariffs/",
        api.products,
        {"collection": "tariffs"},
        name="api-tariffs",
    ),
    path("api/v1/public/sites/<int:site_id>/products/", api.products, name="api-list"),
    path(
        "api/v1/public/sites/<int:site_id>/products/categories/",
        api.references,
        name="api-categories",
    ),
    path(
        "api/v1/public/sites/<int:site_id>/products/brands/",
        api.references,
        {"brands": True},
        name="api-brands",
    ),
    path("api/v1/public/sites/<int:site_id>/products/<int:pk>/", api.products, name="api-detail"),
    path(
        "api/v1/public/sites/<int:site_id>/products/<int:pk>/gallery/",
        api.products,
        {"collection": "gallery"},
        name="api-gallery",
    ),
    path(
        "api/v1/public/sites/<int:site_id>/products/<int:pk>/variants/",
        api.products,
        {"collection": "variants"},
        name="api-variants",
    ),
    path(
        "api/v1/public/sites/<int:site_id>/products/<int:pk>/variants/<int:variant_id>/",
        api.products,
        name="api-variant",
    ),
    path(
        "api/v1/public/sites/<int:site_id>/products/<int:pk>/variants/<int:variant_id>/features/",
        api.products,
        {"collection": "features"},
        name="api-features",
    ),
    path(
        "api/v1/public/sites/<int:site_id>/products/<int:pk>/variants/<int:variant_id>/gallery/",
        api.products,
        {"collection": "gallery"},
        name="api-variant-gallery",
    ),
    path(base, views.catalog, name="list"),
    path(base + "trash/", views.catalog, {"trash": True}, name="trash"),
    path(base + "new/", views.edit, name="create"),
    path(base + "variants/", views.variants, name="all-variants"),
    path(base + "references/<str:kind>/", views.reference_list, name="references"),
    path(
        base + "references/<str:kind>/new/",
        views.reference_list,
        {"create": True},
        name="reference-create",
    ),
    path(
        base + "references/<str:kind>/<int:reference_id>/",
        views.reference_list,
        name="reference-edit",
    ),
    path(base + "<int:pk>/", views.detail, name="detail"),
    path(base + "<int:pk>/preview/", views.preview, name="preview"),
    path(base + "<int:pk>/publication/", views.publish, name="publish"),
    path(base + "<int:pk>/edit/", views.edit, name="edit"),
    path(base + "<int:pk>/trash/", views.lifecycle, name="delete"),
    path(base + "<int:pk>/restore/", views.lifecycle, {"restore": True}, name="restore"),
    path(base + "<int:pk>/gallery/new/", views.gallery, name="image-create"),
    path(base + "<int:pk>/gallery/<int:image_id>/", views.gallery, name="image-edit"),
    path(
        base + "<int:pk>/gallery/<int:image_id>/remove/",
        views.gallery,
        {"remove": True},
        name="image-remove",
    ),
    path(base + "<int:pk>/variants/", views.variants, name="variants"),
    path(base + "<int:pk>/variants/new/", views.variant_edit, name="variant-create"),
    path(base + "<int:pk>/variants/<int:variant_id>/", views.variant_detail, name="variant-detail"),
    path(
        base + "<int:pk>/variants/<int:variant_id>/edit/", views.variant_edit, name="variant-edit"
    ),
    path(base + "<int:pk>/variants/<int:variant_id>/features/", views.features, name="features"),
    path(
        base + "<int:pk>/variants/<int:variant_id>/publication/",
        views.publish,
        name="variant-publish",
    ),
    path(
        base + "<int:pk>/variants/<int:variant_id>/archive/",
        views.variant_lifecycle,
        name="variant-archive",
    ),
    path(
        base + "<int:pk>/variants/<int:variant_id>/gallery/new/",
        views.gallery,
        name="variant-image-create",
    ),
    path(
        base + "<int:pk>/variants/<int:variant_id>/gallery/<int:image_id>/",
        views.gallery,
        name="variant-image-edit",
    ),
    path(
        base + "<int:pk>/variants/<int:variant_id>/gallery/<int:image_id>/remove/",
        views.gallery,
        {"remove": True},
        name="variant-image-remove",
    ),
]

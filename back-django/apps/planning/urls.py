from django.urls import path

from . import api, booking_views, series_views, views

app_name = "planning"
urlpatterns = [
    path("sites/<int:site_id>/planning/reservations/", booking_views.listing, name="booking-list"),
    path(
        "sites/<int:site_id>/planning/reservations/nouvelle/",
        booking_views.create,
        name="booking-create",
    ),
    path(
        "sites/<int:site_id>/planning/reservations/<int:pk>/",
        booking_views.detail,
        name="booking-detail",
    ),
    path(
        "sites/<int:site_id>/planning/reservations/<int:pk>/actions/<str:action>/",
        booking_views.action,
        name="booking-action",
    ),
    path(
        "sites/<int:site_id>/planning/reservations/<int:pk>/notifications/<int:notification_id>/relancer/",
        booking_views.retry_notification,
        name="booking-retry",
    ),
    path(
        "sites/<int:site_id>/planning/disponibilites/",
        booking_views.availability,
        name="booking-availability",
    ),
    path(
        "sites/<int:site_id>/planning/reservations/configuration/<str:kind>/",
        booking_views.configuration,
        name="booking-config",
    ),
    path(
        "sites/<int:site_id>/planning/reservations/configuration/<str:kind>/<int:pk>/",
        booking_views.configuration,
        name="booking-config-edit",
    ),
    path("sites/<int:site_id>/planning/", views.calendar, name="calendar"),
    path("sites/<int:site_id>/planning/nouveau/", views.edit, name="create"),
    path(
        "sites/<int:site_id>/planning/series/<int:pk>/modifier/",
        series_views.edit_series,
        name="series-edit",
    ),
    path("sites/<int:site_id>/planning/<int:pk>/deplacer/", series_views.move, name="move"),
    path("sites/<int:site_id>/planning/corbeille/", views.trash, name="trash"),
    path("sites/<int:site_id>/planning/ressources/", views.resources, name="resources"),
    path(
        "sites/<int:site_id>/planning/ressources/<int:pk>/", views.resources, name="resource-edit"
    ),
    path("sites/<int:site_id>/planning/<int:pk>/", views.detail, name="detail"),
    path("sites/<int:site_id>/planning/<int:pk>/modifier/", views.edit, name="edit"),
    path("sites/<int:site_id>/planning/<int:pk>/archiver/", views.lifecycle, name="archive"),
    path(
        "sites/<int:site_id>/planning/<int:pk>/restaurer/",
        views.lifecycle,
        {"restore": True},
        name="restore",
    ),
    path("api/v1/public/sites/<int:site_id>/events/", api.events, name="api-events"),
    path("api/v1/public/sites/<int:site_id>/events/<int:pk>/", api.events, name="api-event"),
]

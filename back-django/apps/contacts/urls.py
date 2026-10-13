from django.urls import path

from . import api, definition_views, forwarding, views

app_name = "contacts"
urlpatterns = [
    path(
        "sites/<int:site_id>/settings/contact-forms/",
        definition_views.definitions,
        name="definitions",
    ),
    path(
        "sites/<int:site_id>/settings/contact-forms/new/",
        definition_views.definitions,
        {"create": True},
        name="definition-create",
    ),
    path(
        "sites/<int:site_id>/settings/contact-forms/<int:pk>/",
        definition_views.definitions,
        name="definition-edit",
    ),
    path("sites/<int:site_id>/contacts/<int:pk>/forward/", forwarding.forward, name="forward"),
    path(
        "sites/<int:site_id>/contacts/<int:pk>/forward/<int:forward_id>/retry/",
        forwarding.retry,
        name="forward-retry",
    ),
    path("api/v1/public/sites/<int:site_id>/contacts/challenge/", api.challenge, name="challenge"),
    path("api/v1/public/sites/<int:site_id>/contacts/", api.submit, name="submit"),
    path("public/sites/<int:site_id>/contact/", views.public_form, name="public-form"),
    path("sites/<int:site_id>/contacts/", views.contact_list, name="list"),
    path("sites/<int:site_id>/contacts/clients/", views.api_clients, name="clients"),
    path(
        "sites/<int:site_id>/contacts/clients/<int:pk>/revoke/",
        views.revoke_client,
        name="client-revoke",
    ),
    path("sites/<int:site_id>/contacts/<int:pk>/", views.detail, name="detail"),
    path("sites/<int:site_id>/contacts/<int:pk>/archive/", views.lifecycle, name="archive"),
    path(
        "sites/<int:site_id>/contacts/<int:pk>/restore/",
        views.lifecycle,
        {"restore": True},
        name="restore",
    ),
    path(
        "sites/<int:site_id>/contacts/<int:pk>/addressbook/",
        views.addressbook_link,
        name="addressbook",
    ),
    path(
        "sites/<int:site_id>/contacts/<int:pk>/notification/retry/",
        views.retry_notification,
        name="notification-retry",
    ),
]

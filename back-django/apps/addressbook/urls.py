from django.urls import path

from . import views

app_name = "addressbook"
urlpatterns = [
    path("sites/<int:site_id>/addressbook/", views.entry_list, name="list"),
    path("sites/<int:site_id>/addressbook/new/", views.edit, name="create"),
    path("sites/<int:site_id>/addressbook/types/", views.type_list, name="types"),
    path("sites/<int:site_id>/addressbook/types/new/", views.type_edit, name="type-create"),
    path("sites/<int:site_id>/addressbook/types/<int:pk>/edit/", views.type_edit, name="type-edit"),
    path("sites/<int:site_id>/addressbook/<int:pk>/", views.detail, name="detail"),
    path("sites/<int:site_id>/addressbook/<int:pk>/edit/", views.edit, name="edit"),
    path("sites/<int:site_id>/addressbook/<int:pk>/archive/", views.lifecycle, name="archive"),
    path(
        "sites/<int:site_id>/addressbook/<int:pk>/restore/",
        views.lifecycle,
        {"restore": True},
        name="restore",
    ),
    path(
        "sites/<int:site_id>/addressbook/<int:pk>/<slug:section>/new/",
        views.child_edit,
        name="child-create",
    ),
    path(
        "sites/<int:site_id>/addressbook/<int:pk>/<slug:section>/<int:child_id>/edit/",
        views.child_edit,
        name="child-edit",
    ),
]

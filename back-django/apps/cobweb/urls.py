from django.urls import path

from . import views

app_name = "cobweb"
urlpatterns = [
    path("public/sites/<int:site_id>/cobweb/<int:pk>/", views.public_content, name="public-item"),
    path(
        "public/sites/<int:site_id>/cobweb/groupes/<int:pk>/",
        views.public_content,
        {"group": True},
        name="public-group",
    ),
    path("sites/<int:site_id>/cobweb/<int:pk>/apercu/", views.preview, name="preview-item"),
    path(
        "sites/<int:site_id>/cobweb/groupes/<int:pk>/apercu/",
        views.preview,
        {"group": True},
        name="preview-group",
    ),
    path("sites/<int:site_id>/cobweb/groupes/<int:pk>/", views.group_detail, name="group-detail"),
    path(
        "sites/<int:site_id>/cobweb/groupes/<int:pk>/supprimer/",
        views.group_delete,
        name="group-delete",
    ),
    path("sites/<int:site_id>/cobweb/", views.item_list, name="list"),
    path(
        "sites/<int:site_id>/cobweb/fonctions/<slug:function>/",
        views.item_list,
        name="function-list",
    ),
    path("sites/<int:site_id>/cobweb/nouveau/", views.item_edit, name="create"),
    path("sites/<int:site_id>/cobweb/groupes/", views.group_list, name="group-list"),
    path(
        "sites/<int:site_id>/cobweb/diaporamas/",
        views.group_list,
        {"slideshow": True},
        name="slideshow-list",
    ),
    path("sites/<int:site_id>/cobweb/groupes/nouveau/", views.group_edit, name="group-create"),
    path(
        "sites/<int:site_id>/cobweb/groupes/<int:pk>/modifier/", views.group_edit, name="group-edit"
    ),
    path("sites/<int:site_id>/cobweb/<int:pk>/", views.item_detail, name="detail"),
    path("sites/<int:site_id>/cobweb/<int:pk>/modifier/", views.item_edit, name="edit"),
    path("sites/<int:site_id>/cobweb/<int:pk>/supprimer/", views.item_delete, name="delete"),
]

from django.urls import path

from . import views

app_name = "pages"
urlpatterns = [
    path(
        "public/sites/<int:site_id>/pages/<slug:language>/<slug:slug>/",
        views.public_detail,
        name="public-slug",
    ),
    path("sites/<int:site_id>/pages/", views.page_list, name="list"),
    path("sites/<int:site_id>/pages/nouveau/", views.edit, name="create"),
    path("sites/<int:site_id>/pages/<int:pk>/", views.detail, name="detail"),
    path("sites/<int:site_id>/pages/<int:pk>/modifier/", views.edit, name="edit"),
    path("sites/<int:site_id>/pages/<int:pk>/corbeille/", views.lifecycle, name="delete"),
    path(
        "sites/<int:site_id>/pages/<int:pk>/restaurer/",
        views.lifecycle,
        {"restore": True},
        name="restore",
    ),
    path("sites/<int:site_id>/pages/<int:pk>/apercu/", views.preview, name="preview"),
    path("public/sites/<int:site_id>/pages/", views.public_list, name="public-list"),
    path("public/sites/<int:site_id>/pages/<int:pk>/", views.public_detail, name="public-detail"),
]

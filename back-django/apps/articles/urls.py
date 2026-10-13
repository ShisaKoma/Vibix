from django.urls import path

from . import views

app_name = "articles"
urlpatterns = [
    path("sites/<int:site_id>/articles/", views.article_list, name="list"),
    path("sites/<int:site_id>/articles/nouveau/", views.edit, name="create"),
    path("sites/<int:site_id>/articles/<int:pk>/", views.detail, name="detail"),
    path("sites/<int:site_id>/articles/<int:pk>/modifier/", views.edit, name="edit"),
    path("sites/<int:site_id>/articles/<int:pk>/corbeille/", views.lifecycle, name="delete"),
    path(
        "sites/<int:site_id>/articles/<int:pk>/restaurer/",
        views.lifecycle,
        {"restore": True},
        name="restore",
    ),
    path("sites/<int:site_id>/articles/<int:pk>/apercu/", views.preview, name="preview"),
    path("public/sites/<int:site_id>/articles/", views.public_list, name="public-list"),
    path(
        "public/sites/<int:site_id>/articles/<int:pk>/", views.public_detail, name="public-detail"
    ),
]

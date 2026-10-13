from django.urls import path

from . import views

app_name = "configuration"
urlpatterns = [
    path("sites/<int:site_id>/settings/", views.site_settings, name="site-settings"),
    path("sites/<int:site_id>/settings/<str:kind>/", views.lookup_lists, name="lookup-lists"),
    path(
        "sites/<int:site_id>/settings/references/<int:list_id>/",
        views.lookup_edit,
        name="lookup-edit",
    ),
]

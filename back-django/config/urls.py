from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path

from apps.accounts import views as account_views
from apps.cobweb import api as cobweb_api
from apps.core import api, views
from apps.media import api as media_api

urlpatterns = [
    path("", include("apps.products.urls")),
    path("", include("apps.planning.urls")),
    path("", include("apps.contacts.urls")),
    path(
        "api/v1/public/sites/<int:site_id>/cobweb/", cobweb_api.contents, name="api-public-cobweb"
    ),
    path(
        "api/v1/public/sites/<int:site_id>/cobweb/<int:pk>/",
        cobweb_api.contents,
        name="api-public-cobweb-detail",
    ),
    path(
        "api/v1/public/sites/<int:site_id>/cobweb/groups/",
        cobweb_api.contents,
        {"groups": True},
        name="api-public-cobweb-groups",
    ),
    path(
        "api/v1/public/sites/<int:site_id>/cobweb/groups/<int:pk>/",
        cobweb_api.contents,
        {"groups": True},
        name="api-public-cobweb-group",
    ),
    path(
        "api/v1/public/sites/<int:site_id>/cobweb/groups/<int:pk>/members/",
        cobweb_api.members,
        name="api-public-cobweb-members",
    ),
    path(
        "api/v1/public/sites/<int:site_id>/media/<int:pk>/",
        media_api.detail,
        name="api-public-media",
    ),
    path(
        "api/v1/public/sites/<int:site_id>/media/<int:pk>/image/",
        media_api.detail,
        {"image": True},
        name="api-public-image",
    ),
    path("docs/openapi", api.openapi, name="openapi"),
    path("docs/openapi/", api.openapi),
    path("api/v1/public/sites/<int:site_id>/", api.site_settings, name="api-public-site"),
    path(
        "api/v1/public/sites/<int:site_id>/<str:module>/", api.contents, name="api-public-content"
    ),
    path(
        "api/v1/public/sites/<int:site_id>/<str:module>/<int:pk>/",
        api.contents,
        name="api-public-detail",
    ),
    path("", views.dashboard, name="dashboard"),
    path("sites/<int:site_id>/", views.workspace, name="site-workspace"),
    path("sites/<int:site_id>/icon/", views.site_icon, name="site-icon"),
    path("", include("apps.cobweb.urls")),
    path("", include("apps.articles.urls")),
    path("", include("apps.addressbook.urls")),
    path("", include("apps.pages.urls")),
    path("", include("apps.media.urls")),
    path("onboarding/", account_views.first_run, name="first-run"),
    path("accounts/login/", auth_views.LoginView.as_view(), name="login"),
    path("accounts/logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("admin/", admin.site.urls),
    path("", include("apps.configuration.urls")),
    path("health/live/", views.health_live, name="health-live"),
    path("health/ready/", views.health_ready, name="health-ready"),
    path("api/v1/sites/", views.api_sites, name="api-sites"),
    path("api/v1/sites/<int:pk>/", views.api_site_detail, name="api-site-detail"),
]

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.utils import timezone

from apps.articles.models import Article
from apps.configuration.models import LookupEntry, LookupList
from apps.pages.models import Page
from apps.tenancy.models import Site


class PublicApiTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.site = Site.objects.create(code="front", name="Front")
        cls.other = Site.objects.create(code="other", name="Other")
        cls.items = {}
        cls.categories = {}
        for module, model in (("pages", Page), ("articles", Article)):
            lookup = LookupList.objects.create(
                site=cls.site,
                module=module,
                kind="category",
                code=module,
                name=module,
                field_key=f"{module}_{model._meta.model_name}.category",
            )
            root = LookupEntry.objects.create(lookup_list=lookup, value="root", label="Root")
            category = LookupEntry.objects.create(
                lookup_list=lookup, value="child", label="Child", parent=root
            )
            cls.categories[module] = category
            cls.items[module] = model.objects.create(
                site=cls.site,
                category=category,
                title="Published",
                is_online=True,
                content='<p>Safe</p><script>secret()</script><a href="javascript:bad()">Link</a>',
                **({"slug": "published"} if module == "pages" else {}),
            )

    def url(self, module="", pk=None, site=None):
        path = f"/api/v1/public/sites/{(site or self.site).pk}/"
        return path + (f"{module}/" if module else "") + (f"{pk}/" if pk else "")

    def test_public_content_and_sanitization(self):
        for module in self.items:
            response = self.client.get(self.url(module))
            self.assertEqual(response.status_code, 200)
            item = response.json()["results"][0]
            self.assertEqual(item["id"], self.items[module].pk)
            self.assertNotIn("<script", item["content_html"])
            self.assertNotIn("javascript:", item["content_html"])
            self.assertNotIn("created_by", item)
            self.assertIn("no-store", response["Cache-Control"])
            self.assertEqual(self.client.get(self.url(module, item["id"])).json(), item)

    def test_unpublished_expired_future_deleted_and_inactive_hidden(self):
        now = timezone.now()
        for module, item in self.items.items():
            for fields in (
                {"is_online": False},
                {"is_active": False},
                {"deleted_at": now},
                {"starts_at": now + timedelta(days=1)},
                {"ends_at": now},
            ):
                with self.subTest(module=module, fields=fields):
                    original = {field: getattr(item, field) for field in fields}
                    type(item).objects.filter(pk=item.pk).update(**fields)
                    self.assertEqual(self.client.get(self.url(module)).json()["count"], 0)
                    self.assertEqual(self.client.get(self.url(module, item.pk)).status_code, 404)
                    type(item).objects.filter(pk=item.pk).update(**original)

    def test_ancestry_and_list_disabling(self):
        for module, category in self.categories.items():
            LookupEntry.objects.filter(pk=category.parent_id).update(is_active=False)
            self.assertEqual(self.client.get(self.url(module)).json()["results"], [])
            self.assertEqual(self.client.get(self.url(module)).json()["navigation"], [])
            LookupEntry.objects.filter(pk=category.parent_id).update(is_active=True)
            LookupList.objects.filter(pk=category.lookup_list_id).update(is_active=False)
            self.assertEqual(
                self.client.get(self.url(module, self.items[module].pk)).status_code, 404
            )

    def test_group_ancestry(self):
        lookup = LookupList.objects.create(
            site=self.site,
            module="pages",
            kind="category",
            code="groups",
            name="Groups",
            field_key="pages_page.group",
        )
        group = LookupEntry.objects.create(
            lookup_list=lookup, value="hidden", label="Hidden", is_active=False
        )
        Page.objects.filter(pk=self.items["pages"].pk).update(group=group)
        self.assertEqual(self.client.get(self.url("pages")).json()["count"], 0)

    def test_site_isolation_reserved_and_disabled_sites(self):
        for module, item in self.items.items():
            hidden = self.client.get(self.url(module, item.pk, self.other))
            missing = self.client.get(self.url(module, 999999, self.other))
            self.assertEqual(hidden.status_code, 404)
            self.assertEqual(hidden.json(), missing.json())
        self.assertEqual(self.client.get("/api/v1/public/sites/1/").status_code, 404)
        Site.objects.filter(pk=self.site.pk).update(is_active=False)
        self.assertEqual(self.client.get(self.url()).status_code, 404)

    def test_pagination_and_method_limits(self):
        for page in ("0", "-1", "abc", "999999"):
            self.assertEqual(self.client.get(self.url("pages"), {"page": page}).status_code, 400)
        self.assertEqual(self.client.get(self.url("unknown")).status_code, 404)
        for method in ("post", "put", "patch", "delete"):
            response = getattr(self.client, method)(self.url("pages"))
            self.assertEqual(response.status_code, 405)
            self.assertEqual(response.json(), {"error": "method_not_allowed"})

    def test_new_account_keeps_json_without_onboarding_redirect(self):
        user = get_user_model().objects.create_user(username="new-front-reader")
        self.client.force_login(user)
        self.assertEqual(self.client.get(self.url("articles")).status_code, 200)
        self.assertEqual(self.client.get("/api/v1/sites/").status_code, 200)
        self.assertEqual(self.client.get("/docs/openapi").status_code, 200)
        self.assertEqual(self.client.get("/").status_code, 302)

    def test_contract_is_served_without_authentication(self):
        response = self.client.get("/docs/openapi")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response["Content-Type"].startswith("application/yaml"))
        body = b"".join(response.streaming_content).decode()
        self.assertIn("openapi: 3.1.0", body)
        self.assertIn("/api/v1/public/sites/{site_id}/pages/", body)

    def test_pagination_is_bounded_and_stable(self):
        Article.objects.bulk_create(
            [
                Article(
                    site=self.site,
                    category=self.categories["articles"],
                    title=f"Article {index}",
                    is_online=True,
                    position=index,
                )
                for index in range(25)
            ]
        )
        first = self.client.get(self.url("articles")).json()
        second = self.client.get(self.url("articles"), {"page": 2}).json()
        self.assertEqual(first["count"], 26)
        self.assertEqual(len(first["results"]), 20)
        self.assertEqual(len(second["results"]), 6)
        self.assertEqual(first["pages"], 2)
        self.assertFalse(
            {item["id"] for item in first["results"]} & {item["id"] for item in second["results"]}
        )

    def test_csrf_and_cors_are_not_weakened(self):
        client = Client(enforce_csrf_checks=True)
        response = client.post(self.url("pages"))
        self.assertEqual(response.status_code, 403)
        response = client.get(self.url("pages"), HTTP_ORIGIN="https://external.example")
        self.assertNotIn("Access-Control-Allow-Origin", response)
        self.assertEqual(Page.objects.count(), 1)

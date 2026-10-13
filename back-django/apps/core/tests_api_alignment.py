from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import UserProfile
from apps.articles.models import Article
from apps.cobweb.models import CobwebGroup, CobwebGroupMember, CobwebItem
from apps.configuration.models import LookupEntry, LookupList
from apps.media.models import Asset
from apps.pages.models import Page
from apps.tenancy.models import Role, Site, SiteMembership


class SiteModuleApiAlignmentTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.site = Site.objects.create(code="alignment", name="Alignment")
        cls.records = {}
        for module, model, field in (
            ("articles", Article, "articles_article.category"),
            ("pages", Page, "pages_page.category"),
            ("cobweb", CobwebItem, "cobweb_item.category"),
        ):
            lookup = LookupList.objects.create(
                site=cls.site,
                module=module,
                kind="category",
                field_key=field,
                code=f"{module}-categories",
                name=f"{module} categories",
            )
            category = LookupEntry.objects.create(
                lookup_list=lookup, value="published", label="Published"
            )
            cls.records[module] = model.objects.create(
                site=cls.site,
                category=category,
                title="Published",
                is_online=True,
                **({"slug": "published"} if module == "pages" else {}),
            )
        group_lookup = LookupList.objects.create(
            site=cls.site,
            module="cobweb",
            kind="category",
            field_key="cobweb_group.category",
            code="group-categories",
            name="Group categories",
        )
        group_category = LookupEntry.objects.create(
            lookup_list=group_lookup, value="published", label="Published"
        )
        cls.group = CobwebGroup.objects.create(
            site=cls.site, category=group_category, title="Group", is_online=True
        )
        CobwebGroupMember.objects.create(group=cls.group, item=cls.records["cobweb"])
        cls.asset = Asset.objects.create(
            site=cls.site,
            title="Published image",
            alt_text="Published image",
            image="published.png",
            width=1,
            height=1,
            byte_size=1,
        )
        Article.objects.filter(pk=cls.records["articles"].pk).update(picture=cls.asset)

    def public_url(self, suffix=""):
        return f"/api/v1/public/sites/{self.site.pk}/{suffix}"

    def test_site_switch_refuses_every_public_family(self):
        self.assertEqual(self.client.get(self.public_url()).status_code, 200)
        self.assertEqual(
            self.client.get(self.public_url(f"media/{self.asset.pk}/")).status_code, 200
        )
        public_article = reverse(
            "articles:public-detail", args=[self.site.pk, self.records["articles"].pk]
        )
        self.assertEqual(self.client.get(public_article).status_code, 200)
        Site.objects.filter(pk=self.site.pk).update(public_access_enabled=False)
        for suffix in (
            "",
            "articles/",
            "pages/",
            "cobweb/",
            "cobweb/groups/",
            f"cobweb/groups/{self.group.pk}/members/",
            f"media/{self.asset.pk}/",
        ):
            with self.subTest(suffix=suffix):
                self.assertEqual(self.client.get(self.public_url(suffix)).status_code, 404)
        self.assertEqual(self.client.get(public_article).status_code, 404)

    def test_each_module_switch_only_refuses_its_public_routes(self):
        for module, url in (
            ("articles", "articles/"),
            ("pages", "pages/"),
            ("cobweb", "cobweb/"),
        ):
            with self.subTest(module=module):
                self.assertEqual(self.client.get(self.public_url(url)).status_code, 200)
                Site.objects.filter(pk=self.site.pk).update(**{f"{module}_enabled": False})
                self.assertEqual(self.client.get(self.public_url(url)).status_code, 404)
                self.assertEqual(self.client.get(self.public_url()).status_code, 200)
                if module == "articles":
                    self.assertEqual(
                        self.client.get(self.public_url(f"media/{self.asset.pk}/")).status_code,
                        404,
                    )
                if module == "cobweb":
                    self.assertEqual(
                        self.client.get(self.public_url("cobweb/groups/")).status_code, 404
                    )
                Site.objects.filter(pk=self.site.pk).update(**{f"{module}_enabled": True})

    def test_cobweb_group_and_member_reads(self):
        groups = self.client.get(self.public_url("cobweb/groups/"))
        self.assertEqual(groups.status_code, 200)
        self.assertEqual(groups.json()["count"], 1)
        detail = self.client.get(self.public_url(f"cobweb/groups/{self.group.pk}/"))
        self.assertEqual(detail.status_code, 200)
        members = self.client.get(self.public_url(f"cobweb/groups/{self.group.pk}/members/"))
        self.assertEqual(members.status_code, 200)
        self.assertEqual(members.json()["count"], 1)
        self.assertEqual(members.json()["results"][0]["item"]["id"], self.records["cobweb"].pk)

    def test_public_category_filters_and_bounded_page_size(self):
        first = self.records["articles"]
        Article.objects.filter(pk=first.pk).update(in_home=True)
        other_category = LookupEntry.objects.create(
            lookup_list=first.category.lookup_list, value="other", label="Other"
        )
        Article.objects.create(
            site=self.site, category=other_category, title="Other", is_online=True
        )
        url = self.public_url("articles/")
        self.assertEqual(
            self.client.get(url, {"category_id": first.category_id}).json()["count"], 1
        )
        self.assertEqual(self.client.get(url, {"category_code": "published"}).json()["count"], 1)
        self.assertEqual(self.client.get(url, {"in_home": "true"}).json()["count"], 1)
        self.assertEqual(self.client.get(url, {"in_home": "false"}).json()["count"], 1)
        page = self.client.get(url, {"page_size": "1"}).json()
        self.assertEqual((page["count"], page["pages"], len(page["results"])), (2, 2, 1))
        self.assertIn("published", [entry["code"] for entry in page["navigation"]])
        for parameters, error in (
            ({"category_id": "bad"}, "invalid_category_id"),
            ({"category_code": "../other"}, "invalid_category_code"),
            ({"in_home": "yes"}, "invalid_in_home"),
            ({"page_size": "101"}, "invalid_page_size"),
        ):
            with self.subTest(parameters=parameters):
                response = self.client.get(url, parameters)
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.json(), {"error": error})
        self.assertEqual(
            self.client.get(self.public_url("pages/"), {"in_home": "true"}).status_code,
            400,
        )
        page = self.records["pages"]
        pages_url = self.public_url("pages/")
        self.assertEqual(
            self.client.get(pages_url, {"category_code": "published"}).json()["count"], 1
        )
        self.assertEqual(
            self.client.get(pages_url, {"category_id": page.category_id}).json()["count"], 1
        )
        self.assertEqual(self.client.get(pages_url, {"category_code": "other"}).json()["count"], 0)
        self.assertEqual(
            self.client.get(
                self.public_url(f"pages/{page.pk}/"), {"category_code": "other"}
            ).status_code,
            404,
        )

    def test_cobweb_category_filters(self):
        item = self.records["cobweb"]
        url = self.public_url("cobweb/")
        self.assertEqual(self.client.get(url, {"category_id": item.category_id}).json()["count"], 1)
        self.assertEqual(self.client.get(url, {"category_code": "published"}).json()["count"], 1)
        self.assertEqual(self.client.get(url, {"category_id": "999999"}).json()["count"], 0)
        self.assertEqual(
            self.client.get(
                self.public_url("cobweb/groups/"), {"category_id": self.group.category_id}
            ).json()["count"],
            1,
        )
        self.assertEqual(
            self.client.get(
                self.public_url("cobweb/groups/"), {"category_code": "published"}
            ).json()["count"],
            1,
        )
        self.assertEqual(
            self.client.get(
                self.public_url(f"cobweb/groups/{self.group.pk}/members/"),
                {"page_size": "1"},
            ).json()["count"],
            1,
        )

    def test_private_site_api_remains_available_when_public_site_is_closed(self):
        user = get_user_model().objects.create_user(username="alignment-reader")
        UserProfile.objects.create(user=user, first_run_completed_at=timezone.now())
        role = Role.objects.create(code="alignment-reader", name="Alignment reader")
        role.permissions.add(
            Permission.objects.get(content_type__app_label="tenancy", codename="view_site")
        )
        SiteMembership.objects.create(user=user, role=role, site=self.site)
        self.client.force_login(user)
        Site.objects.filter(pk=self.site.pk).update(public_access_enabled=False)
        self.assertEqual(self.client.get(self.public_url()).status_code, 404)
        self.assertEqual(
            self.client.get(reverse("api-site-detail", args=[self.site.pk])).status_code, 200
        )

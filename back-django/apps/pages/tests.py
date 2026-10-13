from datetime import timedelta
from io import BytesIO
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.db import IntegrityError, connection, transaction
from django.test import Client, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone
from django.utils.text import slugify
from PIL import Image

from apps.accounts.models import UserProfile
from apps.audit.models import AuditEvent
from apps.cobweb.models import CobwebItem
from apps.configuration.forms import LookupListForm
from apps.configuration.models import LookupEntry, LookupList
from apps.contentlinks.forms import ContentLinksForm
from apps.contentlinks.models import ContentLink, ContentRecord
from apps.media.models import Asset
from apps.tenancy.models import Role, Site, SiteMembership

from .models import Page, PageImage


def create_page(**fields):
    fields.setdefault("slug", slugify(fields["title"]))
    return Page.objects.create(**fields)


class PageTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(username="page-editor")
        UserProfile.objects.create(user=cls.user, first_run_completed_at=timezone.now())
        cls.site = Site.objects.create(code="page-site", name="Pages site")
        cls.other = Site.objects.create(code="page-other", name="Other")
        cls.role = Role.objects.create(code="page-editor", name="Editor")
        cls.role.permissions.set(
            Permission.objects.filter(content_type__app_label__in=["pages", "media", "cobweb"])
        )
        SiteMembership.objects.create(user=cls.user, site=cls.site, role=cls.role)
        cls.lookup = LookupList.objects.create(
            site=cls.site,
            module="pages",
            kind="category",
            field_key="pages_page.category",
            code="page-categories",
            name="Categories",
        )
        cls.root = LookupEntry.objects.create(lookup_list=cls.lookup, value="news", label="News")
        cls.child = LookupEntry.objects.create(
            lookup_list=cls.lookup, parent=cls.root, value="local", label="Local"
        )
        languages = LookupList.objects.create(
            site=cls.site,
            module="pages",
            kind="choice",
            field_key="pages_page.language",
            code="languages",
            name="Languages",
        )
        cls.language = LookupEntry.objects.create(
            lookup_list=languages, value="fr", label="Français"
        )
        foreign = LookupList.objects.create(
            site=cls.other,
            module="pages",
            kind="category",
            field_key="pages_page.category",
            code="other",
            name="Other",
        )
        cls.foreign_category = LookupEntry.objects.create(
            lookup_list=foreign, value="foreign", label="Foreign"
        )
        cls.page = create_page(
            site=cls.site,
            category=cls.child,
            title="Public page",
            reference="ART-1",
            language=cls.language,
            position=5,
            is_online=True,
            created_by=cls.user,
        )
        cls.draft = create_page(site=cls.site, category=cls.root, title="Secret draft", position=10)
        cls.foreign = create_page(
            site=cls.other, category=cls.foreign_category, title="Foreign secret"
        )
        cobweb_categories = LookupList.objects.create(
            site=cls.site,
            module="cobweb",
            kind="category",
            field_key="cobweb_item.category",
            code="cobweb",
            name="Cobweb",
        )
        cls.cobweb_category = LookupEntry.objects.create(
            lookup_list=cobweb_categories, value="links", label="Links"
        )
        cls.cobweb = CobwebItem.objects.create(
            site=cls.site, category=cls.cobweb_category, title="Cobweb related", is_online=True
        )
        cls.asset = Asset.objects.create(
            site=cls.site,
            title="Gallery image",
            alt_text="Blue image",
            image="test.png",
            width=10,
            height=10,
            byte_size=100,
        )
        cls.foreign_asset = Asset.objects.create(
            site=cls.other,
            title="Foreign image",
            alt_text="Foreign",
            image="foreign.png",
            width=10,
            height=10,
            byte_size=100,
        )

    def setUp(self):
        self.client.force_login(self.user)

    def groups(self):
        lookup = LookupList.objects.create(
            site=self.site,
            module="pages",
            kind="category",
            field_key="pages_page.group",
            code="groups",
            name="Groupes Pages",
        )
        root = LookupEntry.objects.create(
            lookup_list=lookup, value="navigation", label="Navigation"
        )
        child = LookupEntry.objects.create(
            lookup_list=lookup,
            parent=root,
            value="practical",
            label="Informations pratiques",
        )
        return root, child

    def test_slug_collision_rejected_without_overwriting_or_creating_links(self):
        response = self.client.post(
            self.url("create"),
            self.payload(
                slug=self.page.slug.upper(),
                language=self.language.pk,
                title="Collision",
                picture=self.asset.pk,
                gallery_2=self.asset.pk,
                targets=[self.cobweb.content_record.pk],
            ),
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("slug", response.context["form"].errors)
        self.assertFalse(Page.objects.filter(title="Collision").exists())
        self.assertFalse(PageImage.objects.exists())
        self.assertFalse(ContentLink.objects.exists())

    def test_url_language_code_uniqueness_default_and_site_scope(self):
        # Two reference lists with the same language code still share the URL namespace.
        lookup = LookupList.objects.create(
            site=self.site,
            module="pages",
            kind="choice",
            field_key="pages_page.language",
            code="other-languages",
            name="Other languages",
        )
        french = LookupEntry.objects.create(lookup_list=lookup, value="fr", label="French")
        for language in (self.language, french):
            with transaction.atomic(), self.assertRaises(IntegrityError):
                create_page(
                    site=self.site,
                    category=self.root,
                    title="Collision",
                    slug=self.page.slug,
                    language=language,
                )
        without_language = create_page(
            site=self.site,
            category=self.root,
            title="Default",
            slug=self.page.slug,
        )
        with transaction.atomic(), self.assertRaises(IntegrityError):
            create_page(
                site=self.site, category=self.root, title="Other default", slug=self.page.slug
            )
        other = create_page(
            site=self.other, category=self.foreign_category, title="Other site", slug=self.page.slug
        )
        self.assertNotEqual(other.site, without_language.site)

    def test_canonical_url_and_default_language_routes_obey_publication(self):
        anonymous = Client()
        self.assertContains(anonymous.get(self.page.content_record.public_url), self.page.title)
        self.assertEqual(anonymous.get(self.draft.content_record.public_url).status_code, 404)
        self.assertContains(
            anonymous.get(self.url("public-list")), self.page.content_record.public_url
        )
        self.draft.is_online = True
        self.draft.save()
        self.assertContains(anonymous.get(self.draft.content_record.public_url), self.draft.title)
        self.assertEqual(
            anonymous.get(
                reverse(
                    "pages:public-slug",
                    args=[
                        self.site.pk,
                        "en",
                        self.page.slug,
                    ],
                )
            ).status_code,
            404,
        )

        LookupEntry.objects.filter(pk=self.language.pk).update(value="fr-revised")
        self.page.refresh_from_db()
        self.page.is_online = False
        self.page.save(update_fields=["is_online"])
        self.page.refresh_from_db()
        self.assertEqual(self.page.url_language, "fr")

    def test_archived_page_reserves_its_url(self):
        Page.objects.filter(pk=self.page.pk).update(deleted_at=timezone.now(), is_online=False)
        response = self.client.post(
            self.url("create"),
            self.payload(
                title="Reused",
                slug=self.page.slug,
                language=self.language.pk,
            ),
        )
        self.assertIn("slug", response.context["form"].errors)
        self.assertFalse(Page.objects.filter(title="Reused").exists())

    def test_late_database_collision_returns_conflict_and_preserves_all_data(self):
        # Simulate a concurrent insert between form validation and the SQL write.
        with patch("apps.pages.forms.PageForm.clean", forms.ModelForm.clean):
            response = self.client.post(
                self.url("edit", self.draft),
                self.payload(
                    self.draft,
                    title="Changed",
                    slug=self.page.slug,
                    language=self.language.pk,
                    gallery_2=self.asset.pk,
                    targets=[self.cobweb.content_record.pk],
                ),
            )
        self.assertEqual(response.status_code, 409)
        self.assertIn("slug", response.context["form"].errors)
        self.draft.refresh_from_db()
        self.assertEqual(self.draft.title, "Secret draft")
        self.assertFalse(self.draft.gallery.exists())
        self.assertFalse(self.draft.content_record.outgoing_links.exists())

    def test_groups_recursive_search_retention_and_public_ancestry(self):
        root, child = self.groups()
        self.page.group = child
        self.page.save()
        self.assertContains(
            self.client.get(
                self.url("list"),
                {
                    "group": root.pk,
                    "include_descendants": "on",
                },
            ),
            self.page.title,
        )
        self.assertNotContains(
            self.client.get(self.url("list"), {"group": root.pk}), self.page.title
        )
        self.assertContains(
            Client().get(
                self.url("public-list"),
                {
                    "group": root.pk,
                    "include_descendants": "on",
                },
            ),
            self.page.title,
        )
        LookupEntry.objects.filter(pk=root.pk).update(is_active=False)
        self.assertNotContains(Client().get(self.url("public-list")), self.page.title)
        self.assertEqual(Client().get(self.page.content_record.public_url).status_code, 404)
        LookupEntry.objects.filter(pk=root.pk).update(is_active=True, parent=child)
        self.assertNotContains(Client().get(self.url("public-list")), self.page.title)
        LookupEntry.objects.filter(pk=root.pk).update(parent=None)
        LookupEntry.objects.filter(pk=child.pk).update(is_active=False)
        response = self.client.post(self.url("edit", self.page), self.payload(group=child.pk))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            self.client.post(
                self.url("edit", self.draft), self.payload(self.draft, group=child.pk)
            ).status_code,
            200,
        )

    def test_group_wrong_field_and_foreign_choices_rejected(self):
        root, child = self.groups()
        for candidate in (self.root, self.language, self.foreign_category):
            response = self.client.post(
                self.url("edit", self.page), self.payload(group=candidate.pk)
            )
            self.assertIn("group", response.context["form"].errors)
            self.page.group = candidate
            with self.assertRaises(ValidationError):
                self.page.clean()
        self.page.group = child
        self.page.clean()
        form = LookupListForm(
            data={
                "module": "pages",
                "field_key": "pages_page.group",
                "code": "tree",
                "name": "Groups",
                "is_active": "on",
            },
            site=self.site,
            kind="category",
            instance=LookupList(site=self.site, kind="category"),
        )
        self.assertTrue(form.is_valid(), form.errors)

    def test_edit_ui_uses_tabs_and_creation_wizard_with_optional_group_tree(self):
        edit = self.client.get(self.url("edit", self.page))
        self.assertContains(edit, 'data-record-flow="tabs"')
        self.assertContains(edit, 'name="slug"')
        self.assertContains(edit, 'name="gallery_4"')
        self.assertNotContains(edit, 'name="gallery_5"')
        self.assertNotContains(edit, 'name="in_home"')
        root, child = self.groups()
        creation = self.client.get(self.url("create"))
        self.assertContains(creation, 'data-record-flow="wizard"')
        self.assertContains(creation, "data-tree-optional")
        self.assertContains(creation, child.label)

    def test_pages_articles_bidirectional_links_respect_permissions(self):
        from apps.articles.models import Article

        self.role.permissions.add(*Permission.objects.filter(content_type__app_label="articles"))
        lookup = LookupList.objects.create(
            site=self.site,
            module="articles",
            kind="category",
            field_key="articles_article.category",
            code="articles",
            name="Articles",
        )
        category = LookupEntry.objects.create(
            lookup_list=lookup, value="news", label="Articles news"
        )
        article = Article.objects.create(
            site=self.site, category=category, title="Linked article", is_online=True
        )
        ContentLink.objects.create(source=self.page.content_record, target=article.content_record)
        ContentLink.objects.create(source=article.content_record, target=self.page.content_record)
        self.assertContains(
            Client().get(self.page.content_record.public_url), article.content_record.public_url
        )
        self.assertContains(
            Client().get(article.content_record.public_url), self.page.content_record.public_url
        )
        self.role.permissions.remove(Permission.objects.get(codename="view_page"))
        form = ContentLinksForm(site=self.site, user=self.user, source=article.content_record)
        self.assertNotIn(self.page.content_record, form.fields["targets"].queryset)

    def url(self, name, page=None, site=None):
        return reverse(
            f"pages:{name}",
            args=[(site or self.site).pk, page.pk] if page else [(site or self.site).pk],
        )

    def payload(self, page=None, **changes):
        page = page or self.page
        return {
            "title": page.title,
            "slug": page.slug,
            "category": page.category_id,
            "position": page.position,
            "is_active": "on",
            "version": page.updated_at.isoformat(),
            **changes,
        }

    def revoke(self, permission):
        self.role.permissions.remove(
            Permission.objects.get(codename=permission, content_type__app_label="pages")
        )

    def test_crud_and_real_registry(self):
        response = self.client.post(
            self.url("create"),
            self.payload(
                title="New page",
                slug="new-page",
                reference="NEW",
                content="<p><u>Safe</u></p>",
                language=self.language.pk,
                gallery_2=self.asset.pk,
                picture=self.asset.pk,
                targets=[self.cobweb.content_record.pk],
                created_by=self.user.pk + 100,
                site=self.other.pk,
            ),
        )
        self.assertEqual(response.status_code, 302)
        page = Page.objects.get(reference="NEW")
        self.assertEqual(page.site, self.site)
        self.assertEqual(page.created_by, self.user)
        self.assertEqual(page.content_record.source, page)
        self.assertEqual(page.gallery.get(position=2).asset, self.asset)
        self.assertTrue(
            ContentLink.objects.filter(
                source=page.content_record, target=self.cobweb.content_record
            ).exists()
        )
        self.assertTrue(
            AuditEvent.objects.filter(action="pages.page.create", entity_id=str(page.pk)).exists()
        )
        self.assertContains(self.client.get(self.url("detail", page)), "New page")

    def test_authentication_scope_and_http_methods(self):
        for name in ("list", "create", "detail", "edit", "delete", "preview"):
            url = self.url(name, self.page if name not in ("list", "create") else None)
            self.assertEqual(Client().get(url).status_code, 302)
        for name in ("detail", "edit", "delete", "preview"):
            self.assertEqual(self.client.get(self.url(name, self.foreign)).status_code, 404)
        self.assertEqual(self.client.get(self.url("list", site=self.other)).status_code, 404)
        self.assertEqual(self.client.post(self.url("list")).status_code, 405)
        self.assertEqual(self.client.put(self.url("edit", self.page)).status_code, 405)

    def test_scoped_permissions_and_revocation(self):
        for name, permission in (
            ("create", "add_page"),
            ("edit", "change_page"),
            ("delete", "delete_page"),
        ):
            self.revoke(permission)
            self.assertEqual(
                self.client.get(
                    self.url(name, None if name == "create" else self.page)
                ).status_code,
                404,
            )
        self.revoke("view_page")
        self.user.user_permissions.add(*Permission.objects.filter(content_type__app_label="pages"))
        self.assertEqual(self.client.get(self.url("list")).status_code, 404)

    def test_csrf_required(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)
        for name in ("create", "edit", "delete"):
            self.assertEqual(
                client.post(
                    self.url(name, None if name == "create" else self.page),
                    self.payload(confirm="on"),
                ).status_code,
                403,
            )

    def test_foreign_and_wrong_field_choices_rejected(self):
        for change in (
            {"category": self.foreign_category.pk},
            {"category": self.cobweb_category.pk},
            {"language": self.root.pk},
            {"additional_categories": [self.foreign_category.pk]},
            {"picture": self.foreign_asset.pk},
            {"gallery_4": self.foreign_asset.pk},
            {"targets": [self.foreign.content_record.pk]},
        ):
            with self.subTest(change=change):
                response = self.client.post(self.url("edit", self.page), self.payload(**change))
                self.assertEqual(response.status_code, 200)
                self.page.refresh_from_db()
                self.assertEqual(self.page.title, "Public page")

    def test_inactive_values_can_be_preserved_not_newly_selected(self):
        self.child.is_active = self.language.is_active = False
        self.child.save()
        self.language.save()
        self.assertEqual(
            self.client.post(
                self.url("edit", self.page), self.payload(language=self.language.pk)
            ).status_code,
            302,
        )
        self.assertEqual(
            self.client.post(
                self.url("create"), self.payload(language=self.language.pk)
            ).status_code,
            200,
        )

    def test_publish_fields_cannot_be_forged(self):
        self.revoke("publish_page")
        response = self.client.post(
            self.url("edit", self.draft),
            self.payload(self.draft, is_online="on", in_home="on", starts_at="2030-01-01T12:00"),
        )
        self.assertEqual(response.status_code, 302)
        self.draft.refresh_from_db()
        self.assertFalse(self.draft.is_online)
        self.assertIsNone(self.draft.starts_at)
        self.assertIsNone(self.draft.published_at)

    def test_first_publication_date_is_server_controlled_and_stable(self):
        self.client.post(
            self.url("edit", self.draft),
            self.payload(self.draft, is_online="on", published_at="1900-01-01"),
        )
        self.draft.refresh_from_db()
        published = self.draft.published_at
        self.assertIsNotNone(published)
        self.assertGreater(published, timezone.now() - timedelta(minutes=1))
        self.client.post(self.url("edit", self.draft), self.payload(self.draft, is_online="on"))
        self.draft.refresh_from_db()
        self.assertEqual(self.draft.published_at, published)

    def test_gallery_slots_and_media_permission_preservation(self):
        self.client.post(
            self.url("edit", self.page),
            self.payload(
                picture=self.asset.pk,
                **{f"gallery_{position}": self.asset.pk for position in range(2, 5)},
            ),
        )
        self.page.refresh_from_db()
        self.assertEqual(self.page.gallery.count(), 3)
        self.role.permissions.remove(Permission.objects.get(codename="view_asset"))
        self.assertEqual(
            self.client.post(
                self.url("edit", self.page),
                self.payload(picture=self.foreign_asset.pk, gallery_2=self.foreign_asset.pk),
            ).status_code,
            302,
        )
        self.page.refresh_from_db()
        self.assertEqual(self.page.picture, self.asset)
        self.assertEqual(self.page.gallery.count(), 3)

    def test_gallery_invalid_position_and_cross_site_model_validation(self):
        for position in (1, 5):
            with transaction.atomic(), self.assertRaises(IntegrityError):
                PageImage.objects.create(page=self.page, asset=self.asset, position=position)
        with self.assertRaises(ValidationError):
            PageImage(page=self.page, asset=self.foreign_asset, position=2).full_clean()

    def test_rich_content_sanitized_on_write_and_on_read(self):
        self.client.post(
            self.url("edit", self.page),
            self.payload(
                content=(
                    "<p><strong>Safe</strong><u>Under</u><script>bad</script>"
                    '<img src=x onerror=bad><a href="javascript:bad">Link</a></p>'
                )
            ),
        )
        self.page.refresh_from_db()
        self.assertIn("<strong>Safe</strong>", self.page.content)
        for token in ("script", "onerror", "javascript"):
            self.assertNotIn(token, self.page.content)
        Page.objects.filter(pk=self.page.pk).update(
            content='<p onclick="bad">Safe</p><script>bad</script>'
        )
        response = self.client.get(self.url("preview", self.page))
        self.assertContains(response, "<p>Safe</p>")
        self.assertNotContains(response, "onclick")

    def test_version_conflicts_prevent_all_writes(self):
        response = self.client.post(
            self.url("edit", self.page),
            self.payload(
                version="stale",
                title="Changed",
                gallery_2=self.asset.pk,
                targets=[self.cobweb.content_record.pk],
            ),
        )
        self.assertEqual(response.status_code, 409)
        self.page.refresh_from_db()
        self.assertEqual(self.page.title, "Public page")
        self.assertFalse(self.page.gallery.exists())
        self.assertFalse(self.page.content_record.outgoing_links.exists())
        self.assertEqual(
            self.client.post(
                self.url("delete", self.page), {"version": "stale", "confirm": "on"}
            ).status_code,
            409,
        )

    def test_audit_failure_rolls_back_page_gallery_and_links(self):
        with patch("apps.pages.views.audit", side_effect=IntegrityError("audit failed")):
            with self.assertRaises(IntegrityError):
                self.client.post(
                    self.url("create"),
                    self.payload(
                        title="Rollback",
                        gallery_2=self.asset.pk,
                        targets=[self.cobweb.content_record.pk],
                    ),
                )
        self.assertFalse(Page.objects.filter(title="Rollback").exists())
        self.assertFalse(PageImage.objects.exists())

    def test_trash_and_restore_preserve_content_and_remain_offline(self):
        PageImage.objects.create(page=self.page, asset=self.asset, position=2)
        ContentLink.objects.create(
            source=self.cobweb.content_record, target=self.page.content_record
        )
        self.assertEqual(
            self.client.post(self.url("delete", self.page), self.payload(confirm="on")).status_code,
            302,
        )
        self.page.refresh_from_db()
        self.assertIsNotNone(self.page.deleted_at)
        self.assertFalse(self.page.is_online)
        self.assertEqual(self.client.get(self.url("edit", self.page)).status_code, 404)
        self.assertEqual(Client().get(self.url("public-detail", self.page)).status_code, 404)
        self.assertNotContains(self.client.get(self.url("list")), self.page.title)
        self.assertContains(self.client.get(self.url("list"), {"trash": "yes"}), self.page.title)
        self.assertEqual(
            self.client.post(
                self.url("restore", self.page), self.payload(confirm="on")
            ).status_code,
            302,
        )
        self.page.refresh_from_db()
        self.assertIsNone(self.page.deleted_at)
        self.assertFalse(self.page.is_online)
        self.assertEqual(self.page.gallery.count(), 1)
        self.assertTrue(ContentLink.objects.filter(target=self.page.content_record).exists())

    def test_restore_requires_its_own_permission_and_csrf(self):
        Page.objects.filter(pk=self.page.pk).update(deleted_at=timezone.now(), is_online=False)
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)
        self.assertEqual(
            client.post(self.url("restore", self.page), self.payload(confirm="on")).status_code,
            403,
        )
        self.revoke("restore_page")
        self.assertEqual(self.client.get(self.url("restore", self.page)).status_code, 404)

    def test_advanced_search_and_invalid_filters(self):
        self.assertContains(
            self.client.get(self.url("list"), {"q": self.page.slug}), self.page.title
        )
        self.assertContains(
            self.client.get(
                self.url("list"),
                {"q": "ART-1", "language": self.language.pk, "author": self.user.pk},
            ),
            self.page.title,
        )
        self.assertNotContains(
            self.client.get(self.url("list"), {"category": self.root.pk}), self.page.title
        )
        self.assertContains(
            self.client.get(
                self.url("list"), {"category": self.root.pk, "include_descendants": "on"}
            ),
            self.page.title,
        )
        self.draft.additional_categories.add(self.child)
        self.assertContains(
            self.client.get(self.url("list"), {"category": self.child.pk}), self.draft.title
        )
        for filters in (
            {"category": self.foreign_category.pk},
            {"order": "site__password"},
            {"created_from": "bad"},
            {"trash": "all"},
        ):
            with self.subTest(filters=filters):
                response = self.client.get(self.url("list"), filters)
                self.assertNotContains(response, self.page.title)
                self.assertContains(response, "Vérifiez les filtres")

    def test_priority_sort_and_pagination(self):
        response = self.client.get(self.url("list"))
        self.assertLess(
            response.content.index(self.draft.title.encode()),
            response.content.index(self.page.title.encode()),
        )
        for index in range(21):
            create_page(site=self.site, category=self.root, title=f"Pagination {index}")
        self.assertEqual(len(self.client.get(self.url("list")).context["page"]), 20)
        self.assertEqual(len(self.client.get(self.url("list"), {"page": 2}).context["page"]), 3)

    def test_public_list_detail_filter_and_preview(self):
        anonymous = Client()
        response = anonymous.get(self.url("public-list"))
        self.assertContains(response, self.page.title)
        self.assertNotContains(response, self.draft.title)
        self.assertNotContains(response, self.foreign.title)
        self.assertEqual(anonymous.get(self.url("public-detail", self.draft)).status_code, 404)
        self.assertContains(self.client.get(self.url("preview", self.draft)), self.draft.title)
        self.assertNotContains(
            anonymous.get(self.url("public-list"), {"category": self.foreign_category.pk}),
            self.page.title,
        )

    def test_publication_window_category_ancestors_site_and_noindex(self):
        for changes in (
            {"starts_at": timezone.now() + timedelta(days=1)},
            {"starts_at": None, "ends_at": timezone.now() - timedelta(days=1)},
            {"ends_at": None, "deleted_at": timezone.now()},
            {"deleted_at": None, "is_active": False},
        ):
            Page.objects.filter(pk=self.page.pk).update(**changes)
            self.assertEqual(Client().get(self.url("public-detail", self.page)).status_code, 404)
        Page.objects.filter(pk=self.page.pk).update(is_active=True, is_searchable=False)
        self.assertContains(
            Client().get(self.url("public-detail", self.page)), 'content="noindex,nofollow"'
        )
        self.root.is_active = False
        self.root.save()
        self.assertEqual(Client().get(self.url("public-detail", self.page)).status_code, 404)
        self.site.is_active = False
        self.site.save()
        self.assertEqual(Client().get(self.url("public-list")).status_code, 404)

    def test_cross_module_links_round_trip_and_private_filtering(self):
        ContentLink.objects.create(
            source=self.cobweb.content_record, target=self.page.content_record
        )
        ContentLink.objects.create(
            source=self.page.content_record, target=self.draft.content_record
        )
        ContentLink.objects.create(
            source=self.page.content_record, target=self.cobweb.content_record
        )
        cobweb_detail = reverse("cobweb:detail", args=[self.site.pk, self.cobweb.pk])
        cobweb_public = reverse("cobweb:public-item", args=[self.site.pk, self.cobweb.pk])
        self.assertContains(self.client.get(cobweb_detail), self.url("detail", self.page))
        self.assertContains(Client().get(cobweb_public), self.page.content_record.public_url)
        public = Client().get(self.url("public-detail", self.page))
        self.assertNotContains(public, self.draft.title)
        self.assertContains(public, self.cobweb.title)
        self.revoke("view_page")
        self.assertNotContains(self.client.get(cobweb_detail), self.page.title)

    def test_unreadable_or_trashed_links_are_preserved_but_not_selectable(self):
        ContentLink.objects.create(
            source=self.cobweb.content_record, target=self.draft.content_record
        )
        Page.objects.filter(pk=self.draft.pk).update(deleted_at=timezone.now())
        form = ContentLinksForm(
            {"targets": []}, site=self.site, user=self.user, source=self.cobweb.content_record
        )
        self.assertNotIn(self.draft.content_record, form.fields["targets"].queryset)
        self.assertTrue(form.is_valid())
        form.save(self.cobweb.content_record)
        self.assertTrue(ContentLink.objects.filter(target=self.draft.content_record).exists())

    def test_registry_exclusive_source_database_constraint(self):
        with transaction.atomic(), self.assertRaises(IntegrityError):
            ContentRecord.objects.create(site=self.site)

    def test_public_list_blocks_inactive_or_cyclic_category_ancestry(self):
        self.root.is_active = False
        self.root.save()
        self.assertNotContains(Client().get(self.url("public-list")), self.page.title)
        LookupEntry.objects.filter(pk=self.root.pk).update(is_active=True, parent=self.child)
        self.assertNotContains(Client().get(self.url("public-list")), self.page.title)
        self.assertEqual(Client().get(self.url("public-detail", self.page)).status_code, 404)

    def test_public_list_paginated_queries_do_not_grow_with_page_ancestry(self):
        for index in range(21):
            create_page(
                site=self.site, category=self.child, title=f"Published {index}", is_online=True
            )
        with CaptureQueriesContext(connection) as queries:
            response = Client().get(self.url("public-list"))
        self.assertEqual(len(response.context["page"]), 20)
        self.assertEqual(response.context["page"].paginator.count, 22)
        self.assertLess(len(queries), 15)

    def test_gallery_media_serving_and_protected_deletion(self):
        with TemporaryDirectory() as directory, override_settings(MEDIA_ROOT=directory):
            stream = BytesIO()
            Image.new("RGB", (10, 10), "blue").save(stream, format="PNG")
            self.asset.image.save("image.png", ContentFile(stream.getvalue()))
            PageImage.objects.create(page=self.page, asset=self.asset, position=4)
            url = reverse("media:image", args=[self.site.pk, self.asset.pk])
            self.assertEqual(Client().get(url).status_code, 200)
            self.client.post(self.url("delete", self.page), self.payload(confirm="on"))
            self.assertEqual(Client().get(url).status_code, 404)
            self.role.permissions.remove(Permission.objects.get(codename="view_asset"))
            self.assertEqual(self.client.get(url).status_code, 200)
            self.revoke("view_page")
            self.assertEqual(self.client.get(url).status_code, 404)
            self.role.permissions.add(Permission.objects.get(codename="view_asset"))
            response = self.client.post(
                reverse("media:delete", args=[self.site.pk, self.asset.pk]), {"confirm": "on"}
            )
            self.assertEqual(response.status_code, 409)

    def test_configuration_targets_are_real_and_module_matched(self):
        form = LookupListForm(
            {
                "module": "pages",
                "field_key": "pages_page.language",
                "code": "new-languages",
                "name": "Languages",
                "is_active": "on",
            },
            site=self.site,
            kind="choice",
        )
        form.instance.kind = "choice"
        self.assertTrue(form.is_valid(), form.errors)
        bad = LookupListForm(
            {"module": "pages", "field_key": "cobweb_item.kind", "code": "bad", "name": "Bad"},
            site=self.site,
            kind="choice",
        )
        bad.instance.kind = "choice"
        self.assertFalse(bad.is_valid())

    def test_navigation_and_security_headers(self):
        response = self.client.get(self.url("create"))
        self.assertContains(response, 'data-record-flow="wizard"')
        self.assertContains(response, self.url("list"))
        self.assertIn("no-store", response["Cache-Control"])
        self.assertIn("script-src 'self'", response["Content-Security-Policy"])
        self.assertContains(
            self.client.get(reverse("site-workspace", args=[self.site.pk])),
            f'href="{reverse("pages:list", args=[self.site.pk])}"',
        )

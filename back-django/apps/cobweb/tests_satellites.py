from datetime import timedelta
from io import BytesIO
from tempfile import TemporaryDirectory

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from PIL import Image

from apps.accounts.models import UserProfile
from apps.configuration.models import LookupEntry, LookupList
from apps.contentlinks.models import ContentLink
from apps.media.models import Asset
from apps.tenancy.models import Role, Site, SiteMembership

from .models import CobwebGroup, CobwebGroupMember, CobwebItem


class SatelliteTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(username="satellite-editor")
        UserProfile.objects.create(user=cls.user, first_run_completed_at=timezone.now())
        cls.site = Site.objects.create(code="satellite", name="Satellite")
        cls.other = Site.objects.create(code="satellite-other", name="Other")
        cls.role = Role.objects.create(code="satellite-editor", name="Editor")
        cls.role.permissions.set(
            Permission.objects.filter(content_type__app_label__in=["cobweb", "media"])
        )
        SiteMembership.objects.create(user=cls.user, site=cls.site, role=cls.role)
        lookup = LookupList.objects.create(
            site=cls.site,
            module="cobweb",
            kind="category",
            field_key="cobweb_item.category",
            code="items",
            name="Items",
        )
        cls.root = LookupEntry.objects.create(lookup_list=lookup, value="root", label="Root")
        cls.child = LookupEntry.objects.create(
            lookup_list=lookup, parent=cls.root, value="child", label="Child"
        )
        group_lookup = LookupList.objects.create(
            site=cls.site,
            module="cobweb",
            kind="category",
            field_key="cobweb_group.category",
            code="groups",
            name="Groups",
        )
        cls.group_category = LookupEntry.objects.create(
            lookup_list=group_lookup, value="groups", label="Groups"
        )
        cls.item = CobwebItem.objects.create(
            site=cls.site, category=cls.child, title="Public entry", is_online=True
        )
        cls.hidden = CobwebItem.objects.create(
            site=cls.site, category=cls.child, title="Private secret", is_online=False
        )
        cls.group = CobwebGroup.objects.create(
            site=cls.site, category=cls.group_category, title="Public group", is_online=True
        )
        CobwebGroupMember.objects.create(group=cls.group, item=cls.item, position=2)
        CobwebGroupMember.objects.create(group=cls.group, item=cls.hidden, position=1)

    def setUp(self):
        self.client.force_login(self.user)

    def url(self, name, content=None):
        return reverse(
            f"cobweb:{name}", args=[self.site.pk, content.pk] if content else [self.site.pk]
        )

    def payload(self, **extra):
        return {
            "title": self.item.title,
            "category": self.child.pk,
            "position": 0,
            "is_active": "on",
            "version": self.item.updated_at.isoformat(),
            **extra,
        }

    def test_public_group_hides_unpublished_members(self):
        anonymous = Client()
        response = anonymous.get(self.url("public-group", self.group))
        self.assertContains(response, self.item.title)
        self.assertNotContains(response, self.hidden.title)
        self.assertEqual(anonymous.get(self.url("public-item", self.hidden)).status_code, 404)

    def test_ancestor_disabling_blocks_publication(self):
        self.root.is_active = False
        self.root.save()
        self.assertEqual(Client().get(self.url("public-item", self.item)).status_code, 404)

    def test_publication_window_and_site_activation(self):
        for changes in (
            {"starts_at": timezone.now() + timedelta(days=1)},
            {"starts_at": None, "ends_at": timezone.now() - timedelta(days=1)},
            {"ends_at": None, "is_active": False},
        ):
            CobwebItem.objects.filter(pk=self.item.pk).update(**changes)
            self.assertEqual(Client().get(self.url("public-item", self.item)).status_code, 404)
        self.site.is_active = False
        self.site.save()
        self.assertEqual(Client().get(self.url("public-group", self.group)).status_code, 404)

    def test_publish_permission_cannot_be_forged(self):
        self.role.permissions.remove(Permission.objects.get(codename="publish_cobwebitem"))
        response = self.client.post(
            self.url("edit", self.hidden),
            self.payload(
                title=self.hidden.title, version=self.hidden.updated_at.isoformat(), is_online="on"
            ),
        )
        self.assertEqual(response.status_code, 302)
        self.hidden.refresh_from_db()
        self.assertFalse(self.hidden.is_online)

    def test_rich_content_is_sanitized_at_save_and_render(self):
        response = self.client.post(
            self.url("edit", self.item),
            self.payload(
                content=(
                    "<p><strong>Safe</strong><script>alert(1)</script>"
                    '<a href="javascript:alert(1)">bad</a></p>'
                )
            ),
        )
        self.assertEqual(response.status_code, 302)
        self.item.refresh_from_db()
        self.assertIn("<strong>Safe</strong>", self.item.content)
        self.assertNotIn("script", self.item.content)
        self.item.content = "<img src=x onerror=alert(1)><strong>Safe</strong>"
        self.item.save()
        response = self.client.get(self.url("detail", self.item))
        self.assertContains(response, "<strong>Safe</strong>")
        self.assertNotContains(response, "onerror")

    def test_visual_editor_formats_survive_save_without_unsafe_attributes(self):
        content = (
            '<h2 style="color:red" onclick="alert(1)">Titre</h2>'
            "<p><strong>Gras</strong> <em>Italique</em> <u>Souligné</u> "
            "<s>Barré</s></p><blockquote><p>Citation</p></blockquote>"
            "<ul><li>Puces</li></ul><ol><li>Ordre</li></ol>"
            '<p><a href="https://example.com/" target="_blank" '
            'onclick="alert(1)">Lien</a><a href="data:text/html,bad">Refusé</a></p>'
        )
        response = self.client.post(
            self.url("edit", self.item),
            self.payload(content=content, ui_panel="medias", save_action="continue"),
        )
        self.assertRedirects(response, self.url("edit", self.item))
        self.item.refresh_from_db()
        for fragment in (
            "<h2>Titre</h2>",
            "<strong>Gras</strong>",
            "<em>Italique</em>",
            "<u>Souligné</u>",
            "<s>Barré</s>",
            "<blockquote><p>Citation</p></blockquote>",
            "<ul><li>Puces</li></ul>",
            "<ol><li>Ordre</li></ol>",
            'href="https://example.com/"',
            'rel="noopener noreferrer"',
        ):
            self.assertIn(fragment, self.item.content)
            self.assertContains(self.client.get(self.url("detail", self.item)), fragment)
        for forbidden in ("onclick", "style=", "target=", "data:text"):
            self.assertNotIn(forbidden, self.item.content)

    def test_descendant_and_additional_category_search(self):
        response = self.client.get(
            self.url("list"), {"category": self.root.pk, "include_descendants": "on"}
        )
        self.assertContains(response, self.item.title)
        response = self.client.get(self.url("list"), {"category": self.root.pk})
        self.assertNotContains(response, self.item.title)
        self.item.additional_categories.add(self.root)
        self.assertContains(
            self.client.get(self.url("list"), {"category": self.root.pk}), self.item.title
        )

    def test_links_are_saved_and_private_links_filtered_publicly(self):
        self.assertEqual(
            self.client.post(
                self.url("edit", self.item),
                self.payload(
                    targets=[self.hidden.content_record.pk, self.group.content_record.pk],
                    is_online="on",
                ),
            ).status_code,
            302,
        )
        self.assertEqual(ContentLink.objects.filter(source=self.item.content_record).count(), 2)
        response = Client().get(self.url("public-item", self.item))
        self.assertContains(response, self.group.title)
        self.assertNotContains(response, self.hidden.title)

    def test_foreign_site_links_rejected_atomically(self):
        foreign = CobwebItem.objects.create(site=self.other, category=self.child, title="Foreign")
        response = self.client.post(
            self.url("edit", self.item),
            self.payload(title="Changed", targets=[foreign.content_record.pk]),
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["links_form"].errors)
        self.item.refresh_from_db()
        self.assertEqual(self.item.title, "Public entry")

    def test_protected_item_delete_and_group_delete_preserves_items(self):
        self.assertEqual(
            self.client.post(
                self.url("delete", self.item),
                {"confirm": "on", "version": self.item.updated_at.isoformat()},
            ).status_code,
            409,
        )
        self.assertEqual(
            self.client.post(
                self.url("group-delete", self.group),
                {"confirm": "on", "version": self.group.updated_at.isoformat()},
            ).status_code,
            302,
        )
        self.assertTrue(CobwebItem.objects.filter(pk=self.item.pk).exists())

    def test_preview_requires_authentication_and_group_view(self):
        self.assertEqual(Client().get(self.url("preview-group", self.group)).status_code, 302)
        self.assertContains(
            self.client.get(self.url("preview-group", self.group)), self.hidden.title
        )
        self.role.permissions.remove(Permission.objects.get(codename="view_cobwebgroup"))
        self.assertEqual(self.client.get(self.url("preview-group", self.group)).status_code, 404)

    def test_group_stale_delete_is_rejected(self):
        self.assertEqual(
            self.client.post(
                self.url("group-delete", self.group), {"confirm": "on", "version": "old"}
            ).status_code,
            409,
        )
        self.assertTrue(CobwebGroup.objects.filter(pk=self.group.pk).exists())

    def test_group_csrf_enforced(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)
        self.assertEqual(client.post(self.url("group-create"), {}).status_code, 403)

    def test_media_upload_validation_public_access_and_protected_delete(self):
        with TemporaryDirectory() as root, override_settings(MEDIA_ROOT=root):
            upload_url = reverse("media:upload", args=[self.site.pk])
            invalid = SimpleUploadedFile(
                "fake.png", b"<script>alert(1)</script>", content_type="image/png"
            )
            self.assertEqual(
                self.client.post(
                    upload_url, {"title": "Fake", "alt_text": "Fake", "image": invalid}
                ).status_code,
                200,
            )
            self.assertFalse(Asset.objects.exists())
            payload = BytesIO()
            Image.new("RGB", (10, 10), "blue").save(payload, format="PNG")
            image = SimpleUploadedFile(
                "original.png", payload.getvalue(), content_type="text/plain"
            )
            self.assertEqual(
                self.client.post(
                    upload_url, {"title": "Image", "alt_text": "Blue", "image": image}
                ).status_code,
                302,
            )
            asset = Asset.objects.get()
            self.assertEqual(
                self.client.post(
                    reverse("media:edit", args=[self.site.pk, asset.pk]),
                    {"title": "Edited", "alt_text": "Blue rectangle"},
                ).status_code,
                302,
            )
            asset.refresh_from_db()
            self.assertEqual(asset.alt_text, "Blue rectangle")
            image_url = reverse("media:image", args=[self.site.pk, asset.pk])
            self.assertEqual(Client().get(image_url).status_code, 404)
            self.item.picture = asset
            self.item.save()
            response = Client().get(image_url)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response["Content-Type"], "image/png")
            # Close the file without emitting request_finished inside TestCase's transaction.
            response._resource_closers[0]()
            self.assertEqual(
                self.client.post(
                    reverse("media:delete", args=[self.site.pk, asset.pk]), {"confirm": "on"}
                ).status_code,
                409,
            )

    def test_media_permission_and_csrf(self):
        url = reverse("media:upload", args=[self.site.pk])
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)
        self.assertEqual(client.post(url, {}).status_code, 403)
        self.role.permissions.remove(Permission.objects.get(codename="add_asset"))
        self.assertEqual(self.client.get(url).status_code, 404)

    def test_media_selection_rejects_cross_site_and_without_permission(self):
        asset = Asset.objects.create(
            site=self.other,
            title="Foreign image",
            alt_text="Foreign",
            image="private.png",
            width=1,
            height=1,
            byte_size=1,
        )
        response = self.client.post(self.url("edit", self.item), self.payload(picture=asset.pk))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["form"].errors)
        asset.site = self.site
        asset.save()
        self.role.permissions.remove(Permission.objects.get(codename="view_asset"))
        response = self.client.post(self.url("edit", self.item), self.payload(picture=asset.pk))
        self.assertEqual(response.status_code, 302)
        self.item.refresh_from_db()
        self.assertIsNone(self.item.picture_id)

    def test_links_hidden_by_permission_are_preserved_on_save(self):
        ContentLink.objects.create(
            source=self.item.content_record, target=self.group.content_record
        )
        self.role.permissions.remove(Permission.objects.get(codename="view_cobwebgroup"))
        response = self.client.post(self.url("edit", self.item), self.payload())
        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            ContentLink.objects.filter(
                source=self.item.content_record, target=self.group.content_record
            ).exists()
        )

    def test_publication_dates_validation_is_atomic(self):
        response = self.client.post(
            self.url("edit", self.item),
            self.payload(
                title="Invalid dates",
                starts_at="2026-10-05T12:00",
                ends_at="2026-10-04T12:00",
            ),
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("ends_at", response.context["form"].errors)
        self.item.refresh_from_db()
        self.assertEqual(self.item.title, "Public entry")

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import UserProfile
from apps.audit.models import AuditEvent
from apps.configuration.models import LookupEntry
from apps.contentlinks.models import ContentRecord
from apps.media.models import Asset
from apps.tenancy.models import Role, Site, SiteMembership

from .models import CobwebGroup, CobwebGroupMember, CobwebItem
from .seeds import seed_cobweb_references


class SlideshowCreationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.site = Site.objects.create(code="slides", name="Site diaporama")
        cls.other = Site.objects.create(code="other-slides", name="Autre site")
        seed_cobweb_references(cls.site)
        seed_cobweb_references(cls.other)
        cls.user = get_user_model().objects.create_user(username="slides-editor")
        UserProfile.objects.create(user=cls.user, first_run_completed_at=timezone.now())
        cls.role = Role.objects.create(code="slides-editor", name="Éditeur diaporama")
        cls.role.permissions.set(Permission.objects.filter(content_type__app_label="cobweb"))
        SiteMembership.objects.create(user=cls.user, site=cls.site, role=cls.role)
        cls.category = cls.reference(cls.site, "cobweb_group.category", "slideshow")
        cls.presentation = cls.reference(cls.site, "cobweb_group.presentation", "slideshow")
        item_category = cls.reference(cls.site, "cobweb_item.category", "medias")
        asset = Asset.objects.create(
            site=cls.site,
            title="Image de test",
            alt_text="Image fictive",
            image="tests/slide.png",
            width=1,
            height=1,
            byte_size=1,
        )
        cls.first = CobwebItem.objects.create(
            site=cls.site,
            category=item_category,
            title="Image intérieure",
            picture=asset,
            is_online=True,
        )
        cls.second = CobwebItem.objects.create(
            site=cls.site,
            category=item_category,
            title="Image miniature",
            thumbnail=asset,
            is_online=True,
        )
        cls.text_only = CobwebItem.objects.create(
            site=cls.site,
            category=item_category,
            title="Texte sans image",
        )

    @classmethod
    def reference(cls, site, field_key, value):
        return LookupEntry.objects.get(
            lookup_list__site=site, lookup_list__field_key=field_key, value=value
        )

    def setUp(self):
        self.client.force_login(self.user)

    def create_url(self):
        return reverse("cobweb:group-create", args=[self.site.pk]) + "?presentation=slideshow"

    def payload(self, **overrides):
        return {
            "title": "Diaporama de test",
            "category": self.category.pk,
            "presentation": self.presentation.pk,
            "position": 0,
            "is_active": "on",
            "members": [self.first.pk, self.second.pk],
            f"member_position_{self.first.pk}": 2,
            f"member_position_{self.second.pk}": 1,
            **overrides,
        }

    def test_creation_form_preselects_presentation_and_only_offers_images(self):
        response = self.client.get(self.create_url())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["group_form"]["category"].value(), self.category.pk)
        self.assertEqual(
            response.context["group_form"]["presentation"].value(), self.presentation.pk
        )
        choices = {
            int(pk) for pk, label in response.context["members_form"].fields["members"].choices
        }
        self.assertEqual(choices, {self.first.pk, self.second.pk})

    def test_creation_does_not_preselect_a_disabled_presentation_list(self):
        lookup = self.presentation.lookup_list
        lookup.is_active = False
        lookup.save(update_fields=["is_active"])
        response = self.client.get(self.create_url())
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context["group_form"]["presentation"].value())
        response = self.client.post(self.create_url(), self.payload())
        self.assertIn("presentation", response.context["group_form"].errors)
        self.assertFalse(CobwebGroup.objects.exists())

    def test_creation_requires_csrf_token(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)
        self.assertEqual(client.post(self.create_url(), self.payload()).status_code, 403)
        self.assertFalse(CobwebGroup.objects.exists())

    def test_creates_ordered_group_with_audit_and_private_preview(self):
        response = self.client.post(self.create_url(), self.payload())
        self.assertEqual(response.status_code, 302)
        group = CobwebGroup.objects.get(site=self.site, title="Diaporama de test")
        self.assertEqual(group.category, self.category)
        self.assertEqual(group.presentation, self.presentation)
        self.assertFalse(group.is_online)
        self.assertEqual(
            list(group.members.order_by("position").values_list("item_id", flat=True)),
            [self.second.pk, self.first.pk],
        )
        event = AuditEvent.objects.get(action="cobweb.group.create", entity_id=str(group.pk))
        self.assertEqual(event.site, self.site)
        self.assertEqual(event.actor, self.user)
        self.assertEqual(event.entity_type, "cobweb.cobwebgroup")
        self.assertContains(
            self.client.get(reverse("cobweb:slideshow-list", args=[self.site.pk])), group.title
        )
        preview = self.client.get(reverse("cobweb:preview-group", args=[self.site.pk, group.pk]))
        self.assertContains(preview, "data-slideshow")
        self.assertContains(preview, "Diapositive suivante")
        self.assertLess(
            preview.content.index(self.second.title.encode()),
            preview.content.index(self.first.title.encode()),
        )
        self.assertEqual(
            Client().get(reverse("cobweb:public-group", args=[self.site.pk, group.pk])).status_code,
            404,
        )

    def test_plain_group_route_also_rejects_text_only_slide(self):
        url = reverse("cobweb:group-create", args=[self.site.pk])
        response = self.client.post(url, self.payload(members=[self.text_only.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertIn("members", response.context["members_form"].errors)
        self.assertFalse(CobwebGroup.objects.exists())
        self.assertFalse(AuditEvent.objects.filter(action="cobweb.group.create").exists())

    def test_rejects_foreign_category_or_presentation(self):
        for field, field_key in (
            ("category", "cobweb_group.category"),
            ("presentation", "cobweb_group.presentation"),
        ):
            with self.subTest(field=field):
                foreign = self.reference(self.other, field_key, "slideshow")
                response = self.client.post(self.create_url(), self.payload(**{field: foreign.pk}))
                self.assertEqual(response.status_code, 200)
                self.assertIn(field, response.context["group_form"].errors)
                self.assertFalse(CobwebGroup.objects.exists())

    def test_creation_requires_group_permission_and_access_to_site(self):
        permission = Permission.objects.get(
            content_type__app_label="cobweb", codename="add_cobwebgroup"
        )
        self.role.permissions.remove(permission)
        self.assertEqual(self.client.post(self.create_url(), self.payload()).status_code, 404)
        self.role.permissions.add(permission)
        self.assertEqual(
            self.client.post(
                reverse("cobweb:group-create", args=[self.other.pk]), self.payload()
            ).status_code,
            404,
        )
        self.assertFalse(CobwebGroup.objects.exists())

    def test_audit_failure_rolls_back_group_members_and_content_record(self):
        with patch(
            "apps.cobweb.views.AuditEvent.objects.create",
            side_effect=RuntimeError("audit indisponible"),
        ):
            with self.assertRaisesMessage(RuntimeError, "audit indisponible"):
                self.client.post(self.create_url(), self.payload())
        self.assertFalse(CobwebGroup.objects.exists())
        self.assertFalse(CobwebGroupMember.objects.exists())
        self.assertFalse(ContentRecord.objects.filter(group__isnull=False).exists())

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import UserProfile
from apps.configuration.models import LookupEntry, LookupList
from apps.tenancy.models import Role, Site, SiteMembership

from .models import CobwebGroup, CobwebGroupMember, CobwebItem


class CobwebGroupTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(username="group-editor")
        UserProfile.objects.create(user=cls.user, first_run_completed_at=timezone.now())
        cls.site = Site.objects.create(code="group-site", name="Groupe Site")
        cls.other = Site.objects.create(code="group-other", name="Autre Site")
        cls.role = Role.objects.create(code="group-editor", name="Éditeur")
        cls.role.permissions.set(
            Permission.objects.filter(
                content_type__app_label="cobweb",
                content_type__model__in=["cobwebitem", "cobwebgroup"],
            )
        )
        SiteMembership.objects.create(user=cls.user, site=cls.site, role=cls.role)
        cls.item_category = cls.category_for(cls.site, "cobweb_item.category", "items")
        cls.group_category = cls.category_for(cls.site, "cobweb_group.category", "groups")
        cls.other_category = cls.category_for(cls.other, "cobweb_item.category", "other")
        cls.parent = LookupEntry.objects.create(
            lookup_list=cls.item_category, value="root", label="Racine"
        )
        cls.child = LookupEntry.objects.create(
            lookup_list=cls.item_category, value="child", label="Enfant", parent=cls.parent
        )
        cls.group_entry = LookupEntry.objects.create(
            lookup_list=cls.group_category, value="homepage", label="Accueil"
        )
        cls.other_entry = LookupEntry.objects.create(
            lookup_list=cls.other_category, value="secret", label="Secret"
        )
        cls.item_a = CobwebItem.objects.create(site=cls.site, category=cls.child, title="Entrée A")
        cls.item_b = CobwebItem.objects.create(site=cls.site, category=cls.child, title="Entrée B")
        cls.other_item = CobwebItem.objects.create(
            site=cls.other, category=cls.other_entry, title="Secret"
        )

    @classmethod
    def category_for(cls, site, field_key, code):
        return LookupList.objects.create(
            site=site,
            module="cobweb",
            kind="category",
            field_key=field_key,
            code=code,
            name=code,
        )

    def setUp(self):
        self.client.force_login(self.user)

    def group_url(self, action, group=None, site=None):
        kwargs = {"site_id": (site or self.site).pk}
        if group:
            kwargs["pk"] = group.pk
        return reverse(f"cobweb:{action}", kwargs=kwargs)

    def payload(self, **overrides):
        return {
            "title": "Composition accueil",
            "category": self.group_entry.pk,
            "content": "Contenu de groupe",
            "position": 2,
            "is_active": "on",
            "members": [self.item_a.pk, self.item_b.pk],
            f"member_position_{self.item_a.pk}": "3",
            f"member_position_{self.item_b.pk}": "1",
            **overrides,
        }

    def test_group_crud_membership_order_and_audit(self):
        response = self.client.post(self.group_url("group-create"), self.payload())
        self.assertEqual(response.status_code, 302)
        group = CobwebGroup.objects.get(title="Composition accueil")
        self.assertEqual(
            list(group.members.order_by("position").values_list("item_id", flat=True)),
            [self.item_b.pk, self.item_a.pk],
        )
        self.assertContains(self.client.get(self.group_url("group-list")), "Composition accueil")
        response = self.client.post(
            self.group_url("group-edit", group),
            self.payload(
                title="Composition modifiée",
                members=[self.item_a.pk],
                **{
                    f"member_position_{self.item_a.pk}": "4",
                    "version": group.updated_at.isoformat(),
                },
            ),
        )
        self.assertEqual(response.status_code, 302)
        group.refresh_from_db()
        self.assertEqual(group.title, "Composition modifiée")
        self.assertEqual(list(group.members.values_list("item_id", flat=True)), [self.item_a.pk])

    def test_group_rejects_foreign_site_member_and_category(self):
        response = self.client.post(
            self.group_url("group-create"), self.payload(members=[self.other_item.pk])
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["members_form"].errors)
        self.assertFalse(CobwebGroup.objects.exists())
        group = CobwebGroup(site=self.site, category=self.other_entry, title="Invalid")
        with self.assertRaises(ValidationError):
            group.full_clean()

    def test_group_permission_and_site_isolation(self):
        self.assertEqual(
            self.client.get(self.group_url("group-list", site=self.other)).status_code, 404
        )
        permission = Permission.objects.get(
            content_type__app_label="cobweb", codename="add_cobwebgroup"
        )
        self.role.permissions.remove(permission)
        self.assertEqual(
            self.client.post(self.group_url("group-create"), self.payload()).status_code, 404
        )
        self.role.permissions.add(permission)

    def test_group_member_model_rejects_cross_site_association(self):
        category = LookupEntry.objects.create(
            lookup_list=self.group_category, value="validation", label="Validation"
        )
        group = CobwebGroup.objects.create(site=self.site, category=category, title="Valid")
        member = CobwebGroupMember(group=group, item=self.other_item)
        with self.assertRaises(ValidationError):
            member.full_clean()

    def test_item_category_tree_is_rendered(self):
        response = self.client.get(reverse("cobweb:create", kwargs={"site_id": self.site.pk}))
        self.assertContains(response, "Racine")
        self.assertContains(response, "Enfant")
        self.assertContains(response, "tree-children")

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import UserProfile
from apps.configuration.forms import LookupEntryForm, LookupEntryFormSet
from apps.configuration.models import LookupEntry, LookupList
from apps.tenancy.models import Role, Site, SiteMembership

from .forms import AddressForm, EntryForm, ExtraInfoForm
from .models import Address, Entry, EntryType, ExtraInfo
from .seeds import seed_addressbook_references


class ConditionalAddressbookReferenceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.site = Site.objects.create(code="addressbook-conditions", name="Carnet de test")
        seed_addressbook_references(cls.site)
        cls.types = {
            kind: EntryType.objects.get(site=cls.site, code=kind)
            for kind in ("person", "company", "association")
        }

    def reference(self, field_key, value):
        return LookupEntry.objects.get(
            lookup_list__site=self.site,
            lookup_list__field_key=field_key,
            value=value,
        )

    def form(self, kind, **values):
        return EntryForm(
            data={
                "kind": kind,
                "entry_type": self.types[kind].pk,
                "name": "Fiche de test",
                "is_active": "on",
                **values,
            },
            site=self.site,
        )

    def test_installation_seed_is_site_local_idempotent_and_preserves_edits(self):
        madame = self.reference("addressbook_entry.salutation", "madame")
        association = self.reference("addressbook_entry.legal_form", "association-loi-1901")
        self.assertEqual(madame.addressbook_kind, "person")
        self.assertEqual(association.addressbook_kind, "association")
        self.assertEqual(
            set(EntryType.objects.filter(site=self.site).values_list("kind", flat=True)),
            {"person", "company", "association"},
        )
        madame.label = "Madame — personnalisé"
        madame.is_active = False
        madame.save(update_fields=["label", "is_active"])
        self.assertEqual(seed_addressbook_references(self.site), 0)
        madame.refresh_from_db()
        self.assertEqual((madame.label, madame.is_active), ("Madame — personnalisé", False))
        other = Site.objects.create(code="other-conditions", name="Autre site")
        self.assertEqual(seed_addressbook_references(other), 30)
        self.assertFalse(LookupEntry.objects.filter(lookup_list__site=other, pk=madame.pk).exists())

    def test_reference_scope_follows_person_company_and_association(self):
        madame = self.reference("addressbook_entry.salutation", "madame")
        sarl = self.reference("addressbook_entry.legal_form", "sarl")
        association = self.reference("addressbook_entry.legal_form", "association-loi-1901")
        self.assertTrue(self.form("person", salutation=madame.pk).is_valid())
        self.assertTrue(self.form("company", legal_form=sarl.pk).is_valid())
        self.assertTrue(self.form("association", legal_form=association.pk).is_valid())
        self.assertIn("legal_form", self.form("person", legal_form=sarl.pk).errors)
        self.assertIn("salutation", self.form("company", salutation=madame.pk).errors)
        self.assertIn("legal_form", self.form("association", legal_form=sarl.pk).errors)

    def test_form_options_carry_family_for_conditional_editor(self):
        form = EntryForm(site=self.site)
        self.assertIn('data-addressbook-kind="person"', str(form["entry_type"]))
        self.assertIn('data-addressbook-kind="company"', str(form["entry_type"]))
        self.assertIn('data-addressbook-kind="association"', str(form["legal_form"]))

    def test_site_settings_can_edit_scope_only_for_addressbook_references(self):
        madame = self.reference("addressbook_entry.salutation", "madame")
        form = LookupEntryForm(instance=madame)
        self.assertFalse(form.fields["addressbook_kind"].disabled)
        public_list = LookupList.objects.create(
            site=self.site,
            module="articles",
            kind="choice",
            field_key="articles_article.language",
            code="languages",
            name="Langues",
        )
        public_entry = LookupEntry.objects.create(
            lookup_list=public_list, value="fr", label="Français"
        )
        public_form = LookupEntryForm(instance=public_entry)
        self.assertTrue(public_form.fields["addressbook_kind"].disabled)
        self.assertFalse(
            LookupEntryFormSet(instance=madame.lookup_list)
            .extra_forms[0]
            .fields["addressbook_kind"]
            .disabled
        )
        self.assertTrue(
            LookupEntryFormSet(instance=public_list)
            .extra_forms[0]
            .fields["addressbook_kind"]
            .disabled
        )

    def test_child_types_are_site_local_family_scoped_and_validate_value_format(self):
        person = Entry.objects.create(
            site=self.site,
            kind="person",
            entry_type=self.types["person"],
            name="Martin",
        )
        company = Entry.objects.create(
            site=self.site,
            kind="company",
            entry_type=self.types["company"],
            name="Société test",
        )
        home = self.reference("addressbook_address.address_type", "home")
        home.addressbook_kind = "person"
        home.save(update_fields=["addressbook_kind"])
        address_data = {
            "version": person.updated_at.isoformat(),
            "address_type": home.pk,
            "street": "1 rue de test",
            "city": "Paris",
            "country": "France",
            "is_active": "on",
        }
        self.assertTrue(
            AddressForm(address_data, entry=person, instance=Address(entry=person)).is_valid()
        )
        address_data["version"] = company.updated_at.isoformat()
        self.assertFalse(
            AddressForm(address_data, entry=company, instance=Address(entry=company)).is_valid()
        )
        date_type = self.reference("addressbook_extrainfo.info_type", "date")
        self.assertEqual(date_type.value_kind, "date")
        info_data = {
            "version": person.updated_at.isoformat(),
            "label": "Échéance",
            "info_type": date_type.pk,
            "value": "2026-10-02",
            "is_active": "on",
        }
        self.assertTrue(
            ExtraInfoForm(info_data, entry=person, instance=ExtraInfo(entry=person)).is_valid()
        )
        info_data["value"] = "02/10/2026"
        self.assertIn(
            "value",
            ExtraInfoForm(info_data, entry=person, instance=ExtraInfo(entry=person)).errors,
        )
        date_type.addressbook_kind = "association"
        date_type.save(update_fields=["addressbook_kind"])
        info_data["value"] = "2026-10-02"
        self.assertFalse(
            ExtraInfoForm(info_data, entry=person, instance=ExtraInfo(entry=person)).is_valid()
        )

    def test_extra_type_format_is_configured_only_on_its_reference_list(self):
        date_type = self.reference("addressbook_extrainfo.info_type", "date")
        self.assertFalse(LookupEntryForm(instance=date_type).fields["value_kind"].disabled)
        salutation = self.reference("addressbook_entry.salutation", "madame")
        self.assertTrue(LookupEntryForm(instance=salutation).fields["value_kind"].disabled)
        self.assertTrue(
            LookupEntryFormSet(instance=salutation.lookup_list)
            .extra_forms[0]
            .fields["value_kind"]
            .disabled
        )
        person = Entry.objects.create(
            site=self.site,
            kind="person",
            entry_type=self.types["person"],
            name="Martin",
        )
        ExtraInfo.objects.create(
            entry=person, label="Échéance", info_type=date_type, value="2026-10-02"
        )
        date_type.addressbook_kind = "association"
        with self.assertRaises(ValidationError):
            date_type.full_clean()
        date_type.addressbook_kind = ""
        date_type.value_kind = "text"
        with self.assertRaises(ValidationError):
            date_type.full_clean()

    def test_editor_and_site_settings_expose_the_conditional_choices(self):
        user = get_user_model().objects.create_user(username="addressbook-editor")
        UserProfile.objects.create(user=user, first_run_completed_at=timezone.now())
        role = Role.objects.create(code="addressbook-editor", name="Éditeur du carnet")
        role.permissions.add(
            *Permission.objects.filter(
                content_type__app_label="addressbook",
                codename__in=["view_entry", "add_entry", "change_entry"],
            ),
            Permission.objects.get(
                content_type__app_label="tenancy", codename="manage_site_settings"
            ),
        )
        SiteMembership.objects.create(user=user, site=self.site, role=role)
        self.client.force_login(user)
        create_url = reverse("addressbook:create", args=[self.site.pk])
        page = self.client.get(create_url)
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'data-addressbook-field="person"')
        self.assertContains(page, 'data-addressbook-field="organization"')
        self.assertContains(page, "js/addressbook-form.js")
        self.assertContains(page, 'data-addressbook-kind="association"')
        self.assertContains(page, "module=addressbook")
        salutation = self.reference("addressbook_entry.salutation", "madame")
        saved = self.client.post(
            create_url,
            {
                "kind": "person",
                "entry_type": self.types["person"].pk,
                "name": "Martin",
                "salutation": salutation.pk,
                "is_active": "on",
            },
        )
        self.assertEqual(saved.status_code, 302)
        self.assertEqual(Entry.objects.filter(site=self.site, name="Martin").count(), 1)
        entry = Entry.objects.get(site=self.site, name="Martin")
        address_url = reverse(
            "addressbook:child-create", args=[self.site.pk, entry.pk, "addresses"]
        )
        self.assertContains(self.client.get(address_url), "Domicile")
        address_type = self.reference("addressbook_address.address_type", "home")
        address_saved = self.client.post(
            address_url,
            {
                "version": entry.updated_at.isoformat(),
                "address_type": address_type.pk,
                "street": "1 rue de test",
                "city": "Paris",
                "country": "France",
                "is_active": "on",
            },
        )
        self.assertEqual(address_saved.status_code, 302)
        self.assertEqual(entry.addresses.get().address_type_id, address_type.pk)
        entry.refresh_from_db()
        info_url = reverse("addressbook:child-create", args=[self.site.pk, entry.pk, "extra-info"])
        self.assertContains(self.client.get(info_url), "Date")
        date_type = self.reference("addressbook_extrainfo.info_type", "date")
        bad_info = self.client.post(
            info_url,
            {
                "version": entry.updated_at.isoformat(),
                "label": "Échéance",
                "info_type": date_type.pk,
                "value": "02/10/2026",
                "is_active": "on",
            },
        )
        self.assertEqual(bad_info.status_code, 200)
        self.assertFalse(entry.extra_info.exists())
        saved_info = self.client.post(
            info_url,
            {
                "version": entry.updated_at.isoformat(),
                "label": "Échéance",
                "info_type": date_type.pk,
                "value": "2026-10-02",
                "is_active": "on",
            },
        )
        self.assertEqual(saved_info.status_code, 302)
        self.assertEqual(entry.extra_info.get().info_type_id, date_type.pk)
        settings_page = self.client.get(
            reverse("configuration:lookup-edit", args=[self.site.pk, date_type.lookup_list_id])
        )
        self.assertContains(settings_page, "Catégories d’information privée")
        self.assertContains(settings_page, "value_kind")
        sarl = self.reference("addressbook_entry.legal_form", "sarl")
        rejected = self.client.post(
            create_url,
            {
                "kind": "association",
                "entry_type": self.types["association"].pk,
                "name": "Association de test",
                "legal_form": sarl.pk,
                "is_active": "on",
            },
        )
        self.assertEqual(rejected.status_code, 200)
        self.assertContains(rejected, "bon champ")
        self.assertFalse(Entry.objects.filter(site=self.site, name="Association de test").exists())

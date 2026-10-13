from io import StringIO

from django.core.management import call_command
from django.test import TestCase

from apps.audit.models import AuditEvent
from apps.configuration.models import LookupEntry, LookupList
from apps.configuration.seeds import seed_client_references
from apps.tenancy.models import Site


class ClientReferenceTests(TestCase):
    def setUp(self):
        self.site = Site.objects.create(code="client", name="Client")
        self.other = Site.objects.create(code="other-client", name="Other")

    def test_structural_seed_preserves_edits_and_is_site_scoped(self):
        call_command("seed_structural_data", site_code=self.site.code, stdout=StringIO())
        entry = LookupEntry.objects.get(
            lookup_list__site=self.site,
            lookup_list__field_key="api_client.color_scheme",
            value="dark",
        )
        entry.label = "Nuit"
        entry.is_active = False
        entry.save()
        count = AuditEvent.objects.count()
        self.assertEqual(seed_client_references(self.site), 0)
        self.assertEqual(AuditEvent.objects.count(), count)
        entry.refresh_from_db()
        self.assertEqual(entry.label, "Nuit")
        self.assertFalse(entry.is_active)
        self.assertFalse(LookupList.objects.filter(site=self.other).exists())
        self.assertFalse(LookupList.objects.filter(site_id=1, module="api").exists())

    def test_public_options_only_include_active_client_references_of_this_site(self):
        seed_client_references(self.site)
        seed_client_references(self.other)
        LookupEntry.objects.filter(
            lookup_list__site=self.site,
            value="dark",
        ).update(is_active=False)
        data = self.client.get(f"/api/v1/public/sites/{self.site.pk}/").json()
        self.assertEqual(
            data["frontend_options"]["color_schemes"], [{"value": "light", "label": "Light"}]
        )
        self.assertEqual(
            [row["value"] for row in data["frontend_options"]["templates"]],
            ["bloc", "shape", "custom"],
        )
        LookupList.objects.filter(site=self.site, field_key="api_client.template").update(
            is_active=False,
        )
        self.assertEqual(
            self.client.get(f"/api/v1/public/sites/{self.site.pk}/").json()["frontend_options"][
                "templates"
            ],
            [],
        )
        self.site.public_access_enabled = False
        self.site.save()
        self.assertEqual(self.client.get(f"/api/v1/public/sites/{self.site.pk}/").status_code, 404)

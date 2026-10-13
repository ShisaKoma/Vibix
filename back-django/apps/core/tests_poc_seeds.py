from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings

from apps.articles.models import Article
from apps.audit.models import AuditEvent
from apps.cobweb.models import CobwebGroup, CobwebGroupMember, CobwebItem
from apps.configuration.models import LookupEntry
from apps.core.models import SeedRecord
from apps.core.poc_seeds import PocDataset
from apps.media.models import Asset
from apps.pages.models import Page
from apps.tenancy.models import Site


@override_settings(DEBUG=True)
class PocSeedTests(TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        settings = override_settings(MEDIA_ROOT=self.directory.name)
        settings.enable()
        self.addCleanup(settings.disable)
        self.site = Site.objects.create(code="demo", name="Site fictif POC")
        self.other = Site.objects.create(code="other", name="Autre site fictif")

    def seed(self):
        call_command("seed_poc_demo", site_code="demo", stdout=StringIO())

    def test_structural_setup_is_repeatable_without_published_content(self):
        call_command("seed_structural_data", site_code="demo", stdout=StringIO())
        count = LookupEntry.objects.filter(lookup_list__site=self.site).count()
        audit = AuditEvent.objects.filter(site=self.site).count()
        call_command("seed_structural_data", site_code="demo", stdout=StringIO())
        self.assertEqual(LookupEntry.objects.filter(lookup_list__site=self.site).count(), count)
        self.assertEqual(AuditEvent.objects.filter(site=self.site).count(), audit)
        self.assertFalse(Article.objects.exists())
        self.assertFalse(Page.objects.exists())
        self.assertFalse(CobwebGroup.objects.exists())
        self.assertFalse(Asset.objects.exists())
        self.assertFalse(LookupEntry.objects.filter(lookup_list__site=self.other).exists())

    def test_demo_is_repeatable_and_preserves_editor_changes_and_other_sites(self):
        self.seed()
        group = CobwebGroup.objects.get(site=self.site, presentation__value="slideshow")
        group.title = "Titre personnalisé"
        group.save()
        article = Article.objects.get(site=self.site, reference="POC-V1-ARTICLE-01")
        article.is_online = False
        article.save()
        before = {
            model: model.objects.count()
            for model in (
                Article,
                Page,
                CobwebItem,
                CobwebGroup,
                CobwebGroupMember,
                Asset,
                AuditEvent,
            )
        }
        self.seed()
        self.assertEqual(before, {model: model.objects.count() for model in before})
        group.refresh_from_db()
        article.refresh_from_db()
        self.assertEqual(group.title, "Titre personnalisé")
        self.assertFalse(article.is_online)
        self.assertEqual(CobwebGroup.objects.filter(site=self.site).count(), 3)
        self.assertEqual(Asset.objects.filter(site=self.site).count(), 6)
        self.assertFalse(CobwebItem.objects.filter(site=self.other).exists())

    @override_settings(DEBUG=False)
    def test_demo_refuses_production(self):
        with self.assertRaises(CommandError):
            self.seed()
        self.assertFalse(Article.objects.exists())

    def test_demo_refuses_disabled_publication(self):
        self.site.public_access_enabled = False
        self.site.save()
        with self.assertRaises(CommandError):
            self.seed()
        self.assertFalse(Article.objects.exists())

    def test_demo_refuses_non_demo_site(self):
        with self.assertRaises(CommandError):
            call_command("seed_poc_demo", site_code="other", stdout=StringIO())
        self.assertFalse(Article.objects.exists())

    def test_transaction_failure_cleans_generated_files(self):
        original = PocDataset.asset

        def fail_after_asset(dataset, *args, **kwargs):
            original(dataset, *args, **kwargs)
            raise ValueError("Échec simulé")

        with patch.object(PocDataset, "asset", fail_after_asset):
            with self.assertRaises(CommandError):
                self.seed()
        self.assertFalse(Asset.objects.exists())
        self.assertFalse(AuditEvent.objects.filter(action__startswith="demo.poc.").exists())
        self.assertEqual(list(Path(self.directory.name).rglob("*.png")), [])

    def test_published_groups_members_and_images_are_readable_through_api(self):
        self.seed()
        prefix = f"/api/v1/public/sites/{self.site.pk}/"
        self.assertEqual(self.client.get(prefix + "articles/").json()["count"], 3)
        self.assertEqual(self.client.get(prefix + "pages/").json()["count"], 3)
        groups = self.client.get(prefix + "cobweb/groups/").json()["results"]
        self.assertEqual(len(groups), 3)
        slideshow = next(group for group in groups if group["presentation"] == "slideshow")
        members = self.client.get(slideshow["members_url"]).json()["results"]
        self.assertEqual([member["position"] for member in members], [1, 2, 3])
        self.assertEqual(self.client.get(members[0]["item"]["picture"]["url"]).status_code, 200)
        partner_groups = self.client.get(prefix + "cobweb/groups/?category_code=partners").json()
        self.assertEqual(partner_groups["count"], 1)

    def test_repeat_does_not_depend_on_audit_retention(self):
        self.seed()
        records = SeedRecord.objects.filter(site=self.site).count()
        groups = list(CobwebGroup.objects.filter(site=self.site).values_list("pk", flat=True))
        AuditEvent.objects.filter(site=self.site, action__startswith="demo.poc.").delete()
        self.seed()
        self.assertEqual(SeedRecord.objects.filter(site=self.site).count(), records)
        self.assertEqual(
            list(CobwebGroup.objects.filter(site=self.site).values_list("pk", flat=True)), groups
        )

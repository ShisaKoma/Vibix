from types import SimpleNamespace

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import UserProfile
from apps.articles.models import Article
from apps.cobweb.models import CobwebGroup, CobwebItem
from apps.configuration.models import LookupEntry, LookupList
from apps.pages.models import Page
from apps.tenancy.models import Role, Site, SiteMembership

from .forms import ContentLinksForm, ContentTargetsWidget, content_sections
from .models import ContentLink


class ContentLinksPickerTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(username="links-editor")
        UserProfile.objects.create(user=cls.user, first_run_completed_at=timezone.now())
        cls.site = Site.objects.create(code="links-site", name="Site des liaisons")
        cls.other = Site.objects.create(code="links-other", name="Autre site")
        cls.role = Role.objects.create(code="links-editor", name="Éditeur")
        cls.role.permissions.set(
            Permission.objects.filter(content_type__app_label__in=["cobweb", "articles", "pages"])
        )
        SiteMembership.objects.create(user=cls.user, site=cls.site, role=cls.role)

        def category(site, module, field, value, label):
            lookup, _ = LookupList.objects.get_or_create(
                site=site,
                module=module,
                kind="category",
                code=field,
                defaults={"name": field, "field_key": field},
            )
            return LookupEntry.objects.create(lookup_list=lookup, value=value, label=label)

        cls.page_category = category(cls.site, "pages", "pages_page.category", "pages", "Pages")
        cls.article_category = category(
            cls.site, "articles", "articles_article.category", "articles", "Articles"
        )
        cls.item_category = category(cls.site, "cobweb", "cobweb_item.category", "items", "Entrées")
        cls.media_category = category(
            cls.site, "cobweb", "cobweb_group.category", "medias", "Médias"
        )
        cls.partner_category = category(
            cls.site, "cobweb", "cobweb_group.category", "partners", "Partenaires"
        )
        cls.slide_category = category(
            cls.site, "cobweb", "cobweb_group.category", "slideshow", "Diaporamas"
        )
        presentations = LookupList.objects.create(
            site=cls.site,
            module="cobweb",
            kind="choice",
            code="test-presentations",
            field_key="cobweb_group.presentation",
            name="Présentations",
        )
        cls.slideshow = LookupEntry.objects.create(
            lookup_list=presentations, value="slideshow", label="Diaporama"
        )
        cls.cards = LookupEntry.objects.create(
            lookup_list=presentations, value="cards", label="Cartes"
        )
        cls.page = Page.objects.create(
            site=cls.site, category=cls.page_category, title="Page source", slug="page-source"
        )
        cls.article = Article.objects.create(
            site=cls.site, category=cls.article_category, title="Article accessible"
        )
        cls.item = CobwebItem.objects.create(
            site=cls.site, category=cls.item_category, title="Entrée accessible"
        )
        cls.media = CobwebGroup.objects.create(
            site=cls.site,
            category=cls.media_category,
            title="Galerie image",
            presentation=cls.slideshow,
        )
        cls.partners = CobwebGroup.objects.create(
            site=cls.site,
            category=cls.partner_category,
            title="Partenaires locaux",
            presentation=cls.cards,
        )
        cls.slides = CobwebGroup.objects.create(
            site=cls.site,
            category=cls.slide_category,
            title="Bannière accueil",
            presentation=cls.cards,
        )
        cls.deleted = Article.objects.create(
            site=cls.site,
            category=cls.article_category,
            title="Article en corbeille",
            deleted_at=timezone.now(),
        )
        foreign_category = category(
            cls.other, "cobweb", "cobweb_group.category", "foreign", "Autre site"
        )
        cls.foreign = CobwebGroup.objects.create(
            site=cls.other, category=foreign_category, title="Groupe hors site"
        )

    def form(self, data=None, source=None):
        return ContentLinksForm(
            data, site=self.site, user=self.user, source=source or self.page.content_record
        )

    def test_groups_use_functional_category_instead_of_presentation(self):
        form = self.form()
        sections = form.fields["targets"].widget.sections
        self.assertEqual(
            [section["label"] for section in sections],
            ["Articles", "Entrées Cobweb", "Groupes Cobweb"],
        )
        buckets = {
            group["label"]: [record.pk for record in group["records"]]
            for group in sections[-1]["groups"]
        }
        self.assertEqual(
            buckets,
            {
                "Médias": [self.media.content_record.pk],
                "Partenaires": [self.partners.content_record.pk],
                "Diaporamas": [self.slides.content_record.pk],
            },
        )
        html = str(form["targets"])
        self.assertIn("<legend>Groupes Cobweb</legend>", html)
        self.assertIn("Partenaires", html)
        self.assertNotIn("Groupe hors site", html)
        self.assertNotIn("Article en corbeille", html)
        self.assertNotIn("Page source", html)

    def test_selection_reopens_category_and_persists_links(self):
        form = self.form(
            {"targets": [self.partners.content_record.pk, self.slides.content_record.pk]}
        )
        self.assertTrue(form.is_valid(), form.errors)
        context = form.fields["targets"].widget.get_context(
            "targets", form["targets"].value(), {"id": "id_targets"}
        )
        groups = {group["label"]: group for group in context["widget"]["sections"][-1]["groups"]}
        self.assertTrue(groups["Partenaires"]["selected"])
        self.assertTrue(groups["Diaporamas"]["selected"])
        self.assertFalse(groups["Médias"]["selected"])
        form.save(self.page.content_record)
        self.assertEqual(
            set(self.page.content_record.outgoing_links.values_list("target_id", flat=True)),
            {self.partners.content_record.pk, self.slides.content_record.pk},
        )
        reloaded = self.form()
        self.assertIn("checked", str(reloaded["targets"]))
        self.assertIn('content-links-category" open', str(reloaded["targets"]))

    def test_inaccessible_links_are_not_shown_and_are_preserved(self):
        ContentLink.objects.create(
            source=self.page.content_record, target=self.partners.content_record
        )
        ContentLink.objects.create(
            source=self.page.content_record, target=self.article.content_record
        )
        self.role.permissions.remove(
            Permission.objects.get(content_type__app_label="cobweb", codename="view_cobwebgroup")
        )
        form = self.form({"targets": []})
        self.assertTrue(form.is_valid(), form.errors)
        html = str(form["targets"])
        self.assertNotIn("Groupes Cobweb", html)
        self.assertNotIn("Partenaires locaux", html)
        form.save(self.page.content_record)
        self.assertTrue(
            ContentLink.objects.filter(
                source=self.page.content_record, target=self.partners.content_record
            ).exists()
        )
        self.assertFalse(
            ContentLink.objects.filter(
                source=self.page.content_record, target=self.article.content_record
            ).exists()
        )
        forged = self.form({"targets": [self.partners.content_record.pk]})
        self.assertFalse(forged.is_valid())

    def test_foreign_deleted_and_self_targets_are_rejected(self):
        for record in (
            self.foreign.content_record,
            self.deleted.content_record,
            self.page.content_record,
        ):
            with self.subTest(record=record.pk):
                form = self.form({"targets": [record.pk]})
                self.assertFalse(form.is_valid())
                self.assertIn("targets", form.errors)

    def test_picker_renders_in_all_implemented_editors(self):
        self.client.force_login(self.user)
        for route, record in (
            ("pages:edit", self.page),
            ("articles:edit", self.article),
            ("cobweb:edit", self.item),
            ("cobweb:group-edit", self.media),
        ):
            with self.subTest(route=route):
                response = self.client.get(reverse(route, args=[self.site.pk, record.pk]))
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, "content-links-picker")
                self.assertContains(response, "<legend>Groupes Cobweb</legend>", html=True)
                self.assertContains(response, "Partenaires locaux")
                self.assertNotContains(response, "Groupe hors site")

    def test_empty_picker_and_user_titles_are_escaped(self):
        self.role.permissions.clear()
        form = self.form()
        self.assertIn("Aucun autre contenu accessible", str(form["targets"]))
        self.role.permissions.add(
            Permission.objects.get(content_type__app_label="cobweb", codename="view_cobwebgroup")
        )
        self.partners.title = '<script>alert("x")</script>'
        self.partners.save()
        html = str(self.form()["targets"])
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)


class ContentLinksCategoryFallbackTests(SimpleTestCase):
    def test_uncategorized_group_has_a_selectable_fallback(self):
        # The current model requires category; the display fallback does not relax that rule.
        group = SimpleNamespace(category_id=None, title="Composition non classée")
        record = SimpleNamespace(
            pk=99, page_id=None, article_id=None, item_id=None, group=group, source=group
        )
        widget = ContentTargetsWidget(choices=[(99, group.title)])
        widget.sections = content_sections([record])
        html = widget.render("targets", [99], attrs={"id": "id_targets"})
        self.assertIn("Sans catégorie", html)
        self.assertIn('value="99"', html)
        self.assertIn("checked", html)

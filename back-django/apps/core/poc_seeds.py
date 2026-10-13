"""Explicit, versioned POC dataset. Never executed by schema migrations or startup."""

from collections import Counter
from io import BytesIO

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.management.base import CommandError
from django.db import transaction
from django.utils import timezone
from PIL import Image, ImageDraw, ImageFont

from apps.articles.models import Article
from apps.audit.models import AuditEvent
from apps.cobweb.models import CobwebGroup, CobwebGroupMember, CobwebItem
from apps.cobweb.seeds import seed_cobweb_references
from apps.configuration.models import LookupEntry, LookupList
from apps.configuration.seeds import seed_editorial_references
from apps.media.models import Asset
from apps.pages.models import Page
from apps.tenancy.models import Site

from .models import SeedRecord


class PocDataset:
    """Stable v1 seed keys preserve identities even when editors rename demo objects."""

    def __init__(self, site):
        self.site = site
        self.created = Counter()
        self.files = []

    def existing(self, model, key):
        record = SeedRecord.objects.filter(site=self.site, dataset="poc-v1", key=key).first()
        if record is None:
            return None
        if record.entity_type != model._meta.label_lower:
            raise CommandError("La clé POC désigne un autre type d’objet.")
        item = model.objects.filter(pk=record.entity_id).first()
        if item is not None:
            site_id = item.site_id if hasattr(item, "site_id") else item.group.site_id
            if site_id != self.site.pk:
                raise CommandError("Une clé POC pointe vers un objet d’un autre site.")
        return item

    def record(self, instance, key):
        SeedRecord.objects.update_or_create(
            site=self.site,
            dataset="poc-v1",
            key=key,
            defaults={"entity_type": instance._meta.label_lower, "entity_id": instance.pk},
        )
        AuditEvent.objects.create(
            site=self.site,
            action=f"demo.poc.v1.{key}",
            entity_type=instance._meta.label_lower,
            entity_id=str(instance.pk),
        )
        self.created[instance._meta.label_lower] += 1

    def create(self, model, key, **fields):
        existing = self.existing(model, key)
        if existing is not None:
            return existing
        # Stable references are reserved for the dataset, never adopt unrelated rows.
        if (
            "reference" in fields
            and model.objects.filter(
                site=self.site,
                reference=fields["reference"],
            ).exists()
        ):
            raise CommandError(
                f"La référence {fields['reference']} est déjà utilisée hors du jeu POC."
            )
        instance = model(**fields)
        instance.full_clean()
        instance.save()
        self.record(instance, key)
        return instance

    def reference(self, code, value, label=None, parent=None):
        lookup = LookupList.objects.get(site=self.site, code=code)
        entry, created = LookupEntry.objects.get_or_create(
            lookup_list=lookup,
            value=value,
            defaults={"label": label or value, "parent": parent},
        )
        if not entry.is_active or not lookup.is_active:
            raise CommandError(
                f"La référence {code}/{value} est inactive ; aucune réactivation forcée."
            )
        entry.full_clean()
        if created:
            AuditEvent.objects.create(
                site=self.site,
                action="demo.poc.v1.reference.create",
                entity_type="configuration.lookupentry",
                entity_id=str(entry.pk),
            )
            self.created["configuration.lookupentry"] += 1
        return entry

    def asset(self, key, title, color, caption, *, logo=False):
        existing = self.existing(Asset, key)
        if existing is not None:
            return existing
        size = (360, 200) if logo else (1200, 600)
        image = Image.new("RGB", size, color)
        draw = ImageDraw.Draw(image)
        if logo:
            draw.rounded_rectangle((20, 20, 340, 180), radius=24, fill="#FFFFFF")
            draw.text(
                (180, 100), caption, fill=color, font=ImageFont.load_default(size=52), anchor="mm"
            )
        else:
            draw.ellipse((750, -140, 1370, 480), fill="#EAF0E9")
            draw.polygon([(600, 600), (900, 280), (1200, 600)], fill="#D7E4D2")
            draw.text((70, 160), "VIBIX / POC", fill="white", font=ImageFont.load_default(size=26))
            draw.text((70, 250), caption, fill="white", font=ImageFont.load_default(size=46))
            draw.text(
                (70, 450),
                "Illustration et contenu fictifs",
                fill="white",
                font=ImageFont.load_default(size=22),
            )
        buffer = BytesIO()
        image.save(buffer, "PNG")
        payload = buffer.getvalue()
        asset = Asset(
            site=self.site,
            title=title,
            alt_text=title,
            width=size[0],
            height=size[1],
            byte_size=len(payload),
        )
        asset.image.save("poc.png", ContentFile(payload), save=False)
        self.files.append((asset.image.storage, asset.image.name))
        asset.full_clean()
        asset.save()
        self.record(asset, key)
        return asset

    def populate(self):
        seed_editorial_references(self.site)
        seed_cobweb_references(self.site)
        news = self.reference("starter-articles-categories", "poc-news", "Journal du POC")
        events = self.reference("starter-articles-categories", "poc-events", "Rencontres", news)
        self.reference("starter-articles-categories", "poc-future", "À venir", news)
        info = self.reference("starter-pages-categories", "poc-about", "Le collectif")
        practical = self.reference(
            "starter-pages-categories", "poc-practical", "Informations", info
        )
        nav = self.reference("starter-pages-groups", "poc-navigation", "Navigation du POC")
        nav_child = self.reference("starter-pages-groups", "poc-practical", "Vie pratique", nav)
        language_article = self.reference("starter-articles-languages", "fr")
        language_page = self.reference("starter-pages-languages", "fr")
        slides = [
            self.asset(f"asset.slide{index}", title, color, caption)
            for index, title, color, caption in (
                (1, "[Démo] Les idées prennent vie", "#31594B", "Les idees prennent vie"),
                (2, "[Démo] Notre prochain rendez-vous", "#345E82", "Construire ensemble"),
                (3, "[Démo] Les projets du collectif", "#775033", "Un espace pour les projets"),
            )
        ]
        now = timezone.now()
        for index, title, subtitle, category in (
            (
                1,
                "[Démo] Un nouveau chapitre commence",
                "Découvrez le site et ses premiers projets.",
                news,
            ),
            (
                2,
                "[Démo] Une rencontre pour imaginer la suite",
                "Un atelier fictif ouvert aux idées.",
                events,
            ),
            (
                3,
                "[Démo] Dans les coulisses du collectif",
                "Les nouvelles du projet de démonstration.",
                news,
            ),
        ):
            self.create(
                Article,
                f"article.{index}",
                site=self.site,
                category=category,
                language=language_article,
                reference=f"POC-V1-ARTICLE-{index:02d}",
                title=title,
                subtitle=subtitle,
                in_home=index <= 2,
                position=40 - index,
                content="<p>Contenu fictif du jeu POC ViBix.</p><h2>À découvrir</h2>"
                "<p>Un contenu fictif pour découvrir les rubriques et les images.</p>",
                is_online=True,
                published_at=now,
                picture=slides[index - 1],
                thumbnail=slides[index - 1],
            )
        for index, slug, title, category, group in (
            (1, "poc-presentation", "[Démo] Présentation du collectif", info, nav),
            (2, "poc-mission", "[Démo] Notre mission", info, nav),
            (3, "poc-informations", "[Démo] Informations pratiques", practical, nav_child),
        ):
            self.create(
                Page,
                f"page.{index}",
                site=self.site,
                category=category,
                group=group,
                language=language_page,
                slug=slug,
                reference=f"POC-V1-PAGE-{index:02d}",
                title=title,
                subtitle="Une page fictive pour explorer le POC.",
                position=30 - index,
                content="<p>Page de démonstration sans coordonnées personnelles réelles.</p>"
                "<h2>Notre espace</h2><p>Retrouvez ici les informations du collectif fictif.</p>",
                is_online=True,
                published_at=now,
                picture=slides[0],
                thumbnail=slides[0],
            )
        item_category = self.reference("starter-cobweb-items", "medias")
        image_kind = self.reference("starter-cobweb-item-types", "image")
        slide_items = [
            self.create(
                CobwebItem,
                f"slide.{index}",
                site=self.site,
                category=item_category,
                kind=image_kind,
                title=asset.title,
                reference=f"POC-V1-SLIDE-{index:02d}",
                subtitle="Une scène du jeu de démonstration.",
                content="<p>Illustration fictive du POC.</p>",
                position=index,
                picture=asset,
                thumbnail=asset,
                is_online=True,
            )
            for index, asset in enumerate(slides, 1)
        ]
        partners = []
        for index, name, initials, color in (
            (1, "Atelier Horizon", "AH", "#31594B"),
            (2, "Studio Traverse", "ST", "#345E82"),
            (3, "Collectif Canopée", "CC", "#775033"),
        ):
            asset = self.asset(
                f"asset.partner{index}", f"[Démo] Logo {name}", color, initials, logo=True
            )
            partners.append(
                self.create(
                    CobwebItem,
                    f"partner.{index}",
                    site=self.site,
                    category=self.reference("starter-cobweb-items", "partners"),
                    kind=self.reference("starter-cobweb-item-types", "partner"),
                    title=f"[Démo] {name}",
                    reference=f"POC-V1-PARTNER-{index:02d}",
                    subtitle="Partenaire fictif du POC ViBix.",
                    content="<p>Organisation fictive.</p>",
                    link=f"https://example.com/partenaire-{index}",
                    position=index,
                    picture=asset,
                    thumbnail=asset,
                    is_online=True,
                )
            )
        links = [
            self.create(
                CobwebItem,
                f"link.{index}",
                site=self.site,
                category=self.reference("starter-cobweb-items", "sitemap"),
                kind=self.reference("starter-cobweb-item-types", "link"),
                title=f"[Démo] Ressource {index}",
                reference=f"POC-V1-LINK-{index:02d}",
                subtitle="Un lien fictif de démonstration.",
                link=f"https://example.com/ressource-{index}",
                content="<p>Ressource fictive.</p>",
                position=index,
                is_online=True,
            )
            for index in (1, 2)
        ]
        for key, title, category, presentation, members in (
            (
                "slideshow",
                "[POC v1] Les moments du collectif",
                "slideshow",
                "slideshow",
                slide_items,
            ),
            ("partners", "[POC v1] Nos partenaires", "partners", "cards", partners),
            ("resources", "[POC v1] Ressources utiles", "sitemap", "list", links),
        ):
            group = self.create(
                CobwebGroup,
                f"group.{key}",
                site=self.site,
                category=self.reference("starter-cobweb-groups", category),
                presentation=self.reference("starter-cobweb-presentations", presentation),
                title=title,
                content="<p>Composition fictive du jeu POC ViBix.</p>",
                position={"slideshow": 1, "partners": 2, "resources": 3}[key],
                is_online=True,
            )
            for index, item in enumerate(members, 1):
                self.create(
                    CobwebGroupMember,
                    f"member.{key}.{index}",
                    group=group,
                    item=item,
                    position=index,
                )
        return dict(self.created)


def seed_poc_demo(site):
    dataset = PocDataset(site)
    try:
        with transaction.atomic():
            dataset.site = Site.objects.select_for_update().get(pk=site.pk)
            if (
                not settings.DEBUG
                or dataset.site.code != "demo"
                or dataset.site.pk == 1
                or not dataset.site.is_active
            ):
                raise CommandError("Le jeu POC exige un site demo actif et DEBUG=True.")
            if not all(
                (
                    dataset.site.public_access_enabled,
                    dataset.site.articles_enabled,
                    dataset.site.pages_enabled,
                    dataset.site.cobweb_enabled,
                )
            ):
                raise CommandError(
                    "Les modules et l’exposition publique du site demo sont désactivés."
                )
            return dataset.populate()
    except Exception:
        # Media files are outside the database transaction: compensate only new writes.
        for storage, name in dataset.files:
            storage.delete(name)
        raise

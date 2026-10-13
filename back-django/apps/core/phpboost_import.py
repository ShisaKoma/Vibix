"""Conservative, create-only PHPBoost import for the local development site."""

import hashlib
import json
import re
from collections import Counter
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.management.base import CommandError
from django.db import transaction

from apps.articles.models import Article
from apps.audit.models import AuditEvent
from apps.cobweb.content import clean_html
from apps.cobweb.models import CobwebGroup, CobwebGroupMember, CobwebItem
from apps.configuration.models import LookupEntry, LookupList
from apps.pages.models import Page
from apps.tenancy.models import Site
from apps.tenancy.permissions import has_site_permission

from .models import ImportedRecord

PUBLICATION = {"is_active", "is_online", "is_searchable", "starts_at", "ends_at"}
CONTENT = {"title", "content", "position"}
SPECS = {
    "configuration.lookuplist": (
        LookupList,
        {"module", "kind", "field_key", "code", "name", "is_active"},
        {},
        "tenancy.manage_site_settings",
    ),
    "configuration.lookupentry": (
        LookupEntry,
        {"value", "label", "position", "is_active"},
        {"lookup_list": "configuration.lookuplist", "parent": "configuration.lookupentry"},
        "tenancy.manage_site_settings",
    ),
    "articles.article": (
        Article,
        CONTENT | PUBLICATION | {"in_home"},
        {"category": "configuration.lookupentry", "thumbnail": "media.asset"},
        "articles.add_article",
    ),
    "pages.page": (
        Page,
        CONTENT | PUBLICATION | {"slug"},
        {"category": "configuration.lookupentry", "thumbnail": "media.asset"},
        "pages.add_page",
    ),
    "cobweb.cobwebitem": (
        CobwebItem,
        CONTENT | PUBLICATION | {"link"},
        {
            "category": "configuration.lookupentry",
            "picture": "media.asset",
            "thumbnail": "media.asset",
        },
        "cobweb.add_cobwebitem",
    ),
    "cobweb.cobwebgroup": (
        CobwebGroup,
        CONTENT | {"is_active", "is_online", "is_searchable"},
        {"category": "configuration.lookupentry"},
        "cobweb.add_cobwebgroup",
    ),
    "cobweb.cobwebgroupmember": (
        CobwebGroupMember,
        {"position"},
        {"group": "cobweb.cobwebgroup", "item": "cobweb.cobwebitem"},
        "cobweb.change_cobwebgroup",
    ),
}


def digest(value):
    # Match the collector's PHP JSON flags, including escaped line separators.
    serialized = json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    serialized = serialized.replace("\u2028", r"\u2028").replace("\u2029", r"\u2029")
    return hashlib.sha256(serialized.encode()).hexdigest()


def snapshot(instance):
    values = {
        field.attname: field.value_from_object(instance)
        for field in instance._meta.concrete_fields
        if field.name not in {"created_at", "updated_at"}
    }
    if hasattr(instance, "additional_categories"):
        values["additional_categories"] = sorted(
            instance.additional_categories.values_list("pk", flat=True)
        )
    # Dates are serialized independently from the transport checksum.
    return hashlib.sha256(json.dumps(values, sort_keys=True, default=str).encode()).hexdigest()


def site_id(instance):
    if isinstance(instance, LookupEntry):
        return instance.lookup_list.site_id
    if isinstance(instance, CobwebGroupMember):
        return instance.group.site_id
    return instance.site_id


class PhpboostPackage:
    def __init__(self, directory):
        self.directory = Path(directory)
        manifest_bytes = self.read("manifest.json", 1024 * 1024)
        self.checksum = hashlib.sha256(manifest_bytes).hexdigest()
        self.manifest = json.loads(manifest_bytes)
        manifest = self.manifest
        if (manifest.get("format"), manifest.get("format_version"), manifest.get("collector")) != (
            "vibix-collector",
            1,
            "phpboost",
        ):
            raise CommandError("Contrat de paquet non pris en charge.")
        self.source = manifest["source"]["id"]
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,47}", self.source):
            raise CommandError("Identité source invalide.")
        if manifest["policy"].get("publication") != "draft_only":
            raise CommandError("Le paquet doit proposer exclusivement des brouillons.")
        streams = {}
        expected = {"entities.jsonl", "media.jsonl", "deferred.jsonl"}
        if set(manifest["files"]) != expected:
            raise CommandError("Flux de paquet inattendus.")
        for name in sorted(expected):
            data = self.read(name, 64 * 1024 * 1024)
            receipt = manifest["files"][name]
            if hashlib.sha256(data).hexdigest() != receipt["sha256"]:
                raise CommandError(f"Empreinte invalide : {name}.")
            lines = data.splitlines()
            if len(lines) > 25000 or any(len(line) > 4 * 1024 * 1024 for line in lines):
                raise CommandError("Paquet trop volumineux pour cet import pilote.")
            streams[name] = [json.loads(line) for line in lines]
            if len(lines) != receipt["records"]:
                raise CommandError(f"Comptage invalide : {name}.")
        self.entities = streams["entities.jsonl"]
        self.media = streams["media.jsonl"]
        self.deferred_count = len(streams["deferred.jsonl"])
        self.by_key = {}
        for record in self.entities + self.media:
            key = record["key"]
            if not isinstance(key, str) or len(key) > 200 or not key.startswith(self.source + ":"):
                raise CommandError("Clé source invalide.")
            if key in self.by_key:
                raise CommandError("Clé source dupliquée.")
            self.by_key[key] = record
        for record in self.entities:
            self.validate_record(record)

    def read(self, name, limit):
        path = self.directory / name
        if path.is_symlink() or not path.is_file() or path.stat().st_size > limit:
            raise CommandError(f"Fichier absent, lié ou trop volumineux : {name}.")
        return path.read_bytes()

    def validate_record(self, record):
        unsigned = {key: value for key, value in record.items() if key != "checksum"}
        if digest(unsigned) != record["checksum"]:
            raise CommandError("Empreinte d’objet invalide.")
        label = record["model"]
        if label not in SPECS or record["status"] not in {"ready", "review"}:
            raise CommandError("Modèle non pris en charge ou objet bloqué.")
        model, fields, relations, _ = SPECS[label]
        if not isinstance(record["fields"], dict) or not isinstance(record["relations"], dict):
            raise CommandError("Champs ou relations invalides.")
        if set(record["fields"]) - fields or set(record["relations"]) - relations.keys():
            raise CommandError("Champ ou relation hors contrat.")
        for name, value in record["fields"].items():
            field = model._meta.get_field(name)
            kind = field.get_internal_type()
            if kind == "BooleanField" and type(value) is not bool:
                raise CommandError("Booléen invalide.")
            if kind == "PositiveIntegerField" and (type(value) is not int or value < 0):
                raise CommandError("Entier invalide.")
            if kind in {"CharField", "SlugField", "TextField", "URLField"} and not isinstance(
                value, str
            ):
                raise CommandError("Texte invalide.")
        if "is_online" in fields and record["fields"].get("is_online") is not False:
            raise CommandError("Publication interdite dans cet import.")
        for name, key in record["relations"].items():
            if key is None:
                continue
            target = self.by_key.get(key)
            if target is None or target.get("model", "media.asset") != relations[name]:
                raise CommandError("Relation absente ou de type incompatible.")


def cobweb_choice(site, field_key, value):
    entry = (
        LookupEntry.objects.filter(
            lookup_list__site=site,
            lookup_list__module="cobweb",
            lookup_list__kind="choice",
            lookup_list__field_key=field_key,
            lookup_list__is_active=True,
            is_active=True,
            value=value,
        )
        .order_by("pk")
        .first()
    )
    if entry is None:
        raise CommandError("Référentiel Cobweb de démarrage manquant.")
    return entry


@transaction.atomic
def import_package(package, site, actor, *, apply=False, allow_review=False):
    site = Site.objects.select_for_update().get(pk=site.pk)
    if not settings.DEBUG or site.code != "demo" or site.pk == 1 or not site.is_active:
        raise CommandError("Cet import pilote exige DEBUG=True et le site demo actif.")
    if not Site.objects.for_user(actor).filter(pk=site.pk).exists():
        raise CommandError("Acteur non habilité sur le site cible.")
    for label in {row["model"] for row in package.entities}:
        if not has_site_permission(actor, site, SPECS[label][3]):
            raise CommandError(f"Permission manquante : {SPECS[label][3]}.")
    if not allow_review and any(row["status"] == "review" for row in package.entities):
        raise CommandError("Utiliser --allow-review-drafts pour conserver les éléments à réviser.")
    known = {}
    created = Counter()
    unchanged = Counter()
    local_edits = 0
    receipts = {
        row.key: row for row in ImportedRecord.objects.filter(site=site, source=package.source)
    }
    pending = []
    for row in package.entities:
        receipt = receipts.get(row["key"])
        if receipt is None:
            pending.append(row)
            continue
        if receipt.entity_type != row["model"] or receipt.source_checksum != row["checksum"]:
            raise CommandError("Source modifiée : reprise refusée, arbitrage manuel nécessaire.")
        instance = SPECS[row["model"]][0].objects.filter(pk=receipt.entity_id).first()
        if instance is None or site_id(instance) != site.pk:
            raise CommandError("Correspondance cible absente ou hors site ; reprise refusée.")
        if snapshot(instance) != receipt.target_checksum:
            local_edits += 1
        known[row["key"]] = instance
        unchanged[row["model"]] += 1
    if not apply:
        return {
            "mode": "preview",
            "to_create": dict(Counter(r["model"] for r in pending)),
            "existing_retained": dict(unchanged),
            "local_edits_retained": local_edits,
            "media_references_without_import": len(package.media),
            "deferred": package.deferred_count,
        }
    while pending:
        remaining = []
        for row in pending:
            relations = row["relations"]
            dependencies = [
                key
                for name, key in relations.items()
                if key and SPECS[row["model"]][2][name] != "media.asset"
            ]
            if any(key not in known for key in dependencies):
                remaining.append(row)
                continue
            model = SPECS[row["model"]][0]
            values = dict(row["fields"])
            if "content" in values:
                values["content"] = clean_html(values["content"])
            for name, key in relations.items():
                # SQL has no image bytes. Never create broken Asset records or fetch URLs.
                values[name] = None if not key or name in {"picture", "thumbnail"} else known[key]
            if model not in {LookupEntry, CobwebGroupMember}:
                values["site"] = site
            if model in {Article, Page, CobwebItem}:
                values["reference"] = "PB-" + hashlib.sha256(row["key"].encode()).hexdigest()[:16]
                values["is_online"] = False
                values["is_searchable"] = False
            if model in {Article, Page}:
                values.update(created_by=actor, updated_by=actor)
            if model is CobwebItem:
                kind = "image" if row["source"].get("table") == "gallery" else "link"
                values["kind"] = cobweb_choice(site, "cobweb_item.kind", kind)
            if model is CobwebGroup:
                values["presentation"] = cobweb_choice(site, "cobweb_group.presentation", "cards")
            instance = model(**values)
            try:
                instance.full_clean()
            except ValidationError as error:
                # Field names only: validation messages may include source content.
                names = sorted(error.message_dict) if hasattr(error, "message_dict") else []
                raise CommandError(f"Validation refusée pour {row['key']} : {names}.") from None
            instance.save()
            known[row["key"]] = instance
            ImportedRecord.objects.create(
                site=site,
                source=package.source,
                key=row["key"],
                entity_type=row["model"],
                entity_id=instance.pk,
                source_checksum=row["checksum"],
                target_checksum=snapshot(instance),
                package_checksum=package.checksum,
            )
            AuditEvent.objects.create(
                actor=actor,
                site=site,
                action="import.phpboost.create",
                entity_type=row["model"],
                entity_id=str(instance.pk),
            )
            created[row["model"]] += 1
        if len(remaining) == len(pending):
            raise CommandError("Dépendances cycliques : import intégralement annulé.")
        pending = remaining
    return {
        "mode": "applied",
        "created": dict(created),
        "existing_retained": dict(unchanged),
        "local_edits_retained": local_edits,
        "media_references_without_import": len(package.media),
        "deferred": package.deferred_count,
    }

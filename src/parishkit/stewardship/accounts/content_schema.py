"""Versioned canonical page/email content, with inert bounded placeholders.

Each record ID names one immutable content revision. Replacing a slot removes
its selected ID and adds a new one in the same YAML candidate; historical
configuration versions retain the old content. Opaque legacy references remain
unresolved until their owner is configured and never count as readiness.
"""

import hashlib
import json

from parishkit.config import ConfigError
from parishkit.stewardship.schema_primitives import invalid, typed
from parishkit.stewardship.web.content import prepare_content, validate_template

SCHEMA = "campaign-content-v5"
REQUEST_SCHEMA = "campaign-content-patch-v5"
RECOVERY_SCHEMA = "operator-recovery-content-v5"
PAGE_SLOTS = frozenset(
    {
        "welcome",
        "login_help",
        "pre_start",
        "post_end",
        "census",
        "member_census",
        "ministry",
        "financial",
        "additional",
        "review",
        "thank_you",
        "access_denied",
        "submission_confirmation",
    }
)
EMAIL_SLOTS = frozenset(
    {
        "initial",
        "reminder",
        "confirmation",
        "daily_digest",
        "weekly_digest",
        "critical_alert",
    }
)


def validate_content_records(document):
    """Validate canonical sanitized text and campaign/slot/template relationships."""
    sections = document["sections"]
    campaigns = {row["id"]: row["values"] for row in sections.get("campaigns", [])}
    content, selected = {}, set()
    for record in sections.get("content", []):
        value = record["values"]
        if set(value) != {"campaign_id", "kind", "slot", "subject", "html", "text"}:
            invalid()
        typed(value["campaign_id"], "uuid")
        owner = value["campaign_id"]
        kind, slot = value["kind"], value["slot"]
        if (
            owner not in campaigns
            or type(kind) is not str
            or kind not in {"page", "email"}
            or type(slot) is not str
            or slot not in (PAGE_SLOTS if kind == "page" else EMAIL_SLOTS)
        ):
            invalid()
        identity = (owner, kind, slot)
        if kind == "page" and identity in selected:
            invalid()
        selected.add(identity)
        try:
            prepared = prepare_content(value["html"], text=value["text"])
            if prepared.html != value["html"] or prepared.text != value["text"]:
                invalid()
            validate_template(value["html"])
            validate_template(value["text"])
            if kind == "email":
                if type(value["subject"]) is not str or not value["subject"].strip():
                    invalid()
                validate_template(value["subject"], subject=True)
            elif value["subject"] is not None:
                invalid()
        except (ValueError, TypeError):
            invalid()
        content[record["id"]] = value
    for owner, campaign in campaigns.items():
        for slot, reference in campaign["content_versions"].items():
            value = content.get(reference)
            if value is not None and (
                value["campaign_id"],
                value["kind"],
                value["slot"],
            ) != (owner, "page", slot):
                invalid()
    for schedule in sections.get("schedules", []):
        value = schedule["values"]
        template = content.get(value["template_version"])
        if template is not None and (
            template["campaign_id"],
            template["kind"],
            template["slot"],
            template["subject"],
        ) != (value["campaign_id"], "email", value["kind"], value["subject"]):
            invalid()


def remember_content(document, identities):
    """A revision identity can never acquire new text or a different campaign owner."""
    for record in document["sections"].get("content", []):
        # Retain only fixed-size evidence while the ancestry reader releases
        # batches of potentially large page/email bodies.
        fingerprint = hashlib.sha256(
            json.dumps(
                record["values"],
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode("utf-8")
        ).digest()
        if identities.setdefault(record["id"], fingerprint) != fingerprint:
            raise ConfigError("Content revision identities must remain immutable.")


def validate_content_change(before, after):
    """Replacing/removing a selected revision must reconcile every live reference."""
    identities = {}
    remember_content(before, identities)
    remember_content(after, identities)
    removed = {row["id"] for row in before["sections"].get("content", [])} - {
        row["id"] for row in after["sections"].get("content", [])
    }
    references = {
        reference
        for row in after["sections"].get("campaigns", [])
        for reference in row["values"]["content_versions"].values()
    }
    references.update(
        row["values"]["template_version"]
        for row in after["sections"].get("schedules", [])
    )
    if removed & references:
        invalid()

"""Every System logs entry type has a plain-language explanation."""

import re
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from django.template.loader import render_to_string

from parishkit.stewardship.audit import log_descriptions
from parishkit.stewardship.audit.log_descriptions import (
    DESCRIPTIONS,
    DIRECT_AUDIT_TYPES,
    describe,
)
from parishkit.stewardship.audit.log_rows import (
    LogQuery,
    audit_row,
    log_table,
    operational_row,
    page_context,
)
from parishkit.stewardship.audit.schemas import Action
from parishkit.stewardship.observability import Event

PACKAGE = Path(log_descriptions.__file__).parents[1]
NOW = datetime(2026, 9, 28, 12, tzinfo=UTC)


def test_every_defined_type_has_a_sentence():
    """No Action, Event or directly written audit type falls back to its name."""
    defined = {item.value for item in Action} | {item.value for item in Event}
    missing = sorted((defined | DIRECT_AUDIT_TYPES) - set(DESCRIPTIONS))
    assert not missing, f"Add a DESCRIPTIONS sentence for: {missing}"


def _direct_types_in_source():
    """Audit types written as literals by Python owners and SQL triggers.

    Python owners pass ``event_type="..."``; the Admin session owner passes
    its ending ``reason`` through as the type (``reason="..."`` or
    ``_revoke(row, now, "...")``). SQL triggers insert literals into
    ``stewardship_audit_event``, sometimes schema-qualified as ``public.``;
    there, only snake_case words with at least one underscore are type-shaped,
    which skips column values such as 'parish'. Prefix-built types (a literal
    ending in "_") are covered by ``PREFIXES``.
    """
    found = set()
    for path in PACKAGE.rglob("*.py"):
        found |= set(re.findall(r'event_type="([a-z][a-z0-9_]*)"', path.read_text()))
    sessions = (PACKAGE / "accounts" / "sessions.py").read_text()
    found |= set(re.findall(r'\breason\s*=\s*"([a-z][a-z0-9_]*)"', sessions))
    found |= set(re.findall(r'_revoke\([^)]*"([a-z][a-z0-9_]*)"\)', sessions))
    for path in (PACKAGE / "schema").glob("*.sql"):
        for insert in re.findall(
            r"INSERT INTO (?:public\.)?stewardship_audit_event\b(.{0,700}?);",
            path.read_text(),
            re.S,
        ):
            found |= {
                literal
                for literal in re.findall(r"'([a-z][a-z0-9]*_[a-z0-9_]*)'", insert)
                if not literal.endswith("_")
            }
    return found


def test_directly_written_audit_types_are_registered():
    """A new direct audit type in the source needs a sentence and a registry entry."""
    found = _direct_types_in_source()
    # One of each source shape, so a broken pattern cannot pass vacuously.
    assert {
        "admin_login",
        "configuration_activated",
        "admin_logout",
        "admin_privileges_changed",
        "operator_admin_recovered",
    } <= found
    assert not found - DIRECT_AUDIT_TYPES - set(DESCRIPTIONS)
    assert not found - DIRECT_AUDIT_TYPES - {item.value for item in Action}


def test_prefix_built_types_are_explained_with_their_state():
    """Trigger-built types read as a sentence plus the state in words."""
    assert describe("config_request_applied") == (
        "A settings change request moved to a new step: applied."
    )
    assert describe("campaign_mail_awaiting_delivery").endswith(
        "new step: awaiting delivery."
    )
    # An exact sentence wins over a matching prefix.
    assert (
        describe("campaign_boundary_completed")
        == (DESCRIPTIONS["campaign_boundary_completed"])
    )
    assert describe("brand_new_type") == "Brand new type."


def _audit(kind, context=None):
    """One audit record shaped as the view's query returns it."""
    return audit_row(
        {
            "id": uuid4(),
            "created_at": NOW,
            "event_type": "family_login",
            "actor_id": uuid4(),
            "correlation_id": uuid4(),
            "campaign_reference": None,
            "subject_id": None,
            "auditcontext__context": context,
            "auditcontext__actor_kind": kind,
        }
    )


def test_actor_kind_names_families_system_and_operator():
    """Actors with no portal account are named by the kind the audit recorded."""
    assert str(_audit("family")["actor_kind_label"]).startswith("A Family")
    assert str(_audit("operator")["actor_kind_label"]).startswith("The server")
    # Portal users are named from their account instead; unknown kinds say nothing.
    assert _audit("portal_user")["actor_kind_label"] is None
    assert _audit(None)["actor_kind_label"] is None


def test_detail_fields_are_named_in_words_but_exports_keep_names():
    """The page says "Lag microseconds"; the stored field name is unchanged."""
    row = _audit("system", {"count": 7, "outcome": "succeeded"})
    assert row["details"] == [("count", "7"), ("outcome", "succeeded")]
    assert row["detail_rows"] == [("Count", "7"), ("Outcome", "succeeded")]


def test_page_shows_explanation_actor_kind_and_related_link():
    """The rendered row explains itself, names the actor kind and links related."""
    audit = _audit("family", {"outcome": "succeeded"})
    audit.update(actor=None, actor_worker=False, task_subject=False)
    operational = operational_row(
        {
            "id": uuid4(),
            "created_at": NOW,
            "level": "ERROR",
            "event": "source_retention_skipped",
            "actor_id": None,
            "correlation_id": uuid4(),
            "context": {"outcome": "failed", "retryable": True},
        }
    )
    operational.update(actor=None, actor_worker=False, task_subject=False)
    html = render_to_string(
        "stewardship/logs.html",
        page_context(
            LogQuery(),
            log_table(
                LogQuery(), [operational, audit], through=NOW, action="/admin/logs"
            ),
        ),
    )
    assert "Removing old ParishSoft copies was skipped this time" in html
    assert "A Family signed in to the Family form." in html
    assert "A Family (using their code or link)" in html
    assert "<dt>Retryable</dt>" in html and "<dt>retryable</dt>" not in html
    assert html.count('class="link-button">Show related entries</button>') == 2

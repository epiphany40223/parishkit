"""``send controls``: the Pause and resume mail page's read.

Pure tests: the golden document, built through the projection the command
uses from a stand-in for the page's own status; the catalog entry; the
ledger; and usage errors. The command against a real database is in
database/test_admin_delivery_control_cli_postgresql.py.
"""

import io
import json
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID

import pytest

from parishkit.stewardship import admin_cli, admin_delivery_control
from parishkit.stewardship.admin_parity import LEDGER

from .test_admin_reads import FORBIDDEN, members

CAMPAIGN = UUID("00000000-0000-4000-8000-000000000091")
PAUSED_AT = datetime(2054, 10, 5, 14, 30, tzinfo=UTC)
# Every member name the document may hold, at any depth. ``reason`` is the
# pausing Administrator's own text, which the page shows too, so it alone is
# allowed past the personal-data pattern.
ALLOWED = {
    "campaign_id",
    "available",
    "paused",
    "paused_at",
    "reason",
    "inventory",
    "queued",
    "held",
    "submitting",
    "unknown",
    "stranded",
    "types",
    "next_due",
    "at",
    "count",
    "health",
    "ready",
    "checked_at",
    "expires_at",
    "resume_available",
    "resolve_available",
    "resolution",
    "kind",
    "name",
    "cancellable",
    "release",
    "cancel",
    "clear",
    "family_recovery",
    "selected",
    "coalesced",
    "skipped",
    "blocked",
    "deferred",
    "unmaterialized",
    "report_recovery",
    "slots",
    "prepared",
    "pause_available",
}
PUBLIC = {"reason"}


def counts(**values):
    """One mail type's four counts, zero unless given."""
    return {
        name: values.get(name, 0)
        for name in ("queued", "held", "submitting", "unknown")
    }


class Label:
    """A stand-in lazy translation: text only when converted."""

    def __str__(self):
        """The label's words."""
        return "Submission receipts"


def status(*, paused, available=True):
    """A stand-in for ``delivery_control_commands.controls``'s status.

    It carries the SQL views' fingerprints and the digest impact's per-type
    groups, which the command must never print.
    """
    return {
        "available": available,
        # Paused here means a paused, closed campaign: its held messages are
        # resolved, not resumed.
        "resume_available": False,
        "resolve_available": paused,
        "inventory": {
            "queued": 2,
            "held": 3 if paused else 0,
            "submitting": 1,
            "unknown": 0,
            "stranded": 0,
            # Keyed by mail type, as the SQL view gives them: the command
            # must print the types as values, never as member names.
            "types": {
                "reminder": counts(queued=2, held=3 if paused else 0),
                "receipt": counts(submitting=1),
                "daily_digest": counts(queued=1),
            },
            "fingerprint": "f" * 64,
        },
        "resolution": {
            "types": [
                {
                    "kind": "receipt",
                    "label": Label(),
                    "held": 1,
                    "cancellable": False,
                }
            ],
            "release": False,
            "cancel": False,
            "uncancellable": ["Submission receipts"],
            "clear": False,
        },
        "next_due": {
            "at": "2054-10-06T13:00:00+00:00",
            "count": 2,
            "types": {"reminder": 1, "weekly_digest": 1},
        },
        "health": {
            "ready": False,
            "proof": None,
            "checked_at": None,
            "expires_at": None,
        },
        "family_recovery": {
            "selected": 4,
            "coalesced": 1,
            "skipped": 0,
            "blocked": 0,
            "deferred": 0,
            "unmaterialized": 2,
            "fingerprint": "a" * 64,
        }
        if paused
        else None,
        "digest_recovery": {
            "selected": 1,
            "slots": 2,
            "blocked": 0,
            "unmaterialized": 0,
            "prepared": 2,
            "types": {"daily_digest": {"slots": 2}},
            "fingerprint": "b" * 64,
        }
        if paused
        else None,
    }


def campaign(*, paused):
    """A stand-in current campaign, paused or not."""
    return SimpleNamespace(
        pk=CAMPAIGN,
        delivery_paused=paused,
        paused_at=PAUSED_AT if paused else None,
        pause_reason="Wrong reminder text" if paused else "",
    )


def document(*, paused, available=True):
    """The command's document for a stand-in campaign and status."""
    return admin_delivery_control.controls_model(
        campaign(paused=paused), status(paused=paused, available=available)
    ).to_document()


def test_a_paused_closed_campaign_is_its_golden_document():
    """Counts, choices and the reason; no fingerprint or internal member."""
    result = document(paused=True)
    assert result == {
        "campaign_id": str(CAMPAIGN),
        "available": True,
        "paused": True,
        "paused_at": "2054-10-05T14:30:00+00:00",
        "reason": "Wrong reminder text",
        "inventory": {
            "queued": 2,
            "held": 3,
            "submitting": 1,
            "unknown": 0,
            "stranded": 0,
            "types": [
                {
                    "kind": "daily_digest",
                    "queued": 1,
                    "held": 0,
                    "submitting": 0,
                    "unknown": 0,
                },
                {
                    "kind": "receipt",
                    "queued": 0,
                    "held": 0,
                    "submitting": 1,
                    "unknown": 0,
                },
                {
                    "kind": "reminder",
                    "queued": 2,
                    "held": 3,
                    "submitting": 0,
                    "unknown": 0,
                },
            ],
        },
        "next_due": {
            "at": "2054-10-06T13:00:00+00:00",
            "count": 2,
            "types": [
                {"kind": "reminder", "count": 1},
                {"kind": "weekly_digest", "count": 1},
            ],
        },
        "health": {"ready": False, "checked_at": None, "expires_at": None},
        "pause_available": False,
        "resume_available": False,
        "resolve_available": True,
        "resolution": {
            "types": [
                {
                    "kind": "receipt",
                    "name": "Submission receipts",
                    "held": 1,
                    "cancellable": False,
                }
            ],
            "release": False,
            "cancel": False,
            "clear": False,
        },
        "family_recovery": {
            "selected": 4,
            "coalesced": 1,
            "skipped": 0,
            "blocked": 0,
            "deferred": 0,
            "unmaterialized": 2,
        },
        "report_recovery": {
            "selected": 1,
            "slots": 2,
            "unmaterialized": 0,
            "prepared": 2,
            "blocked": 0,
        },
    }
    assert set(members(result)) == ALLOWED
    for name in ALLOWED - PUBLIC:
        assert FORBIDDEN.search(name) is None, name
    # The document is plain JSON.
    json.dumps(result)


def test_a_running_campaign_prints_no_pause_or_recovery():
    """Not paused: no time, reason or recovery impact, as on the page."""
    result = document(paused=False)
    assert result["paused"] is False
    assert result["paused_at"] is None and result["reason"] is None
    assert result["family_recovery"] is None and result["report_recovery"] is None
    # The page draws no Resolve held messages choices then, and offers Pause.
    assert result["resolution"] is None
    assert result["pause_available"] is True
    assert set(result) == set(admin_delivery_control.DeliveryControls.field_names())


@pytest.mark.parametrize("paused", [False, True])
def test_without_controls_only_the_facts_print(paused):
    """Not a live campaign: the page draws no control, so none is offered and
    no choice or recovery prints; the pause and the counts still do."""
    result = document(paused=paused, available=False)
    assert not result["available"]
    assert not result["pause_available"]
    assert not result["resume_available"] and not result["resolve_available"]
    assert result["resolution"] is None
    assert result["family_recovery"] is None and result["report_recovery"] is None
    assert result["paused"] is paused
    assert (result["reason"] is not None) is paused
    assert result["inventory"]["queued"] == 2


def test_the_catalog_entry():
    """Any session, no state change, no event, prompt or recent sign-in."""
    entry = {item["name"]: item for item in admin_cli.catalog()}["send controls"]
    assert entry["scope"] == "read_only" and not entry["changes_state"]
    assert entry["audit_event"] is None and entry["pr"] == 7
    assert not entry["prompts"] and not entry["fresh_gated"]
    assert {option["name"] for option in entry["options"]} == {
        "--config",
        "--session-stdin",
    }
    assert entry["arguments"] == []
    assert entry["result_fields"] == list(
        admin_delivery_control.DeliveryControls.field_names()
    )


def test_the_ledger_covers_the_page_with_its_changes_owed():
    """The page's read is covered; its previews and confirmation are PR 7b's."""
    entry = LEDGER["delivery_control"]
    assert entry.kind == "command"
    assert entry.detail == ("send controls",)
    assert entry.owed == "PR 7b"


@pytest.mark.parametrize(
    "argv",
    [
        ["send", "controls", "extra"],
        ["send", "controls", "--campaign", str(CAMPAIGN)],
    ],
)
def test_malformed_commands_are_usage_errors(argv):
    """The read takes nothing: the current campaign only, as the page."""
    out = io.StringIO()
    code = admin_cli.main(
        [*argv, "--config", "web.yaml", "--session-stdin"],
        stdin=io.BytesIO(b""),
        stdout=out,
        stderr=out,
    )
    assert code == 2 and json.loads(out.getvalue())["error"]["code"] == "usage"

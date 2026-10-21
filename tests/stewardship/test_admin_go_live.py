"""Unit checks of the Go live page's commands (ADM-11 PR 12, #1809).

The commands' flows run against PostgreSQL in
``database/test_admin_go_live_actions_cli_postgresql.py``; here, the parts
that need no database: the catalog entries, the prompts' words, the
Testing submissions file and when a status watch stops.
"""

import csv
import io
from types import SimpleNamespace
from uuid import uuid4

import pytest
from django.core import signing

from parishkit.stewardship import admin_cli, admin_go_live


def test_the_catalog_entries_follow_the_page():
    """Reads for any session, actions for a full one; the irreversible and
    privileged actions prompt and are fresh-gated; previews record nothing."""
    entries = {entry["name"]: entry for entry in admin_cli.catalog()}
    go_live = {name: entry for name, entry in entries.items() if name[:8] == "go-live "}
    assert {name for name, entry in go_live.items() if entry["pr"] == 12} == {
        "go-live preview",
        "go-live start",
        "go-live status",
        "go-live retry",
        "go-live refresh-again",
        "go-live stop",
        "go-live confirm-preview",
        "go-live confirm",
        "go-live cancel-preview",
        "go-live cancel",
        "go-live families",
    }
    for name in ("go-live status", "go-live families"):
        assert go_live[name]["scope"] == "read_only"
        assert (
            not go_live[name]["changes_state"] and go_live[name]["audit_event"] is None
        )
    for name in ("preview", "confirm-preview", "cancel-preview"):
        entry = go_live[f"go-live {name}"]
        assert entry["scope"] == "full" and not entry["changes_state"], name
        assert entry["audit_event"] is None and not entry["prompts"], name
    for verb in ("start", "confirm", "cancel"):
        entry = go_live[f"go-live {verb}"]
        assert entry["prompts"] and entry["fresh_gated"], verb
        assert entry["audit_event"] == f"admin_cmd_go_live_{verb}"
        assert {"--token", "--yes"} <= {option["name"] for option in entry["options"]}
    for verb in ("refresh-again", "stop"):
        entry = go_live[f"go-live {verb}"]
        assert entry["prompts"] and not entry["fresh_gated"], verb
    retry = go_live["go-live retry"]
    assert retry["changes_state"] and not retry["prompts"]
    assert retry["audit_event"] == "admin_cmd_go_live_retry"
    assert go_live["go-live status"]["watch"]
    assert go_live["go-live families"]["streams"]


def test_the_testing_submissions_file_neutralizes_formulas():
    """Each Family's DUID and name, a name that looks like a formula quoted."""
    body = admin_go_live.families_csv(
        [{"duid": 12, "name": "Smith"}, {"duid": 13, "name": "=HYPERLINK(1)"}]
    )
    rows = list(csv.reader(io.StringIO(body.decode("utf-8"))))
    assert rows == [["duid", "name"], ["12", "Smith"], ["13", "'=HYPERLINK(1)"]]
    assert body.endswith(b"\r\n")


@pytest.mark.parametrize(
    ("state", "stopping", "terminal"),
    [
        ("running", None, False),
        ("ready", None, True),
        ("failed", None, True),
        ("stopped", {"state": "stopping"}, False),
        ("stopped", {"state": "stop_failed"}, True),
    ],
)
def test_a_status_watch_stops_once_nothing_moves_by_itself(state, stopping, terminal):
    """Running, or a stop still running, keeps a watch going."""
    values = dict.fromkeys(admin_go_live.GoLiveStatus.field_names())
    values |= {"state": state, "stopping": stopping}
    assert admin_go_live.GoLiveStatus(**values).terminal is terminal


@pytest.mark.parametrize(
    ("kind", "words"),
    [
        ("cleanup", "Testing data deletion stops at the next safe point."),
        ("links", "The prepared Family links are discarded."),
        (None, "Stop go-live. Testing data already deleted"),
    ],
)
def test_the_stop_prompt_says_what_stops(kind, words):
    """The Stop go-live dialog's sentence for the step's state."""
    (line,) = admin_go_live.stop_prompt_lines(SimpleNamespace(stop_kind=kind))
    assert words in line and line.endswith("Testing work can start again.")


def test_the_confirm_prompt_reads_the_review_binding(monkeypatch):
    """The dialog's question and headline counts; an altered token is invalid.

    The token's owner is checked against the session before the prompt
    (``_own``, which needs the database); here it passes.
    """
    from parishkit.stewardship.accounts.go_live_confirm_views import SALT

    monkeypatch.setattr(admin_go_live, "_own", lambda *args: None)

    counts = {
        "active_families": 40,
        "eligible_families": 30,
        "family_messages": 28,
    }
    binding = {
        "actor": str(uuid4()),
        "request": str(uuid4()),
        "preparation": str(uuid4()),
        "digest": "a" * 64,
        "target": "active",
        "counts": counts,
        "key": str(uuid4()),
    }
    token = signing.Signer(salt=SALT).sign_object(binding)
    lines = admin_go_live.confirm_prompt_lines(None, None, token)
    assert lines[0].startswith("Go live now? The campaign becomes active at once")
    assert "30 of 40" in lines[1] and "at once: 28" in lines[1]
    with pytest.raises(ValueError):
        admin_go_live.confirm_prompt_lines(None, None, token + "x")


def test_the_cancel_token_binds_the_page_review_and_the_reason(monkeypatch):
    """The prompt shows the reason and counts; either part altered is invalid."""
    from parishkit.stewardship.accounts.confirmation_views import SALT

    monkeypatch.setattr(admin_go_live, "_own", lambda *args: None)

    inventory = dict.fromkeys(admin_go_live.CANCEL_COUNTS, 2) | {"fingerprint": "f"}
    review = signing.Signer(salt=SALT).sign_object(
        {
            "actor": str(uuid4()),
            "confirmation": str(uuid4()),
            "inventory": inventory,
            "key": str(uuid4()),
        }
    )
    signer = signing.Signer(salt=admin_go_live.CANCEL_SALT)
    token = signer.sign_object({"review": review, "reason": "Dates moved"})
    lines = admin_go_live.cancel_prompt_lines(None, None, token)
    assert lines[1] == "Reason: Dates moved"
    assert "Unsent messages to cancel: 2." in lines[2]
    assert "fingerprint" not in " ".join(lines)
    with pytest.raises(ValueError):
        admin_go_live.cancel_prompt_lines(None, None, token + "x")
    forged = signer.sign_object({"review": review + "x", "reason": "Dates moved"})
    with pytest.raises(ValueError):
        admin_go_live.cancel_prompt_lines(None, None, forged)

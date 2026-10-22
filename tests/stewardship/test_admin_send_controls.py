"""Pause and resume mail's controls on the command line (ADM-11 PR 7b, #463).

Pure tests: the preview documents built from stand-ins for the page's own
signed bindings (with the fingerprints they carry, which must never print),
the confirmation prompt's lines, the catalog entries and usage errors. The
commands against a real database, through the page's own functions and the
SQL guard, are in database/test_admin_send_controls_cli_postgresql.py.
"""

import io
import json
import pathlib
from uuid import UUID

import pytest

from parishkit.stewardship import admin_cli, admin_delivery_control
from parishkit.stewardship.accounts import delivery_control_commands as commands

from .test_admin_reads import FORBIDDEN, members

CAMPAIGN = UUID("00000000-0000-4000-8000-000000000092")
KEY = "00000000-0000-4000-8000-000000000093"
EXPIRES = "2054-10-05T14:35:00+00:00"
# The members a preview document may hold, at any depth. ``reason`` is the
# Administrator's own text and ``token`` the page's signed binding, which
# the page's hidden form field carries too.
ALLOWED = {
    "action",
    "campaign_id",
    "reason",
    "expires_at",
    "inventory",
    "queued",
    "held",
    "submitting",
    "unknown",
    "stranded",
    "types",
    "kind",
    "plan",
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
    "resolution",
    "decision",
    "coverage",
    "messages",
    "items",
    "daily_ranges",
    "preview",
    "token",
}
PUBLIC = {"reason", "token"}


def counts(**values):
    """One mail type's four counts, zero unless given."""
    return {
        name: values.get(name, 0)
        for name in ("queued", "held", "submitting", "unknown")
    }


def binding(action, selection):
    """A stand-in for the page's signed binding, fingerprints included."""
    return {
        "key": KEY,
        "actor": "00000000-0000-4000-8000-000000000094",
        "campaign": str(CAMPAIGN),
        "action": action,
        "campaign_version": 3,
        "runtime_version": 7,
        "observed": "2054-10-05T14:30:00+00:00",
        "expires": EXPIRES,
        "inventory": {
            "queued": 3,
            "held": 2,
            "submitting": 1,
            "unknown": 0,
            "stranded": 1,
            "types": {
                "reminder": counts(queued=3, held=1, submitting=1),
                "receipt": counts(held=1),
            },
            "fingerprint": "f" * 64,
        },
        "selection": selection,
        "reason": "Wrong reminder text",
    }


FAMILY = {
    "selected": 4,
    "coalesced": 1,
    "skipped": 2,
    "blocked": 0,
    "deferred": 0,
    "unmaterialized": 3,
    "fingerprint": "a" * 64,
}
DIGESTS = {
    "selected": 1,
    "slots": 2,
    "unmaterialized": 0,
    "prepared": 2,
    "blocked": 0,
    "types": {"daily_digest": {"slots": 2}},
    "fingerprint": "b" * 64,
}
HEALTH = {"ready": True, "proof": "c" * 64, "checked_at": None}
INVENTORY = {
    "queued": 3,
    "held": 2,
    "submitting": 1,
    "unknown": 0,
    "stranded": 1,
    "types": [
        {"kind": "receipt", "queued": 0, "held": 1, "submitting": 0, "unknown": 0},
        {"kind": "reminder", "queued": 3, "held": 1, "submitting": 1, "unknown": 0},
    ],
}


def document(value, token="signed"):
    """The preview document the command prints for a stand-in binding."""
    result = admin_delivery_control.preview_model(value, token).to_document()
    assert set(members(result)) <= ALLOWED
    for name in set(members(result)) - PUBLIC:
        assert FORBIDDEN.search(name) is None, name
    # Never a fingerprint, at any depth, and plain JSON.
    assert "f" * 64 not in json.dumps(result)
    return result


def test_a_pause_preview_is_its_golden_document():
    """The bound counts, the reason and the token; nothing a pause lacks."""
    assert document(binding("pause", {})) == {
        "action": "pause",
        "campaign_id": str(CAMPAIGN),
        "reason": "Wrong reminder text",
        "expires_at": EXPIRES,
        "inventory": INVENTORY,
        "plan": None,
        "family_recovery": None,
        "report_recovery": None,
        "resolution": None,
        "preview": {"token": "signed"},
    }


def test_a_resume_preview_prints_the_recovery_plan():
    """The overdue recovery as ``send controls`` prints it, no fingerprint."""
    selection = {
        "plan": "family",
        "health": HEALTH,
        "family": FAMILY,
        "digests": DIGESTS,
    }
    result = document(binding("resume", selection))
    assert result["action"] == "resume" and result["plan"] == "family"
    assert result["family_recovery"] == {
        "selected": 4,
        "coalesced": 1,
        "skipped": 2,
        "blocked": 0,
        "deferred": 0,
        "unmaterialized": 3,
    }
    assert result["report_recovery"] == {
        "selected": 1,
        "slots": 2,
        "unmaterialized": 0,
        "prepared": 2,
        "blocked": 0,
    }
    assert result["resolution"] is None
    assert "c" * 64 not in json.dumps(result)


def test_a_resume_before_the_start_has_no_recovery():
    """Nothing is overdue before the start: only the plan's name."""
    result = document(binding("resume", {"plan": "before_start", "health": HEALTH}))
    assert result["plan"] == "before_start"
    assert result["family_recovery"] is None and result["report_recovery"] is None


def test_a_resolution_preview_prints_its_choice_and_coverage():
    """The decision, the selected types and each type's follow-up coverage."""
    selection = {
        "plan": "closed",
        "decision": "release",
        "types": ["receipt"],
        "health": HEALTH,
        "coverage": {
            "receipt": {
                "messages": 1,
                "items": 2,
                "daily_ranges": 0,
                "fingerprint": "d" * 64,
            }
        },
    }
    result = document(binding("resolve", selection))
    assert result["plan"] is None
    assert result["resolution"] == {
        "decision": "release",
        "types": ["receipt"],
        "coverage": [{"kind": "receipt", "messages": 1, "items": 2, "daily_ranges": 0}],
    }
    assert "d" * 64 not in json.dumps(result)


def prompt(monkeypatch, value):
    """The prompt's lines for a token the page's own parser reads as ``value``."""
    monkeypatch.setattr(commands, "_binding", lambda token: value)
    return admin_delivery_control.confirm_prompt_lines("token")


# The preview panels' cautions, word for word as the page draws them.
PAUSE_CAUTION = (
    "Email may keep going out while you read this. Confirming holds every "
    "unsent live email at that moment and records the counts. If the campaign "
    "settings or your permissions change first, you need a new preview."
)
RESUME_CAUTION = (
    "When you confirm, the test email check, the email counts, the campaign "
    "dates and your sign-in are checked again. If any of them changed, nothing "
    "is released and you need a new preview."
)
RESOLVE_CAUTION = (
    "These counts are checked again when you confirm. Other held types and "
    "emails that may not have arrived stay as they are. The pause ends only "
    "when no email is held or waiting for a decision on whether it arrived."
)


def test_the_prompt_says_what_each_confirmation_does(monkeypatch):
    """The page's preview panel in words: action, reason, counts and caution."""
    assert prompt(monkeypatch, binding("pause", {})) == (
        "Pause live Family email for the current campaign: every unsent "
        "live email is held when you confirm.",
        "Reason: Wrong reminder text",
        "Unsent messages to hold: 3. Still sending: 1. Not sure it arrived: 0.",
        PAUSE_CAUTION,
    )
    resume = binding(
        "resume",
        {"plan": "family", "health": HEALTH, "family": FAMILY, "digests": DIGESTS},
    )
    assert prompt(monkeypatch, resume) == (
        "Resume live Family email for the current campaign.",
        "Reason: Wrong reminder text",
        "Overdue Family emails to send: 4. Duplicate overdue sends to skip: 1. "
        "Overdue sends that no longer apply: 2. Report emails to send: 1.",
        RESUME_CAUTION,
    )
    early = binding("resume", {"plan": "before_start", "health": HEALTH})
    assert prompt(monkeypatch, early) == (
        "Resume live Family email for the current campaign.",
        "Reason: Wrong reminder text",
        "The campaign has not started, so nothing is overdue. Held messages "
        "to release: 2.",
        RESUME_CAUTION,
    )
    cancel = binding(
        "resolve",
        {
            "plan": "closed",
            "decision": "cancel",
            "types": ["receipt", "weekly_digest"],
            "health": None,
            "coverage": {
                "weekly_digest": {
                    "messages": 2,
                    "items": 0,
                    "daily_ranges": 0,
                    "fingerprint": "e" * 64,
                },
                "receipt": {
                    "messages": 1,
                    "items": 3,
                    "daily_ranges": 1,
                    "fingerprint": "d" * 64,
                },
            },
        },
    )
    assert prompt(monkeypatch, cancel) == (
        "Resolve held messages of the closed campaign: cancel receipt, weekly_digest.",
        "Reason: Wrong reminder text",
        "receipt: 1 held messages.",
        "weekly_digest: 0 held messages.",
        "Held invitations and reminders to cancel because the campaign is closed: 1.",
        "receipt: Follow-up items covered: 3; Daily report ranges covered: 1.",
        "weekly_digest: Follow-up items covered: 0; Daily report ranges covered: 0.",
        RESOLVE_CAUTION,
    )


def test_the_prompt_and_the_page_share_their_cautions():
    """The page draws the same constants the prompt prints: no copy to drift."""
    template = (
        pathlib.Path(commands.__file__).parent
        / "templates"
        / "stewardship"
        / "delivery-control.html"
    ).read_text()
    for name in ("pause", "resume", "resolve"):
        assert f"{{{{ cautions.{name} }}}}" in template
    for name in ("items", "daily_ranges"):
        assert f"{{{{ coverage_labels.{name} }}}}" in template
    assert [str(text) for text in commands.CAUTIONS.values()] == [
        PAUSE_CAUTION,
        RESUME_CAUTION,
        RESOLVE_CAUTION,
    ]
    # Nor does the template keep its own copy of any of them.
    for text in (PAUSE_CAUTION, RESUME_CAUTION, RESOLVE_CAUTION):
        assert text not in template


def test_an_altered_token_is_invalid_before_the_prompt(settings):
    """The page's own parser refuses it; the command reports ``invalid``."""
    settings.SECRET_KEY = "x" * 50
    with pytest.raises(ValueError, match="not valid"):
        admin_delivery_control.confirm_prompt_lines("not-a-token")


def test_the_parser_types_are_the_pages():
    """The parser's spelled-out types and decisions are the page's own."""
    assert (
        tuple(sorted(commands.RESOLVABLE_TYPES)) == admin_delivery_control.RESOLVE_TYPES
    )


def test_the_parser_decisions_are_the_pages():
    """Every parser decision passes the page's own ``_closed_selection`` shape
    check, and one outside them does not. Nothing held, so the page refuses
    each choice as out of date before it reads anything."""
    from parishkit.stewardship.storage import StaleRecordError

    current = {"held": 1, "stranded": 0, "submitting": 0, "unknown": 0, "types": {}}
    for decision in admin_delivery_control.RESOLVE_DECISIONS:
        with pytest.raises(StaleRecordError):
            commands._closed_selection(None, current, decision=decision, types=[])
    with pytest.raises(ValueError, match="valid held-message"):
        commands._closed_selection(None, current, decision="hold", types=[])


def test_the_reason_limit_is_the_pages():
    """The command bounds a ``-`` reason by the page's own limit."""
    assert commands.REASON_LIMIT == 1024
    with pytest.raises(ValueError, match="1,024"):
        commands._preview(None, None, None, reason="x" * 1025, action="pause")


def entries():
    """The catalog's entries by name."""
    return {item["name"]: item for item in admin_cli.catalog()}


@pytest.mark.parametrize(
    "name", ["send pause-preview", "send resume-preview", "send resolve-preview"]
)
def test_the_preview_catalog_entries(name):
    """Full scope (the page's preview records activity), no event or prompt."""
    entry = entries()[name]
    assert entry["scope"] == "full" and not entry["changes_state"]
    assert entry["audit_event"] is None and entry["pr"] == 7
    assert not entry["prompts"] and not entry["fresh_gated"]
    options = {option["name"]: option for option in entry["options"]}
    assert options["--reason"]["required"]
    assert entry["result_fields"] == list(
        admin_delivery_control.ControlPreview.field_names()
    )
    if name == "send resolve-preview":
        assert options["--decision"]["choices"] == ["release", "cancel", "clear"]
        # Report emails (#1786); the digests' held messages stay resolvable.
        assert options["--type"]["choices"] == [
            "admin_report",
            "daily_digest",
            "receipt",
            "weekly_digest",
        ]
        assert not options["--type"]["required"]
    else:
        assert set(options) == {"--config", "--session-stdin", "--reason"}


def test_the_confirm_catalog_entry():
    """Full scope, fresh-gated and prompting, with its own described event."""
    entry = entries()["send confirm"]
    assert entry["scope"] == "full" and entry["changes_state"]
    assert entry["fresh_gated"] and entry["prompts"]
    assert entry["audit_event"] == "admin_cmd_send_confirm" and entry["pr"] == 7
    options = {option["name"] for option in entry["options"]}
    assert options == {"--config", "--session-stdin", "--token", "--yes"}
    assert entry["result_fields"] == ["created", "command_id", "action", "status"]


def run(*argv):
    """Run a command line that fails before admission; (exit, document)."""
    out = io.StringIO()
    code = admin_cli.main(
        [*argv, "--config", "web.yaml", "--session-stdin"],
        stdin=io.BytesIO(b""),
        stdout=out,
        stderr=out,
    )
    return code, json.loads(out.getvalue())


@pytest.mark.parametrize(
    "argv",
    [
        ["send", "pause-preview"],
        ["send", "resume-preview", "--reason", "x", "extra"],
        ["send", "resolve-preview", "--reason", "x"],
        ["send", "resolve-preview", "--decision", "hold", "--reason", "x"],
        [
            "send",
            "resolve-preview",
            "--decision",
            "release",
            "--type",
            "initial",
            "--reason",
            "x",
        ],
        ["send", "confirm"],
        # Standard input cannot carry both the token and the answer.
        ["send", "confirm", "--token", "-"],
    ],
)
def test_malformed_commands_are_usage_errors(argv):
    """Exit 2, ``usage``, before anything is read or admitted."""
    code, document = run(*argv)
    assert code == 2 and document["error"]["code"] == "usage", document


def test_a_resolution_choice_the_form_cannot_make_is_a_usage_error(monkeypatch):
    """Release and Cancel take a type, Clear none, as the page's form."""
    calls = []
    monkeypatch.setattr(
        admin_delivery_control, "preview_control", lambda *a, **k: calls.append(k)
    )
    context = {"caller": None, "stdin": io.BytesIO(b"")}
    for decision, types in (("release", None), ("cancel", []), ("clear", ["receipt"])):
        args = type("Args", (), {"decision": decision, "type": types, "reason": "x"})
        with pytest.raises(admin_cli.UsageError):
            admin_cli.send_resolve_preview(args, None, None, context)
    args = type(
        "Args",
        (),
        {"decision": "cancel", "type": ["receipt", "receipt"], "reason": "x"},
    )
    admin_cli.send_resolve_preview(args, None, None, context)
    # A repeated type is sent once, as the page's checkbox is.
    assert calls == [{"reason": "x", "decision": "cancel", "types": ["receipt"]}]

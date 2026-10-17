"""``integration list``, ``show`` and ``status``: the Integrations pages' reads.

Pure tests: the golden documents, built through the projections the commands
use from stand-ins for the pages' own parts; the member allowlist; the
catalog entries; the ledger; and usage errors. The commands against a real
database are in database/test_admin_integrations_cli_postgresql.py.
"""

import io
import json
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID

import pytest

from parishkit.stewardship import admin_cli, admin_integrations
from parishkit.stewardship.accounts.backup_destination import (
    OffsiteStatus,
    ProbeStatus,
)
from parishkit.stewardship.accounts.integration_credentials import CredentialSummary
from parishkit.stewardship.admin_parity import LEDGER

from .test_admin_reads import FORBIDDEN, members

DIGEST = "d" * 64
REQUEST = UUID("00000000-0000-4000-8000-000000000463")
AT = datetime(2054, 10, 5, 14, 30, tzinfo=UTC)
# Every member name the documents may hold, at any depth, outside the stored
# settings (checked below) and the refresh facts (``refresh status``'s).
ALLOWED = {
    "version",
    "integrations",
    "target",
    "name",
    "configured",
    "removable",
    "settings",
    "refresh_schedule",
    "key",
    "state",
    "at",
    "message",
    "request_id",
    "source",
    "offsite_backup",
    "last_copy_at",
    "set_name",
    "probe",
    "folder_url",
    "requested_at",
    "saved_after",
    "pending",
}
# The stored public settings: the parish's own mailbox and addresses, which
# the page shows too, are the only names allowed past the personal-data
# pattern.
SETTINGS = {
    "organization_id",
    "delegated_email",
    "sender",
    "sender_name",
    "reply_to",
    "channel_id",
}
PUBLIC = {"delegated_email"}


def configuration(*records):
    """A stand-in applied configuration holding the given integration records."""
    return SimpleNamespace(
        active_configuration=SimpleNamespace(
            digest=DIGEST,
            canonical_document={
                "sections": {
                    "integrations": [
                        {
                            "id": index,
                            "values": {
                                "kind": kind,
                                "settings": settings,
                                "credential_fingerprint": "f" * 64,
                            },
                        }
                        for index, (kind, settings) in enumerate(records, 1)
                    ]
                }
            },
        )
    )


def refresh():
    """A stand-in ``full_refresh_status`` result, every fact empty."""
    return SimpleNamespace(
        succeeded_at=AT,
        failed_at=None,
        failed_task_id=None,
        running=False,
        delta_succeeded_at=None,
        delta_failed_at=None,
        frequency="nightly",
        next_full_at=None,
        delta_refresh="off",
        full_started_at=AT,
        data_as_of=AT,
        connection=None,
        overdue_at=None,
        out_of_date=False,
        late_minutes=None,
        held_for_send=False,
        resume_at=None,
        catching_up=False,
    )


def document(model):
    """A read's document as it prints, through JSON."""
    return json.loads(json.dumps(model.to_document()))


def test_the_google_workspace_page_is_its_golden_document():
    """Its settings and key line print; no fingerprint, refresh or backup."""
    config = configuration(("google_workspace", {"delegated_email": "mail@x.org"}))
    key = CredentialSummary("updated", AT, "Key updated.", REQUEST)
    model = admin_integrations.show_model(
        config, "google_workspace", key=key, refresh=None, backup=None
    )
    assert document(model) == {
        "version": DIGEST,
        "target": "google_workspace",
        "name": "Google Workspace mail",
        "configured": True,
        "removable": False,
        "settings": {"delegated_email": "mail@x.org"},
        "refresh_schedule": None,
        "key": {
            "state": "updated",
            "at": AT.isoformat(),
            "message": "Key updated.",
            "request_id": str(REQUEST),
        },
        "source": None,
        "offsite_backup": None,
        "probe": None,
    }
    assert "f" * 64 not in json.dumps(document(model))


def test_an_optional_integration_not_set_up_prints_empty_settings():
    """Slack before setup: not configured, nothing to remove, no key line."""
    model = admin_integrations.show_model(
        configuration(), "slack", key=None, refresh=None, backup=None
    )
    result = document(model)
    assert result["configured"] is False and result["removable"] is False
    assert result["settings"] == {} and result["key"] is None


def test_parishsoft_prints_the_refresh_facts_of_refresh_status():
    """The page's full refresh line, as ``refresh status``'s ``source``."""
    from parishkit.stewardship.admin_refresh import refresh_fields

    status = refresh()
    model = admin_integrations.show_model(
        configuration(("parishsoft", {"organization_id": "12"})),
        "parishsoft",
        key=None,
        refresh=status,
        backup=None,
    )
    assert model.source == refresh_fields(status)
    assert document(model)["settings"] == {"organization_id": "12"}
    # An existing schedule (the defaults) as the editor shows it (#632).
    assert document(model)["refresh_schedule"] == {
        "rules": [
            {"kind": "full", "at": "02:00"},
            {"kind": "quick", "every": 15, "from": "00:00", "to": "23:45"},
        ],
        "skips": [],
        "skip_around_family_emails": False,
    }


def test_the_backup_page_prints_its_copy_and_check():
    """The off-site copy status and the Administrator's latest check."""
    link = "https://drive.google.com/drive/folders/abc"
    offsite = OffsiteStatus("uploaded", "Copied.", AT, AT, "set-1")
    probe = ProbeStatus("pending", "Checking…", AT, link, AT, "abc", False)
    backup = admin_integrations.backup_documents({"offsite": offsite, "probe": probe})
    model = admin_integrations.show_model(
        configuration(("backup", {"target": link})),
        "backup",
        key=None,
        refresh=None,
        backup=backup,
    )
    result = document(model)
    assert result["removable"] is True
    assert result["offsite_backup"] == {
        "state": "uploaded",
        "message": "Copied.",
        "at": AT.isoformat(),
        "last_copy_at": AT.isoformat(),
        "set_name": "set-1",
    }
    # The folder ID is the link's; only the link prints, as on the page.
    assert result["probe"] == {
        "state": "pending",
        "message": "Checking…",
        "at": AT.isoformat(),
        "folder_url": link,
        "requested_at": AT.isoformat(),
        "saved_after": False,
    }


def test_status_is_terminal_once_nothing_is_pending():
    """A watch stops when the key change or folder check has finished."""
    running = admin_integrations.IntegrationStatus("slack", True, None, None, None)
    done = admin_integrations.IntegrationStatus("slack", False, None, None, None)
    assert not running.terminal and done.terminal


def test_every_member_name_is_allowed():
    """No member name says email, code, token, fingerprint and the like."""
    link = "https://drive.google.com/drive/folders/abc"
    backup = admin_integrations.backup_documents(
        {
            "offsite": OffsiteStatus("none", "None yet.", None, None, None),
            "probe": ProbeStatus("failed", "No.", AT, link, AT, "abc", True),
        }
    )
    key = CredentialSummary("pending", AT, "Checking.", REQUEST)
    documents = [
        document(
            admin_integrations.show_model(
                configuration(("backup", {"target": link})),
                "backup",
                key=key,
                refresh=None,
                backup=backup,
            )
        ),
        document(
            admin_integrations.IntegrationStatus(
                "slack", True, admin_integrations.key_document(key), None, None
            )
        ),
        document(
            admin_integrations.IntegrationList(
                DIGEST, [{"target": "slack", "name": "Slack", "configured": False}]
            )
        ),
    ]
    for result in documents:
        names = set(members({k: v for k, v in result.items() if k != "settings"}))
        assert names <= ALLOWED, names - ALLOWED
    for name in ALLOWED | SETTINGS - PUBLIC:
        assert FORBIDDEN.search(name) is None, name


def test_the_parser_targets_match_the_module():
    """The parser's spelled-out targets are the commands' own."""
    assert admin_cli.INTEGRATION_SHOW_TARGETS == admin_integrations.SHOW_TARGETS
    assert admin_cli.INTEGRATION_STATUS_TARGETS == admin_integrations.STATUS_TARGETS


@pytest.mark.parametrize(
    ("name", "model", "arguments", "watch"),
    [
        ("integration list", admin_integrations.IntegrationList, [], False),
        ("integration show", admin_integrations.IntegrationShow, ["TARGET"], False),
        (
            "integration status",
            admin_integrations.IntegrationStatus,
            ["TARGET"],
            True,
        ),
    ],
)
def test_the_catalog_entries(name, model, arguments, watch):
    """Any session, no state change, no event, prompt or recent sign-in."""
    entry = {item["name"]: item for item in admin_cli.catalog()}[name]
    assert entry["scope"] == "read_only" and not entry["changes_state"]
    assert entry["audit_event"] is None and entry["pr"] == 10
    assert not entry["prompts"] and not entry["fresh_gated"]
    assert entry["arguments"] == arguments
    assert entry["watch"] is watch
    assert entry["result_fields"] == list(model.field_names())


def test_the_ledger_covers_the_pages_with_their_changes_owed():
    """The reads are covered, and with them every change the pages make."""
    assert LEDGER["integrations"].detail == ("integration list",)
    assert LEDGER["integration_status"].detail == ("integration status",)
    settings = LEDGER["integration_settings"]
    assert settings.kind == "command" and settings.owed is None
    assert settings.detail[0] == "integration show"
    assert LEDGER["credential_status"].detail == ("integration key status",)


@pytest.mark.parametrize(
    "argv",
    [
        ["integration", "list", "slack"],
        ["integration", "show"],
        ["integration", "show", "backup_key"],
        ["integration", "show", "nowhere"],
        ["integration", "status", "email"],
        ["integration", "status"],
    ],
)
def test_malformed_commands_are_usage_errors(argv):
    """An extra argument, a missing or unknown target is a usage error."""
    out = io.StringIO()
    code = admin_cli.main(
        [*argv, "--config", "web.yaml", "--session-stdin"],
        stdin=io.BytesIO(b""),
        stdout=out,
        stderr=out,
    )
    assert code == 2 and json.loads(out.getvalue())["error"]["code"] == "usage"


def test_a_required_integration_not_set_up_is_not_available(monkeypatch):
    """Outgoing email addresses with no record: the page's 404 (a
    ``LookupError``) is ``not_available``, reported after the recheck."""
    import contextlib

    from parishkit.stewardship.accounts import admin_editing
    from parishkit.stewardship.admin_reads import NotAvailable
    from parishkit.stewardship.campaigns import work_locks

    with pytest.raises(LookupError):
        admin_integrations.show_model(
            configuration(), "email", key=None, refresh=None, backup=None
        )
    calls = []
    monkeypatch.setattr(admin_integrations, "_admit", lambda *a: calls.append("admit"))
    monkeypatch.setattr(
        admin_integrations, "_recheck", lambda *a: calls.append("recheck")
    )
    monkeypatch.setattr(admin_integrations, "_held", lambda step: step())
    monkeypatch.setattr(work_locks, "read_transaction", contextlib.nullcontext)
    monkeypatch.setattr(
        admin_editing, "editable_configuration", lambda service: configuration()
    )
    service = SimpleNamespace(store=None)
    with pytest.raises(NotAvailable):
        admin_integrations.read_integration_show(None, service, "email")
    assert calls == ["admit", "recheck"]


def test_a_watch_timeout_names_the_command_and_no_record(caplog):
    """``integration status`` follows a target, not a record with a UUID, so
    its watch names no subject (the log admits only a UUID there); its
    timeout line still names the command."""
    import logging

    from parishkit.stewardship.observability import Event, emit

    spec = admin_cli.BY_NAME["integration status"]
    assert spec.watch and spec.watch_subject is None
    caplog.set_level(logging.WARNING, logger="parishkit.stewardship")
    emit(
        Event.TASK_TIMED_OUT,
        level=logging.WARNING,
        timeout="automation_watch",
        limit_seconds=2,
        elapsed_seconds=3,
        watched_command=spec.name,
        subject_id=None,
    )
    assert caplog.records[-1].levelno == logging.WARNING


# ``integration schedule-preview`` (#632 slice 5a): the page's live check.
ZONE = "America/New_York"
NOW = datetime(2054, 10, 9, 16, tzinfo=UTC)
STORED = {
    "organization_id": "1",
    "full_refresh": "daily",
    "nightly_time": "00:00",
    "full_refresh_times": ["00:00", "12:00"],
    "delta_refresh": "off",
}
# Every member name of the preview's document, at any depth.
PREVIEW_ALLOWED = {
    "version",
    "timezone",
    "proposed",
    "changed",
    "schedule",
    "rules",
    "skips",
    "skip_around_family_emails",
    "kind",
    "at",
    "every",
    "from",
    "to",
    "problems",
    "field",
    "message",
    "blocking",
    "status",
    "preview",
    "days",
    "day",
    "weekday",
    "today",
    "change",
    "counts",
    "times",
    "text",
    "struck",
    "due_at",
    "line",
    "summary",
    "warning",
    "freshness",
    "estimated_windows",
}


def schedule_preview(posted=None):
    """The command's document for ``posted`` rules (else the stored schedule).

    Built as the command builds it: the page's editor, its ``editor_view``
    over a database-free preview, then ``schedule_model``.
    """
    from parishkit.stewardship.accounts.refresh_schedule_forms import (
        ScheduleEditor,
        editor_view,
    )
    from parishkit.stewardship.admin_integration_changes import schedule_rows

    from .test_refresh_schedule_editor import reader

    editor = ScheduleEditor(stored=STORED)
    if posted is not None:
        editor = ScheduleEditor(schedule_rows(posted, editor), stored=STORED)
    view = editor_view(editor, timezone=ZONE, now=NOW, preview_reader=reader)
    model = admin_integrations.schedule_model(
        configuration(), view, proposed=posted is not None
    )
    return document(model), view


def test_the_applied_schedule_is_the_pages_preview():
    """No option: the stored schedule as the page shows it, its seven days
    and summary in the page's words, without the grid's cells."""
    from parishkit.stewardship.source.refresh_rules import converted_rules

    result, view = schedule_preview()
    assert result["version"] == DIGEST and result["timezone"] == ZONE
    assert not result["proposed"] and not result["changed"]
    assert result["schedule"] == converted_rules(STORED)
    assert result["problems"] == [] and not result["blocking"]
    assert result["status"] == view.status
    preview = result["preview"]
    assert len(preview["days"]) == 7
    assert preview["summary"] == view.preview["summary"]
    assert preview["freshness"] == view.preview["freshness"]
    first = preview["days"][0]
    shown = view.preview["list"]["days"][0]
    assert first["counts"] == shown["summary"]
    # Each line's words as the page draws them: the wall time, then what runs.
    assert [entry["text"] for entry in first["times"]] == [
        f"{entry['wall']} {entry['text']}" for entry in shown["items"]
    ]
    assert "cells" not in first
    assert set(members(result)) <= PREVIEW_ALLOWED, set(members(result))
    # The stored setting's own name, which `integration show` prints too.
    for name in PREVIEW_ALLOWED - {"skip_around_family_emails"}:
        assert FORBIDDEN.search(name) is None, name


def test_a_proposed_schedule_reports_the_pages_problems_without_refusing():
    """Two full refreshes 5 minutes apart: the page's problem at its row,
    blocking a save of this changed schedule; the preview still shows."""
    posted = {
        "rules": [
            {"kind": "full", "at": "02:00"},
            {"kind": "full", "at": "02:15"},
            {"kind": "full", "at": "02:20"},
        ],
        "skips": [],
    }
    result, view = schedule_preview(posted)
    assert result["proposed"] and result["changed"] and result["blocking"]
    fields = {problem["field"] for problem in result["problems"]}
    assert fields and fields <= {
        f"refresh_schedule.rules[{index}]" for index in range(3)
    }
    assert {problem["message"] for problem in result["problems"]} == {
        problem.text for problem in view.editor.problems()
    }
    assert result["status"] == view.status
    assert set(members(result)) <= PREVIEW_ALLOWED, set(members(result))


def test_a_proposed_schedule_without_problems_previews_its_days():
    """A changed schedule the page would save: no problems, its preview."""
    posted = {"rules": [{"kind": "full", "at": "03:00"}], "skips": []}
    result, _ = schedule_preview(posted)
    assert result["changed"] and not result["blocking"]
    assert result["problems"] == []
    assert result["schedule"]["rules"] == posted["rules"]
    for day in result["preview"]["days"]:
        assert [entry["text"][:5] for entry in day["times"]] == ["03:00"]


def test_a_row_that_cannot_be_read_has_no_preview():
    """A range ending before it starts: no document, nothing to preview."""
    posted = {
        "rules": [{"kind": "full", "every": 60, "from": "18:00", "to": "08:00"}],
        "skips": [],
    }
    result, _ = schedule_preview(posted)
    assert result["schedule"] is None and result["preview"] is None
    assert result["blocking"] and result["problems"]
    assert set(members(result)) <= PREVIEW_ALLOWED, set(members(result))


def stand_ins(monkeypatch, calls, stored=STORED):
    """Replace admission, the snapshot and the configuration for a read."""
    import contextlib

    from parishkit.stewardship.accounts import admin_editing
    from parishkit.stewardship.campaigns import work_locks

    monkeypatch.setattr(admin_integrations, "_admit", lambda *a: calls.append("admit"))
    monkeypatch.setattr(
        admin_integrations, "_recheck", lambda *a: calls.append("recheck")
    )
    monkeypatch.setattr(admin_integrations, "_held", lambda step: step())
    monkeypatch.setattr(work_locks, "read_transaction", contextlib.nullcontext)
    monkeypatch.setattr(
        admin_editing,
        "editable_configuration",
        lambda service: configuration(("parishsoft", stored)),
    )
    return SimpleNamespace(store=None)


def test_an_added_quick_rule_is_refused_after_the_recheck(monkeypatch):
    """The page cannot add a quick update, so neither can the preview: the
    refusal of 5b's ``integration preview``, after the session recheck."""
    from parishkit.stewardship.admin_changes import InvalidChange

    calls = []
    service = stand_ins(monkeypatch, calls)
    text = json.dumps({"rules": [{"kind": "quick", "at": "05:00"}]})
    with pytest.raises(InvalidChange) as refused:
        admin_integrations.read_schedule_preview(None, service, text)
    assert calls == ["admit", "recheck"]
    assert [p["field"] for p in refused.value.fields] == ["refresh_schedule.rules[0]"]


def test_a_malformed_document_is_refused_before_any_read(monkeypatch):
    """Not the stored shape: refused without reading or echoing it."""
    calls = []
    service = stand_ins(monkeypatch, calls)
    with pytest.raises(ValueError, match="not YAML or JSON"):
        admin_integrations.read_schedule_preview(None, service, "{rules: [")
    assert calls == []


def test_parishsoft_not_set_up_is_not_available(monkeypatch):
    """No ParishSoft record: the page's 404, after the recheck."""
    from parishkit.stewardship.accounts import admin_editing
    from parishkit.stewardship.admin_reads import NotAvailable

    calls = []
    stand_ins(monkeypatch, calls)
    monkeypatch.setattr(
        admin_editing, "editable_configuration", lambda service: configuration()
    )
    with pytest.raises(NotAvailable):
        admin_integrations.read_schedule_preview(None, SimpleNamespace(store=None))
    assert calls == ["admit", "recheck"]


def test_the_schedule_preview_catalog_entry_and_ledger():
    """Any session, no state change, no event or prompt; the page's live
    check is covered in the ledger."""
    entry = {item["name"]: item for item in admin_cli.catalog()}[
        "integration schedule-preview"
    ]
    assert entry["scope"] == "read_only" and not entry["changes_state"]
    assert entry["audit_event"] is None and entry["pr"] == 10
    assert not entry["prompts"] and not entry["fresh_gated"]
    assert entry["arguments"] == [] and entry["watch"] is False
    assert entry["result_fields"] == list(
        admin_integrations.SchedulePreview.field_names()
    )
    check = LEDGER["refresh_schedule_check"]
    assert check.kind == "command"
    assert check.detail == ("integration schedule-preview",)


def test_a_schedule_preview_takes_no_target():
    """A target is a usage error: only ParishSoft has a refresh schedule."""
    out = io.StringIO()
    code = admin_cli.main(
        [
            "integration",
            "schedule-preview",
            "parishsoft",
            "--config",
            "web.yaml",
            "--session-stdin",
        ],
        stdin=io.BytesIO(b""),
        stdout=out,
        stderr=out,
    )
    assert code == 2 and json.loads(out.getvalue())["error"]["code"] == "usage"


def test_the_role_is_checked_before_standard_input_is_read(monkeypatch):
    """Staff is denied before ``--refresh-schedule -`` reads anything (#1339)."""
    stdin = io.BytesIO(b'{"rules": []}')

    def denied(context, runtime):
        """The role check, refusing as for Staff."""
        raise PermissionError("Staff")

    monkeypatch.setattr(admin_cli, "_role_first", denied)
    monkeypatch.setattr(
        admin_integrations,
        "read_schedule_preview",
        lambda *a: pytest.fail("read after a refused role"),
    )
    args = SimpleNamespace(refresh_schedule="-")
    with pytest.raises(PermissionError):
        admin_cli.integration_schedule_preview(
            args, None, None, {"caller": None, "stdin": stdin}
        )
    assert stdin.tell() == 0

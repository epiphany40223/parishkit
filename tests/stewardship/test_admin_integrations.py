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
    """The reads are covered; saving and the key steps are still PR 10's."""
    assert LEDGER["integrations"].detail == ("integration list",)
    assert LEDGER["integration_status"].detail == ("integration status",)
    settings = LEDGER["integration_settings"]
    assert settings.kind == "command" and settings.owed == "PR 10"
    assert settings.detail == ("integration show",)
    assert LEDGER["credential_status"].kind == "pending"


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

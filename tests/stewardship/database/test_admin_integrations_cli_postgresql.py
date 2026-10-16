"""``integration list``, ``show`` and ``status``: the Integrations pages' reads.

Process admission is replaced by the test's own Django assembly; everything
after it is the real command line on the restricted web login. The commands
read through the pages' own helpers (``integration_views.integration_list``,
``key_summary`` and ``backup_state``), so a key change staged through the
settings page's own intake, and an access check queued as the page queues
it, read the same from the command line.
"""

from datetime import UTC, timedelta
from uuid import uuid4

import pytest
from django.db import transaction
from django.utils import timezone

from parishkit.stewardship.accounts.backup_destination import request_probe
from parishkit.stewardship.accounts.integration_forms import LABELS
from parishkit.stewardship.accounts.integration_views import key_summary
from parishkit.stewardship.accounts.secret_requests import stage_secret_request
from parishkit.stewardship.audit.models import AuditEvent

from . import campaign_builders
from .automation_builders import as_staff, paired
from .test_admin_digest_list_cli_postgresql import revoke
from .test_admin_test_sample_cli_postgresql import runner

pytestmark = pytest.mark.django_db(transaction=True)
FOLDER = "1AbCdEfGhIjKlMnOpQrStUv"
LINK = f"https://drive.google.com/drive/folders/{FOLDER}"
MAILBOX = "mail@example.org"
READS = [
    ("integration", "list"),
    ("integration", "show", "parishsoft"),
    ("integration", "status", "parishsoft"),
    ("integration", "schedule-preview"),
]


@pytest.fixture
def admin(auth_service, google, monkeypatch):
    """Commands over the authentication service the pages run on."""
    return runner(auth_service, monkeypatch)


@pytest.fixture
def backup(monkeypatch, request):
    """An applied configuration with Workspace mail and the backup folder."""
    original = campaign_builders.configuration_document

    def document():
        """The synthetic configuration, plus the two Google records."""
        value = original()
        value["sections"]["integrations"] += [
            {
                "id": str(uuid4()),
                "values": {
                    "kind": "google_workspace",
                    "settings": {"delegated_email": MAILBOX},
                    "credential_fingerprint": "b" * 64,
                },
            },
            {
                "id": str(uuid4()),
                "values": {
                    "kind": "backup",
                    "settings": {"target": LINK},
                    "credential_fingerprint": None,
                },
            },
        ]
        return value

    monkeypatch.setattr(campaign_builders, "configuration_document", document)
    return request.getfixturevalue("admin")


def read(admin, secret, *argv):
    """Run one read and return its result, which must succeed."""
    code, document, _ = admin(*argv, secret=secret)
    assert code == 0, document
    return document["result"]


def test_the_list_is_the_pages_rows(admin):
    """A read-only session lists every integration; nothing is recorded."""
    _, reader, _ = paired(admin.service, scope="read-only")
    events = AuditEvent.objects.count()
    result = read(admin, reader, "integration", "list")
    assert result["version"] == admin.service.store.active().digest
    assert result["integrations"] == [
        {"target": name, "name": str(label), "configured": name == "parishsoft"}
        for name, label in LABELS.items()
    ]
    assert AuditEvent.objects.count() == events


def test_parishsoft_shows_its_settings_and_refresh_facts(admin):
    """The stored organization, no key change yet, and ``refresh status``'s
    facts; no fingerprint is printed."""
    _, reader, _ = paired(admin.service, scope="read-only")
    events = AuditEvent.objects.count()
    result = read(admin, reader, "integration", "show", "parishsoft")
    assert result["version"] == admin.service.store.active().digest
    assert (result["target"], result["name"]) == ("parishsoft", "ParishSoft")
    assert result["configured"] and not result["removable"]
    assert result["settings"]["organization_id"] == "12345"
    assert result["key"] is None
    assert result["offsite_backup"] is None and result["probe"] is None
    refresh = read(admin, reader, "refresh", "status")["source"]
    assert set(result["source"]) == set(refresh) - {"refreshed_at"}
    assert result["source"]["data_as_of"] == refresh["data_as_of"]
    assert "a" * 64 not in str(result)
    assert AuditEvent.objects.count() == events


def test_an_optional_integration_not_set_up(admin):
    """Slack before setup: nothing configured, nothing to remove, no key."""
    _, reader, _ = paired(admin.service, scope="read-only")
    result = read(admin, reader, "integration", "show", "slack")
    assert not result["configured"] and not result["removable"]
    assert result["settings"] == {} and result["key"] is None
    # Nothing is pending, so a watch stops at its first read.
    status = read(admin, reader, "integration", "status", "slack", "--watch", "2")
    assert status == {
        "target": "slack",
        "pending": False,
        "key": None,
        "offsite_backup": None,
        "probe": None,
    }


def test_a_staged_key_change_reads_pending(admin):
    """The page's key line, the same as the page's own helper reads it."""
    from parishkit.stewardship.accounts.admin_editing import editable_configuration

    _, reader, row = paired(admin.service, scope="read-only")
    request_id = uuid4()
    # The settings page's own intake, with synthetic references: no key.
    stage_secret_request(
        request_id=request_id,
        target="parishsoft",
        staging_reference=uuid4(),
        actor_id=row.principal_id,
        reauthenticated_at=timezone.now() - timedelta(minutes=1),
        expires_at=timezone.now() + timedelta(minutes=10),
        expected_fingerprint="a" * 64,
        correlation_id=uuid4(),
    )
    events = AuditEvent.objects.count()
    key = read(admin, reader, "integration", "show", "parishsoft")["key"]
    with transaction.atomic():
        page = key_summary(editable_configuration(admin.service), "parishsoft")
    assert key == {
        "state": page.kind,
        "at": page.at.astimezone(UTC).isoformat(),
        "message": str(page.message),
        "request_id": str(request_id),
    }
    assert key["state"] == "pending"
    status = read(admin, reader, "integration", "status", "parishsoft")
    assert status["pending"] and status["key"] == key
    assert AuditEvent.objects.count() == events


def test_the_backup_page_shows_its_copy_and_this_administrators_check(backup):
    """The folder link, no copy yet, and the session Administrator's check,
    which another Administrator's does not replace."""
    _, reader, row = paired(backup.service, scope="read-only")
    with transaction.atomic():
        request_probe(uuid4(), "0OtherFolder1234567890", MAILBOX)
    result = read(backup, reader, "integration", "show", "backup")
    assert result["configured"] and result["removable"]
    assert result["settings"] == {"target": LINK}
    assert result["offsite_backup"]["state"] == "none"
    assert result["probe"] is None and result["key"] is None
    with transaction.atomic():
        request_probe(row.principal_id, FOLDER, MAILBOX)
    result = read(backup, reader, "integration", "show", "backup")
    assert result["probe"]["state"] == "pending"
    assert result["probe"]["folder_url"] == LINK
    status = read(backup, reader, "integration", "status", "backup")
    assert status["pending"] and status["probe"] == result["probe"]
    assert status["offsite_backup"] == result["offsite_backup"]
    workspace = read(backup, reader, "integration", "show", "google_workspace")
    assert workspace["settings"] == {"delegated_email": MAILBOX}


@pytest.mark.parametrize("argv", READS)
def test_a_reader_who_is_not_an_administrator_is_denied(admin, monkeypatch, argv):
    """Staff cannot open the Integrations pages: denied, nothing printed."""
    _, secret, _ = paired(admin.service)
    as_staff(monkeypatch)
    code, document, _ = admin(*argv, secret=secret)
    assert code == 1 and document["error"]["code"] == "denied", document
    assert "result" not in document


@pytest.mark.parametrize("argv", READS)
def test_an_ended_session_reads_nothing(admin, argv):
    """A revoked session is exit 5."""
    _, secret, _ = paired(admin.service)
    revoke(admin.service, secret)
    code, document, _ = admin(*argv, secret=secret)
    assert code == 5 and document["error"]["code"] == "session_ended", document
    assert "result" not in document


@pytest.mark.parametrize(
    "argv",
    [
        ("integration", "show", "email"),
        ("integration", "show", "google_workspace"),
        ("integration", "status", "google_workspace"),
    ],
)
def test_a_required_integration_not_set_up_is_not_available(admin, caplog, argv):
    """No email or Workspace record (the page's 404): exit 1 ``not_available``,
    nothing printed and no ERROR logged."""
    import logging

    _, reader, _ = paired(admin.service, scope="read-only")
    caplog.set_level(logging.INFO)
    code, document, _ = admin(*argv, secret=reader)
    assert code == 1 and document["error"]["code"] == "not_available", document
    assert "result" not in document
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]


def test_an_ended_session_is_exit_5_before_not_available(admin):
    """The recheck comes first: a revoked session reading a missing record."""
    _, secret, _ = paired(admin.service)
    revoke(admin.service, secret)
    code, document, _ = admin("integration", "show", "email", secret=secret)
    assert code == 5 and document["error"]["code"] == "session_ended", document


# ``integration schedule-preview`` (#632 slice 5a): the page's live check.
PROPOSED = b'{"rules": [{"kind": "full", "at": "03:00"}], "skips": []}'


def test_the_applied_schedule_previews_as_the_page_shows_it(admin):
    """A read-only session previews the applied schedule: the document
    ``integration show`` prints, seven days, no problems, nothing recorded."""
    _, reader, _ = paired(admin.service, scope="read-only")
    events = AuditEvent.objects.count()
    result = read(admin, reader, "integration", "schedule-preview")
    shown = read(admin, reader, "integration", "show", "parishsoft")
    assert result["version"] == shown["version"]
    assert not result["proposed"] and not result["changed"]
    assert result["schedule"] == shown["refresh_schedule"]
    assert result["problems"] == [] and not result["blocking"]
    assert len(result["preview"]["days"]) == 7
    assert result["preview"]["summary"] and result["preview"]["freshness"]
    assert AuditEvent.objects.count() == events


def test_a_proposed_schedule_from_standard_input_previews_its_days(admin):
    """A changed schedule read from standard input: its own times, each day."""
    _, reader, _ = paired(admin.service, scope="read-only")
    events = AuditEvent.objects.count()
    code, document, _ = admin(
        "integration",
        "schedule-preview",
        "--refresh-schedule",
        "-",
        secret=reader,
        answer=PROPOSED,
    )
    assert code == 0, document
    result = document["result"]
    assert result["proposed"] and result["changed"] and not result["blocking"]
    assert result["schedule"]["rules"] == [{"kind": "full", "at": "03:00"}]
    for day in result["preview"]["days"]:
        assert [entry["text"][:5] for entry in day["times"]] == ["03:00"]
    assert AuditEvent.objects.count() == events


def test_the_pages_problems_are_reported_not_refused(admin):
    """Full refreshes 5 minutes apart: exit 0 with the page's problem at its
    row, blocking a save of this schedule."""
    _, reader, _ = paired(admin.service, scope="read-only")
    schedule = (
        '{"rules": [{"kind": "full", "at": "02:00"},'
        ' {"kind": "full", "at": "02:15"}, {"kind": "full", "at": "02:20"}]}'
    )
    result = read(
        admin, reader, "integration", "schedule-preview", "--refresh-schedule", schedule
    )
    assert result["changed"] and result["blocking"] and result["problems"]
    assert {p["field"] for p in result["problems"]} <= {
        f"refresh_schedule.rules[{index}]" for index in range(3)
    }


@pytest.mark.parametrize(
    "schedule",
    [
        '{"rules": [{"kind": "full", "at": "02:00", "secret-7"}]}',
        '{"rules": [{"kind": "quick", "at": "05:00"}]}',
        '{"rules": [{"kind": "full", "at": "secret-7"}]}',
    ],
)
def test_a_document_the_page_cannot_post_is_invalid_without_echo(admin, schedule):
    """Another shape, or an added quick update: exit 1, input not repeated."""
    _, reader, _ = paired(admin.service, scope="read-only")
    code, document, err = admin(
        "integration",
        "schedule-preview",
        "--refresh-schedule",
        schedule,
        secret=reader,
    )
    assert code == 1 and document["error"]["code"] == "invalid", document
    assert "result" not in document
    assert "secret-7" not in str(document) + err

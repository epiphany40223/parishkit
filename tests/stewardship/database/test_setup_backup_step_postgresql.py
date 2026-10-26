"""The setup wizard's optional Off-site backups step (#2200).

The step saves only public values in its own draft section; finalization
applies the Off-site backups integration and, in the configured marker's
transaction, queues exactly one "Test access" check of the folder through
the worker's definer, which refuses any other caller or transaction.
"""

from uuid import uuid4

import pytest
from django.db import DatabaseError, IntegrityError, connection, transaction

from parishkit.stewardship.accounts import integration_views
from parishkit.stewardship.accounts.setup_forms import (
    BACKUP_LOCAL_REASON,
    BACKUP_MISSING,
    initial_values,
)
from parishkit.stewardship.accounts.setup_models import SetupAttempt, SetupDraftSection
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.jobs.backup_models import BackupDriveProbe
from parishkit.stewardship.jobs.dispatch import claim_hint
from parishkit.stewardship.jobs.lifetime import maintain_execution
from parishkit.stewardship.jobs.queues import WorkQueue
from parishkit.stewardship.jobs.scheduler import scheduler_session
from parishkit.stewardship.source.setup_final_execution import finalization_handler
from parishkit.stewardship.source.setup_final_production import produce_finalization
from parishkit.stewardship.source.setup_final_tasks import TASK_TYPE

from ..test_setup_forms import VALUES
from . import test_setup_preview_postgresql as preview
from .credential_builders import keys
from .test_background_grants_postgresql import task_login
from .test_bootstrap_postgresql import bootstrapped  # noqa: F401
from .test_configuration_service_postgresql import config_role  # noqa: F401
from .test_runtime_auth_grants_postgresql import web_login
from .test_setup_final_execution_postgresql import cleanup_completed_setup
from .test_setup_final_loading_postgresql import prepared, wait_for_source
from .test_setup_loading_postgresql import pages
from .test_setup_views_postgresql import (
    post,
    setup_http,  # noqa: F401
    started,
)
from .test_source_refreshing_postgresql import fake_provider

pytestmark = pytest.mark.django_db(transaction=True)

FOLDER = "A1b2C3d4E5f6G7h8I9j0"
LINK = f"https://drive.google.com/drive/folders/{FOLDER}"
ON = {"enabled": True, "folder_link": LINK, "acknowledged": True}
OFF = {"enabled": False, "folder_link": "", "acknowledged": False}


def version():
    """The attempt's current version, as the page's hidden field carries it."""
    return str(SetupAttempt.objects.get().version)


def test_the_step_saves_only_its_public_values_and_says_what_it_needs(
    setup_http,  # noqa: F811
    google,
):
    """Off saves the step as skipped; on needs a folder and the key word, and
    stores the canonical link. A refused save writes nothing."""
    with web_login():
        browser = started()
        page = browser.get("/admin/setup/backup")
        assert page.status_code == 200
        for text in (
            b"Off-site backups (optional)",
            b"How backups are protected",
            b"Continue without off-site backups for now",
            b"Save and continue",
            b'data-complete-hint="reserve"',
        ):
            assert text in page.content, text
        assert b"never holds the private key" in page.content
        skipped = post(browser, "/admin/setup/backup", {"version": version()})
        assert skipped.status_code == 302, skipped.content
        assert SetupDraftSection.objects.get(step="backup").values == OFF
        # Copies on without the private-key checkbox: refused at the field.
        before = version()
        refused = post(
            browser,
            "/admin/setup/backup",
            {"enabled": "on", "folder_link": LINK, "version": before},
        )
        assert refused.status_code == 400
        assert BACKUP_MISSING.encode() in refused.content
        assert SetupDraftSection.objects.get(step="backup").values == OFF
        assert version() == before
        # A link Drive shows in the address bar is stored in canonical form.
        saved = post(
            browser,
            "/admin/setup/backup",
            {
                "enabled": "on",
                "folder_link": f"https://drive.google.com/drive/u/0/folders/{FOLDER}"
                "?usp=sharing",
                "acknowledged": "on",
                "version": version(),
            },
        )
        assert saved.status_code == 302, saved.content
        assert SetupDraftSection.objects.get(step="backup").values == ON
        revisit = browser.get("/admin/setup/backup")
        assert LINK.encode() in revisit.content
        # A document link is refused with the Off-site backups page's words.
        wrong = post(
            browser,
            "/admin/setup/backup",
            {
                "enabled": "on",
                "folder_link": "https://docs.google.com/document/d/x",
                "acknowledged": "on",
                "version": version(),
            },
        )
        assert wrong.status_code == 400
        assert b"Enter a link to a folder on drive.google.com." in wrong.content
        assert SetupDraftSection.objects.get(step="backup").values == ON


def test_the_local_environment_without_drive_cannot_turn_copies_on(
    setup_http,  # noqa: F811
    google,
    monkeypatch,
):
    """Where the local environment does not reach Google Drive the switch is
    drawn unavailable with why, and the server refuses a tick anyway."""
    monkeypatch.setattr(integration_views, "_drive_in_local", lambda: True)
    with web_login():
        browser = started()
        page = browser.get("/admin/setup/backup")
        assert page.status_code == 200
        assert BACKUP_LOCAL_REASON.encode() in page.content
        assert b'disabled aria-describedby="id_enabled_helptext"' in page.content
        refused = post(
            browser,
            "/admin/setup/backup",
            {
                "enabled": "on",
                "folder_link": LINK,
                "acknowledged": "on",
                "version": version(),
            },
        )
        assert refused.status_code == 400
        assert not SetupDraftSection.objects.filter(step="backup").exists()
        skipped = post(browser, "/admin/setup/backup", {"version": version()})
        assert skipped.status_code == 302
        assert SetupDraftSection.objects.get(step="backup").values == OFF


def test_the_draft_guard_admits_only_the_steps_declared_fields(
    setup_http,  # noqa: F811
    google,
):
    """The SQL guard, not only the form, refuses any other backup field."""
    from parishkit.stewardship.campaigns.work_locks import work_transaction

    with web_login():
        browser = started()
        assert browser.get("/admin/setup/backup").status_code == 200
        attempt = SetupAttempt.objects.get()
        refused = pytest.raises(
            IntegrityError, match="Only declared public setup fields"
        )
        with refused, work_transaction():
            SetupDraftSection.objects.create(
                attempt=attempt,
                step="backup",
                values=OFF | {"key": "never stored"},
                actor_id=attempt.owner_id,
                correlation_id=uuid4(),
            )
    assert not SetupDraftSection.objects.filter(step="backup").exists()


@pytest.mark.parametrize("backup", [None, OFF, ON])
def test_completion_queues_one_folder_check_only_when_the_step_is_on(
    setup_http,  # noqa: F811
    monkeypatch,
    tmp_path,
    config_role,  # noqa: F811
    backup,
):
    """Finishing setup applies the folder and queues exactly one Test access
    check of it, as the setup's Administrator and the applied mailbox user;
    a step left off, or never opened, applies and queues nothing."""
    if backup is not None:
        monkeypatch.setattr(preview, "VALUES", VALUES | {"backup": backup})
    request, attempt, _ = prepared(setup_http, monkeypatch, tmp_path)
    ring = keys()
    with task_login(ServiceRole.SCHEDULER, exact=True), scheduler_session() as guard:
        (produced,) = produce_finalization(setup_http.store, guard)
    fake_provider(monkeypatch, pages())
    wait_for_source()
    handler = finalization_handler(
        setup_http.store,
        credential_path=tmp_path / "parishsoft" / "credential",
        general=ring.general,
        mac=ring.mac,
        public=ring.public,
    )
    with task_login(ServiceRole.WORKER, exact=True, reconnect=True):
        execution = claim_hint(
            produced,
            queue=WorkQueue.SOURCE,
            worker_id=uuid4(),
            handlers={TASK_TYPE: handler},
        )
        with maintain_execution(execution):
            handler.execute(execution)
    completed = SetupAttempt.objects.get()
    assert completed.state == "completed"
    document = setup_http.store.active().document()
    folders = [
        record["values"]["settings"]
        for record in document["sections"]["integrations"]
        if record["values"]["kind"] == "backup"
    ]
    on = backup is ON
    assert folders == ([{"target": LINK}] if on else [])
    probes = list(BackupDriveProbe.objects.all())
    assert len(probes) == (1 if on else 0)
    if on:
        (probe,) = probes
        assert probe.state == "pending"
        assert probe.folder_id == FOLDER
        assert probe.requested_by_id == completed.owner_id
        assert probe.subject == VALUES["mail"]["delegated_email"]
    cleanup_completed_setup()
    assert (
        not SetupDraftSection.objects.filter(step="backup", scrubbed_at=None)
        .exclude(values={})
        .exists()
    )


def test_the_folder_check_definer_refuses_other_callers_and_transactions(
    setup_http,  # noqa: F811
    google,
):
    """Only the worker may call it, and only in the configured marker's own
    transaction; without one it refuses and queues nothing."""
    call = "SELECT stewardship_setup_backup_check_v1()"
    with (
        web_login(),
        pytest.raises(DatabaseError) as denied,
        transaction.atomic(),
        connection.cursor() as cursor,
    ):
        cursor.execute(call)
    assert getattr(denied.value.__cause__, "sqlstate", None) == "42501"
    with (
        task_login(ServiceRole.WORKER, exact=True),
        pytest.raises(DatabaseError) as refused,
        transaction.atomic(),
        connection.cursor() as cursor,
    ):
        cursor.execute(call)
    assert getattr(refused.value.__cause__, "sqlstate", None) == "55000"
    assert not BackupDriveProbe.objects.exists()
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT has_function_privilege('public', "
            "'public.stewardship_setup_backup_check_v1()', 'EXECUTE')"
        )
        assert cursor.fetchone() == (False,)


def test_initial_values_round_trip_the_saved_section():
    """The page shows what was saved, booleans and link alike."""
    assert initial_values("backup", ON) == ON

"""The System health page under the real web login (ADM-13 PR 2, #530).

The page and its polled fragment read the service status records, open
incidents, waiting retries, the refused load's drop counts and the applied
migrations through the restricted web login, record one audited view per page
open (never per poll), and stay Administrator-only.
"""

from datetime import timedelta
from uuid import uuid4

import pytest
from django.db import connection, transaction

from parishkit.stewardship import system_health
from parishkit.stewardship.audit.models import AuditContext
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.jobs.operational_models import OperationalIncident
from parishkit.stewardship.source.drop_models import SourceDropCount
from parishkit.stewardship.source.failures import settle_failed_read
from parishkit.stewardship.source.loading import CountCheck, DestructiveSourceChange

from ..policy_factory import address
from .auth_builders import signed_in
from .campaign_builders import change
from .test_background_grants_postgresql import task_login
from .test_system_health_records_postgresql import (
    raw,
    refused_attempt,
    source_singletons,  # noqa: F401
)

pytestmark = pytest.mark.django_db(transaction=True)
PAGE = "/admin/system/health/"
STATUS = "/admin/system/health/status"


def views():
    """The audit contexts of System health page views."""
    return list(
        AuditContext.objects.filter(
            event__event_type="system_health_viewed"
        ).values_list("context", flat=True)
    )


def insert_row(service="web", *, process="main", sender=None, age=None):
    """Insert a status row as the schema owner, ``age`` since its last report.

    The owner bypasses the guard, so the row supplies ``sender_since``
    itself, as the guard would on a service's own write.
    """
    age = age or timedelta()
    raw(
        "INSERT INTO stewardship_service_status (id,service,process,target,"
        "started_at,reported_at,application_version,debug_logging,sender_state,"
        "sender_since) VALUES (%s,%s,%s,NULL,now()-%s,now()-%s,'1.0.0',false,%s,"
        "CASE WHEN %s::text IS NULL THEN NULL ELSE now()-%s END)",
        [uuid4(), service, process, age, age, sender, sender, age],
    )


def running_services():
    """Every core service reporting now, as a healthy deployment does."""
    for service in system_health.CORE_SERVICES:
        insert_row(service, sender="running" if service == "mail-dispatch" else None)
    insert_row("mail-dispatch", process="mail", sender="running")


def test_an_administrator_opens_the_page_and_only_the_page_is_audited(
    auth_service, google
):
    """Real grants: the page, its fragment, the menu entry and one audit row."""
    running_services()
    browser, _ = signed_in()
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        response = browser.get(PAGE)
        assert response.status_code == 200
        assert response["Cache-Control"] == "no-store"
        body = response.content.decode()
        assert "Everything is working" in body
        assert "All services run version 1.0.0." in body
        assert "The database matches the version that is running." in body
        assert "Nothing is holding Family email back." in body
        assert f'data-live-url="{STATUS}"' in body
        assert 'data-live-interval="10000"' in body
        # The first System menu entry.
        assert body.index('href="/admin/system/health/"') < body.index(
            'href="/admin/configuration/integrations"'
        )
        fragment = browser.get(STATUS)
        assert fragment.status_code == 200
        assert "Everything is working" in fragment.content.decode()
        assert "<html" not in fragment.content.decode()
        opened = browser.get("/admin/system/")
    assert opened.status_code == 302 and opened["Location"] == PAGE
    assert views() == [{"outcome": "succeeded", "count": 0}]


def test_problems_come_from_status_rows_incidents_and_retries(auth_service, google):
    """A halted sender, a stopped scheduler, an incident and waiting retries."""
    running_services()
    # The scheduler restarted: its old row is replaced, not "not running".
    insert_row("scheduler", age=timedelta(hours=2))
    insert_row("mail-dispatch", sender="halted")
    insert_row("worker", process="source", age=timedelta(minutes=10))
    insert_row("web", age=timedelta(seconds=30))
    OperationalIncident.objects.create(
        kind="backup_offsite_failed",
        signal_level="WARNING",
        suppression_seconds=900,
        escalation_seconds=900,
    )
    browser, _ = signed_in()
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        body = browser.get(STATUS).content.decode()
        found = system_health.read_health(auth_service.store)[0]
    codes = [problem.code for problem in found.problems]
    assert codes == ["sender_halted", "not_running", "backup_offsite_failed"]
    assert found.problems[1].service == "worker"
    assert found.problems[1].process == "source"
    assert "3 problems need attention" in body
    assert "Mail sender 1 has stopped all Family email" in body
    assert "ParishSoft refresh worker has not reported since" in body
    assert "Copying backups off-site has failed since" in body
    assert "Running (2 processes)" in body
    # The polled fragment is never audited.
    assert views() == []


@pytest.mark.parametrize("role", ["staff", "ministry_leader"])
def test_the_page_is_for_administrators_only(auth_service, google, role):
    """Neither the page, its fragment, the redirect target nor the menu entry."""
    store = auth_service.store
    change(
        store,
        store.active(),
        uuid4(),
        [
            {
                "operation": "add",
                "section": "login_rules",
                **address("reader@example.org", roles=(role,)),
            }
        ],
    )
    google[0]["email"] = "reader@example.org"
    browser, _ = signed_in()
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        assert browser.get(PAGE).status_code == 403
        assert browser.get(STATUS).status_code == 403
        home = browser.get("/admin/").content
    assert b'href="/admin/system/health/"' not in home
    assert views() == []


def test_a_signed_out_request_reads_nothing(auth_service, google):
    """Without a sign-in, neither the page nor its fragment answers."""
    from django.test import Client

    running_services()
    browser = Client()
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        for path in (PAGE, STATUS):
            response = browser.get(path)
            assert response.status_code == 403, path
            assert b"Mail sender" not in response.content
    assert views() == []


def test_unknown_query_options_are_refused(auth_service, google):
    """The page takes no options; a stray one is a plain refusal."""
    browser, _ = signed_in()
    assert browser.get(PAGE + "?all=1").status_code == 400
    assert views() == []


def test_a_refused_load_shows_its_counts_until_a_full_refresh_follows(tmp_path):
    """The web login reads the newest refused attempt's counts in check order."""
    attempt, execution, lease = refused_attempt(tmp_path)
    checks = (
        CountCheck("valid_email_contacts", 900, 400, 25, True),
        CountCheck("family", 1084, 1080, 25, False),
    )
    settle_failed_read(
        execution,
        DestructiveSourceChange(
            "PRIVATE",
            measure="valid_email_contacts",
            before=900,
            after=400,
            checks=checks,
        ),
        source_claim=lease,
    )
    refused = SourceDropCount.objects.filter(attempt=attempt).first().created_at
    with task_login(ServiceRole.WEB, exact=True, reconnect=True), transaction.atomic():
        at, counts = system_health._refused(None)
        assert at == refused
        assert [(count.measure, count.failed) for count in counts] == [
            ("family", False),
            ("valid_email_contacts", True),
        ]
        # A full refresh that promoted after the refusal settles it.
        assert system_health._refused(refused + timedelta(seconds=1)) == (None, ())


def test_the_schema_check_compares_every_applied_migration(auth_service):
    """Equal sets match; a missing or extra applied migration does not."""
    assert system_health._schema_current()
    with connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO django_migrations (app,name,applied) "
            "VALUES ('stewardship_jobs','9999_future',now())"
        )
    try:
        assert not system_health._schema_current()
    finally:
        with connection.cursor() as cursor:
            cursor.execute("DELETE FROM django_migrations WHERE name='9999_future'")


def test_an_unreadable_configuration_answers_503(auth_service, google, monkeypatch):
    """A damaged configuration (such as an unreadable backup key) is a 503."""
    from parishkit.config import ConfigError
    from parishkit.stewardship.accounts import system_health_views

    def damaged(store):
        """As system_health._backup raises for an unreadable configured key."""
        raise ConfigError("The configured backup key cannot be read.")

    monkeypatch.setattr(system_health_views, "read_health", damaged)
    browser, _ = signed_in()
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        assert browser.get(PAGE).status_code == 503
        assert browser.get(STATUS).status_code == 503
    assert views() == []

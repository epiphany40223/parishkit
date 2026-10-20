"""The System health page under the real web login (ADM-13 PR 2, #530).

The page and its polled fragment read the service status records, open
incidents, waiting retries, the 24-hour send count, the refused load's drop
counts and the applied migrations through the restricted web login, record
one audited view per page open (never per poll), and stay
Administrator-only.
"""

import json
import re
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
from .test_admin_status_cli_postgresql import admin, one, session  # noqa: F401
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
        assert "All working" in body
        # Seven tiles, Working and closed (#1214).
        assert body.count("data-health-tile=") == 7
        assert body.count(" hidden>") >= 7
        assert "All services run version 1.0.0." in body
        assert "The database matches the version that is running." in body
        assert "Nothing is holding Family email back." in body
        assert f'data-live-url="{STATUS}"' in body
        assert 'data-live-interval="10000"' in body
        # The first System menu entry.
        assert body.index('href="/admin/system/health/"') < body.index(
            'href="/admin/system/integrations/"'
        )
        fragment = browser.get(STATUS)
        assert fragment.status_code == 200
        assert "All working" in fragment.content.decode()
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
        whole = browser.get(PAGE).content.decode()
        found = system_health.read_health(auth_service.store)[0]
    codes = [problem.kind for problem in found.problems]
    assert codes == ["sender_halted", "not_running", "backup_offsite_failed"]
    assert [
        (subject.service, subject.process) for subject in found.problems[1].subjects
    ] == [("worker", "source")]
    # Mail (halted) and Version and services (a stopped worker) are Error
    # tiles, Backups (a failed off-site copy) a Warning tile: the line
    # counts tiles (#1214).
    line = body[: body.index("</h2>")]
    assert "2 errors" in line and "and 1 warning" in line
    # Troubled tiles start open, Working ones closed.
    for tile, shown in (("mail", True), ("backups", True), ("debug", False)):
        band = whole[whole.index(f'id="health-{tile}-details"') :]
        band = band[: band.index(">")]
        assert ("hidden" not in band) is shown, tile
    assert "Mail sender 1 has stopped all Family email" in body
    assert "The ParishSoft refresh worker has not reported since" in body
    assert "Copying backups off-site has failed since" in body
    assert "Running (2 processes)" in body
    # The 24-hour count comes through its definer function, inside the
    # page's read-only snapshot, under the web login (ADM-13 PR 2c);
    # test_mail_daily_limit_postgresql keeps it equal to the mail sender's.
    assert (found.daily_sent, found.daily_limit) == (0, 1600)
    # Below the limit nothing waits for it, so there is no resume time (#530).
    assert found.daily_resume_at is None and "data-daily-resume" not in body
    assert "Emails went to 0 recipients in the last 24 hours." in body
    # The polled fragment is never audited; the page view counts problems.
    assert views() == [{"outcome": "succeeded", "count": 3}]


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
    # Problems exist (no core service has reported, and the worker's refresh
    # process stopped), so only the role keeps the lines off Home.
    insert_row("worker", process="source", age=timedelta(minutes=10))
    browser, _ = signed_in()
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        assert browser.get(PAGE).status_code == 403
        assert browser.get(STATUS).status_code == 403
        home = browser.get("/admin/").content
    assert b'href="/admin/system/health/"' not in home
    # Nor Home's System health problem lines, nor any problem sentence.
    assert b"data-home-health" not in home and b"data-problem=" not in home
    assert b"system problem" not in home and b"has not reported" not in home
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
    """The page takes no options; a stray one is a plain refusal. The
    fragment takes only the page-load tiles' states (``loaded``), which it
    counts into the line and otherwise ignores (#1214)."""
    running_services()
    browser, _ = signed_in()
    assert browser.get(PAGE + "?all=1").status_code == 400
    assert browser.get(PAGE + "?loaded=resave:error").status_code == 400
    assert browser.get(STATUS + "?all=1").status_code == 400
    assert browser.get(STATUS + "?loaded=resave:error&all=1").status_code == 400
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        counted = browser.get(STATUS + "?loaded=resave:error").content.decode()
        forged = browser.get(STATUS + "?loaded=mail:error,resave:fine").content
    line = counted[: counted.index("</h2>")]
    assert "1 error" in line
    assert b"All working" in forged[: forged.index(b"</h2>")]
    assert views() == []


def test_a_slow_recheck_runs_the_page_load_checks_and_stays_passive(
    auth_service, google, monkeypatch
):
    """#1790: an ordinary poll never runs the two page-load checks; only
    ``?slow=1`` does, once each, and answers the tiles' lasting copies and
    the fragment address carrying their fresh states. Neither poll records
    an audited view or renews the sign-in's idle time; ``slow`` takes only
    ``1``."""
    from parishkit.stewardship.accounts import system_health_views
    from parishkit.stewardship.accounts.models import PortalSession

    calls = []

    def form(campaign_id):
        """Three Families the form cannot open."""
        calls.append("form")
        return 3

    def resave(service, campaign_id):
        """Nothing to re-save."""
        calls.append("resave")
        return []

    monkeypatch.setattr(system_health_views, "source_form_summary", form)
    monkeypatch.setattr(system_health_views, "resave_summary", resave)
    running_services()
    browser, _ = signed_in()
    before = PortalSession.objects.get().last_activity_at
    assert browser.get(STATUS + "?slow=2").status_code == 400
    assert browser.get(STATUS + "?slow=1&all=1").status_code == 400
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        echoed = browser.get(STATUS + "?loaded=resave:error")
        assert echoed.status_code == 200 and calls == []
        body = echoed.content.decode()
        assert "1 error" in body[: body.index("</h2>")]
        assert "data-live-mirror-lasting" not in body
        assert "data-live-url" not in body
        assert 'data-live-slow-every="300000"' in body
        slow = browser.get(STATUS + "?loaded=resave:error&slow=1")
        assert slow.status_code == 200 and calls == ["form", "resave"]
        assert slow["Cache-Control"] == "no-store"
        body = slow.content.decode()
    # The fresh reads replace the states the page passed.
    line = body[: body.index("</h2>")]
    assert "1 warning" in line and "error" not in line
    assert f'data-live-url="{STATUS}?loaded=source-form%3Awarning"' in body
    assert body.count("data-live-mirror-lasting") == 4
    assert "3 Families cannot open the form" in body
    assert "No saved page or email needs re-saving." in body
    assert views() == []
    assert PortalSession.objects.get().last_activity_at == before


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
        at, counts, found = system_health._refused(None)
        assert at == refused and found == attempt.pk
        assert [(count.measure, count.failed) for count in counts] == [
            ("family", False),
            ("valid_email_contacts", True),
        ]
        # A full refresh that promoted after the refusal settles it.
        assert system_health._refused(refused + timedelta(seconds=1)) == (
            None,
            (),
            None,
        )


def test_the_schema_check_compares_every_applied_migration(auth_service):
    """Equal sets match; a missing or extra applied migration does not."""
    assert system_health._facts().schema_current
    with connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO django_migrations (app,name,applied) "
            "VALUES ('stewardship_jobs','9999_future',now())"
        )
    try:
        assert not system_health._facts().schema_current
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


def test_home_counts_problems_in_one_line_linking_the_page(auth_service, google):
    """Administrators see one short line on Home: the count, the worst state's
    word and one link to System health, never the problem's sentence
    (#1214, #1673)."""
    running_services()
    insert_row("worker", process="source", age=timedelta(minutes=10))
    browser, _ = signed_in()
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        home = browser.get("/admin/").content.decode()
    assert home_count(home) == (1, "Error")
    assert "The ParishSoft refresh worker has not reported since" not in home
    assert listed(home) == []
    line = home[home.index("data-home-health") :]
    line = line[: line.index("</li>")]
    assert 'href="/admin/system/health/">See System health</a>' in line


def test_the_command_reads_the_page_and_watches_until_healthy(admin, google):  # noqa: F811
    """``system health``: the page's read, audited once, never a sentence."""
    running_services()
    insert_row("mail-dispatch", sender="halted")
    secret = session(admin, scope="read-only")
    before = len(views())
    code, document = one(admin, "system", "health", secret=secret)
    assert code == 0 and document["ok"] and document["final"], document
    result = document["result"]
    assert [problem["kind"] for problem in result["problems"]] == ["sender_halted"]
    assert result["schema_current"] is True
    assert "has stopped" not in json.dumps(document)
    assert len(views()) == before + 1
    # With problems, a watch runs to its timeout (exit 7) with the last state,
    # still audited once.
    code, documents = admin(
        "system", "health", "--watch", "2", "--timeout", "3", secret=secret
    )
    assert code == 7 and documents[-1]["error"]["code"] == "watch_timeout"
    assert len(views()) == before + 2


def test_a_watch_of_a_healthy_system_stops_at_once(admin, google):  # noqa: F811
    """Nothing needs attention: one final document, exit 0."""
    running_services()
    secret = session(admin, scope="read-only")
    code, documents = admin("system", "health", "--watch", "2", secret=secret)
    assert code == 0 and len(documents) == 1 and documents[0]["final"]
    assert documents[0]["result"]["problems"] == []


def test_home_problem_lines_cost_no_query_whatever_is_open(
    auth_service, google, monkeypatch
):
    """Home's query count is the same with problems open, a backup overdue too.

    The lines ride on Home's refresh-status statement, so they add no query
    of their own, and nothing in them reads more when an incident opens.
    The Family portal switch is cached for three seconds per process; on a
    slow runner the two counts can straddle its expiry and differ by that one
    unrelated read, so the cache is held for the test.
    """
    from django.test.utils import CaptureQueriesContext

    from parishkit.stewardship.accounts import family_maintenance

    monkeypatch.setattr(family_maintenance, "CACHE_SECONDS", 3600)

    running_services()
    browser, _ = signed_in()

    def count():
        """How many statements one Home view runs under the web login."""
        with (
            task_login(ServiceRole.WEB, exact=True, reconnect=True),
            CaptureQueriesContext(connection) as queries,
        ):
            assert browser.get("/admin/").status_code == 200
        return len(queries)

    count()  # Fill per-process caches first.
    healthy = count()
    insert_row("worker", process="source", age=timedelta(minutes=10))
    OperationalIncident.objects.create(
        kind="backup_rpo_breach",
        signal_level="WARNING",
        suppression_seconds=900,
        escalation_seconds=900,
    )
    troubled = count()
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        home = browser.get("/admin/").content.decode()
    assert home_count(home) == (2, "Error")
    assert troubled == healthy
    # The shared backup kinds (#1214, slice 1) ride on the same statement:
    # a backup sealed to another key than the configured one and a request
    # the server never picked up cost nothing more either. Setting a key
    # also raises a security event, which Home's own notice reads, so the
    # count is compared from there.
    configure_backup_key(auth_service.store)
    keyed = count()
    backup_run(fingerprint="e" * 16)
    unpicked_request()
    more = count()
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        home = browser.get("/admin/").content.decode()
        status = browser.get(STATUS).content.decode()
    assert 'data-problem="backup_key_mismatch"' in status
    assert 'data-problem="backup_request_failed"' in status
    assert home_count(home)[0] == len(listed(status))
    assert more == keyed


def test_several_stopped_services_are_one_problem_on_home_and_the_page(
    auth_service, google
):
    """One condition in several processes is listed once on the page and
    counted once on Home (#686, #1673)."""
    # Web and both mail senders run; the worker and scheduler stopped.
    insert_row("web")
    insert_row("mail-dispatch", sender="running")
    insert_row("mail-dispatch", process="mail", sender="running")
    insert_row("worker", age=timedelta(hours=1))
    insert_row("scheduler", age=timedelta(hours=2))
    browser, _ = signed_in()
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        home = browser.get("/admin/").content.decode()
        page = browser.get(STATUS).content.decode()
    assert page.count('data-problem="not_running"') == 1, page
    assert (
        "The background worker and the scheduler have not reported for more "
        "than "
        "three minutes"
    ) in page
    assert home_count(home)[0] == len(listed(page))


def configure_backup_key(store):
    """Apply a configuration that sets a new backup key; its fingerprint.

    The activation records the ``backup_key_replaced`` security event whose
    time ``key_mismatch`` compares backups with.
    """
    from parishkit.stewardship import backup_sealing

    _, public = backup_sealing.generate_keypair()
    change(
        store,
        store.active(),
        uuid4(),
        [
            {
                "operation": "add",
                "section": "integrations",
                "id": str(uuid4()),
                "values": {
                    "kind": "backup_key",
                    "settings": {"public_key": public.strip()},
                    "credential_fingerprint": None,
                },
            }
        ],
    )
    return backup_sealing.parse_public_key(public.strip()).fingerprint


def backup_run(*, fingerprint, age=timedelta()):
    """Record one backup sealed to ``fingerprint`` that started and finished
    ``age`` ago (a key set before then is the one it read as it started)."""
    from parishkit.stewardship.jobs.backup_models import BackupRun

    from .test_backup_postgresql import FACTS, now

    instant = now() - age
    with transaction.atomic():
        return BackupRun.objects.create(
            **{
                **FACTS,
                "started_at": instant,
                "completed_at": instant,
                "recipient_fingerprint": fingerprint,
            }
        )


def unpicked_request():
    """A Take a backup now request that has waited an hour, as the owner."""
    raw(
        "INSERT INTO stewardship_backup_request (id,created_at,actor_id,"
        "session_id,authenticated_at,state) VALUES (%s,now()-interval '1 hour',"
        "%s,%s,now()-interval '1 hour','waiting')",
        [uuid4(), uuid4(), uuid4()],
    )


def listed(body):
    """The problem kinds a body lists, in order."""
    import re

    return re.findall(r'data-problem="([a-z_]+)"', body)


def home_count(body):
    """Home's System health count line (#1673) as (count, state word), or None."""
    match = re.search(
        r'data-home-health>.*?class="status-word">(\w+)</span></span>'
        r" (\d+) system problems?\.",
        body,
        re.DOTALL,
    )
    return (int(match[2]), match[1]) if match else None


def test_the_shared_backup_problems_on_home_and_the_page(auth_service, google):
    """A key mismatch counts only for a backup started after the configured
    key was set, and stands in for the changed-key incident; a request that
    was never picked up is listed too; Home counts them (#1673)."""
    running_services()
    configured = configure_backup_key(auth_service.store)
    # Before the key was set: the ordinary day after a key change.
    backup_run(fingerprint="d" * 16, age=timedelta(days=1))
    OperationalIncident.objects.create(
        kind="backup_key_changed",
        signal_level="WARNING",
        suppression_seconds=900,
        escalation_seconds=900,
    )
    browser, _ = signed_in()

    def bodies():
        """Home's and the page's bodies, under the web login."""
        with task_login(ServiceRole.WEB, exact=True, reconnect=True):
            home = browser.get("/admin/")
            page = browser.get(PAGE)
        assert (home.status_code, page.status_code) == (200, 200)
        return home.content.decode(), page.content.decode()

    home, page = bodies()
    assert listed(home) == [] and home_count(home) is not None
    assert "backup_key_changed" in listed(page)
    assert "backup_key_mismatch" not in listed(page)
    # Sealed to another key after it was set: one warning, not two.
    backup_run(fingerprint="e" * 16)
    unpicked_request()
    home, page = bodies()
    kinds = listed(page)
    assert "backup_key_changed" not in kinds, kinds
    assert "backup_key_mismatch" in kinds and "backup_request_failed" in kinds
    assert "The server did not pick up the backup requested at" in page
    assert "The server did not pick up the backup requested at" not in home
    # Sealed to the configured key: no mismatch.
    backup_run(fingerprint=configured)
    home, page = bodies()
    assert "backup_key_mismatch" not in listed(page)


def test_home_states_an_unreadable_backup_key_while_the_page_is_unavailable(
    auth_service, google, monkeypatch
):
    """Home shows the warning's own sentence, with no link, instead of a
    count line linking System health, whose page keeps its 503 (#1673).

    Only System health's own reading of the key fails here: the applied
    configuration is still valid, as a damaged key record would not be.
    """
    from parishkit.config import ConfigError

    running_services()
    configure_backup_key(auth_service.store)

    def unreadable(_configuration):
        """The configured key cannot be read."""
        raise ConfigError("The configured backup key cannot be read.")

    browser, _ = signed_in()
    monkeypatch.setattr(system_health, "configured_fingerprint", unreadable)
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        home = browser.get("/admin/")
        page = browser.get(PAGE)
    assert home.status_code == 200
    assert page.status_code == 503
    body = home.content.decode()
    assert home_count(body) is None
    line = re.search(
        r'<li class="notice" data-problem="backup_key_unchecked"[^>]*>.*?</li>',
        body,
        re.DOTALL,
    ).group(0)
    assert '<span class="status-word">Warning</span>' in line
    assert "so System health cannot be shown" in line
    assert "<a " not in line


def test_a_backup_running_across_a_key_change_is_no_mismatch(auth_service, google):
    """A backup that started before the key changed and finished after it is
    sealed, correctly, to the old key: no mismatch, and the changed-key
    incident stays the one warning."""
    from parishkit.stewardship.jobs.backup_models import BackupRun

    from .test_backup_postgresql import FACTS

    running_services()
    with connection.cursor() as cursor:
        cursor.execute("SELECT statement_timestamp()")
        (before,) = cursor.fetchone()
    configure_backup_key(auth_service.store)
    with transaction.atomic():
        BackupRun.objects.create(
            **{
                **FACTS,
                "started_at": before - timedelta(minutes=5),
                "completed_at": before + timedelta(minutes=30),
                "recipient_fingerprint": "e" * 16,
            }
        )
    OperationalIncident.objects.create(
        kind="backup_key_changed",
        signal_level="WARNING",
        suppression_seconds=900,
        escalation_seconds=900,
    )
    browser, _ = signed_in()
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        home = browser.get("/admin/")
        page = browser.get(PAGE)
    assert (home.status_code, page.status_code) == (200, 200)
    kinds = listed(page.content.decode())
    assert "backup_key_mismatch" not in kinds and "backup_key_changed" in kinds
    assert home_count(home.content.decode()) is not None


def test_home_in_production_counts_off_site_copies_not_set_up(auth_service, google):
    """In Production with no off-site copies, Home counts one more problem;
    neither mode repeats the invitation to set them up, which System health
    keeps (#1673)."""
    from parishkit.stewardship.accounts.runtime_models import SystemConfiguration

    from .auth_builders import unguarded

    running_services()
    browser, _ = signed_in()
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        testing = browser.get("/admin/").content.decode()
    assert "Encrypted copies of every backup" not in testing
    with unguarded():
        SystemConfiguration.objects.update(mode="production")
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        response = browser.get("/admin/")
    assert response.status_code == 200
    body = response.content.decode()
    before = home_count(testing)[0] if home_count(testing) else 0
    assert home_count(body)[0] == before + 1
    assert "Encrypted copies of every backup" not in body
    assert "Set up off-site backups" not in body

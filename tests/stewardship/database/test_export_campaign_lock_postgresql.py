"""Export admission orders per campaign, not behind the global work lock (#147).

Queueing an export used to take the global work-order lock (736220,1), so it
waited behind every source promotion, installer and task transition. It now
takes only its campaign's export lock (736232). These tests use real competing
connections: admission completes while another session holds the global lock,
and every transition that can close admission (the purge work gate, a
lifecycle transition, the go-live gate) still waits for exports admitted
before it and is seen by exports admitted after it.
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from queue import Queue
from threading import Event
from time import monotonic, sleep
from uuid import uuid4

import pytest
from django.db import DatabaseError, connection, connections, transaction
from django.utils import timezone

from parishkit.stewardship.campaigns.credential_models import CampaignCredentialState
from parishkit.stewardship.campaigns.production_storage import (
    CleanupInventory,
    TestingSummary,
    begin_transition,
)
from parishkit.stewardship.campaigns.rehearsals import invalidate_rehearsal
from parishkit.stewardship.campaigns.runtime import campaign_transaction
from parishkit.stewardship.campaigns.runtime_models import CampaignWorkGate
from parishkit.stewardship.campaigns.work_locks import (
    lock_campaign_exports,
    lock_current_campaign_exports,
    work_transaction,
)
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.reports import export_services
from parishkit.stewardship.reports.directories import DirectoryQuery
from parishkit.stewardship.reports.directory_exports import create_directory_export
from parishkit.stewardship.reports.export_models import (
    DirectoryExportSnapshot,
    ExportPublication,
    ExportRequest,
)
from parishkit.stewardship.reports.financial import FinancialQuery
from parishkit.stewardship.reports.financial_exports import create_financial_export
from parishkit.stewardship.reports.information import InformationQuery
from parishkit.stewardship.reports.information_exports import create_information_export
from parishkit.stewardship.reports.ministries import MinistryQuery
from parishkit.stewardship.reports.ministry_exports import create_ministry_export
from parishkit.stewardship.storage import StorageInvariantError

from .auth_builders import signed_in
from .test_background_grants_postgresql import task_login
from .test_export_jobs_postgresql import (  # noqa: F401
    request_export,
    run_export,
    scenario,
)
from .test_export_views_postgresql import post
from .test_financial_exports_postgresql import three_families
from .test_ministry_exports_postgresql import leader
from .test_ministry_reports_postgresql import setup as ministry_setup
from .test_policy_postgresql import user
from .test_report_workspace_postgresql import read

pytestmark = pytest.mark.django_db(transaction=True)

GLOBAL = "classid=736220 AND objid=1"
CAMPAIGN = "classid=736232"


def wait_for_lock(pid, which):
    """Require a real blocked contender on the named advisory lock."""
    deadline = monotonic() + 10
    while monotonic() < deadline:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT EXISTS (SELECT 1 FROM pg_locks WHERE pid=%s "
                f"AND locktype='advisory' AND {which} AND objsubid=2 AND NOT granted)",
                [pid],
            )
            if cursor.fetchone()[0]:
                return
        sleep(0.01)
    pytest.fail("Contender did not wait on the expected lock")


def contender(ready, action):
    """Own a distinct connection and always close it before fixture teardown."""
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_backend_pid()")
            ready.put(cursor.fetchone()[0])
        return action()
    finally:
        connections.close_all()


def participation(request):
    """A ready exact generation; returns its campaign and a queueing callable."""
    store, principal, facts, _ = request.getfixturevalue("scenario")
    return facts.campaign_id, lambda: request_export((store, principal, facts, None))


def directory(request):
    """A promoted current source and the real directory capture."""
    harness = request.getfixturevalue("live_response_service")
    actor = user("admin@example.org").pk
    return harness.campaign.pk, lambda: create_directory_export(
        harness.service.store,
        actor,
        campaign_id=harness.campaign.pk,
        query=DirectoryQuery(sort="duid"),
        postal=False,
        mac=harness.rings.mac,
        format="csv",
        browser_timezone="UTC",
        request_key=uuid4(),
    )


def information(request):
    """The additional-information capture over a live response service."""
    harness = request.getfixturevalue("live_response_service")
    actor = user("admin@example.org").pk
    return harness.campaign.pk, lambda: create_information_export(
        harness.service.store,
        actor,
        campaign_id=harness.campaign.pk,
        query=InformationQuery(disposition="all"),
        history=False,
        format="csv",
        browser_timezone="UTC",
        request_key=uuid4(),
    )


def financial(request):
    """Three real pledging households and the financial capture."""
    harness = three_families(request.getfixturevalue("response_service"))
    actor = user("admin@example.org").pk
    return harness.campaign.pk, lambda: create_financial_export(
        harness.service.store,
        actor,
        campaign_id=harness.campaign.pk,
        query=FinancialQuery(),
        format="csv",
        browser_timezone="UTC",
        request_key=uuid4(),
    )


def ministry(request):
    """A Ministry leader's scoped capture of one assigned Ministry."""
    harness = ministry_setup(request.getfixturevalue("response_service"))
    _, actor, _, _ = leader(harness, request.getfixturevalue("google"))
    return harness.campaign.pk, lambda: create_ministry_export(
        harness.service.store,
        actor,
        campaign_id=harness.campaign.pk,
        query=MinistryQuery(),
        ministry_id=9,
        action="join",
        format="csv",
        browser_timezone="UTC",
        request_key=uuid4(),
    )


@pytest.mark.parametrize(
    "kind", [participation, directory, information, financial, ministry]
)
def test_export_is_admitted_while_another_session_holds_the_global_lock(request, kind):
    """No export admission waits on 736220,1; each takes only its campaign lock."""
    campaign_id, create = kind(request)
    ready = Queue()

    def admit():
        """Queue under the web role and report every advisory lock it held."""
        with transaction.atomic():
            result = create()
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT classid,objid FROM pg_locks WHERE pid=pg_backend_pid() "
                    "AND locktype='advisory' AND granted"
                )
                held = cursor.fetchall()
        return result, held

    with (
        task_login(ServiceRole.WEB, exact=True, reconnect=True),
        ThreadPoolExecutor(max_workers=1) as pool,
    ):
        with work_transaction():
            future = pool.submit(contender, ready, admit)
            ready.get(timeout=5)
            # Completes while this session still holds the global lock.
            result, held = future.result(timeout=60)
        assert ExportRequest.objects.filter(pk=result.pk, campaign_id=campaign_id)
    classes = {row[0] for row in held}
    assert 736232 in classes and 736220 not in classes


def test_export_post_is_admitted_while_another_session_holds_the_global_lock(
    live_response_service, google
):
    """The whole Admin POST, sign-in check included, completes during a hold."""
    harness = live_response_service
    browser, login = signed_in()
    assert login.status_code == 302
    route = f"/admin/reports/{harness.campaign.pk}/families/"
    fields = DirectoryQuery().form_values() | dict(
        format="csv", browser_timezone="UTC", request_key=str(uuid4())
    )
    ready = Queue()
    with (
        task_login(ServiceRole.WEB, exact=True, reconnect=True),
        ThreadPoolExecutor(max_workers=1) as pool,
    ):
        assert read(browser, route)[0].status_code == 200
        with work_transaction():
            future = pool.submit(
                contender, ready, lambda: post(browser, route + "export", fields)
            )
            ready.get(timeout=5)
            assert future.result(timeout=60).status_code == 302
    assert ExportRequest.objects.filter(request_key=fields["request_key"]).exists()


def transition_statement(kind, campaign_id):
    """Run one real transition entry point that can change export admission."""
    if kind == "work_gate":
        # The trigger takes the campaign lock before its maintenance-window
        # check, which then refuses this current campaign.
        with pytest.raises(DatabaseError, match="maintenance window|purge preparation"):
            CampaignWorkGate.objects.create(
                campaign_id=campaign_id,
                request_id=uuid4(),
                initiated_by_id=uuid4(),
                actor_id=uuid4(),
                correlation_id=uuid4(),
                state="preparing",
            )
    elif kind == "lifecycle":
        with (
            pytest.raises(DatabaseError, match="transition"),
            transaction.atomic(),
            connection.cursor() as cursor,
        ):
            cursor.execute(
                "INSERT INTO stewardship_campaign_transition(id,campaign_id,action) "
                "VALUES (%s,%s,'none')",
                [uuid4(), campaign_id],
            )
    elif kind == "go_live_gate":
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute(
                "UPDATE stewardship_campaign_credentials "
                "SET go_live_gate=true,version=version+1 WHERE campaign_id=%s",
                [campaign_id],
            )
            assert cursor.rowcount == 1
    elif kind == "campaign_transaction":
        with campaign_transaction(campaign_id, correlation_id=uuid4()):
            pass
    elif kind == "begin_transition":
        # Go-live intake takes the campaign lock before its row locks; this
        # refused admission then rolls it back without closing the gate.
        now = timezone.now()
        with pytest.raises((PermissionError, StorageInvariantError)):
            begin_transition(
                campaign_id=campaign_id,
                request_key=uuid4(),
                actor_id=uuid4(),
                correlation_id=uuid4(),
                inventory=CleanupInventory("a" * 64, {"sessions": 1}),
                summary=TestingSummary("b" * 64, 0, 0, 0, 0, 0, 0),
                acknowledged_at=now,
                reauthenticated_at=now - timedelta(seconds=1),
                admit=lambda *args: False,
            )
    elif kind == "activation":
        # A configuration activation (an end edit or reopen) locks the
        # current campaign's export lock before its campaign row lock.
        with (
            pytest.raises(DatabaseError),
            transaction.atomic(),
            connection.cursor() as cursor,
        ):
            cursor.execute(
                "UPDATE stewardship_system_configuration "
                "SET active_configuration_id=gen_random_uuid(),version=version+1 "
                "WHERE current_campaign_id=%s",
                [campaign_id],
            )
            assert cursor.rowcount == 1
            cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
    elif kind == "current_campaign":
        with work_transaction():
            lock_current_campaign_exports()
    else:
        invalidate_rehearsal(campaign_id=campaign_id, admit=lambda campaign: True)
    return kind


@pytest.mark.parametrize(
    "kind",
    [
        "work_gate",
        "lifecycle",
        "go_live_gate",
        "campaign_transaction",
        "invalidate_rehearsal",
        "begin_transition",
        "activation",
        "current_campaign",
    ],
)
def test_transition_waits_for_an_export_admitted_before_it(
    scenario,  # noqa: F811
    monkeypatch,
    kind,
):
    """Each transition's own SQL or Python path queues behind an open admission."""
    store, principal, facts, _ = scenario
    campaign_id = facts.campaign_id
    assert CampaignCredentialState.objects.filter(
        campaign_id=campaign_id, go_live_gate=False
    ).exists()
    inside, release = Event(), Event()
    original = export_services.authorize

    def paused(*args, **kwargs):
        """Hold the admission open after it took its campaign lock."""
        inside.set()
        assert release.wait(20)
        return original(*args, **kwargs)

    monkeypatch.setattr(export_services, "authorize", paused)
    ready = Queue()
    with ThreadPoolExecutor(max_workers=2) as pool:
        admission = pool.submit(contender, Queue(), lambda: request_export(scenario))
        assert inside.wait(20)
        blocked = pool.submit(
            contender, ready, lambda: transition_statement(kind, campaign_id)
        )
        wait_for_lock(ready.get(timeout=5), CAMPAIGN)
        assert not blocked.done()
        release.set()
        exported = admission.result(timeout=30)
        assert blocked.result(timeout=30) == kind
    assert ExportRequest.objects.filter(pk=exported.pk).exists()


def close_gate(campaign_id):
    """Simulate the purge owner's reserved gate, as the export suites do."""
    with connection.cursor() as cursor:
        cursor.execute(
            "ALTER TABLE stewardship_campaign_work_gate DISABLE TRIGGER USER"
        )
        CampaignWorkGate.objects.create(
            campaign_id=campaign_id,
            request_id=uuid4(),
            initiated_by_id=uuid4(),
            state="preparing",
        )
        cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
        cursor.execute("ALTER TABLE stewardship_campaign_work_gate ENABLE TRIGGER USER")


@pytest.mark.parametrize("kind", ["work_gate", "go_live_gate"])
def test_export_waits_for_a_closing_transition_and_is_then_refused(
    scenario,  # noqa: F811
    kind,
):
    """An admission queued behind a closing transition sees it and is refused."""
    _, _, facts, _ = scenario
    campaign_id = facts.campaign_id
    assert CampaignCredentialState.objects.filter(
        campaign_id=campaign_id, go_live_gate=False
    ).exists()
    ready = Queue()
    started, release = Event(), Event()

    def admit(campaign):
        """Let the export queue while the go-live owner holds its locks."""
        started.set()
        assert release.wait(20)
        return True

    def close():
        """Close admission through the real transition owner."""
        if kind == "work_gate":
            with campaign_transaction(campaign_id, correlation_id=uuid4()):
                close_gate(campaign_id)
                started.set()
                assert release.wait(20)
        else:
            invalidate_rehearsal(campaign_id=campaign_id, admit=admit)

    with ThreadPoolExecutor(max_workers=2) as pool:
        closing = pool.submit(contender, Queue(), close)
        assert started.wait(20)
        admission = pool.submit(contender, ready, lambda: request_export(scenario))
        wait_for_lock(ready.get(timeout=5), CAMPAIGN)
        assert not admission.done()
        release.set()
        closing.result(timeout=30)
        with pytest.raises(PermissionError):
            admission.result(timeout=30)
    assert not ExportRequest.objects.exists()


def test_sql_capture_guard_takes_the_campaign_lock_itself(scenario):  # noqa: F811
    """A capture trigger orders itself even without the Python helper.

    A transition holds the global and campaign locks; a direct capture insert
    queues on the campaign lock, never on the global one, before any check.
    """
    _, _, facts, _ = scenario
    ready = Queue()

    def capture():
        """Insert a capture directly; the trigger refuses this synthetic actor."""
        with (
            pytest.raises(DatabaseError, match="capture is unavailable"),
            transaction.atomic(),
        ):
            DirectoryExportSnapshot.objects.create(
                campaign_id=facts.campaign_id,
                configuration_id=uuid4(),
                actor_id=uuid4(),
                correlation_id=uuid4(),
                parameters={},
            )

    with ThreadPoolExecutor(max_workers=1) as pool:
        with work_transaction():
            lock_campaign_exports(facts.campaign_id)
            future = pool.submit(contender, ready, capture)
            pid = ready.get(timeout=5)
            wait_for_lock(pid, CAMPAIGN)
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT EXISTS (SELECT 1 FROM pg_locks WHERE pid=%s "
                    f"AND locktype='advisory' AND {GLOBAL})",
                    [pid],
                )
                assert cursor.fetchone()[0] is False
        future.result(timeout=30)


def test_regenerate_reads_its_campaign_then_takes_only_that_lock(
    scenario,  # noqa: F811
    monkeypatch,
):
    """Regeneration skips a global-lock holder but queues behind a transition."""
    store, principal, _, _ = scenario
    original = request_export(scenario)
    run_export(scenario, original)
    publication = ExportPublication.objects.get(request=original)
    later = publication.expires_at + timedelta(minutes=1)
    monkeypatch.setattr(export_services, "database_now", lambda: later)

    def regenerate():
        """Regenerate the expired export through the real service."""
        return export_services.regenerate_export(
            store, principal.pk, original.pk, request_key=uuid4()
        )

    ready = Queue()
    with ThreadPoolExecutor(max_workers=1) as pool:
        with work_transaction():
            first = pool.submit(contender, ready, regenerate)
            ready.get(timeout=5)
            # Completes while this session holds the global lock.
            assert first.result(timeout=60).fact_set_id == original.fact_set_id
        with work_transaction():
            lock_campaign_exports(original.campaign_id)
            second = pool.submit(contender, ready, regenerate)
            wait_for_lock(ready.get(timeout=5), CAMPAIGN)
            assert not second.done()
        regenerated = second.result(timeout=60)
    assert regenerated.pk != original.pk
    assert ExportRequest.objects.filter(campaign_id=original.campaign_id).count() == 3

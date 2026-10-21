"""An ended export's saved data is discarded, and a live one's never (#1398).

Frozen migration 0109: when an export ends (its file's window is over, it
was cancelled and its work stopped, or its failed run was not retried
within one window), its capture's document is cleared, its fact and source
pins are released and its link to a fact generation is cleared, once, by
the owner-run discard functions the scheduler calls
(``reports.export_cleanup.produce_discards``). The request and its history
stay, and each discard is recorded. The capture triggers admit exactly that
clearing; a stopped batch is logged as a timeout and a skipped export as a
WARNING.
"""

import json
from uuid import uuid4

import pytest
from django.db import DatabaseError, connection, transaction

from parishkit.stewardship.audit.models import AuditContext
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.jobs.scheduler import scheduler_session
from parishkit.stewardship.jobs.storage import _status
from parishkit.stewardship.reports import export_cleanup
from parishkit.stewardship.reports.export_cleanup import produce_discards
from parishkit.stewardship.reports.export_models import (
    ExportDiscard,
    ExportRequest,
    InformationExportSnapshot,
    ParticipationExportSnapshot,
)
from parishkit.stewardship.reports.export_services import cancel_export
from parishkit.stewardship.reports.models import CampaignFactPin

from .test_background_grants_postgresql import task_login
from .test_export_cleanup_postgresql import expire_publication
from .test_export_jobs_postgresql import (  # noqa: F401
    request_export,
    run_export,
    scenario,
)
from .test_taskrun_postgresql import act

pytestmark = pytest.mark.django_db(transaction=True)


def discard(monkeypatch):
    """One scheduler discard pass, as the real producer runs it."""
    monkeypatch.setattr(export_cleanup, "_last_discard", [None])
    with task_login(ServiceRole.SCHEDULER), scheduler_session() as guard:
        assert produce_discards(guard) == ()


def age(table, row_id, column, minutes):
    """Install an aged fixture as schema owner, with every guard back after."""
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(f"ALTER TABLE {table} DISABLE TRIGGER USER")
        cursor.execute(
            f"UPDATE {table} SET {column}=clock_timestamp()-make_interval(mins=>%s) "
            "WHERE id=%s",
            [minutes, row_id],
        )
        cursor.execute(f"ALTER TABLE {table} ENABLE TRIGGER USER")


def ended(request_id, window=None):
    """``stewardship_export_ended_v1`` under ``window`` minutes (unset: 240).

    The setting is local to this transaction, so it never carries into a
    later test on the same connection.
    """
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(
            "SELECT set_config('stewardship.export_window_minutes', %s, true)",
            ["" if window is None else str(window)],
        )
        cursor.execute("SELECT stewardship_export_ended_v1(%s)", [request_id])
        return cursor.fetchone()[0]


def test_an_ended_exports_pin_goes_and_a_live_ones_stays(
    scenario,  # noqa: F811
    monkeypatch,
):
    """A file past its window loses its fact pin, once, with a record; a
    file still downloadable keeps everything, and a direct discard of it is
    refused."""
    live = request_export(scenario)
    run_export(scenario, live)
    gone = request_export(scenario)
    run_export(scenario, gone)
    expire_publication(gone)
    # The scheduler looks only at requests at least the shortest window old.
    for row in (live, gone):
        age("stewardship_export_request", row.pk, "created_at", 20)
    pins = CampaignFactPin.objects.filter(parent_kind="export")
    assert set(pins.values_list("parent_id", flat=True)) == {live.pk, gone.pk}
    discard(monkeypatch)
    assert set(pins.values_list("parent_id", flat=True)) == {live.pk}
    record = ExportDiscard.objects.get()
    assert (record.request_id, record.exact_request_id) == (gone.pk, None)
    # The Response dashboard's Summary capture (#1557) is cleared with it;
    # the live export's stays whole.
    assert (record.documents, record.pins, record.by_deploy) == (1, 1, False)
    assert ParticipationExportSnapshot.objects.get(request=gone).document == {}
    assert ParticipationExportSnapshot.objects.get(request=live).document != {}
    # The request and its history stay; only its link to the generation
    # goes, so compaction may remove it. The live one keeps its link.
    assert ExportRequest.objects.get(pk=gone.pk).fact_set_id is None
    assert ExportRequest.objects.get(pk=live.pk).fact_set_id == live.fact_set_id
    # Again: nothing more is discarded, and the live export keeps its pin.
    discard(monkeypatch)
    assert ExportDiscard.objects.count() == 1 and pins.filter(parent_id=live.pk)
    with (
        pytest.raises(DatabaseError, match="has not ended"),
        transaction.atomic(),
        connection.cursor() as cursor,
    ):
        cursor.execute("SELECT stewardship_export_discard_v1(%s, false)", [live.pk])


def test_the_window_counts_from_publication_and_its_setting(scenario):  # noqa: F811
    """Thirty minutes after its file: live under the 60-minute default and
    the unset (longest) window, ended under 15; a seven-day record does not
    keep it past the setting."""
    request = request_export(scenario)
    run_export(scenario, request)
    publication = request.exportpublication
    age("stewardship_export_publication", publication.pk, "created_at", 30)
    assert ended(request.pk) is False
    assert ended(request.pk, 60) is False
    assert ended(request.pk, 15) is True
    # A setting outside 15-240 is ignored for the longest window.
    assert ended(request.pk, 5) is False


def test_a_failed_export_ends_one_window_after_its_failure(
    scenario,  # noqa: F811
    monkeypatch,
):
    """A failed export keeps its data while it can still be retried, and
    loses it one window after its latest failure."""
    request = request_export(scenario)
    act(act(_status(request.task), "claim"), "permanent_failure")
    latest = request.task.chain_runs.order_by("-retry_sequence").first()
    age("stewardship_export_request", request.pk, "created_at", 300)
    assert ended(request.pk, 60) is False
    discard(monkeypatch)
    assert not ExportDiscard.objects.exists()
    age("stewardship_task_run", latest.pk, "updated_at", 241)
    discard(monkeypatch)
    assert ExportDiscard.objects.get().request_id == request.pk
    assert not CampaignFactPin.objects.filter(parent_id=request.pk).exists()


def test_a_cancelled_export_ends_only_once_its_work_stopped(
    scenario,  # noqa: F811
    monkeypatch,
):
    """A cancellation still waiting for the worker's safe point keeps the
    capture a render may be reading; once the run is cancelled it goes."""
    store, principal, _, _ = scenario
    request = request_export(scenario)
    cancel_export(store, principal.pk, request.pk)
    age("stewardship_export_request", request.pk, "created_at", 30)
    assert ended(request.pk, 60) is False
    discard(monkeypatch)
    assert not ExportDiscard.objects.exists()
    act(act(_status(request.task), "claim"), "safe_cancel")
    assert ended(request.pk, 60) is True
    discard(monkeypatch)
    assert ExportDiscard.objects.get().request_id == request.pk


def test_the_deploy_sweep_is_safe_to_repeat(scenario):  # noqa: F811
    """The migration's sweep (by_deploy) discards every ended export with the
    longest window and, run again, nothing more."""
    request = request_export(scenario)
    run_export(scenario, request)
    expire_publication(request)
    age("stewardship_export_request", request.pk, "created_at", 20)
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(
            "SELECT set_config('stewardship.export_window_minutes','240',true)"
        )
        for discarded in (1, 0):
            cursor.execute(
                "SELECT stewardship_export_discard_batch_v1(1000, true)::text"
            )
            assert json.loads(cursor.fetchone()[0]) == {
                "discarded": discarded,
                "skipped": 0,
            }
    assert ExportDiscard.objects.get().by_deploy


def test_only_the_scheduler_may_discard_and_no_one_may_edit_a_capture(
    scenario,  # noqa: F811
):
    """The scheduler's batch is the scheduler's alone, and no login may run
    a single discard or a batch it labels as the deploy's."""
    request = request_export(scenario)
    for role in (ServiceRole.WEB, ServiceRole.WORKER):
        with (
            task_login(role),
            pytest.raises(DatabaseError, match="permission denied"),
            transaction.atomic(),
            connection.cursor() as cursor,
        ):
            cursor.execute("SELECT stewardship_export_discard_due_v1(1)")
    for statement, values in (
        ("SELECT stewardship_export_discard_v1(%s, false)", [request.pk]),
        ("SELECT stewardship_exact_export_discard_v1(%s, false)", [request.pk]),
        ("SELECT stewardship_export_discard_batch_v1(1, true)", []),
    ):
        with (
            task_login(ServiceRole.SCHEDULER),
            pytest.raises(DatabaseError, match="permission denied"),
            transaction.atomic(),
            connection.cursor() as cursor,
        ):
            cursor.execute(statement, values)


def test_the_export_events_name_their_report_and_format(scenario):  # noqa: F811
    """export_requested carries the report kind and format (#1398)."""
    request = request_export(scenario)
    [context] = AuditContext.objects.filter(
        event__event_type="export_requested", event__subject_id=request.pk
    ).values_list("context", flat=True)
    assert context["report"] == "participation" and context["format"] == "csv"


def test_an_ended_information_exports_capture_is_cleared(
    live_response_service, monkeypatch, tmp_path
):
    """The capture's document, which holds the Families' text, is cleared to
    {} once every export using it has ended; its row and every other column
    stay, and the capture table still refuses any other change.

    A capture shared by two requests (an earlier release's regeneration made
    such pairs) stays whole while the other is live: the first discard
    records 0 documents, and the second, once it ends too, clears it.
    """
    from parishkit.stewardship.reports.information import InformationQuery
    from parishkit.stewardship.reports.information_exports import (
        create_information_export,
    )

    from .test_policy_postgresql import user
    from .test_weekly_observation_postgresql import respond

    harness = live_response_service
    respond(harness, "A private Family request")
    actor = user("admin@example.org").pk

    def export():
        """One information export, queued (there is no worker here)."""
        with task_login(ServiceRole.WEB, exact=True, reconnect=True):
            return create_information_export(
                harness.service.store,
                actor,
                campaign_id=harness.campaign.pk,
                query=InformationQuery(),
                history=False,
                format="csv",
                browser_timezone="UTC",
                request_key=uuid4(),
            )

    def end(request):
        """Fail its run and let one window pass since, as a fixture."""
        act(act(_status(request.task), "claim"), "permanent_failure")
        latest = request.task.chain_runs.order_by("-retry_sequence").first()
        age("stewardship_task_run", latest.pk, "updated_at", 241)
        age("stewardship_export_request", request.pk, "created_at", 241)

    request, other = export(), export()
    snapshot = InformationExportSnapshot.objects.get(pk=request.information_snapshot_id)
    assert snapshot.document["rows"]
    # The second request shares the first one's capture (an aged fixture,
    # installed as schema owner).
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute("SET LOCAL session_replication_role = replica")
        cursor.execute(
            "UPDATE stewardship_export_request SET information_snapshot_id=%s "
            "WHERE id=%s",
            [snapshot.pk, other.pk],
        )
    end(request)
    discard(monkeypatch)
    first = ExportDiscard.objects.get()
    assert (first.request_id, first.documents) == (request.pk, 0)
    assert InformationExportSnapshot.objects.get(pk=snapshot.pk).document["rows"]
    end(other)
    discard(monkeypatch)
    second = ExportDiscard.objects.get(request_id=other.pk)
    assert second.documents == 1
    cleared = InformationExportSnapshot.objects.get(pk=snapshot.pk)
    assert cleared.document == {}
    assert (cleared.row_count, cleared.parameters, cleared.source_id) == (
        snapshot.row_count,
        snapshot.parameters,
        snapshot.source_id,
    )
    # Even the owner can change nothing else, nor fill the document again.
    for change in ("row_count=row_count+1", "document='{\"rows\": []}'"):
        with (
            pytest.raises(DatabaseError, match="immutable"),
            transaction.atomic(),
            connection.cursor() as cursor,
        ):
            cursor.execute(
                f"UPDATE stewardship_information_export_snapshot SET {change} "
                "WHERE id=%s",
                [snapshot.pk],
            )


def test_an_ended_latest_data_export_releases_its_inputs(
    response_service, tmp_path, monkeypatch
):
    """A latest-data export ends with the file it handed off to: once that
    file's data is discarded, its own source pin goes and its inputs no
    longer protect their fact generation from compaction."""
    from parishkit.stewardship.jobs.dispatch import execute_hint
    from parishkit.stewardship.jobs.queues import WorkQueue
    from parishkit.stewardship.reports.exact_models import ExactExportResolution
    from parishkit.stewardship.reports.export_tasks import export_handler
    from parishkit.stewardship.source.snapshot_models import SourceSnapshotPin

    from .test_exact_exports_postgresql import request_exact, run_exact
    from .test_policy_postgresql import user

    harness = response_service
    principal = user("admin@example.org")
    with task_login(ServiceRole.WEB, exact=True):
        request = request_exact(harness, principal, population_scope="current")
    with task_login(ServiceRole.WORKER, exact=True):
        assert run_exact(harness, request)
    resolution = ExactExportResolution.objects.get(request=request)
    root = tmp_path / "reports"
    root.mkdir(mode=0o700)
    with task_login(ServiceRole.WORKER, reconnect=True):
        assert execute_hint(
            resolution.export.task_id,
            queue=WorkQueue.GENERAL,
            worker_id=uuid4(),
            handlers={
                "report_export": export_handler(store=harness.service.store, root=root)
            },
        )
    pins = SourceSnapshotPin.objects.filter(parent_kind="report", parent_id=request.pk)
    assert pins.exists()
    # Inside the window nothing goes.
    for table, row in (
        ("stewardship_export_request", resolution.export_id),
        ("stewardship_exact_export_request", request.pk),
    ):
        age(table, row, "created_at", 20)
    discard(monkeypatch)
    assert not ExportDiscard.objects.exists() and pins.exists()
    expire_publication(resolution.export)
    discard(monkeypatch)
    assert set(ExportDiscard.objects.values_list("request_id", "exact_request_id")) == {
        (resolution.export_id, None),
        (None, request.pk),
    }
    assert not pins.exists()
    assert not CampaignFactPin.objects.filter(parent_id=resolution.export_id).exists()
    # Its inputs no longer hold their fact generation.
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT count(*) FROM stewardship_exact_export_request r "
            "JOIN stewardship_daily_fact_set f ON ROW(r.campaign_id,"
            "r.population_scope,r.source_id,r.submission_watermark,"
            "r.timezone_configuration_id,r.through_date)=ROW(f.campaign_id,"
            "f.population_scope,f.source_id,f.submission_watermark,"
            "f.timezone_configuration_id,f.through_date) WHERE r.id=%s "
            "AND NOT EXISTS(SELECT 1 FROM stewardship_export_discard d "
            "WHERE d.exact_request_id=r.id)",
            [request.pk],
        )
        assert cursor.fetchone()[0] == 0


CAPTURES = (
    "information",
    "directory",
    "ministry",
    "financial",
    "family_test_names",
    "timeline",
    "comparison",
    "participation",
)
# A value for each NOT NULL column type a capture row needs (an aged
# fixture: its keys are never checked, since the transaction rolls back).
FILLERS = {
    "uuid": "gen_random_uuid()",
    "jsonb": """'{"rows": [1]}'::jsonb""",
    "integer": "0",
    "bigint": "0",
    "smallint": "0",
    "boolean": "false",
    "text": "'x'",
    "character varying": "'x'",
    "timestamp with time zone": "clock_timestamp()",
    "date": "current_date",
}


@pytest.mark.parametrize("kind", CAPTURES)
def test_each_capture_trigger_admits_only_the_discards_clearing(kind):
    """Every capture kind's trigger admits the owner clearing the document to
    {}, and still refuses every other change; the admission check says no
    to a runtime login."""
    table = f"stewardship_{kind}_export_snapshot"
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(
            "SELECT column_name, data_type FROM information_schema.columns "
            "WHERE table_schema='public' AND table_name=%s AND is_nullable='NO' "
            "AND column_default IS NULL",
            [table],
        )
        columns = dict(cursor.fetchall())
        names = ", ".join(columns)
        values = ", ".join(FILLERS[kind_] for kind_ in columns.values())
        # Every trigger (and key check) is off for the fixture's insert only.
        cursor.execute("SET LOCAL session_replication_role = replica")
        cursor.execute(f"INSERT INTO {table} ({names}) VALUES ({values}) RETURNING id")
        [row] = cursor.fetchone()
        cursor.execute("SET LOCAL session_replication_role = origin")
        # Neither another document nor any other column, even with the
        # clearing.
        for change in (
            """document='{"rows": []}'""",
            "id=gen_random_uuid()",
            "document='{}', id=gen_random_uuid()",
        ):
            with pytest.raises(DatabaseError, match="immutable"), transaction.atomic():
                cursor.execute(f"UPDATE {table} SET {change} WHERE id=%s", [row])
        cursor.execute(f"UPDATE {table} SET document='{{}}' WHERE id=%s", [row])
        assert cursor.rowcount == 1
        # Cleared, it can be neither filled again nor deleted.
        for statement in (
            f"UPDATE {table} SET document='{{\"rows\": [2]}}' WHERE id=%s",
            f"DELETE FROM {table} WHERE id=%s",
        ):
            with pytest.raises(DatabaseError, match="immutable"), transaction.atomic():
                cursor.execute(statement, [row])
        transaction.set_rollback(True)
    with task_login(ServiceRole.WORKER), connection.cursor() as cursor:
        cursor.execute(
            "SELECT stewardship_export_capture_cleared_v1(%s::regclass, "
            """'{"document": {"rows": [1]}}', '{"document": {}}')""",
            [table],
        )
        assert cursor.fetchone()[0] is False


def test_compaction_removes_an_ended_exports_generation(tmp_path, monkeypatch):
    """A superseded generation an export used stays while the export lives;
    once the export has ended and its data is discarded, fact compaction
    removes the generation (the request no longer links it)."""
    from dataclasses import replace

    from parishkit.stewardship.accounts.authority import AuthorityStore
    from parishkit.stewardship.accounts.configuration_schema import validate_sections
    from parishkit.stewardship.campaigns.credential_models import (
        CampaignCredentialState,
    )
    from parishkit.stewardship.reports.facts import publish_fact_set
    from parishkit.stewardship.reports.retention import compact_facts

    from .fact_builders import fact_fixture, staged_facts
    from .test_policy_postgresql import user
    from .test_source_snapshots_postgresql import permit

    inputs, owner, source = fact_fixture(tmp_path)
    CampaignCredentialState.objects.get_or_create(campaign_id=inputs.campaign_id)
    old, _ = staged_facts(inputs, owner, source)
    publish_fact_set(old.pk, owner, admit=permit, interactive=True)
    root = tmp_path / "reports"
    root.mkdir(mode=0o700)
    setup = (
        AuthorityStore(tmp_path, validate_sections),
        user("admin@example.org"),
        old,
        root,
    )
    request = request_export(setup)
    run_export(setup, request)
    new, _ = staged_facts(replace(inputs, submission_watermark=2), owner, source)
    publish_fact_set(new.pk, owner, admit=permit, interactive=True)
    # The live export keeps its generation.
    assert compact_facts(inputs.campaign_id, owner, admit=permit) == []
    expire_publication(request)
    age("stewardship_export_request", request.pk, "created_at", 20)
    discard(monkeypatch)
    assert ExportRequest.objects.get(pk=request.pk).fact_set_id is None
    assert compact_facts(inputs.campaign_id, owner, admit=permit) == [old.pk]


def test_a_discard_held_up_by_a_lock_is_logged_as_a_timeout(
    scenario,  # noqa: F811
    monkeypatch,
):
    """A batch that waits past its lock limit is rolled back and logged with
    what stopped it, the limit and the time it took; nothing is discarded."""
    from django.db import connections

    from parishkit.stewardship.audit.models import OperationalLog

    request = request_export(scenario)
    run_export(scenario, request)
    expire_publication(request)
    age("stewardship_export_request", request.pk, "created_at", 20)
    monkeypatch.setitem(export_cleanup.DISCARD_LIMITS, "lock_timeout", 1)
    other = connections.create_connection("default")
    try:
        other.set_autocommit(False)
        with other.cursor() as cursor:
            # A retry holding the export's runs.
            cursor.execute(
                "SELECT 1 FROM stewardship_task_run WHERE root_id=%s FOR UPDATE",
                [request.task_id],
            )
        discard(monkeypatch)
    finally:
        other.rollback()
        other.close()
    assert not ExportDiscard.objects.exists()
    entry = OperationalLog.objects.get(event="task_timed_out")
    assert (entry.level, entry.context["what"]) == ("WARNING", "lock_timeout")
    assert entry.context["limit_seconds"] == 1 and "elapsed_seconds" in entry.context
    # The next pass, with nothing in its way, discards it.
    discard(monkeypatch)
    assert ExportDiscard.objects.get().request_id == request.pk


def test_discards_run_at_most_once_a_minute_and_log_skipped_exports(
    scenario,  # noqa: F811
    monkeypatch,
):
    """A second pass within the minute does nothing, failed or not; an export
    the SQL could not discard is counted in a WARNING operational entry."""
    from parishkit.stewardship.audit.models import OperationalLog

    request = request_export(scenario)
    run_export(scenario, request)
    monkeypatch.setattr(export_cleanup, "_last_discard", [None])
    clock = iter((1000.0, 1030.0, 1061.0)).__next__
    with task_login(ServiceRole.SCHEDULER), scheduler_session() as guard:
        assert produce_discards(guard, clock=clock) == ()
    expire_publication(request)
    age("stewardship_export_request", request.pk, "created_at", 20)
    with task_login(ServiceRole.SCHEDULER), scheduler_session() as guard:
        # Thirty seconds later: the throttle holds, nothing goes.
        assert produce_discards(guard, clock=clock) == ()
        assert not ExportDiscard.objects.exists()
        assert produce_discards(guard, clock=clock) == ()
    assert ExportDiscard.objects.get().request_id == request.pk
    # A failing discard is skipped, counted and logged; the pass goes on.
    failing = request_export(scenario)
    run_export(scenario, failing)
    expire_publication(failing)
    age("stewardship_export_request", failing.pk, "created_at", 20)
    with connection.cursor() as cursor:
        # A fact pin the discard cannot remove stands in for any failure.
        cursor.execute(
            "CREATE FUNCTION public.pk_test_refuse_v1() RETURNS trigger "
            "LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'synthetic'; END $$"
        )
        cursor.execute(
            "CREATE TRIGGER pk_test_refuse BEFORE DELETE ON stewardship_fact_pin "
            f"FOR EACH ROW WHEN (OLD.parent_id='{failing.pk}') "
            "EXECUTE FUNCTION public.pk_test_refuse_v1()"
        )
    try:
        discard(monkeypatch)
    finally:
        with connection.cursor() as cursor:
            cursor.execute("DROP FUNCTION public.pk_test_refuse_v1() CASCADE")
    assert not ExportDiscard.objects.filter(request_id=failing.pk).exists()
    entry = OperationalLog.objects.get(event="task_failed")
    assert entry.level == "WARNING" and entry.schema == "failure"
    assert entry.context == {
        "failure": "export_discard",
        "count": 1,
        "outcome": "retry",
    }
    discard(monkeypatch)
    assert ExportDiscard.objects.filter(request_id=failing.pk).exists()

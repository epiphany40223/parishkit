"""Family email progress against PostgreSQL (#413): counts, cost, grants and locks.

A real prepared (and a real uncertain) Family email is read through the
restricted web login, including while another session holds the global
work-order lock the send's own workers take. Launch scale, every message and
occurrence state, Family recovery and the reminder hand-over are measured on
temporary copies of the three tables the panel reads: temporary tables are
searched before ``public``, keep the real indexes, and can hold about 1,100
Families' rows without running 1,100 real preparations. Every generated
identifier is derived from a fixed name and row number, so runs repeat.

The send's total counts the Families planning has not reached yet, so it is
also checked part-way through real planning (Testing and a reminder) and on
the launch-scale copies, which add Families with no occurrence yet.
"""

import json
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import timedelta
from queue import Queue
from uuid import UUID, uuid4

import pytest
from django.db import connection
from django.db.models import F
from psycopg import sql

from parishkit.stewardship.accounts.models import PortalSession
from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.audit.models import AuditEvent
from parishkit.stewardship.campaigns.credential_models import (
    CampaignCredentialState,
    FamilyCampaign,
)
from parishkit.stewardship.campaigns.family_identity import FamilyStatus
from parishkit.stewardship.campaigns.family_schedule_planning import plan_family
from parishkit.stewardship.campaigns.lifecycle import Action
from parishkit.stewardship.campaigns.models import RestoreDeliveryHold
from parishkit.stewardship.campaigns.runtime_models import ActivationCatchUpDemand
from parishkit.stewardship.campaigns.schedule_models import (
    ScheduleDefinition,
    ScheduleOccurrence,
)
from parishkit.stewardship.campaigns.schedule_production import (
    FamilyScheduleProducer,
)
from parishkit.stewardship.campaigns.schedules import (
    change_occurrence,
    create_occurrence,
    record_fulfillment,
)
from parishkit.stewardship.campaigns.work_locks import (
    read_transaction,
    work_transaction,
)
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.jobs import send_progress
from parishkit.stewardship.jobs.dispatch import execute_hint
from parishkit.stewardship.jobs.outbox_models import OutboxMessage
from parishkit.stewardship.jobs.ownership import database_now
from parishkit.stewardship.jobs.scheduler import scheduler_session

from ..policy_factory import address
from .auth_builders import signed_in
from .campaign_builders import (
    add_draft,
    admit_test_work,
    advance,
    campaign_clock,
    change,
    claimed_task,
    command,
    occurrence,
    restored_runtime,
)
from .credential_builders import family_campaign, populate
from .test_background_grants_postgresql import task_login
from .test_catchup_preparation_postgresql import execution_arguments
from .test_delivery_views_postgresql import uncertain
from .test_export_campaign_lock_postgresql import contender
from .test_family_auth_postgresql import family_service  # noqa: F401
from .test_family_mail_dispatch_postgresql import prepare
from .test_family_mail_preparation_postgresql import family_mail  # noqa: F401
from .test_family_schedule_planning_postgresql import add_reminders

pytestmark = pytest.mark.django_db(transaction=True)

PAGE = "/admin/deliveries/family-progress"
STATUS = PAGE + "/status"
SHADOWED = (
    "stewardship_schedule_definition",
    "stewardship_schedule_occurrence",
    "stewardship_outbox_message",
    "stewardship_family_campaign",
)
# Launch-scale Families planning has not reached yet: eligible ones the send
# will still email, and ineligible ones it never will.
UNPLANNED = range(9001, 9021)
INELIGIBLE = range(9101, 9106)
# Unrelated rows (other schedules' occurrences, receipts and digests) so the
# planner sees a realistically mixed table rather than only this send: about
# a launch's worth, then about several years' worth of outgoing mail.
NOISE = 4000
YEARS = 50000


def prepared(harness):
    """One real prepared invitation, made when its schedule falls due."""
    with campaign_clock(ScheduleDefinition.objects.get().current_revision.due_at):
        return prepare(harness)


def source(harness):
    """The real (message, occurrence) identifiers that clones copy."""
    message = prepared(harness)
    return message.pk, ScheduleOccurrence.objects.get(outbox_id=message.pk).pk


def scope():
    """The current campaign, mode and Production cycle, as the view derives them."""
    configuration = SystemConfiguration.objects.select_related("current_campaign").get()
    campaign = configuration.current_campaign
    production = configuration.mode == "production"
    return (
        campaign.pk,
        configuration.mode,
        campaign.production_cycle if production else 0,
    )


def test_the_web_login_follows_a_real_send_during_a_work_order_hold(
    family_mail,  # noqa: F811
    google,
):
    """A prepared email is remaining; polls finish while the lock is held."""
    message = prepared(family_mail)
    browser, _ = signed_in()
    ready = Queue()
    with (
        task_login(ServiceRole.WEB, exact=True, reconnect=True),
        ThreadPoolExecutor(max_workers=1) as pool,
    ):
        page = browser.get(PAGE)
        assert page.status_code == 200
        body = page.content.decode()
        assert "Invitation email" in body
        assert "0 of 1 emails finished (0%)" in body
        assert 'data-live-url="/admin/deliveries/family-progress/status"' in body
        assert "data-live-pending" in body
        assert "live-status-v1.js" in body
        # The panel is in the sidebar and the breadcrumb trail.
        assert (
            '<a href="/admin/deliveries/family-progress" aria-current="page">' in body
        )
        assert '<span aria-current="page">Family email progress</span>' in body
        activity = PortalSession.objects.get().last_activity_at
        views = AuditEvent.objects.filter(event_type="delivery_viewed").count()
        assert views == 1
        # Another session holds the global work-order lock, as a sending
        # worker does; the poll must not wait for it.
        with work_transaction():
            future = pool.submit(contender, ready, lambda: browser.get(STATUS))
            ready.get(timeout=5)
            status = future.result(timeout=60)
        assert status.status_code == 200
        fragment = status.content.decode()
        assert 'data-live-status="send-progress"' in fragment
        assert "data-live-pending" in fragment and "data-live-url" not in fragment
        # A fragment, not a page: no Admin chrome and no second audited view.
        assert 'aria-label="Administration"' not in fragment
    assert AuditEvent.objects.filter(event_type="delivery_viewed").count() == views
    assert PortalSession.objects.get().last_activity_at == activity
    assert family_mail.code not in body + fragment
    assert message.sealed_substitutions not in body + fragment


def test_a_finished_send_is_not_in_progress_and_is_summarised(
    family_mail,  # noqa: F811
    google,
):
    """Delivery unknown is settled: uncertain, never sent, and not in progress.

    The page says no send is in progress, summarises the send in one line
    with no progress bar, links to Outgoing mail and keeps checking.
    """
    uncertain(family_mail)
    browser, _ = signed_in()
    with task_login(ServiceRole.WEB, exact=True):
        body = browser.get(STATUS).content.decode()
    assert "No Family email send is in progress right now" in body
    assert "Last send: Invitation email, finished <time" in body
    assert "0 sent, 0 failed, 1 uncertain, 0 not sent." in body
    assert 'href="/admin/deliveries">Outgoing mail</a>' in body
    assert "<progress" not in body
    assert "data-live-pending" in body


@pytest.mark.parametrize("role", ["staff", "ministry_leader"])
def test_progress_is_for_administrators_only(auth_service, google, role):
    """Neither the page nor its fragment nor the sidebar entry reach other roles."""
    store = auth_service.store
    add_draft(store, store.active(), uuid4())
    admin, _ = signed_in()
    assert admin.get(PAGE).status_code == 200
    assert b"Family email progress" in admin.get("/admin/").content
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
    reader, _ = signed_in()
    assert reader.get(PAGE).status_code == 403
    assert reader.get(STATUS).status_code == 403
    assert b"Family email progress" not in reader.get("/admin/").content


def test_no_send_yet_says_so_and_keeps_checking(auth_service, google):
    """A draft campaign with nothing due: nothing in progress, still checking."""
    store = auth_service.store
    add_draft(store, store.active(), uuid4())
    browser, _ = signed_in()
    with task_login(ServiceRole.WEB, exact=True):
        body = browser.get(PAGE).content.decode()
    assert "No Family email send is in progress right now" in body
    assert "Last send" not in body
    assert "data-live-pending" in body


def test_unknown_query_options_are_refused(auth_service, google):
    """The page takes no options; a stray one is a plain refusal, not ignored."""
    browser, _ = signed_in()
    assert browser.get(PAGE + "?page=2").status_code == 400


@contextmanager
def shadowed():
    """Temporary copies of the tables the panel reads, with their real indexes."""
    with connection.cursor() as cursor:
        for table in SHADOWED:
            cursor.execute(
                sql.SQL("CREATE TEMP TABLE {} (LIKE public.{} INCLUDING ALL)").format(
                    sql.Identifier(table), sql.Identifier(table)
                )
            )
        cursor.execute(
            "INSERT INTO pg_temp.stewardship_schedule_definition "
            "SELECT * FROM public.stewardship_schedule_definition"
        )
    try:
        yield
    finally:
        with connection.cursor() as cursor:
            for table in SHADOWED:
                cursor.execute(
                    sql.SQL("DROP TABLE pg_temp.{}").format(sql.Identifier(table))
                )


def plan():
    """Rows of a launch-size invitation send: one tuple per occurrence.

    Each is (family, occurrence state, message state or None, minutes since
    it settled or None, recovery generation, occurrence reason). Expected
    per-Family counts: sent 605, failed 15 (5 before preparation), uncertain
    5, remaining 455 (10 of them submitting and 10 waiting to retry, 50 not
    yet prepared), could not be emailed 8, not needed 15, and 215 settled in
    the last five minutes.
    """
    rows = []

    def add(count, occurrence, message, ago=None, generation=0, family=None, reason=""):
        """Append ``count`` occurrences; each new Family unless one is named."""
        for _ in range(count):
            rows.append(
                (family or len(rows) + 1, occurrence, message, ago, generation, reason)
            )

    add(400, "succeeded", "delivered", 10)
    add(200, "succeeded", "delivered", 1)
    add(10, "failed", "permanent_failure", 1)
    add(5, "delivery_unknown", "delivery_unknown", 1)
    add(385, "pending", "pending")
    add(10, "pending", "submitting")
    add(10, "pending", "retry_wait")
    add(10, "skipped", "cancelled", 10, reason="family_responded")
    add(50, "pending", None)
    add(5, "failed", None, reason="preparation_failed")
    add(5, "skipped", None, reason="no_deliverable_recipient")
    add(3, "skipped", None, reason="family_ineligible")
    add(5, "coalesced", None, reason="missed_family_recovery")
    # Deliverability recovery: an earlier failed attempt and its newer,
    # delivered replacement count once, as the newer.
    for family in range(5001, 5006):
        add(1, "failed", "permanent_failure", 10, 0, family)
        add(1, "succeeded", "delivered", 10, 1, family)
    return rows


def clone_rows(rows, *, source, definition, due_minutes, label, cycle):
    """Clone the real prepared ``source`` (message, occurrence) once per row.

    Identifiers derive from ``label`` and the row number. Remaining messages
    were last changed when created; settled ones ``ago`` minutes before now.
    """
    message, occurrence = source
    columns = {
        "occurrence": [
            field.column for field in ScheduleOccurrence._meta.concrete_fields
        ],
        "message": [field.column for field in OutboxMessage._meta.concrete_fields],
    }
    occurrence_values = {
        "id": "md5(%(label)s||'o'||p.n)::uuid",
        "definition_id": "%(definition)s",
        "target": "'family:'||md5(%(label)s||'f'||p.family)::uuid",
        "occurrence_key": "md5(%(label)s||'k'||p.n)||md5(%(label)s||'K'||p.n)",
        "state": "p.occurrence",
        "reason": "p.reason",
        "replacement_id": "CASE WHEN p.occurrence='coalesced' "
        "THEN md5(%(label)s||'r'||p.n)::uuid END",
        "outbox_id": "CASE WHEN p.message IS NULL THEN NULL "
        "ELSE md5(%(label)s||'m'||p.n)::uuid END",
        "recovery_generation": "p.generation",
        "production_cycle": "%(cycle)s",
        "due_at": "now()-%(due)s*interval '1 minute'",
        # A recovery attempt was created after the attempt it replaces.
        "created_at": "now()-(%(due)s-p.generation)*interval '1 minute'",
    }
    message_values = {
        "id": "md5(%(label)s||'m'||p.n)::uuid",
        "semantic_key": "md5(%(label)s||'o'||p.n)::uuid",
        "task_id": "md5(%(label)s||'t'||p.n)::uuid",
        "state": "p.message",
        "created_at": "now()-%(due)s*interval '1 minute'",
        "updated_at": "CASE WHEN p.ago IS NULL THEN now()-%(due)s*interval '1 minute' "
        "ELSE now()-p.ago*interval '1 minute' END",
        "pause_hold_id": "NULL",
        # The outbox CHECK constraints apply to these copies too: a settled
        # email has no sealed values and a finish time, and one handed to
        # the mail service has a numbered attempt.
        "finished_at": "CASE WHEN p.message=ANY(%(terminal)s) THEN "
        "now()-coalesce(p.ago,0)*interval '1 minute' END",
        "sealed_substitutions": "CASE WHEN p.message<>ALL(%(terminal)s) "
        "THEN r.sealed_substitutions END",
        "sealed_key_id": "CASE WHEN p.message<>ALL(%(terminal)s) "
        "THEN r.sealed_key_id END",
        "attempt": "CASE WHEN p.message=ANY(%(unsent)s) THEN 0 ELSE 1 END",
        "run_id": "CASE WHEN p.message<>ALL(%(unsent)s) "
        "THEN md5(%(label)s||'run'||p.n)::uuid END",
        "task_fence": "CASE WHEN p.message<>ALL(%(unsent)s) THEN 1 END",
        "worker_id": "CASE WHEN p.message<>ALL(%(unsent)s) "
        "THEN md5(%(label)s||'w'||p.n)::uuid END",
        "submitted_at": "CASE WHEN p.message<>ALL(%(unsent)s) "
        "THEN now()-%(due)s*interval '1 minute' END",
        "provider_deadline": "CASE WHEN p.message<>ALL(%(unsent)s) THEN now() END",
    }

    def select(kind, overrides, source):
        """INSERT … SELECT copying the real row except the planned columns."""
        names = sql.SQL(",").join(map(sql.Identifier, columns[kind]))
        values = sql.SQL(",").join(
            sql.SQL(overrides[name]) if name in overrides else sql.Identifier("r", name)
            for name in columns[kind]
        )
        return sql.SQL(
            "INSERT INTO pg_temp.{} ({}) SELECT {} FROM public.{} r, pk413_plan p "
            "WHERE r.id=%(source)s{}"
        ).format(
            sql.Identifier(source),
            names,
            values,
            sql.Identifier(source),
            sql.SQL(" AND p.message IS NOT NULL" if kind == "message" else ""),
        )

    params = {
        "label": label,
        "definition": definition,
        "due": due_minutes,
        "cycle": cycle,
        "terminal": ["delivered", "permanent_failure", "cancelled"],
        "unsent": ["pending", "cancelled"],
    }
    with connection.cursor() as cursor:
        cursor.execute(
            "CREATE TEMP TABLE pk413_plan AS SELECT * FROM unnest("
            "%s::int[],%s::int[],%s::text[],%s::text[],%s::int[],%s::int[],%s::text[]"
            ") AS p(n,family,occurrence,message,ago,generation,reason)",
            [list(range(1, len(rows) + 1)), *map(list, zip(*rows, strict=True))],
        )
        cursor.execute(
            select("occurrence", occurrence_values, "stewardship_schedule_occurrence"),
            params | {"source": occurrence},
        )
        cursor.execute(
            select("message", message_values, "stewardship_outbox_message"),
            params | {"source": message},
        )
        cursor.execute("DROP TABLE pk413_plan")


def clone_families(family, numbers, *, label, eligible=True):
    """Copy the real Family row once per number, as the Family ``clone_rows`` targets.

    Each copy's id is the one ``clone_rows`` derives from ``label`` and the
    Family number, so a copy with occurrences is planned and one without is
    not. An ineligible copy is one the send never emails.
    """
    columns = [field.column for field in FamilyCampaign._meta.concrete_fields]
    overrides = {"id": "md5(%(label)s||'f'||n)::uuid", "family_duid": "100000+n"}
    if not eligible:
        overrides |= {"email_eligible": "false", "email_deliverable": "false"}
    with connection.cursor() as cursor:
        cursor.execute(
            sql.SQL(
                "INSERT INTO pg_temp.stewardship_family_campaign ({}) SELECT {} "
                "FROM public.stewardship_family_campaign r, "
                "unnest(%(numbers)s::int[]) n WHERE r.id=%(source)s"
            ).format(
                sql.SQL(",").join(map(sql.Identifier, columns)),
                sql.SQL(",").join(
                    sql.SQL(overrides[name])
                    if name in overrides
                    else sql.Identifier("r", name)
                    for name in columns
                ),
            ),
            {"label": label, "numbers": list(numbers), "source": family},
        )


def add_noise(first, count, models=(ScheduleOccurrence, OutboxMessage)):
    """Other schedules' occurrences and other mail, which the panel never reads.

    Rows ``first`` to ``first + count - 1`` are added, then the copies are
    analyzed, as autovacuum would analyze the real tables.
    """
    for model, overrides in (
        (
            ScheduleOccurrence,
            {
                "id": "md5('pk413-noise-o'||n)::uuid",
                "definition_id": "md5('pk413-noise-d')::uuid",
                "target": "'family:'||md5('pk413-noise-f'||n)::uuid",
                "occurrence_key": "md5('pk413-noise-k'||n)",
                "outbox_id": "NULL",
            },
        ),
        (
            OutboxMessage,
            {
                "id": "md5('pk413-noise-m'||n)::uuid",
                "semantic_key": "md5('pk413-noise-s'||n)::uuid",
                "task_id": "md5('pk413-noise-t'||n)::uuid",
                "purpose": "'receipt'",
            },
        ),
    ):
        if model not in models:
            continue
        table = sql.Identifier(model._meta.db_table)
        columns = [field.column for field in model._meta.concrete_fields]
        with connection.cursor() as cursor:
            cursor.execute(
                sql.SQL(
                    "INSERT INTO pg_temp.{} ({}) SELECT {} FROM "
                    "(SELECT * FROM pg_temp.{} LIMIT 1) r, "
                    "generate_series(%s,%s) n"
                ).format(
                    table,
                    sql.SQL(",").join(map(sql.Identifier, columns)),
                    sql.SQL(",").join(
                        sql.SQL(overrides[name])
                        if name in overrides
                        else sql.Identifier("r", name)
                        for name in columns
                    ),
                    table,
                ),
                [first, first + count - 1],
            )
    with connection.cursor() as cursor:
        for table in SHADOWED:
            cursor.execute(sql.SQL("ANALYZE pg_temp.{}").format(sql.Identifier(table)))


def explain(cursor, statement, values):
    """Execution plan with actual timings for one panel statement."""
    cursor.execute("EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + statement, values)
    return cursor.fetchone()[0][0]


def measure(values):
    """Plans of the panel statements; each reads occurrences by index, quickly.

    Returns (choose, count, waiting, owed, due): a reminder also reads the
    invitation of each Family whose reminder is not prepared yet, to find
    reminders held behind it, every send counts the Families planning has
    not reached yet (here as for an invitation in ``values["mode"]``), and
    the due sends not started yet are looked up.
    The schedule occurrences of one campaign's Family definitions are always
    found through the definition index, never by scanning every occurrence.
    """
    with read_transaction(), connection.cursor() as cursor:
        cursor.execute(send_progress._SCOPE, values)
        epoch = cursor.fetchone()[3]
        plans = (
            explain(cursor, send_progress._LATEST_SEND, values),
            explain(cursor, send_progress._COUNTS, values),
            explain(cursor, send_progress._WAITING, values),
            explain(
                cursor,
                send_progress.owed_statement(values["mode"], "initial"),
                values | {"epoch": epoch},
            ),
            explain(cursor, send_progress._DUE_SENDS, values),
        )
    for plan_ in plans:
        assert "stewardship_schedule_occurrence" not in relations(
            plan_["Plan"], "Seq Scan"
        ), json.dumps(plan_)
        assert plan_["Execution Time"] < 100, json.dumps(plan_)
    return plans


def relations(node, kind):
    """Names of the relations a plan reads with ``kind`` (e.g. Seq Scan)."""
    found = {node["Relation Name"]} if node.get("Node Type") == kind else set()
    for child in node.get("Plans", []):
        found |= relations(child, kind)
    return found


def test_launch_scale_counts_every_state_cheaply_through_indexes(
    family_mail,  # noqa: F811
    google,
    capsys,
):
    """About 1,100 Families: exact counts, index plans and a few milliseconds."""
    real = source(family_mail)
    family = OutboxMessage.objects.get(pk=real[0]).family_id
    campaign, mode, cycle = scope()
    initial = ScheduleDefinition.objects.get(kind="initial")
    # Planning reads due times on the campaign clock, so the panel does too.
    with shadowed(), campaign_clock(initial.current_revision.due_at):
        rows = plan()
        clone_rows(
            rows,
            source=real,
            cycle=cycle,
            definition=initial.pk,
            due_minutes=20,
            label="pk413-i",
        )
        planned = sorted({row[0] for row in rows})
        clone_families(family, [*planned, *UNPLANNED], label="pk413-i")
        clone_families(family, INELIGIBLE, label="pk413-i", eligible=False)
        add_noise(1, NOISE)
        with read_transaction():
            now = database_now()
            counts = send_progress.read_send(campaign, mode, cycle, now)
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT count(*) FROM pg_locks WHERE pid=pg_backend_pid() "
                    "AND locktype='advisory'"
                )
                assert cursor.fetchone()[0] == 0
        assert counts.kind == "initial"
        assert (
            counts.sent,
            counts.failed,
            counts.prepare_failed,
            counts.uncertain,
            counts.remaining,
            counts.waiting,
            counts.unreachable,
            counts.not_needed,
            counts.recent,
        ) == (605, 15, 5, 5, 475, 0, 8, 15, 215)
        # The 20 eligible Families planning has not reached are remaining
        # and not prepared; the ineligible ones are in no count.
        assert (counts.unplanned, counts.unprepared) == (20, 70)
        shown = send_progress.progress(counts)
        assert (shown.total, shown.done, shown.percent) == (1100, 625, 56)
        # 215 settled in five minutes is 43 a minute; 475 left is 12 minutes.
        assert shown.rate == 43 and shown.minutes_left == 12
        # The page renders the same read for the Admin.
        body = signed_in()[0].get(STATUS).content.decode()
        assert "625 of 1,100 emails finished (56%)" in body
        assert "43.0 emails per minute" in body

        values = {
            "campaign": campaign,
            "mode": mode,
            "cycle": cycle,
            "since": now - send_progress.RATE_WINDOW,
            "definition": initial.pk,
            "revision": initial.current_revision_id,
            "unreachable": list(send_progress.UNREACHABLE_REASONS),
        }
        launch = measure(values)
        # With several years of other mail, the send's own messages are
        # found by primary key rather than by reading the whole outbox.
        add_noise(NOISE + 1, YEARS, models=(OutboxMessage,))
        years = measure(values)
        for counted in years[1:]:
            assert "stewardship_outbox_message" not in relations(
                counted["Plan"], "Seq Scan"
            ), json.dumps(counted)
        with capsys.disabled():
            for label, (choose, count, reminder, owed, due) in (
                ("launch", launch),
                ("years", years),
            ):
                print(
                    f"\n#413 {label} cost: choose {choose['Execution Time']:.2f} ms,"
                    f" count {count['Execution Time']:.2f} ms,"
                    f" held reminders {reminder['Execution Time']:.2f} ms,"
                    f" not yet planned {owed['Execution Time']:.2f} ms,"
                    f" due not started {due['Execution Time']:.2f} ms"
                )

        # A reminder that fell due later takes over the panel while both are
        # in progress (the invitation still has 475 remaining): the rule is
        # the in-progress send that fell due most recently. The copied
        # reminder shares the invitation's revision, and so its due time;
        # on that tie the send whose occurrences fell due last (the
        # reminder's, two minutes ago) goes first.
        columns = [field.column for field in ScheduleDefinition._meta.concrete_fields]
        overrides = {"id": "md5('pk413-reminder')::uuid", "kind": "'reminder'"}
        with connection.cursor() as cursor:
            cursor.execute(
                sql.SQL(
                    "INSERT INTO pg_temp.stewardship_schedule_definition ({}) "
                    "SELECT {} FROM pg_temp.stewardship_schedule_definition r "
                    "WHERE r.id=%s RETURNING id"
                ).format(
                    sql.SQL(",").join(map(sql.Identifier, columns)),
                    sql.SQL(",").join(
                        sql.SQL(overrides[name])
                        if name in overrides
                        else sql.Identifier("r", name)
                        for name in columns
                    ),
                ),
                [initial.pk],
            )
            reminder_id = cursor.fetchone()[0]
        clone_rows(
            [
                (1, "succeeded", "delivered", 1, 0, ""),
                (2, "succeeded", "delivered", 1, 0, ""),
                (3, "pending", "pending", None, 0, ""),
            ],
            source=real,
            cycle=cycle,
            definition=reminder_id,
            due_minutes=2,
            label="pk413-r",
        )
        with read_transaction():
            later = send_progress.read_send(campaign, mode, cycle, database_now())
        assert later.kind == "reminder"
        # No copied Family's invitation was delivered (there is no such
        # fulfillment), so no Family is still owed this reminder.
        assert (later.sent, later.remaining, later.recent) == (2, 1, 2)
        assert later.unplanned == 0


def test_a_campaign_without_family_occurrences_has_no_send(family_mail):  # noqa: F811
    """Before anything is due there is no send to show."""
    campaign, mode, cycle = scope()
    with read_transaction():
        assert send_progress.read_send(campaign, mode, cycle, database_now()) is None


def catch_up(campaign, actor, at):
    """Activate at ``at`` and run the real activation catch-up worker."""
    with campaign_clock(at):
        command(campaign, actor, Action.ACTIVATE)
        demand = ActivationCatchUpDemand.objects.get(completed_at__isnull=True)
        with task_login(ServiceRole.WORKER, exact=True):
            assert execute_hint(**execution_arguments(demand))
    demand.refresh_from_db()
    assert demand.completed_at is not None


def test_production_launch_reads_only_its_mode_and_cycle_from_real_rows(tmp_path):
    """Testing rows and a withdrawn cycle's row are not part of the launch."""
    store, campaign, actor, rings = family_campaign(tmp_path)
    populate(
        campaign,
        rings,
        [
            FamilyStatus(1, True, True, True, True),
            FamilyStatus(2, True, True, True, True),
            FamilyStatus(3, True, True, True, False, "eligible", "provider_suppressed"),
            FamilyStatus(4, True, True, False, False, "eligible", "no_eligible_email"),
        ],
        generation=2,
    )
    definition = ScheduleDefinition.objects.select_related("current_revision").get()
    families = list(FamilyCampaign.objects.order_by("family_duid"))
    starts = campaign.active_configuration.starts_at

    def occurrence(mode, family):
        """One real guarded occurrence of the invitation for ``family``."""
        return create_occurrence(
            definition_id=definition.pk,
            revision_id=definition.current_revision_id,
            mode=mode,
            target=f"family:{family.pk}",
            slot="once",
            due_at=definition.current_revision.due_at,
            actor_id=actor,
            correlation_id=uuid4(),
            admit=admit_test_work,
        )

    # Occurrences exist only inside the campaign interval; activation and
    # withdrawal before the start keep the campaign returnable to Testing.
    with campaign_clock(starts - timedelta(days=1)):
        command(campaign, actor, Action.ACTIVATE)
    with campaign_clock(starts):
        retired = occurrence("production", families[1])
        # Return to Testing settles this cycle's open work first, as the
        # withdrawal workflow does (production_withdrawn).
        change_occurrence(
            occurrence_id=retired.pk,
            state="skipped",
            expected_version=retired.version,
            actor_id=actor,
            correlation_id=uuid4(),
            admit=admit_test_work,
            reason="production_withdrawn",
        )
    with campaign_clock(starts - timedelta(days=1)):
        command(campaign, actor, Action.WITHDRAW, reason="Correct setup")
    # Back in Testing, a rehearsal invitation is scheduled.
    with campaign_clock(starts):
        testing = occurrence("testing", families[0])
    campaign.refresh_from_db()
    assert (testing.production_cycle, retired.production_cycle) == (0, 0)
    assert campaign.production_cycle == 1
    catch_up(campaign, actor, definition.current_revision.due_at)
    configuration = SystemConfiguration.objects.get()
    assert configuration.mode == "production"
    assert set(ScheduleOccurrence.objects.values_list("mode", "production_cycle")) == {
        ("testing", 0),
        ("production", 0),
        ("production", 1),
    }
    with read_transaction():
        now = database_now()
        launch = send_progress.read_send(campaign.pk, "production", 1, now)
        rehearsal = send_progress.read_send(campaign.pk, "testing", 0, now)
        retired_cycle = send_progress.read_send(campaign.pk, "production", 0, now)
    assert launch.kind == "initial"
    # Families 1 and 2 wait for preparation; family 3 has no deliverable
    # address and family 4 no eligible email, by their real skip reasons.
    assert (launch.remaining, launch.unreachable, launch.not_needed) == (2, 2, 0)
    assert launch.sent == launch.failed == launch.uncertain == launch.waiting == 0
    # Each mode and cycle is its own send.
    assert (rehearsal.remaining, rehearsal.unreachable) == (1, 0)
    assert (retired_cycle.remaining, retired_cycle.not_needed) == (0, 1)


def test_a_page_opened_before_go_live_schedules_keeps_checking(tmp_path):
    """While activation catch-up is unfinished, a send is about to start."""
    _, campaign, actor, _ = family_campaign(tmp_path)
    due = ScheduleDefinition.objects.get().current_revision.due_at
    with campaign_clock(due):
        command(campaign, actor, Action.ACTIVATE)
    campaign.refresh_from_db()
    # Before the invitation is due there is no send at all; once it is due,
    # it is a due send not started yet: in progress, its Family owed.
    with campaign_clock(due - timedelta(minutes=1)), read_transaction():
        now = database_now()
        assert send_progress.read_send(campaign.pk, "production", 0, now) is None
    with campaign_clock(due), read_transaction():
        now = database_now()
        due_send = send_progress.read_send(campaign.pk, "production", 0, now)
        assert due_send.in_progress and due_send.kind == "initial"
        assert (due_send.remaining, due_send.unplanned, due_send.sent) == (1, 1, 0)
    with read_transaction():
        now = database_now()
        assert send_progress.upcoming(campaign, "production", 0, now)
        # Testing is not followed this way.
        assert not send_progress.upcoming(campaign, "testing", 0, now)
    demand = ActivationCatchUpDemand.objects.get()
    with campaign_clock(due), task_login(ServiceRole.WORKER, exact=True):
        assert execute_hint(**execution_arguments(demand))
    with read_transaction():
        now = database_now()
        assert send_progress.read_send(campaign.pk, "production", 0, now).remaining == 1
        # Scheduled now, and the invitation is not due within the hour.
        assert not send_progress.upcoming(campaign, "production", 0, now)


def test_a_reminder_held_behind_an_uncertain_invitation_lets_the_send_finish(
    family_mail,  # noqa: F811
):
    """Planning holds the reminder (delivery_unresolved); it is not remaining.

    The invitation's delivery is really unknown, so the reminder's real
    occurrence stays pending with no email until the invitation is resolved.
    Without its own bucket the reminder send would never finish.
    """
    harness = family_mail
    message = uncertain(harness)
    reminders = add_reminders(harness.service.store, harness.campaign, uuid4())
    last = ScheduleDefinition.objects.get(pk=UUID(reminders[-1]["id"]))
    with (
        campaign_clock(last.current_revision.due_at),
        task_login(ServiceRole.SCHEDULER, exact=True),
        scheduler_session() as guard,
    ):
        held = plan_family(guard, family_id=message.family_id, worker_id=uuid4())
    assert held.held and held.reason == "delivery_unresolved"
    reminder = ScheduleOccurrence.objects.get(definition=last)
    assert reminder.state == "pending" and reminder.outbox_id is None
    campaign, mode, cycle = scope()
    with read_transaction():
        counts = send_progress.read_send(campaign, mode, cycle, database_now())
    assert counts.kind == "reminder"
    assert (counts.waiting, counts.remaining) == (1, 0)
    assert send_progress.progress(counts).active is False


def read_current():
    """Read the current send as the page does: read-only, as the web login."""
    campaign, mode, cycle = scope()
    with task_login(ServiceRole.WEB, exact=True), read_transaction():
        return send_progress.read_send(campaign, mode, cycle, database_now())


def test_the_total_counts_families_planning_has_not_reached(
    family_service,  # noqa: F811
):
    """Part-way through real Testing planning, the total is the whole send.

    Of five Families, three will be emailed: one undeliverable Family is
    skipped once planned, and one without an eligible address is never
    planned at all. With two planned, the other two eligible Families
    already count as remaining, so the total is three from the start.
    """
    harness = family_service
    populate(
        harness.campaign,
        harness.rings,
        [
            FamilyStatus(1, True, True, True, True),
            FamilyStatus(2, True, True, True, True),
            FamilyStatus(3, True, True, True, True),
            FamilyStatus(4, True, True, True, False, "eligible", "provider_suppressed"),
            FamilyStatus(5, True, True, False, False, "eligible", "no_eligible_email"),
        ],
        generation=2,
    )
    families = dict(FamilyCampaign.objects.values_list("family_duid", "pk"))
    definition = ScheduleDefinition.objects.select_related("current_revision").get()
    with campaign_clock(definition.current_revision.due_at):
        with scheduler_session() as guard:
            for duid in (1, 4):
                plan_family(guard, family_id=families[duid], worker_id=uuid4())
        early = read_current()
        assert early.kind == "initial"
        assert (
            early.remaining,
            early.unprepared,
            early.unplanned,
            early.unreachable,
        ) == (3, 3, 2, 1)
        shown = send_progress.progress(early)
        assert (shown.total, shown.percent, shown.active) == (3, 0, True)
        # The rest of the population, through the real scheduler sweep.
        with (
            task_login(ServiceRole.SCHEDULER, exact=True),
            scheduler_session() as guard,
        ):
            FamilyScheduleProducer(uuid4())(guard)
        late = read_current()
        assert (late.remaining, late.unplanned, late.unreachable) == (3, 0, 1)
        assert send_progress.progress(late).total == 3
        # While the Family population is being refreshed, who will be
        # emailed is not known: no total and no percentage.
        CampaignCredentialState.objects.filter(
            campaign_id=harness.campaign.pk, go_live_gate=False
        ).update(population_dirty=True, version=F("version") + 1)
        unknown = read_current()
        assert (unknown.unplanned, unknown.remaining) == (None, 3)
        shown = send_progress.progress(unknown)
        assert (shown.total, shown.percent, shown.active) == (None, None, True)
    # Before the schedule's due time (as after it is moved to a later time)
    # planning creates nothing more, so no Family still counts.
    with campaign_clock(definition.current_revision.due_at - timedelta(minutes=1)):
        assert read_current().unplanned == 0


def test_a_reminder_total_counts_only_families_whose_invitation_was_delivered(
    family_service,  # noqa: F811
    auth_service,
):
    """A reminder is owed only after a delivered invitation.

    Families 1 and 3 had their invitation delivered and family 2 did not
    (planning would send it the invitation instead). With only family 3's
    reminder planned, family 1 is still counted and family 2 is not.
    """
    harness, actor = family_service, uuid4()
    populate(
        harness.campaign,
        harness.rings,
        [FamilyStatus(n, True, True, True, True) for n in (1, 2, 3)],
        generation=2,
    )
    families = dict(FamilyCampaign.objects.values_list("family_duid", "pk"))
    reminders = add_reminders(auth_service.store, harness.campaign, actor)
    first = ScheduleDefinition.objects.get(pk=UUID(reminders[0]["id"]))
    initial = ScheduleDefinition.objects.get(kind="initial")
    with campaign_clock(initial.current_revision.due_at):
        for duid in (1, 3):
            row = occurrence(initial, actor, target=f"family:{families[duid]}")
            task = claimed_task("schedule_occurrence", row.pk, actor)
            row = advance(row, actor, "running", task_id=task.run_id, fence=task.fence)
            row = advance(row, actor, "succeeded", fence=task.fence)
            record_fulfillment(
                occurrence_id=row.pk,
                disposition="delivered",
                actor_id=actor,
                correlation_id=uuid4(),
                admit=admit_test_work,
            )
    with campaign_clock(first.current_revision.due_at):
        with scheduler_session() as guard:
            planned = plan_family(guard, family_id=families[3], worker_id=actor)
        assert ScheduleOccurrence.objects.get(pk=planned.selected).definition == first
        counts = read_current()
    assert counts.kind == "reminder"
    assert (counts.remaining, counts.unplanned, counts.waiting) == (2, 1, 0)
    assert send_progress.progress(counts).total == 2


def test_in_progress_is_a_moment_in_time_across_a_moved_schedule(
    family_service,  # noqa: F811
    auth_service,
):
    """Due and not started, in progress, cancelled by a move, then due again.

    1. Due, nothing planned yet: in progress, every Family owed.
    2. One Family planned: still in progress, the others owed.
    3. The schedule moves a day later: the planned email is cancelled
       (``schedule_replaced``), nothing is owed until the new time, so no
       send is in progress; the old send is what the summary describes.
    4. At the new time the new revision is a new send, due and not started.
    """
    harness, actor = family_service, uuid4()
    populate(
        harness.campaign,
        harness.rings,
        [FamilyStatus(n, True, True, True, True) for n in (1, 2, 3)],
        generation=2,
    )
    families = dict(FamilyCampaign.objects.values_list("family_duid", "pk"))
    definition = ScheduleDefinition.objects.select_related("current_revision").get()
    due = definition.current_revision.due_at
    with campaign_clock(due):
        waiting = read_current()
        assert waiting.in_progress
        assert (waiting.remaining, waiting.unplanned, waiting.sent) == (3, 3, 0)
        with scheduler_session() as guard:
            plan_family(guard, family_id=families[1], worker_id=actor)
        running = read_current()
        assert running.in_progress
        assert (running.remaining, running.unprepared, running.unplanned) == (3, 3, 2)
        moved = change(
            auth_service.store,
            auth_service.store.active(),
            actor,
            [
                {
                    "operation": "update",
                    "section": "schedules",
                    "id": str(definition.pk),
                    "values": {"date": "2026-10-02"},
                }
            ],
        )
        assert moved.state == "applied"
        assert ScheduleOccurrence.objects.get().reason == "schedule_replaced"
        cancelled = read_current()
        assert not cancelled.in_progress
        assert (cancelled.not_needed, cancelled.remaining, cancelled.unplanned) == (
            1,
            0,
            0,
        )
        assert not send_progress.progress(cancelled).active
    definition.refresh_from_db()
    later = definition.current_revision.due_at
    assert later > due
    with campaign_clock(later):
        again = read_current()
    assert again.in_progress
    assert (again.remaining, again.unplanned, again.not_needed) == (3, 3, 0)


def three_families(harness):
    """Families 1 to 3, all eligible with a deliverable address, by DUID."""
    populate(
        harness.campaign,
        harness.rings,
        [FamilyStatus(n, True, True, True, True) for n in (1, 2, 3)],
        generation=2,
    )
    return dict(FamilyCampaign.objects.values_list("family_duid", "pk"))


def test_an_invitation_still_sending_is_not_hidden_by_a_later_reminder(
    family_service,  # noqa: F811
    auth_service,
):
    """A reminder falls due mid-invitation: the invitation stays on the page.

    Family 2's reminder is coalesced into its still-pending invitation, so
    the reminder (the latest send) has nothing to do, while the invitation
    still has two pending emails and family 3 owed.
    """
    harness, actor = family_service, uuid4()
    families = three_families(harness)
    rows = add_reminders(auth_service.store, harness.campaign, actor)
    reminder = ScheduleDefinition.objects.get(pk=UUID(rows[0]["id"]))
    initial = ScheduleDefinition.objects.select_related("current_revision").get(
        kind="initial"
    )
    with campaign_clock(initial.current_revision.due_at), scheduler_session() as guard:
        plan_family(guard, family_id=families[1], worker_id=actor)
    with campaign_clock(reminder.current_revision.due_at):
        with scheduler_session() as guard:
            plan_family(guard, family_id=families[2], worker_id=actor)
        coalesced = ScheduleOccurrence.objects.get(
            definition=reminder, target=f"family:{families[2]}"
        )
        assert coalesced.state == "coalesced"
        counts = read_current()
    assert counts.kind == "initial" and counts.in_progress
    assert (counts.remaining, counts.unplanned) == (3, 1)


def test_a_schedule_moved_earlier_shows_its_new_send(
    family_service,  # noqa: F811
    auth_service,
):
    """Moved to an earlier time already past: its started send stays shown.

    The first revision's occurrence (due later) is the most recently due,
    but its email was cancelled by the move; the new revision, due earlier,
    has one planned email and two Families owed.
    """
    harness, actor = family_service, uuid4()
    families = three_families(harness)
    definition = ScheduleDefinition.objects.select_related("current_revision").get()
    first = definition.current_revision.due_at
    with campaign_clock(first + timedelta(hours=2)):
        with scheduler_session() as guard:
            plan_family(guard, family_id=families[1], worker_id=actor)
        moved = change(
            auth_service.store,
            auth_service.store.active(),
            actor,
            [
                {
                    "operation": "update",
                    "section": "schedules",
                    "id": str(definition.pk),
                    "values": {"time": "08:00:00"},
                }
            ],
        )
        assert moved.state == "applied"
        definition.refresh_from_db()
        assert definition.current_revision.due_at < first
        with scheduler_session() as guard:
            plan_family(guard, family_id=families[2], worker_id=actor)
        counts = read_current()
    assert counts.in_progress and counts.kind == "initial"
    assert (counts.remaining, counts.unplanned, counts.not_needed) == (3, 2, 0)


# One Family per planning rule (#431 review M2). Families 1, 2 and 7 are
# eligible with a deliverable address; 3 has none; 4 is not email-eligible;
# 5 is inactive; 6 is eligible but under an unreviewed restore hold for the
# send. Family 8's invitation was already delivered, so it is sent, not owed,
# in the invitation's send. For a reminder, 1, 2, 6 and 8 had their
# invitation delivered and 7 did not, so planning sends 7 the invitation
# instead. (A Family that responded needs a real submission, which these
# fixtures cannot make cheaply; the planner's own tests cover that rule.)
RULES = [
    FamilyStatus(1, True, True, True, True),
    FamilyStatus(2, True, True, True, True),
    FamilyStatus(3, True, True, True, False, "eligible", "provider_suppressed"),
    FamilyStatus(4, True, True, False, False, "eligible", "no_eligible_email"),
    FamilyStatus(5, False, False, False, False, "inactive", "ineligible"),
    FamilyStatus(6, True, True, True, True),
    FamilyStatus(7, True, True, True, True),
    FamilyStatus(8, True, True, True, True),
]
OWED = {"initial": 3, "reminder": 3}
# Already delivered within the send itself (family 8's invitation).
SENT = {"initial": 1, "reminder": 0}
INVITED = (1, 2, 6, 8)


def deliver(row, actor):
    """Settle one invitation occurrence as delivered, with its fulfillment."""
    task = claimed_task("schedule_occurrence", row.pk, actor)
    row = advance(row, actor, "running", task_id=task.run_id, fence=task.fence)
    row = advance(row, actor, "succeeded", fence=task.fence)
    record_fulfillment(
        occurrence_id=row.pk,
        disposition="delivered",
        actor_id=actor,
        correlation_id=uuid4(),
        admit=admit_test_work,
    )


def restore_hold(campaign, definition, mode, family_id, actor):
    """An unreviewed restore hold on ``definition`` for one Family."""
    start = campaign.active_configuration.starts_at
    with restored_runtime(start) as restore_id:
        RestoreDeliveryHold.objects.create(
            restore_id=restore_id,
            definition=definition,
            mode=mode,
            target=f"family:{family_id}",
            slot="once",
            backup_at=start,
            window_start=start,
            window_end=start + timedelta(days=1),
            discovery="inventory",
            actor_id=actor,
            correlation_id=uuid4(),
        )


def sweep(actor):
    """Plan every Family once, as the scheduler's sweep does."""
    with scheduler_session() as guard:
        for family_id in FamilyCampaign.objects.values_list("pk", flat=True):
            plan_family(guard, family_id=family_id, worker_id=actor)


def check_owed_matches_planning(kind, plan, *, owed=None, sent=None):
    """What the panel says is owed is exactly what planning then creates.

    Read just before planning, the send's owed Families are its whole
    remaining work; after ``plan`` they are all pending occurrences of the
    send's revision, the total is unchanged and nothing is owed. ``owed``
    and ``sent`` default to the fixture's ``OWED`` and ``SENT``.
    """
    owed = OWED[kind] if owed is None else owed
    sent = SENT[kind] if sent is None else sent
    before = read_current()
    assert before.kind == kind and before.in_progress
    assert (before.unplanned, before.remaining, before.sent) == (
        owed,
        owed,
        sent,
    )
    plan()
    after = read_current()
    assert after.kind == kind
    assert (after.unplanned, after.remaining, after.sent) == (0, owed, sent)
    assert (
        send_progress.progress(after).total
        == send_progress.progress(before).total
        == owed + sent
    )
    definition = (
        ScheduleDefinition.objects.get(kind=kind)
        if kind == "initial"
        else (
            ScheduleDefinition.objects.filter(kind="reminder").order_by(
                "current_revision__due_at"
            )[0]
        )
    )
    assert (
        ScheduleOccurrence.objects.filter(
            revision_id=definition.current_revision_id, state="pending"
        ).count()
        == owed
    )


@pytest.mark.parametrize("kind", ["initial", "reminder"])
def test_owed_families_match_testing_planning_rule_by_rule(
    family_service,  # noqa: F811
    auth_service,
    kind,
):
    """Testing: the count before the real planner equals what it creates."""
    harness, actor = family_service, uuid4()
    populate(harness.campaign, harness.rings, RULES, generation=2)
    families = dict(FamilyCampaign.objects.values_list("family_duid", "pk"))
    initial = ScheduleDefinition.objects.select_related("current_revision").get()
    send = initial
    delivered = (8,)
    if kind == "reminder":
        rows = add_reminders(auth_service.store, harness.campaign, actor)
        send = ScheduleDefinition.objects.get(pk=UUID(rows[0]["id"]))
        delivered = INVITED
    with campaign_clock(initial.current_revision.due_at):
        for duid in delivered:
            deliver(
                occurrence(initial, actor, target=f"family:{families[duid]}"),
                actor,
            )
    restore_hold(harness.campaign, send, "testing", families[6], actor)
    send.refresh_from_db()
    with campaign_clock(send.current_revision.due_at):
        check_owed_matches_planning(kind, lambda: sweep(actor))


@pytest.mark.parametrize("kind", ["initial", "reminder"])
def test_owed_families_match_production_planning_rule_by_rule(tmp_path, kind):
    """Production: the count before catch-up or the sweep equals what it creates.

    The invitation is planned by the real activation catch-up, a reminder by
    ordinary planning after it.
    """
    store, campaign, actor, rings = family_campaign(tmp_path)
    populate(campaign, rings, RULES, generation=2)
    families = dict(FamilyCampaign.objects.values_list("family_duid", "pk"))
    initial = ScheduleDefinition.objects.select_related("current_revision").get()
    send = initial
    if kind == "reminder":
        rows = add_reminders(store, campaign, actor)
        send = ScheduleDefinition.objects.get(pk=UUID(rows[0]["id"]))
    restore_hold(campaign, send, "production", families[6], actor)
    due = initial.current_revision.due_at
    with campaign_clock(due):
        command(campaign, actor, Action.ACTIVATE)
    demand = ActivationCatchUpDemand.objects.get()

    def catch_up():
        """The real activation catch-up worker plans every Family."""
        with task_login(ServiceRole.WORKER, exact=True):
            assert execute_hint(**execution_arguments(demand))

    if kind == "initial":
        with campaign_clock(due):
            # Before go-live no Production invitation can exist, so family 8
            # is simply owed here: the activation catch-up plans it too.
            check_owed_matches_planning(kind, catch_up, owed=4, sent=0)
        return
    with campaign_clock(due):
        catch_up()
        for duid in INVITED:
            deliver(
                ScheduleOccurrence.objects.get(
                    definition=initial, target=f"family:{families[duid]}"
                ),
                actor,
            )
    send.refresh_from_db()
    with campaign_clock(send.current_revision.due_at):
        check_owed_matches_planning(kind, lambda: sweep(actor))

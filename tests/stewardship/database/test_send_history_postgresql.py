"""Family email send history against PostgreSQL (#432): counts, links and locks.

A real prepared invitation is read through the restricted web login while
another session holds the global work-order lock. Finished sends, failures
(including one before preparation and a recovered Family), a reminder whose
remaining emails a schedule edit cancelled, and the same invitation in
Testing and in Production are built on temporary copies of the tables the
panel reads (see ``test_send_progress_postgresql``). Each linked count is
then opened on Outgoing mail and must list exactly the emails it counts.
"""

import hashlib
import re
from concurrent.futures import ThreadPoolExecutor
from queue import Queue
from types import SimpleNamespace
from urllib.parse import urlencode
from uuid import UUID, uuid4

import pytest
from django.db import connection
from psycopg import sql

from parishkit.stewardship.accounts.models import PortalSession
from parishkit.stewardship.audit.models import AuditEvent
from parishkit.stewardship.campaigns.models import Campaign, ScheduleRevision
from parishkit.stewardship.campaigns.schedule_models import (
    ScheduleDefinition,
    ScheduleOccurrence,
)
from parishkit.stewardship.campaigns.work_locks import (
    read_transaction,
    work_transaction,
)
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.jobs import send_history, send_progress
from parishkit.stewardship.jobs.delivery_metadata import listing
from parishkit.stewardship.jobs.ownership import database_now
from parishkit.stewardship.jobs.send_history import (
    SendKey,
    count_row,
    list_sends,
    mark_live,
)
from parishkit.stewardship.web.contracts import PageWindow

from ..policy_factory import address
from .auth_builders import signed_in
from .campaign_builders import add_draft, campaign_clock, change
from .plan_work import rows_visited
from .test_background_grants_postgresql import task_login
from .test_export_campaign_lock_postgresql import contender
from .test_family_auth_postgresql import family_service  # noqa: F401
from .test_family_mail_preparation_postgresql import family_mail  # noqa: F401
from .test_send_progress_postgresql import (
    NOISE,
    add_noise,
    clone_rows,
    explain,
    prepared,
    relations,
    scope,
    shadowed,
    source,
)

pytestmark = pytest.mark.django_db(transaction=True)

PAGE = "/admin/mail/family-history/"
DELIVERIES = "/admin/mail/outgoing/"
# The cloned reminder definition, copied from the invitation's row.
REMINDER = UUID(hashlib.md5(b"pk432-reminder").hexdigest())


def message_id(label, n):
    """The id ``clone_rows`` gives row ``n``'s email: md5(label||'m'||n)."""
    return UUID(hashlib.md5(f"{label}m{n}".encode()).hexdigest())


def emails(label, rows, state, *, superseded=()):
    """Expected ids of ``rows``' emails in ``state``, skipping replaced rows."""
    return {
        message_id(label, n)
        for n, row in enumerate(rows, start=1)
        if row[2] == state and n not in superseded
    }


def plan(*groups):
    """Rows for ``clone_rows`` from (count, occurrence, message, ago, reason).

    Each row is a new Family's first attempt.
    """
    rows = []
    for count, occurrence, message, ago, reason in groups:
        for _ in range(count):
            rows.append((len(rows) + 1, occurrence, message, ago, 0, reason))
    return rows


def listed(key, state):
    """The ids Outgoing mail lists for ``key`` in ``state`` (at most 100)."""
    _, rows, has_next, total = listing(
        PageWindow(1, 100), state=state, query="", send=key
    )
    assert not has_next and total == (len(rows), False)
    return {row["id"] for row in rows}


def add_reminder():
    """A reminder schedule in the copied definitions, like the invitation.

    It is a current schedule (with the invitation's current revision), so it
    is Reminder 1.
    """
    with connection.cursor() as cursor:
        columns = [field.column for field in ScheduleDefinition._meta.concrete_fields]
        cursor.execute(
            sql.SQL(
                "INSERT INTO pg_temp.stewardship_schedule_definition ({}) SELECT {} "
                "FROM pg_temp.stewardship_schedule_definition r WHERE r.kind='initial'"
            ).format(
                sql.SQL(",").join(map(sql.Identifier, columns)),
                sql.SQL(",").join(
                    sql.SQL("%(id)s")
                    if name == "id"
                    else sql.SQL("'reminder'")
                    if name == "kind"
                    else sql.Identifier("r", name)
                    for name in columns
                ),
            ),
            {"id": REMINDER},
        )


def move_revision(label, count, revision):
    """Move one label's ``count`` cloned occurrences to an earlier ``revision``.

    They then belong to a send whose schedule has since been changed.
    """
    with connection.cursor() as cursor:
        cursor.execute(
            "UPDATE pg_temp.stewardship_schedule_occurrence SET revision_id=%s "
            "WHERE id IN (SELECT md5(%s||'o'||n)::uuid "
            "FROM generate_series(1,%s) n)",
            [revision, label, count],
        )


def move_to_mode(label, count, mode, cycle):
    """Move one label's ``count`` cloned rows to ``mode`` and ``cycle``.

    The outbox CHECK constraints tie the mode to its routing and credential
    namespace, so those move with it.
    """
    production = mode == "production"
    with connection.cursor() as cursor:
        cursor.execute(
            "UPDATE pg_temp.stewardship_schedule_occurrence SET mode=%(mode)s, "
            "routing=%(routing)s, production_cycle=%(cycle)s "
            "WHERE id IN (SELECT md5(%(label)s||'o'||n)::uuid "
            "FROM generate_series(1,%(count)s) n)",
            {
                "mode": mode,
                "routing": "production" if production else "testing_override",
                "cycle": cycle,
                "label": label,
                "count": count,
            },
        )
        cursor.execute(
            "UPDATE pg_temp.stewardship_outbox_message SET mode=%(mode)s, "
            "routing=%(routing)s, credential_namespace=%(namespace)s, "
            "rehearsal_epoch_id=CASE WHEN %(production)s THEN NULL "
            "ELSE gen_random_uuid() END, "
            "token_generation_id=CASE WHEN %(production)s THEN gen_random_uuid() END, "
            "credential_epoch_id=CASE WHEN %(production)s THEN gen_random_uuid() END "
            "WHERE id IN (SELECT md5(%(label)s||'m'||n)::uuid "
            "FROM generate_series(1,%(count)s) n)",
            {
                "mode": mode,
                "routing": "production" if production else "testing_override",
                "namespace": "production" if production else "rehearsal",
                "production": production,
                "label": label,
                "count": count,
            },
        )


def test_the_web_login_lists_a_real_send_during_a_work_order_hold(
    family_mail,  # noqa: F811
    google,
):
    """A prepared invitation is in progress; both pages answer under the lock."""
    message = prepared(family_mail)
    campaign, mode, cycle = scope()
    occurrence = ScheduleOccurrence.objects.get(outbox_id=message.pk)
    token = SendKey(occurrence.definition_id, occurrence.revision_id, mode, cycle).token
    browser, _ = signed_in()
    ready = Queue()
    with (
        # On the campaign clock: a send is in progress once it is due (BG-12).
        campaign_clock(occurrence.due_at),
        task_login(ServiceRole.WEB, exact=True, reconnect=True),
        ThreadPoolExecutor(max_workers=1) as pool,
    ):
        activity = PortalSession.objects.get().last_activity_at
        views = AuditEvent.objects.filter(event_type="delivery_viewed").count()
        with work_transaction():
            pages = pool.submit(
                contender,
                ready,
                lambda: (
                    browser.get(PAGE),
                    browser.get(
                        DELIVERIES
                        + "?"
                        + urlencode({"send": token, "state": "pending"})
                    ),
                ),
            )
            ready.get(timeout=5)
            page, filtered = pages.result(timeout=60)
    assert page.status_code == 200 and filtered.status_code == 200
    body = page.content.decode()
    assert "Invitation" in body
    # In progress in the current mode, so the row links to the live panel.
    assert 'href="/admin/mail/family-progress/">In progress: watch it live' in body
    assert "1 to send" in body
    # In the sidebar beside the progress panel, and in the breadcrumb trail.
    assert f'<a href="{PAGE}" aria-current="page">' in body
    assert '<span aria-current="page">Family email history</span>' in body
    listed_body = filtered.content.decode()
    assert f'href="/admin/mail/outgoing/{message.pk}/"' in listed_body
    assert "Showing only the emails of one Family email send" in listed_body
    assert f'name="send" value="{token}"' in listed_body
    # Two audited views; reading never renews the Admin's idle time.
    assert AuditEvent.objects.filter(event_type="delivery_viewed").count() == views + 2
    assert PortalSession.objects.get().last_activity_at == activity
    assert family_mail.code not in body + listed_body
    assert message.sealed_substitutions not in body + listed_body


def test_sends_count_like_the_panel_and_link_to_exactly_their_emails(
    family_mail,  # noqa: F811
    google,
):
    """Finished, failed, edited and other-mode sends; every link is exact."""
    real = source(family_mail)
    revision = ScheduleOccurrence.objects.get(pk=real[1]).revision_id
    campaign_id, mode, cycle = scope()
    other = "production" if mode == "testing" else "testing"
    initial = ScheduleDefinition.objects.get(kind="initial")
    # A finished invitation with failures: 2 before preparation, and Family
    # 31's failed first attempt replaced by a delivered recovery.
    invitation = plan(
        (20, "succeeded", "delivered", 10, ""),
        (3, "failed", "permanent_failure", 5, ""),
        (2, "failed", None, None, "preparation_failed"),
        (1, "delivery_unknown", "delivery_unknown", 5, ""),
        (2, "skipped", "cancelled", 8, "family_responded"),
        (1, "skipped", None, None, "no_deliverable_recipient"),
    ) + [
        (31, "failed", "permanent_failure", 10, 0, ""),
        (31, "succeeded", "delivered", 10, 1, ""),
    ]
    # A reminder whose 6 unsent emails a schedule edit cancelled (its new
    # revision has planned nothing yet).
    reminder = plan(
        (10, "succeeded", "delivered", 30, ""),
        (6, "skipped", "cancelled", 20, "schedule_replaced"),
    )
    # The same invitation, sent in the other mode.
    elsewhere = plan(
        (4, "succeeded", "delivered", 40, ""),
        (1, "failed", "permanent_failure", 40, ""),
    )
    browser = signed_in()[0]
    # An earlier revision of the schedule (the fixture applied it before the
    # current one): the reminder's send used it, so its schedule has since
    # changed.
    earlier = (
        ScheduleRevision.objects.filter(record_id=initial.current_revision.record_id)
        .exclude(pk=revision)
        .values_list("pk", flat=True)
        .first()
    )
    assert earlier is not None
    with shadowed(), campaign_clock(initial.current_revision.due_at):
        add_reminder()
        for rows, definition, due, label in (
            (invitation, initial.pk, 60, "pk432-i"),
            (reminder, REMINDER, 20, "pk432-r"),
            (elsewhere, initial.pk, 100, "pk432-t"),
        ):
            clone_rows(
                rows,
                source=real,
                cycle=cycle,
                definition=definition,
                due_minutes=due,
                label=label,
            )
        move_to_mode("pk432-t", len(elsewhere), other, 0)
        move_revision("pk432-r", len(reminder), earlier)
        keys = {
            "invitation": SendKey(initial.pk, revision, mode, cycle),
            "reminder": SendKey(REMINDER, earlier, mode, cycle),
            "elsewhere": SendKey(initial.pk, revision, other, 0),
        }
        with read_transaction():
            campaign = Campaign.objects.get(pk=campaign_id)
            now = database_now()
            sends = list_sends(campaign_id)
            rows = {
                sent.key: count_row(campaign, sent, mode=mode, paused=False, now=now)
                for sent in sends
            }
            panel = send_progress.read_send(campaign_id, mode, cycle, now)
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT count(*) FROM pg_locks WHERE pid=pg_backend_pid() "
                    "AND locktype='advisory'"
                )
                assert cursor.fetchone()[0] == 0
        # Newest first: the reminder, then the invitation in each mode.
        assert [sent.key for sent in sends] == [
            keys["reminder"],
            keys["invitation"],
            keys["elsewhere"],
        ]
        assert [sent.number for sent in sends] == [1, None, None]
        assert [sent.replaced for sent in sends] == [True, False, False]
        # Nothing is in progress, so nothing links to the live panel.
        assert not any(row.live for row in rows.values())

        def summary(key):
            """The row's (sent, failed, uncertain, cancelled, not needed,
            couldn't be emailed, remaining) and its total."""
            sent = rows[key].send
            return (
                sent.counts.sent,
                sent.counts.failed,
                sent.counts.uncertain,
                rows[key].emails.get("cancelled", 0),
                sent.counts.not_needed,
                sent.counts.unreachable,
                sent.counts.remaining,
            ), sent.total

        assert summary(keys["invitation"]) == ((21, 5, 1, 2, 2, 1, 0), 27)
        assert summary(keys["reminder"]) == ((10, 0, 0, 6, 6, 0, 0), 10)
        assert summary(keys["elsewhere"]) == ((4, 1, 0, 0, 0, 0, 0), 5)
        # With nothing in progress the panel summarises the most recent send,
        # the reminder, and counts it the same way.
        assert panel == rows[keys["reminder"]].send.counts
        # Two of the invitation's 5 failures have no email to list.
        failed = next(
            o
            for o in rows[keys["invitation"]].outcomes
            if o.state == "permanent_failure"
        )
        assert (failed.count, failed.listed) == (5, 3)

        # Every linked count opens exactly the emails it counts: Family 31's
        # replaced failure is not listed, nor the other mode's emails.
        recovered = {len(invitation) - 1}
        for key, label, rows_ in (
            (keys["invitation"], "pk432-i", invitation),
            (keys["reminder"], "pk432-r", reminder),
            (keys["elsewhere"], "pk432-t", elsewhere),
        ):
            for outcome in rows[key].outcomes:
                expected = emails(
                    label,
                    rows_,
                    outcome.state,
                    superseded=recovered if label == "pk432-i" else (),
                )
                assert listed(key, outcome.state) == expected, (label, outcome)
                assert len(expected) == outcome.listed
            assert listed(key, "all") == {
                message_id(label, n)
                for n, row in enumerate(rows_, start=1)
                if row[2] and not (label == "pk432-i" and n in recovered)
            }

        # With other schedules' occurrences and other mail around, listing
        # the sends and reading one send's emails go through the definition
        # index, never a scan of every occurrence, and visit only a few hundred
        # rows: far fewer than the unrelated ones (work, not wall-clock time,
        # is the budget, since CI's shared CPUs make time noise; #697).
        add_noise(1, NOISE)
        with read_transaction(), connection.cursor() as cursor:
            for statement, values in (
                (send_history._SENDS, {"campaign": campaign_id}),
                (send_history._EMAIL_STATES, keys["invitation"].values()),
                (send_history._EMAIL_IDS, keys["invitation"].values()),
            ):
                plan_ = explain(cursor, statement, values)
                assert "stewardship_schedule_occurrence" not in relations(
                    plan_["Plan"], "Seq Scan"
                ), statement
                assert rows_visited(plan_["Plan"]) < NOISE // 4, statement

        # The pages render the same reads for the Admin.
        body = browser.get(PAGE).content.decode()
        token = keys["invitation"].token.replace(":", "%3A")
        assert f"send={token}&amp;state=permanent_failure" in body
        assert ">5<br><a " in body and "show 3" in body
        name = "Invitation, " + ("Testing" if mode == "testing" else "Production")
        assert f'aria-label="21 sent emails of {name}: show them">21</a>' in body
        assert re.search(r"Reminder 1\s*<br>\(schedule changed later\)", body)
        failures = browser.get(
            DELIVERIES
            + "?"
            + urlencode(
                {"send": keys["invitation"].token, "state": "permanent_failure"}
            )
        ).content.decode()
        assert "Showing 1–3 of 3" in failures
        cancelled = browser.get(
            DELIVERIES
            + "?"
            + urlencode({"send": keys["reminder"].token, "state": "cancelled"})
        ).content.decode()
        assert "Showing 1–6 of 6" in cancelled
        assert "Reminder 1" in cancelled

        # A well-formed send that planned nothing is refused, not ignored:
        # an unknown revision, the wrong cycle, or a mode it never sent in.
        for unknown in (
            SendKey(initial.pk, uuid4(), mode, cycle),
            SendKey(initial.pk, revision, mode, cycle + 5),
            SendKey(REMINDER, revision, other, 0),
            SendKey(uuid4(), revision, mode, cycle),
        ):
            assert (
                browser.get(
                    DELIVERIES + "?" + urlencode({"send": unknown.token})
                ).status_code
                == 400
            ), unknown

        # A Production send of an earlier Production cycle is marked so.
        production = next(sent for sent in sends if sent.key.mode == "production")
        later = SimpleNamespace(
            pk=campaign_id, production_cycle=production.key.cycle + 1
        )
        with read_transaction():
            assert count_row(
                later, production, mode=mode, paused=False, now=database_now()
            ).earlier


def test_a_malformed_or_unknown_send_filter_is_refused(auth_service, google):
    """Outgoing mail never silently drops a send filter it cannot honor."""
    store = auth_service.store
    add_draft(store, store.active(), uuid4())
    browser, _ = signed_in()
    assert browser.get(DELIVERIES).status_code == 200
    for value in (
        "nonsense",
        f"{uuid4()}:{uuid4()}:testing:0",
        f"{uuid4()}:{uuid4()}:production",
    ):
        response = browser.get(DELIVERIES + "?" + urlencode({"send": value}))
        assert response.status_code == 400, value
    assert browser.get(PAGE + "?q=1").status_code == 400
    # Every shown send is counted, so a page holds at most 100 and no "All".
    assert browser.get(PAGE + "?size=100").status_code == 200
    assert browser.get(PAGE + "?size=all").status_code == 400
    assert browser.get(PAGE + "?size=250").status_code == 400
    # No sends yet: the page says so.
    assert (
        "No invitation or reminder has been sent in this campaign yet."
        in browser.get(PAGE).content.decode()
    )


@pytest.mark.parametrize("role", ["staff", "ministry_leader"])
def test_send_history_is_for_administrators_only(auth_service, google, role):
    """Neither the page nor its sidebar entry reach other roles."""
    store = auth_service.store
    add_draft(store, store.active(), uuid4())
    admin, _ = signed_in()
    assert admin.get(PAGE).status_code == 200
    assert b"Family email history" in admin.get("/admin/").content
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
    # The role is checked before the query, so even a bad query is a 403,
    # and a send filter on Outgoing mail is refused like the page itself.
    send = f"{uuid4()}:{uuid4()}:testing:0"
    for path in (
        PAGE,
        PAGE + "?bogus=1",
        DELIVERIES + "?" + urlencode({"send": send, "state": "delivered"}),
        DELIVERIES + "?send=nonsense",
    ):
        assert reader.get(path).status_code == 403, path
    assert b"Family email history" not in reader.get("/admin/").content


def test_only_the_send_the_panel_shows_links_to_it(
    family_mail,  # noqa: F811
    google,
):
    """Two sends in progress at once: both say so; only the panel's links."""
    real = source(family_mail)
    initial = ScheduleDefinition.objects.get(kind="initial")
    campaign_id, mode, cycle = scope()
    browser = signed_in()[0]
    with shadowed(), campaign_clock(initial.current_revision.due_at):
        add_reminder()
        for rows, definition, due, label in (
            (
                plan(
                    (5, "succeeded", "delivered", 50, ""),
                    (3, "pending", "pending", None, ""),
                ),
                initial.pk,
                60,
                "pk432-a",
            ),
            (
                plan(
                    (2, "succeeded", "delivered", 10, ""),
                    (4, "pending", "pending", None, ""),
                ),
                REMINDER,
                20,
                "pk432-b",
            ),
        ):
            clone_rows(
                rows,
                source=real,
                cycle=cycle,
                definition=definition,
                due_minutes=due,
                label=label,
            )
        with read_transaction():
            campaign = Campaign.objects.get(pk=campaign_id)
            now = database_now()
            rows = [
                count_row(campaign, sent, mode=mode, paused=False, now=now)
                for sent in list_sends(campaign_id)
            ]
            panel = send_progress.read_send(campaign_id, mode, cycle, now)
        rows = mark_live(rows, panel)
        assert [row.send.active for row in rows] == [True, True]
        (live,) = [row for row in rows if row.live]
        assert live.send.counts == panel
        body = browser.get(PAGE).content.decode()
        assert body.count('href="/admin/mail/family-progress/">In progress') == 1
        assert body.count("In progress") >= 2

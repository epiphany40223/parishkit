"""The Administrator-only combined log screen under the real web database role."""

import json
import re
from collections import Counter
from datetime import timedelta
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from django.db import connection, transaction
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from parishkit.stewardship.accounts.policy_models import PortalUser
from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.audit import log_views
from parishkit.stewardship.audit.models import (
    AuditContext,
    AuditEvent,
    OperationalLog,
)
from parishkit.stewardship.audit.schemas import Action, ActorKind, ContextKind, Outcome
from parishkit.stewardship.audit.services import operational, record_action
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.observability import Event

from ..policy_factory import address
from .auth_builders import signed_in
from .campaign_builders import change
from .test_background_grants_postgresql import task_login

pytestmark = pytest.mark.django_db(transaction=True)
URL = "/admin/logs"


def post(browser, values=None):
    """Filters travel only with a genuine CSRF token."""
    token = browser.cookies["pk_admin_csrf"].value
    return browser.post(URL, {"csrfmiddlewaretoken": token} | (values or {}))


def views():
    """Audit contexts for this page only."""
    return list(
        AuditContext.objects.filter(event__event_type="system_logs_viewed").values_list(
            "context", flat=True
        )
    )


def diagnostics(levels=("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")):
    """One safe operational entry per level, written by the real service."""
    for level in levels:
        operational(
            Event.TASK_FAILED,
            level=level,
            schema=ContextKind.TASK,
            context={"outcome": Outcome.FAILED, "count": len(level)},
        )


def audit_entries(response):
    """How many listed entries are audit records, by their table cell.

    The page's own introduction also mentions audit records, so plain text
    would match on every page.
    """
    return response.content.decode().count('<span class="log-kind">Audit record</span>')


def identifiers(response):
    """Each listed entry's correlation identifier, top to bottom."""
    return [
        UUID(value)
        for value in re.findall(
            r'<dd class="log-correlation">([0-9a-f-]{36})</dd>',
            response.content.decode(),
        )
    ]


def next_fields(response):
    """The hidden fields of the navigator's Next form, or None on the last page.

    Every paging control is a CSRF POST form; nothing is a link.
    """
    body = response.content.decode()
    assert 'href="?' not in body
    forms = re.findall(r'<form method="post" action="/admin/logs">(.*?)</form>', body)
    for form in forms:
        if ">Next</button>" in form:
            return dict(
                re.findall(
                    r'<input type="hidden" name="([a-z_]+)" value="([^"]*)">', form
                )
            )
    return None


def levels(response):
    """The severity words shown in the table, in order."""
    return re.findall(
        r'class="log-level log-level-([a-z]+)"', response.content.decode()
    )


def test_administrator_reads_both_sources_and_filters_privately(auth_service, google):
    """Real grants: the default page, closed POST filters and one clean audit row."""
    browser, login = signed_in()
    assert login.status_code == 302
    diagnostics()
    with transaction.atomic():
        marked = record_action(
            Action.DASHBOARD_VIEWED,
            actor_kind=ActorKind.SYSTEM,
            context={"outcome": Outcome.SUCCEEDED, "count": 7},
        )
    # Audit records are immutable, so search for the correlation it was given.
    marked.refresh_from_db()
    correlation = marked.correlation_id
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        response = browser.get(URL)
        assert response.status_code == 200
        assert response["Cache-Control"] == "no-store"
        body = response.content.decode()
        # DEBUG is hidden until chosen; audit records appear beside diagnostics.
        assert set(levels(response)) == {"info", "warning", "error", "critical"}
        assert audit_entries(response) >= 2 and "admin_login" in body
        assert "task_failed" in body and "<dt>Outcome</dt><dd>failed</dd>" in body
        # The signed-in Administrator is named on screen for their own login.
        assert "admin@example.org" in body
        # Identifiers never travel in a URL. The refusal explains where filters
        # go, offers no guidance about the value, and never echoes it.
        refused = browser.get(URL + f"?correlation={correlation}")
        assert refused.status_code == 400
        assert b"never in a web address" in refused.content
        assert b"Identifiers must be complete" not in refused.content
        assert str(correlation).encode() not in refused.content
        assert browser.post(URL, {"source": "audit"}).status_code == 403  # No CSRF.

        chosen = post(browser, {"applied": "yes", "debug": "yes", "error": "yes"})
        assert sorted(set(levels(chosen))) == ["debug", "error"]
        assert b'name="debug" value="yes" checked' in chosen.content
        none = post(browser, {"applied": "yes", "source": "operational"})
        assert levels(none) == [] and b"No matching entries." in none.content
        only = post(browser, {"source": "audit"})
        assert levels(only) == [] and audit_entries(only) >= 2
        found = post(browser, {"correlation": str(correlation)})
        assert found.content.count(b"<tr>") == 2  # The heading and that one entry.
        assert b"dashboard_viewed" in found.content
        typed = post(browser, {"event": "task_failed", "source": "both"})
        assert audit_entries(typed) == 0 and len(levels(typed)) == 4
        # A type its owner writes directly, outside both reviewed vocabularies.
        logins = post(browser, {"event": "admin_login"})
        assert audit_entries(logins) == 1 and levels(logins) == []
        # The day the entries were really stored on, not the wall clock now.
        today = OperationalLog.objects.order_by("-created_at").first().created_at.date()
        later = post(browser, {"start": (today + timedelta(days=2)).isoformat()})
        assert b"No matching entries." in later.content
        during = post(browser, {"start": today.isoformat(), "end": today.isoformat()})
        assert b"task_failed" in during.content
        for invalid in (
            {"actor": "not-a-uuid"},
            {"event": "drop table"},
            {"text": "anything"},
            {"start": "2026-02-30"},
            # The last representable day: refused, never an unhandled overflow.
            {"end": "9999-12-31"},
            # The retired keyset cursor and malformed paging values.
            {"before": "2026-09-20T12:00:00.123456+00:00"},
            {"through": "2026-09-20T12:00:00"},
            {"sort": "created_at"},
            {"size": "all"},
            {"page": "0x1"},
        ):
            refused = post(browser, invalid)
            assert refused.status_code == 400
            assert b"Identifiers must be complete" in refused.content
            # The error explains itself and never echoes what was submitted.
            assert b"not-a-uuid" not in refused.content
            assert b"drop table" not in refused.content
    contexts = views()
    # One audit row per successful view, counting entries and naming nothing.
    # Nine successful views above; the refused ones are not views.
    assert len(contexts) == 9 and all(
        set(context) == {"outcome", "count"} and context["outcome"] == "succeeded"
        for context in contexts
    )
    assert "@" not in str(contexts) and str(correlation) not in str(contexts)


def test_pages_read_one_snapshot_while_the_log_grows(auth_service, google):
    """New entries arriving while reading cannot skip or repeat an older one,
    and the navigator says which page of how many is shown."""
    browser, _ = signed_in()
    diagnostics(("INFO",) * 30)
    wanted = {"applied": "yes", "info": "yes", "source": "operational", "size": "25"}
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        first = post(browser, wanted)
        seen = identifiers(first)
        assert len(seen) == 25 and b"Page 1 of 2" in first.content
        following = next_fields(first)
        assert following["page"] == "2" and following["sort"] == "newest"
        assert following["size"] == "25" and following["through"]
        # The log grows between the two requests.
        diagnostics(("INFO",) * 2)
        second = post(browser, following)
        older = identifiers(second)
        assert len(older) == 5 and not set(older) & set(seen)
        assert b"Page 2 of 2" in second.content and next_fields(second) is None
        # Applying the filters again takes a new snapshot with the new entries.
        again = post(browser, wanted)
        assert "Showing 1–25 of 32" in again.content.decode()
    stored = list(
        OperationalLog.objects.filter(level="INFO")
        .order_by("-created_at", "-id")
        .values_list("correlation_id", flat=True)
    )
    # Exactly the entries after the first page as it stood, none skipped.
    start = stored.index(seen[-1]) + 1
    assert older == stored[start : start + 5]


def test_the_time_column_sorts_both_ways_on_the_server(auth_service, google):
    """Oldest first starts from the oldest stored entry; the heading toggles."""
    browser, _ = signed_in()
    diagnostics(("INFO",) * 3)
    wanted = {"applied": "yes", "info": "yes", "source": "operational"}
    stored = list(
        OperationalLog.objects.filter(level="INFO")
        .order_by("created_at", "id")
        .values_list("correlation_id", flat=True)
    )
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        newest = post(browser, wanted)
        assert identifiers(newest) == stored[::-1]
        body = newest.content.decode()
        assert 'aria-sort="descending"' in body and "(sort ascending)" in body
        assert '<input type="hidden" name="sort" value="oldest">' in body
        oldest = post(browser, wanted | {"sort": "oldest"})
        assert identifiers(oldest) == stored
        assert 'aria-sort="ascending"' in oldest.content.decode()
        # Applying the filters again keeps rows-per-page and sort, never the
        # snapshot or page.
        body = post(browser, wanted | {"sort": "oldest", "size": "25"}).content.decode()
        filters = body[body.index('class="log-filters"') :]
        filters = filters[: filters.index("</form>")]
        assert '<input type="hidden" name="size" value="25">' in filters
        assert '<input type="hidden" name="sort" value="oldest">' in filters
        assert 'name="through"' not in filters and 'name="page"' not in filters


def test_a_bounded_count_says_more_than_and_paging_stops_at_its_depth(
    auth_service, google, monkeypatch
):
    """Past the count bound the navigator says "more than"; a page past the
    paging depth shows the last reachable page and says why."""
    browser, _ = signed_in()
    monkeypatch.setattr(log_views, "EXPORT_LIMIT", 30)
    diagnostics(("INFO",) * 40)
    wanted = {"applied": "yes", "info": "yes", "source": "operational", "size": "25"}
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        first = post(browser, wanted)
        assert b"of more than 30" in first.content and b"Page 1 of" not in first.content
        assert next_fields(first)["page"] == "2"
        deep = post(browser, next_fields(first) | {"page": "9"})
        assert len(identifiers(deep)) == 5 and b"data-depth-limited" in deep.content
        assert next_fields(deep) is None


def test_pages_cross_both_tables_through_entries_sharing_one_instant(
    auth_service, google
):
    """Ties are ordered by identifier, identically in PostgreSQL and in the merge."""
    browser, _ = signed_in()
    moment = timezone.now() - timedelta(hours=1)
    # Random identifiers, each doubling as its correlation so the page shows it.
    # Every entry in both tables shares one instant: only identifiers order them.
    diagnostic, audited = [uuid4() for _ in range(15)], [uuid4() for _ in range(15)]
    OperationalLog.objects.bulk_create(
        OperationalLog(
            id=key,
            correlation_id=key,
            created_at=moment,
            level="INFO",
            event="task_failed",
            schema="task",
            context={},
        )
        for key in diagnostic
    )
    AuditEvent.objects.bulk_create(
        AuditEvent(
            id=key, correlation_id=key, created_at=moment, event_type="task_failed"
        )
        for key in audited
    )
    walked = []
    values = {"applied": "yes", "info": "yes", "event": "task_failed", "size": "25"}
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        for _ in range(4):
            response = post(browser, values)
            assert response.status_code == 200
            walked.extend(identifiers(response))
            values = next_fields(response)
            if values is None:
                break
    # Every entry exactly once, in one total order, across the page boundary.
    assert walked == sorted([*diagnostic, *audited], reverse=True)
    assert len(walked) == len(set(walked)) == 30 and values is None


@pytest.mark.parametrize("role", ["staff", "ministry_leader"])
def test_logs_are_not_exposed_to_other_roles(auth_service, google, role):
    """Neither a direct URL nor a forged filter form reaches the logs."""
    store = auth_service.store
    receipt = change(
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
    assert receipt.state == "applied"
    google[0]["email"] = "reader@example.org"
    browser, _ = signed_in()
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        response = browser.get(URL)
        assert response.status_code == 403
        assert post(browser, {"source": "audit"}).status_code == 403
        home = browser.get("/admin/").content
    assert b"admin_login" not in response.content
    # A denied reader submitted nothing that could be corrected.
    assert b"Identifiers must be complete" not in response.content
    assert b'href="/admin/logs"' not in home
    assert views() == []


def test_access_lost_during_the_request_discloses_and_audits_nothing(
    auth_service, google, monkeypatch
):
    """The recheck after rendering, not the admission before it, is what refuses."""
    browser, _ = signed_in()
    genuine, calls = log_views._principal, []

    def demoted(request, store, *, final=False):
        """Admit the request normally, then lose access before the recheck."""
        calls.append(final)
        if final:
            raise PermissionError("Access was revoked during the request.")
        return genuine(request, store)

    monkeypatch.setattr(log_views, "_principal", demoted)
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        response = browser.get(URL)
    assert calls == [False, True] and response.status_code == 403
    assert b"admin_login" not in response.content
    assert views() == []


def test_restore_review_makes_the_logs_unavailable(auth_service, google, monkeypatch):
    """An untrusted restored configuration is not yet the parish's own record."""
    browser, _ = signed_in()
    # The flag changes only through its guarded runtime transition, so observe
    # it the way the campaign admission tests do: on the row the view reads.
    runtime = SystemConfiguration.objects.get()
    runtime.restore_review_required = True
    with (
        monkeypatch.context() as patch,
        task_login(ServiceRole.WEB, exact=True, reconnect=True),
    ):
        patch.setattr(SystemConfiguration.objects, "first", lambda: runtime)
        response = browser.get(URL)
    assert response.status_code == 503 and response["Retry-After"] == "5"
    assert b"admin_login" not in response.content and views() == []
    # An outage is not a filter mistake, so no filter guidance is offered.
    assert b"Identifiers must be complete" not in response.content


def test_a_restore_review_beginning_during_the_request_audits_nothing(
    auth_service, google, monkeypatch
):
    """The check after rendering refuses too, before anything is audited as viewed."""
    browser, _ = signed_in()
    genuine = SystemConfiguration.objects.filter

    def restoring(*args, **kwargs):
        """Report a review only to the post-render check, which asks for one."""
        rows = genuine(*args, **kwargs)
        if kwargs == {"restore_review_required": True}:
            rows.exists = lambda: True
        return rows

    with (
        monkeypatch.context() as patch,
        task_login(ServiceRole.WEB, exact=True, reconnect=True),
    ):
        patch.setattr(SystemConfiguration.objects, "filter", restoring)
        response = browser.get(URL)
    assert response.status_code == 503 and response["Retry-After"] == "5"
    assert b"admin_login" not in response.content and views() == []


def test_a_page_costs_a_bounded_number_of_queries(auth_service, google):
    """Five hundred more entries add no read: per table, one ordered key read
    and one read of the page's own rows, however many rows match."""
    browser, _ = signed_in()
    # Entries by several distinct actors, so an actor lookup written per row
    # would show as many reads; one set lookup shows as exactly one.
    actors = [
        PortalUser.objects.create(
            google_subject=f"actor-{index}",
            email=f"actor{index}@example.org",
            verified_at=timezone.now(),
        ).pk
        for index in range(5)
    ]
    with (
        task_login(ServiceRole.WEB, exact=True, reconnect=True),
        CaptureQueriesContext(connection) as few,
    ):
        assert browser.get(URL).status_code == 200
    OperationalLog.objects.bulk_create(
        OperationalLog(
            level="INFO",
            event="task_failed",
            schema="task",
            context={},
            actor_id=actors[index % len(actors)],
        )
        for index in range(500)
    )
    with (
        task_login(ServiceRole.WEB, exact=True, reconnect=True),
        CaptureQueriesContext(connection) as many,
    ):
        response = browser.get(URL)
    # Count only the reads the page itself makes: each log table's ordered
    # keys and its page rows by identifier set, and the one actor lookup by
    # identifier set (the bounded counts are neither ordered nor by id).
    # Total statements also include the session's throttled idle-activity
    # update, which depends on timing, sign-in's own reads of the portal user
    # by primary key, and the Family maintenance banner's read of its latest
    # switch event, which a process-wide cache of a few seconds skips or
    # repeats depending on timing.
    reads = [
        Counter(
            table
            for query in captured
            for table in (
                "stewardship_operational_log",
                "stewardship_audit_event",
                "stewardship_portal_user",
            )
            if query["sql"].startswith("SELECT")
            and f'FROM "{table}"' in query["sql"]
            and ("ORDER BY" in query["sql"] or '"id" IN (' in query["sql"])
            and "family_maintenance_" not in query["sql"]
        )
        for captured in (few, many)
    ]
    assert response.status_code == 200
    for captured in reads:
        # A table whose rows are not on the page skips its row read.
        assert 1 <= captured["stewardship_operational_log"] <= 2
        assert 1 <= captured["stewardship_audit_event"] <= 2
        assert captured["stewardship_portal_user"] == 1
    # Every row on the page shows its actor's resolved address. The entries
    # share one instant, so which rows the page holds depends on identifiers.
    resolved = sum(
        response.content.count(f"actor{index}@example.org".encode())
        for index in range(5)
    )
    assert resolved == 50
    assert response.content.count(b"<tr>") == 51 and next_fields(response)


def export(browser, values=None):
    """Download the filtered log with a genuine CSRF token."""
    token = browser.cookies["pk_admin_csrf"].value
    return browser.post(
        URL + "/export", {"csrfmiddlewaretoken": token} | (values or {})
    )


def test_export_downloads_filtered_entries_as_csv_or_json_lines(auth_service, google):
    """Exports carry the screen's filters and detail, newest first, and are audited."""
    diagnostics()
    browser, _ = signed_in()
    response = export(browser, {"applied": "yes", "error": "yes", "source": "both"})
    assert response.status_code == 200
    assert response["Content-Type"] == "text/csv"
    assert "attachment" in response["Content-Disposition"]
    lines = response.content.decode().splitlines()
    assert lines[0].startswith("time,source,level,type,actor_email")
    assert any(",ERROR,task_failed," in line for line in lines)
    assert not any(",WARNING," in line for line in lines)
    jsonl = export(
        browser,
        {
            "applied": "yes",
            "critical": "yes",
            "format": "jsonl",
            "source": "operational",
        },
    )
    records = [json.loads(line) for line in jsonl.content.decode().splitlines()]
    assert [record["level"] for record in records] == ["CRITICAL"]
    assert records[0]["details"] == {"count": "8", "outcome": "failed"}
    assert list(
        AuditContext.objects.filter(
            event__event_type="system_logs_exported"
        ).values_list("context", flat=True)
    ) == [
        {"outcome": "succeeded", "count": len(lines) - 1},
        {"outcome": "succeeded", "count": 1},
    ]


def test_export_times_use_the_chosen_timezone(auth_service, google):
    """UTC by default; a supported zone name shifts every time."""
    diagnostics(("ERROR",))
    browser, _ = signed_in()
    values = {"applied": "yes", "error": "yes", "source": "operational"}
    utc = export(browser, values | {"format": "jsonl"}).content.decode()
    local = export(
        browser, values | {"format": "jsonl", "timezone": "America/New_York"}
    ).content.decode()
    assert json.loads(utc)["time"].endswith("+00:00")
    assert json.loads(local)["time"][-6:] in {"-04:00", "-05:00"}


@pytest.mark.parametrize(
    "values",
    [{"format": "xlsx"}, {"timezone": "Mars/Base"}, {"level": "x"}],
)
def test_export_refuses_unknown_choices(auth_service, google, values):
    """Only the closed formats, supported zones and screen filters are accepted."""
    browser, _ = signed_in()
    assert export(browser, values).status_code == 400


def test_export_is_administrator_only_and_post_only(auth_service, google):
    """A GET or a non-Administrator gets nothing."""
    browser, _ = signed_in()
    assert browser.get(URL + "/export").status_code == 405
    store = auth_service.store
    change(
        store,
        store.active(),
        store.active().version_id,
        [
            {
                "operation": "add",
                "section": "login_rules",
                **address("staff@example.org", roles=("staff",)),
            }
        ],
    )
    google[0]["email"] = "staff@example.org"
    staff, _ = signed_in()
    assert export(staff).status_code == 403


@pytest.mark.parametrize("oldest", [False, True])
@pytest.mark.parametrize("model", [AuditEvent, OperationalLog])
def test_page_keys_are_read_from_the_creation_time_index(auth_service, model, oldest):
    """Either order reads its keys from the (created_at, id) index, and the
    snapshot bound is an index condition, so no page sorts the table."""
    query = SimpleNamespace(actor=None, correlation=None, days=(None, None))
    with transaction.atomic():
        with connection.cursor() as cursor:
            # The test tables are nearly empty; make the planner show the
            # index path it would take for a large table.
            cursor.execute("SET LOCAL enable_seqscan = off")
            cursor.execute("SET LOCAL enable_bitmapscan = off")
        rows = log_views._filtered(model.objects.all(), query, timezone.now())
        plan = log_views._ordered(rows, oldest=oldest)[:50].explain()
    assert "created_id" in plan and "Sort" not in plan, plan
    assert re.search(r"Index Cond: \(created_at <= ", plan), plan


def test_level_filtered_reads_and_counts_stay_on_indexes(auth_service):
    """A level-filtered page and each log's bounded count read an index
    under a LIMIT, so neither scans or counts a whole growing log."""
    from parishkit.stewardship.web.tables import COUNT_LIMIT

    query = SimpleNamespace(actor=None, correlation=None, days=(None, None))
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute("SET LOCAL enable_seqscan = off")
        levels = OperationalLog.objects.filter(level__in=["ERROR", "CRITICAL"])
        rows = log_views._filtered(levels, query, timezone.now())
        page = log_views._ordered(rows, oldest=False)[:50].explain()
        counts = [
            log_views._filtered(model.objects.all(), query, timezone.now())
            .order_by()[: COUNT_LIMIT + 1]
            .explain()
            for model in (OperationalLog, AuditEvent)
        ]
    for plan in (page, *counts):
        assert "Limit" in plan and "Seq Scan" not in plan, plan
        assert re.search(r"Index|Bitmap", plan), plan

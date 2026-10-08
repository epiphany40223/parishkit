"""The Administrator-only combined log screen under the real web database role."""

import json
import re
from collections import Counter
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from django.db import connection, transaction
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from parishkit.stewardship.accounts.policy_models import PortalUser
from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.audit import log_search, log_views
from parishkit.stewardship.audit.log_rows import DETAIL_FIELDS, DETAIL_LIMIT
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
URL = "/admin/system/logs/"


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
            schema=ContextKind.FAILURE,
            context={
                "failure": "alert_mail",
                "outcome": Outcome.FAILED,
                "count": len(level),
            },
        )


def audit_entries(response):
    """How many listed entries are audit records, by their Level cell's icon.

    The page's own introduction and its Show choices also mention audit
    records, so plain text would match on every page (#601).
    """
    return response.content.decode().count(
        '<span class="log-level" title="Audit record">'
        '<svg class="level-icon level-icon-audit"'
    )


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
    forms = re.findall(
        r'<form method="post" action="/admin/system/logs/(?:#[a-z-]+)?">(.*?)</form>',
        body,
    )
    for form in forms:
        if ">Next</button>" in form:
            return dict(
                re.findall(
                    r'<input type="hidden" name="([a-z_]+)" value="([^"]*)">', form
                )
            )
    return None


def levels(response):
    """The severity of each operational entry in the table, in order, by its
    Level cell's icon (the Show choices' own icons sit outside that cell);
    audit records' icons are counted by ``audit_entries`` instead."""
    found = re.findall(
        r'<span class="log-level"[^>]*><svg class="level-icon level-icon-([a-z]+)"',
        response.content.decode(),
    )
    return [level for level in found if level != "audit"]


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
        only = post(browser, {"applied": "yes", "audit": "yes"})
        assert levels(only) == [] and audit_entries(only) >= 2
        found = post(browser, {"correlation": str(correlation)})
        assert found.content.count(b"<tr>") == 2  # The heading and that one entry.
        assert b"dashboard_viewed" in found.content
        typed = post(browser, {"event": "task_failed"})
        assert audit_entries(typed) == 0 and len(levels(typed)) == 4
        # A type its owner writes directly, outside both reviewed vocabularies.
        logins = post(browser, {"event": "admin_login"})
        assert audit_entries(logins) == 1 and levels(logins) == []
        # The day the entries were really stored on, not the wall clock now;
        # days are in the browser's zone, here UTC (local days are covered by
        # test_dates_are_days_in_the_browser_zone).
        today = OperationalLog.objects.order_by("-created_at").first().created_at.date()
        later = post(
            browser, {"start": (today + timedelta(days=2)).isoformat(), "zone": "UTC"}
        )
        assert b"No matching entries." in later.content
        during = post(
            browser,
            {"start": today.isoformat(), "end": today.isoformat(), "zone": "UTC"},
        )
        assert b"task_failed" in during.content
        for invalid in (
            {"actor": "not-a-uuid"},
            {"event": "drop table"},
            # Search text never holds an address (#536).
            {"text": "admin@example.org"},
            {"ministry": "042"},
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
    # Eight successful views above; the refused ones are not views.
    assert len(contexts) == 8 and all(
        set(context) == {"outcome", "count"} and context["outcome"] == "succeeded"
        for context in contexts
    )
    assert "@" not in str(contexts) and str(correlation) not in str(contexts)


def test_pages_read_one_snapshot_while_the_log_grows(auth_service, google):
    """New entries arriving while reading cannot skip or repeat an older one,
    and the navigator says which page of how many is shown."""
    browser, _ = signed_in()
    diagnostics(("INFO",) * 30)
    wanted = {"applied": "yes", "info": "yes", "size": "25"}
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
    wanted = {"applied": "yes", "info": "yes"}
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
    wanted = {"applied": "yes", "info": "yes", "size": "25"}
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
    values = {
        "applied": "yes",
        "info": "yes",
        "audit": "yes",
        "event": "task_failed",
        "size": "25",
    }
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


def test_dates_are_days_in_the_browser_zone(auth_service, google):
    """From and Through select the viewer's local days, not UTC days (#558).

    8 March 2026 is New York's spring-forward day: it runs from 05:00 UTC
    (midnight EST) to 04:00 UTC on 9 March (midnight EDT), 23 hours. An entry
    at 23:30 EDT is already 9 March in UTC but belongs to the local 8 March.
    """
    browser, _ = signed_in()
    stamps = {
        "before": datetime(2026, 3, 8, 4, 59, 59, tzinfo=UTC),
        "midnight": datetime(2026, 3, 8, 5, tzinfo=UTC),
        "evening": datetime(2026, 3, 9, 3, 30, tzinfo=UTC),
        "after": datetime(2026, 3, 9, 4, tzinfo=UTC),
    }
    keys = {name: uuid4() for name in stamps}
    OperationalLog.objects.bulk_create(
        OperationalLog(
            id=keys[name],
            correlation_id=keys[name],
            created_at=moment,
            level="INFO",
            event="task_failed",
            schema="task",
            context={},
        )
        for name, moment in stamps.items()
    )
    day = {"applied": "yes", "info": "yes"}
    day |= {"start": "2026-03-08", "end": "2026-03-08"}

    def listed(values):
        """The fixture entries a filtered page and its export list, by name."""
        names = {key: name for name, key in keys.items()}
        response = post(browser, values)
        assert response.status_code == 200
        shown = [names[key] for key in identifiers(response) if key in names]
        exported = export(browser, values | {"format": "jsonl"})
        assert exported.status_code == 200
        records = [json.loads(line) for line in exported.content.decode().splitlines()]
        assert [
            names[UUID(record["correlation_id"])]
            for record in records
            if UUID(record["correlation_id"]) in names
        ] == shown
        return shown, response.content.decode()

    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        shown, body = listed(day | {"zone": "America/New_York"})
        assert shown == ["evening", "midnight"]
        # The zone is carried with the other filters by every table control
        # (headings and navigators) and by the export form, as POST fields.
        assert body.count('name="zone" value="America/New_York"') >= 3
        assert "?zone=" not in body
        # In UTC the same day is a different 24 hours.
        assert listed(day | {"zone": "UTC"})[0] == ["midnight", "before"]
        # East of UTC: 8 March in Tokyo ends at 15:00 UTC on 8 March.
        assert listed(day | {"zone": "Asia/Tokyo"})[0] == ["midnight", "before"]
        # Only From: everything from local midnight on, the later entry too.
        since = {key: value for key, value in day.items() if key != "end"}
        later = listed(since | {"zone": "America/New_York"})[0]
        assert later[-2:] == ["evening", "midnight"] and "before" not in later
        # A date without a zone the server knows is never read as UTC.
        before = len(views())
        for zone in ({}, {"zone": ""}, {"zone": "Mars/Base"}):
            refused = post(browser, day | zone)
            assert refused.status_code == 400
            assert b"without your computer&#x27;s time zone" in refused.content
            assert b"Identifiers must be complete" not in refused.content
            assert export(browser, day | zone).status_code == 400
        assert len(views()) == before


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
    assert b'href="/admin/system/logs/"' not in home
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
        URL + "export/", {"csrfmiddlewaretoken": token} | (values or {})
    )


def test_export_downloads_filtered_entries_as_csv_or_json_lines(auth_service, google):
    """Exports carry the screen's filters and detail, newest first, and are audited."""
    diagnostics()
    browser, _ = signed_in()
    response = export(browser, {"applied": "yes", "error": "yes", "audit": "yes"})
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
        },
    )
    records = [json.loads(line) for line in jsonl.content.decode().splitlines()]
    assert [record["level"] for record in records] == ["CRITICAL"]
    assert records[0]["details"] == {
        "count": "8",
        "failure": "alert_mail",
        "outcome": "failed",
    }
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
    values = {"applied": "yes", "error": "yes"}
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
    assert browser.get(URL + "export/").status_code == 405
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
    query = SimpleNamespace(actor=None, correlation=None, bounds=(None, None))
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

    query = SimpleNamespace(actor=None, correlation=None, bounds=(None, None))
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


def test_show_choices_select_kinds_of_entry(auth_service, google):
    """The six Show checkboxes pick operational levels and audit records
    together (#601): audit only, operational only, both, and never none.
    A retired Source value from an older tab is mapped onto them for one
    release, and the critical-events banner and "Same campaign" forms list
    what they did before."""
    browser, _ = signed_in()
    diagnostics()
    campaign = uuid4()
    AuditEvent.objects.bulk_create(
        AuditEvent(event_type="campaign_configured", campaign_reference=campaign)
        for _ in range(2)
    )
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        # The first view: every level but Debug, plus audit records.
        first = post(browser)
        assert set(levels(first)) == {"info", "warning", "error", "critical"}
        assert audit_entries(first) >= 3
        audit_only = post(browser, {"applied": "yes", "audit": "yes"})
        assert levels(audit_only) == [] and audit_entries(audit_only) >= 3
        operational_only = post(
            browser, {"applied": "yes", "warning": "yes", "critical": "yes"}
        )
        assert levels(operational_only) == ["critical", "warning"]
        assert audit_entries(operational_only) == 0
        both = post(browser, {"applied": "yes", "debug": "yes", "audit": "yes"})
        assert levels(both) == ["debug"] and audit_entries(both) >= 3
        # Nothing ticked is refused with its own message, not the identifier
        # guidance: no value was mistyped, every choice was left unticked.
        for none in ({"applied": "yes"}, {"applied": "yes", "source": "operational"}):
            refused = post(browser, none)
            assert refused.status_code == 400
            assert b"Choose at least one kind of entry to show." in refused.content
            assert b"Identifiers must be complete" not in refused.content
            assert export(browser, none).status_code == 400
        # A campaign filter with Audit record unticked says why it is empty.
        unticked = post(
            browser, {"applied": "yes", "info": "yes", "campaign": str(campaign)}
        )
        assert (
            b"Campaign and subject filters list audit records only" in unticked.content
        )
        # The retired Source, as an older open tab still sends it.
        legacy = post(browser, {"applied": "yes", "error": "yes", "source": "audit"})
        assert levels(legacy) == [] and audit_entries(legacy) >= 3
        legacy = post(browser, {"source": "operational"})
        assert set(levels(legacy)) == {"info", "warning", "error", "critical"}
        assert audit_entries(legacy) == 0
        legacy = post(browser, {"applied": "yes", "error": "yes", "source": "both"})
        assert levels(legacy) == ["error"] and audit_entries(legacy) >= 3
        # ...whose page then carries the checkboxes, never the Source.
        body = legacy.content.decode()
        assert 'name="source"' not in body
        assert '<input type="hidden" name="audit" value="yes">' in body
        assert b'name="audit" value="yes" checked' in legacy.content
        for refused in (
            {"source": "everything"},
            {"applied": "yes", "audit": "yes", "source": "audit"},
            {"applied": "yes", "audit": "on"},
            {"audit": "yes"},
        ):
            assert post(browser, refused).status_code == 400
        # "Same campaign": that campaign's audit records only.
        same = post(
            browser, {"applied": "yes", "audit": "yes", "campaign": str(campaign)}
        )
        assert levels(same) == [] and audit_entries(same) == 2
        # Its old form, from a page opened before #601, lists the same.
        old = post(browser, {"source": "audit", "campaign": str(campaign)})
        assert levels(old) == [] and audit_entries(old) == 2
        # The critical-events banner: Critical operational entries only.
        banner = post(browser, {"applied": "yes", "critical": "yes"})
        assert levels(banner) == ["critical"] and audit_entries(banner) == 0
        # The export takes the same checkboxes.
        exported = export(browser, {"applied": "yes", "audit": "yes"})
        rows = exported.content.decode().splitlines()[1:]
        assert rows and all(row.split(",")[1] == "Audit" for row in rows)


def searchable():
    """Three audit records and one diagnostic the search tests tell apart.

    Returns the audit records' correlation identifiers by name. ``scoped``
    names Ministries 5 and 42 in its retained result scope; ``reviewed``
    holds an Administrator's review reason longer than the page shows;
    ``subject`` is about one subject.
    """
    subject = uuid4()
    with transaction.atomic():
        scoped = record_action(
            Action.MINISTRY_REPORT_VIEWED,
            actor_kind=ActorKind.SYSTEM,
            context={"outcome": Outcome.SUCCEEDED, "ministry_duids": [5, 42]},
        )
        single = record_action(
            Action.MINISTRY_FOLLOWUP_VIEWED,
            actor_kind=ActorKind.SYSTEM,
            context={"outcome": Outcome.SUCCEEDED, "ministry_duid": 42},
        )
        reviewed = record_action(
            Action.DASHBOARD_VIEWED,
            actor_kind=ActorKind.SYSTEM,
            subject_id=subject,
            context={"decision": "keep_role", "review_reason": "x" * 130 + "needle"},
        )
    operational(
        Event.TASK_FAILED,
        level="ERROR",
        schema=ContextKind.FAILURE,
        context={"failure": "alert_mail", "outcome": Outcome.FAILED, "count": 3},
    )
    for record in (scoped, single, reviewed):
        record.refresh_from_db()
    return {
        "scoped": scoped.correlation_id,
        "single": single.correlation_id,
        "reviewed": reviewed.correlation_id,
        "subject": subject,
    }


def test_search_finds_types_explanations_and_shown_detail_only(auth_service, google):
    """Text search (#536) matches the type, the type's explanation and the
    detail values the page shows, in either case; never a key name or a
    value too long to show."""
    browser, _ = signed_in()
    made = searchable()
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        # A detail value, in another case, on an operational entry.
        mail = post(browser, {"text": "ALERT_MAIL"})
        assert levels(mail) == ["error"] and audit_entries(mail) == 0
        # Part of a stored type.
        typed = post(browser, {"text": "followup_vie"})
        assert identifiers(typed) == [made["single"]]
        # A Ministry DUID is a detail value too.
        assert made["scoped"] in identifiers(post(browser, {"text": "42"}))
        # A key name is never searched, and a value the page hides (a
        # review reason over 128 characters) cannot be found.
        for hidden in ("review_reason", "needle"):
            assert b"No matching entries." in post(browser, {"text": hidden}).content
        # An audit event some SQL trigger wrote without a context row is
        # still found by its type.
        bare = uuid4()
        AuditEvent.objects.bulk_create(
            [AuditEvent(correlation_id=bare, event_type="weekly_manual_requested")]
        )
        assert identifiers(post(browser, {"text": "weekly_manual"})) == [bare]
        # Each value is matched on its own: never through JSON quoting or
        # across two values ("alert_mail", then "failed").
        for across in ('"', 'mail", "fail', "mail failed"):
            assert b"No matching entries." in post(browser, {"text": across}).content
        # LIKE wildcards are literal text.
        assert b"No matching entries." in post(browser, {"text": "%"}).content
        # A double quote or backslash in a shown value is found, though the
        # quick JSON-text prefilter escapes both.
        with transaction.atomic():
            noted = record_action(
                Action.DASHBOARD_VIEWED,
                actor_kind=ActorKind.SYSTEM,
                context={"decision": "keep_role", "review_reason": 'say "yes" \\ ok'},
            )
        noted.refresh_from_db()
        quoted = noted.correlation_id
        for found in ('"yes"', 'say "', "\\ ok"):
            assert identifiers(post(browser, {"text": found})) == [quoted], found
        # The type's plain explanation, as the page shows it.
        explained = post(
            browser, {"applied": "yes", "audit": "yes", "text": "someone OPENED"}
        )
        assert {made["scoped"], made["single"]} <= set(identifiers(explained))
        assert made["reviewed"] not in identifiers(explained)
        # The export carries the search like every other filter.
        exported = export(browser, {"text": "followup_vie", "format": "jsonl"})
        assert [
            json.loads(line)["correlation_id"]
            for line in exported.content.decode().splitlines()
        ] == [str(made["single"])]

    # Both logs' own checks refuse a stored fraction, so the shown-value
    # condition is checked on a literal context: a fraction, alone or in a
    # list, is never found; a whole number is.
    def shown(context, text):
        """Whether the exact condition finds ``text`` in ``context``."""
        sql = log_search.SHOWN_VALUE_SQL.format(column="%s::jsonb")
        with connection.cursor() as cursor:
            cursor.execute(
                f"SELECT {sql}",
                [
                    json.dumps(context),
                    DETAIL_LIMIT,
                    sorted(DETAIL_FIELDS),
                    log_search.like_pattern(text),
                ],
            )
            return cursor.fetchone()[0]

    assert not shown({"count": 2.5}, "2.5")
    assert not shown({"ministry_duids": [7, 7.5]}, "7")
    assert shown({"count": 25}, "25") and shown({"ministry_duids": [7, 8]}, "8")
    assert not shown({"not_reviewed": 25}, "25")


def test_ministry_and_subject_filters(auth_service, google):
    """A Ministry DUID matches a single Ministry and a retained Ministry set
    (#536); a subject lists only its audit records."""
    browser, _ = signed_in()
    made = searchable()
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        both = post(browser, {"ministry": "42"})
        assert set(identifiers(both)) == {made["scoped"], made["single"]}
        assert identifiers(post(browser, {"ministry": "5"})) == [made["scoped"]]
        # Containment, not text: 4 is not 42.
        assert b"No matching entries." in post(browser, {"ministry": "4"}).content
        about = post(browser, {"subject": str(made["subject"])})
        assert identifiers(about) == [made["reviewed"]] and levels(about) == []
        assert b'id="log-audit-' in about.content and b'-subject"' in about.content


def test_links_carry_only_non_identifying_filters(auth_service, google):
    """A GET may carry the link filters (#536) and is audited like a view;
    search text, an identifier, the snapshot or a POST's query string is
    refused unechoed."""
    browser, _ = signed_in()
    made = searchable()
    before = len(views())
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        linked = browser.get(
            URL,
            {"applied": "yes", "audit": "yes", "event": "ministry_followup_viewed"},
        )
        assert linked.status_code == 200
        assert identifiers(linked) == [made["single"]]
        body = linked.content.decode()
        # The page draws its own address, and the form shows what it applied.
        assert (
            '<a href="/admin/system/logs/?applied=yes&amp;audit=yes'
            '&amp;event=ministry_followup_viewed" data-page-address>' in body
        )
        assert 'value="ministry_followup_viewed"' in body
        dated = browser.get(
            URL, {"ministry": "42", "start": "2020-01-01", "zone": "Asia/Tokyo"}
        )
        assert set(identifiers(dated)) == {made["scoped"], made["single"]}
        assert 'data-link-zone="Asia/Tokyo" hidden' in dated.content.decode()
        for refused in (
            # Search text is private, like Find a Family's (#536 review).
            {"text": "followup_vie"},
            {"correlation": str(made["single"])},
            {"subject": str(made["subject"])},
            {"through": "2026-09-20T12:00:00.123456+00:00"},
            {"page": "2"},
            {"csrfmiddlewaretoken": "x"},
        ):
            answer = browser.get(URL, refused)
            assert answer.status_code == 400
            assert b"never in a web address" in answer.content
            for value in (made["single"], made["subject"], "followup_vie"):
                assert str(value).encode() not in answer.content
        # A POST never comes with a query string.
        token = browser.cookies["pk_admin_csrf"].value
        mixed = browser.post(URL + "?text=x", {"csrfmiddlewaretoken": token})
        assert mixed.status_code == 400
        # A bad link value gets the value guidance, not the address refusal.
        bad = browser.get(URL, {"ministry": "x"})
        assert bad.status_code == 400 and b"never in a web address" not in bad.content
    assert len(views()) == before + 2


def test_a_read_past_its_limit_is_stopped_recorded_and_unavailable(
    auth_service, google, monkeypatch
):
    """Each log read runs under a statement timeout (#536 review): a read
    that outlives it is stopped, recorded as a timeout with what stopped it,
    the limit and the time taken, and answered with the unavailable page;
    nothing is audited as a view or export."""
    browser, _ = signed_in()
    monkeypatch.setattr(log_views, "READ_SECONDS", 0.2)
    real = log_views._sources

    def slow(query, through):
        """A read that takes longer than the limit."""
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_sleep(2)")
        return real(query, through)

    monkeypatch.setattr(log_views, "_sources", slow)
    before = len(views())
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        answer = post(browser, {"text": "anything"})
        assert answer.status_code == 503 and answer["Retry-After"] == "5"
        assert export(browser, {"text": "anything"}).status_code == 503
    assert len(views()) == before
    stops = list(
        OperationalLog.objects.filter(event="task_timed_out").values_list(
            "context", flat=True
        )
    )
    assert len(stops) == 2
    for stop in stops:
        assert stop["what"] == "statement_timeout"
        assert stop["elapsed_seconds"] >= 0 and "limit_seconds" in stop
        assert "task_id" not in stop

"""The Administrator-only combined log screen under the real web database role."""

import re
from collections import Counter
from datetime import timedelta
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
            r"Correlation ([0-9a-f-]{36})", response.content.decode()
        )
    ]


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
        assert "task_failed" in body and "<dt>outcome</dt><dd>failed</dd>" in body
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
            {"before": "2026-09-20T12:00:00.123456+00:00"},
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


def test_older_entries_are_reached_by_a_stable_cursor(
    auth_service, google, monkeypatch
):
    """New entries arriving while reading cannot skip or repeat an older one."""
    browser, _ = signed_in()
    monkeypatch.setattr(log_views, "PAGE_SIZE", 3)
    diagnostics(("INFO",) * 7)
    wanted = {"applied": "yes", "info": "yes", "source": "operational"}
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        first = post(browser, wanted)
        seen = identifiers(first)
        assert len(seen) == 3 and b"Older entries" in first.content
        cursor = dict(
            re.findall(
                r'name="(before(?:_id)?)" value="([^"]+)"', first.content.decode()
            )
        )
        assert set(cursor) == {"before", "before_id"}
        # The log grows between the two requests.
        diagnostics(("INFO",) * 2)
        second = post(browser, wanted | cursor)
        older = identifiers(second)
        assert len(older) == 3 and not set(older) & set(seen)
        assert b"Back to the newest entries" in second.content
    stored = list(
        OperationalLog.objects.filter(level="INFO")
        .order_by("-created_at", "-id")
        .values_list("correlation_id", flat=True)
    )
    # Exactly the entries after the first page as it stood, none skipped.
    start = stored.index(seen[-1]) + 1
    assert older == stored[start : start + 3]


def test_a_cursor_crosses_both_tables_through_entries_sharing_one_instant(
    auth_service, google, monkeypatch
):
    """Ties are ordered by identifier, identically in PostgreSQL and in the merge."""
    browser, _ = signed_in()
    monkeypatch.setattr(log_views, "PAGE_SIZE", 7)
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
    wanted = {"applied": "yes", "info": "yes", "event": "task_failed"}
    walked, cursor = [], {}
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        for _ in range(6):
            response = post(browser, wanted | cursor)
            assert response.status_code == 200
            walked.extend(identifiers(response))
            cursor = dict(
                re.findall(
                    r'name="(before(?:_id)?)" value="([^"]+)"',
                    response.content.decode(),
                )
            )
            if not cursor:
                break
    # Every entry exactly once, in one total order, across five page boundaries.
    assert walked == sorted([*diagnostic, *audited], reverse=True)
    assert len(walked) == len(set(walked)) == 30 and not cursor


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
    """Five hundred more entries add no read: one per table, however many rows."""
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
    # Count only the reads the page itself makes: the two log tables, ordered,
    # and the one actor lookup by identifier set. Total statements also include
    # the session's throttled idle-activity update, which depends on timing,
    # and sign-in's own reads of the portal user by primary key.
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
        )
        for captured in (few, many)
    ]
    assert response.status_code == 200
    for captured in reads:
        assert captured["stewardship_operational_log"] == 1
        assert captured["stewardship_audit_event"] == 1
        assert captured["stewardship_portal_user"] == 1
    # Every row on the page shows its actor's resolved address. The entries
    # share one instant, so which rows the page holds depends on identifiers.
    resolved = sum(
        response.content.count(f"actor{index}@example.org".encode())
        for index in range(5)
    )
    assert resolved == 50
    assert (
        response.content.count(b"<tr>") == 51 and b"Older entries" in response.content
    )

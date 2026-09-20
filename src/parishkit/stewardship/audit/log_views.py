"""Administrator-only combined operational and audit log screen."""

from datetime import UTC, datetime, time, timedelta

from django.core.exceptions import ObjectDoesNotExist
from django.db import DatabaseError, transaction
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import render
from django.template.loader import render_to_string
from django.views.decorators.http import require_http_methods

from parishkit.config import ConfigError
from parishkit.stewardship.accounts.authentication import runtime
from parishkit.stewardship.accounts.limiting import LimiterUnavailable
from parishkit.stewardship.accounts.policy import Capability, allows
from parishkit.stewardship.accounts.policy_models import PortalUser
from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.accounts.sessions import authenticated_admin
from parishkit.stewardship.web.contracts import MESSAGES, ErrorCode

from .log_rows import (
    PAGE_SIZE,
    LogQuery,
    audit_row,
    merge,
    operational_row,
    page_context,
)
from .models import AuditEvent, OperationalLog
from .schemas import Action, ActorKind, Outcome
from .services import record_action

UNAVAILABLE = (ConfigError, LimiterUnavailable, ObjectDoesNotExist)


def _error(code, status):
    """A fixed, accessible message with no submitted filter and no DB chrome.

    An unavailable database must not be queried again by the Admin navigation
    context processor while the error itself is rendered.
    """
    response = HttpResponse(
        render_to_string(
            "stewardship/logs-error.html",
            # Filter guidance only when a filter was the problem: a denied reader
            # or an outage submitted nothing that could be corrected.
            {"message": MESSAGES[code], "invalid": code is ErrorCode.INVALID},
        ),
        status=status,
    )
    response.stewardship_safe_error = True
    response["Cache-Control"] = "no-store"
    if status == 503:
        response["Retry-After"] = "5"
    return response


def _principal(request, store, *, final=False):
    """Only an Administrator reads the logs; the final recheck renews nothing."""
    actor = authenticated_admin(
        request, store=store, activity=not final, read_only=final
    )
    if not allows(actor, Capability.SYSTEM_LOGS):
        raise PermissionError("System logs require an Administrator.")
    return actor


def _bounded(rows, query):
    """Apply the filters both sources share, then the keyset cursor.

    Dates are whole UTC days, matching how the entries are stored.
    """
    if query.actor:
        rows = rows.filter(actor_id=query.actor)
    if query.correlation:
        rows = rows.filter(correlation_id=query.correlation)
    if query.start:
        day = datetime.fromisoformat(query.start).date()
        rows = rows.filter(created_at__gte=datetime.combine(day, time.min, UTC))
    if query.end:
        day = datetime.fromisoformat(query.end).date() + timedelta(days=1)
        rows = rows.filter(created_at__lt=datetime.combine(day, time.min, UTC))
    if query.cursor:
        instant, identifier = query.cursor
        rows = rows.filter(
            Q(created_at__lt=instant) | Q(created_at=instant, id__lt=identifier)
        )
    return rows.order_by("-created_at", "-id")[: PAGE_SIZE + 1]


def _load(query):
    """One bounded page from each source; a campaign filter is audit-only."""
    operational, audit = [], []
    if query.source != "audit" and not query.campaign and query.levels:
        rows = OperationalLog.objects.filter(level__in=query.levels)
        if query.event:
            rows = rows.filter(event=query.event)
        operational = [
            operational_row(record)
            for record in _bounded(rows, query).values(
                "id",
                "created_at",
                "level",
                "event",
                "actor_id",
                "correlation_id",
                "context",
            )
        ]
    if query.source != "operational":
        rows = AuditEvent.objects.all()
        if query.event:
            rows = rows.filter(event_type=query.event)
        if query.campaign:
            rows = rows.filter(campaign_reference=query.campaign)
        audit = [
            audit_row(record)
            for record in _bounded(rows, query).values(
                "id",
                "created_at",
                "event_type",
                "actor_id",
                "correlation_id",
                "campaign_reference",
                "subject_id",
                "auditcontext__context",
            )
        ]
    # The same size bounds each source's read, so the merge is the true next page.
    page, following = merge(operational, audit, size=PAGE_SIZE)
    # Shown on screen only, for the actors on this page; never audited or logged.
    actors = dict(
        PortalUser.objects.filter(
            pk__in={row["actor_id"] for row in page if row["actor_id"]}
        ).values_list("id", "email")
    )
    for row in page:
        row["actor"] = actors.get(row["actor_id"])
    return page, following


@require_http_methods(["GET", "POST"])
def logs(request):
    """Capture a bounded page, render outside the transaction, then recheck.

    The log only grows, so an ordinary snapshot is coherent and the shared work
    lock is not taken. Every view is itself audited, so it appears in the list
    the next time; that is expected, not a loop. Text and JSONL export, free-text
    search and Ministry scope filtering belong to later increments.
    """
    try:
        service = runtime()
        actor = _principal(request, service.store)
        if request.GET:
            raise ValueError("Log filters require private POST state.")
        parameters = request.POST.copy()
        parameters.pop("csrfmiddlewaretoken", None)
        query = LogQuery.parse(parameters)
        with transaction.atomic():
            configuration = SystemConfiguration.objects.first()
            if configuration is None or configuration.restore_review_required:
                return _error(ErrorCode.UNAVAILABLE, 503)
            rows, following = _load(query)
        response = render(
            request, "stewardship/logs.html", page_context(query, rows, following)
        )
        with transaction.atomic():
            current = _principal(request, service.store, final=True)
            if current.identity != actor.identity:
                raise PermissionError("Log reader changed.")
            if SystemConfiguration.objects.filter(
                restore_review_required=True
            ).exists():
                return _error(ErrorCode.UNAVAILABLE, 503)
            record_action(
                Action.LOGS_VIEWED,
                actor_kind=ActorKind.PORTAL_USER,
                actor_id=current.identity,
                # A count only: never a filter, an identifier or an address.
                context={"outcome": Outcome.SUCCEEDED, "count": len(rows)},
            )
        response["Cache-Control"] = "no-store"
        return response
    except PermissionError:
        return _error(ErrorCode.DENIED, 403)
    except DatabaseError:
        return _error(ErrorCode.UNAVAILABLE, 503)
    except UNAVAILABLE:
        return _error(ErrorCode.UNAVAILABLE, 503)
    except ValueError:
        return _error(ErrorCode.INVALID, 400)

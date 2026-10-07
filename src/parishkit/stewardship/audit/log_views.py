"""Administrator-only combined operational and audit log screen and export.

The reads themselves are request-free, in ``log_reads``, which the command
line's ``logs list`` and ``logs export`` share (ADM-11 PR 8a).
"""

from datetime import UTC, datetime

from django.core.exceptions import ObjectDoesNotExist
from django.db import DatabaseError, transaction
from django.http import HttpResponse
from django.shortcuts import render
from django.template.loader import render_to_string
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_http_methods

from parishkit.config import ConfigError
from parishkit.stewardship.accounts.authentication import runtime
from parishkit.stewardship.accounts.limiting import LimiterUnavailable
from parishkit.stewardship.accounts.policy import Capability, allows
from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.accounts.sessions import authenticated_admin
from parishkit.stewardship.jobs.ownership import database_now
from parishkit.stewardship.web.contracts import MESSAGES, ErrorCode
from parishkit.stewardship.web.dates import UnknownZone
from parishkit.stewardship.web.exports import download_headers

from .log_reads import (
    EXPORT_FORMATS,
    export_body,
    export_choice,
    export_file_name,
    export_rows,
    load_page,
)
from .log_rows import LogQuery, NothingShown, page_context
from .schemas import Action, ActorKind, Outcome
from .services import record_action

UNAVAILABLE = (ConfigError, LimiterUnavailable, ObjectDoesNotExist)
# From and Through arrived without the browser's zone (a tab opened before
# #558, or a zone the server's catalog lacks); they are never read as UTC.
ZONE_MESSAGE = _(
    "The dates came without your computer's time zone. Return to the logs and "
    "apply the filters again; if this repeats, check your computer's time zone "
    "setting."
)
# A submitted form ticked none of the six Show choices (#601); the page's gate
# normally keeps Apply unavailable, so this answers a bypassed or stale form.
NOTHING_MESSAGE = _(
    "Choose at least one kind of entry to show. Return to the logs and tick a "
    "level or Audit record before applying the filters."
)


def _error(code, status, *, query_string=False, message=None):
    """A fixed, accessible message with no submitted filter and no DB chrome.

    An unavailable database must not be queried again by the Admin navigation
    context processor while the error itself is rendered. Filter guidance is
    shown only when a filter value was the problem: a denied reader or an
    outage submitted nothing that could be corrected, and a query string is
    refused for where it was sent, not for what it said. A specific
    ``message`` (dates without a known zone, #558, or no kind of entry
    ticked, #601) replaces the closed one and the value guidance, since no
    value the reader typed was wrong.
    """
    response = HttpResponse(
        render_to_string(
            "stewardship/logs-error.html",
            {
                "message": message or MESSAGES[code],
                "invalid": code is ErrorCode.INVALID
                and not query_string
                and message is None,
                "query_string": query_string,
            },
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


@require_http_methods(["GET", "POST"])
def logs(request):
    """Capture a bounded page, render outside the transaction, then recheck.

    The log only grows, so an ordinary snapshot is coherent and the shared work
    lock is not taken. Every view is itself audited, so it appears in the list
    the next time; that is expected, not a loop. Downloads are ``export_logs``;
    free-text search and Ministry scope filtering belong to later increments.
    """
    try:
        service = runtime()
        actor = _principal(request, service.store)
        if request.GET:
            return _error(ErrorCode.INVALID, 400, query_string=True)
        parameters = request.POST.copy()
        parameters.pop("csrfmiddlewaretoken", None)
        query = LogQuery.parse(parameters)
        with transaction.atomic():
            configuration = SystemConfiguration.objects.first()
            if configuration is None or configuration.restore_review_required:
                return _error(ErrorCode.UNAVAILABLE, 503)
            through = query.snapshot or database_now()
            table, depth_limited = load_page(query, through=through)
        response = render(
            request,
            "stewardship/logs.html",
            page_context(query, table, depth_limited=depth_limited),
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
                Action.SYSTEM_LOGS_VIEWED,
                actor_kind=ActorKind.PORTAL_USER,
                actor_id=current.identity,
                # A count only: never a filter, an identifier or an address.
                context={"outcome": Outcome.SUCCEEDED, "count": len(table.rows)},
            )
        response["Cache-Control"] = "no-store"
        return response
    except PermissionError:
        return _error(ErrorCode.DENIED, 403)
    except DatabaseError:
        return _error(ErrorCode.UNAVAILABLE, 503)
    except UNAVAILABLE:
        return _error(ErrorCode.UNAVAILABLE, 503)
    except UnknownZone:
        return _error(ErrorCode.INVALID, 400, message=ZONE_MESSAGE)
    except NothingShown:
        return _error(ErrorCode.INVALID, 400, message=NOTHING_MESSAGE)
    except ValueError:
        return _error(ErrorCode.INVALID, 400)


@require_http_methods(["POST"])
def export_logs(request):
    """Download the filtered log, newest first, bounded to EXPORT_LIMIT entries.

    The same closed filters as the screen arrive in the CSRF POST body, plus a
    format (CSV or JSON Lines) and a timezone: UTC or one supported timezone
    name, which the page offers from the browser's own zone. The filters'
    ``zone`` (where From and Through days fall) is separate from that display
    choice: it is always the browser zone the page applied. The page's
    snapshot, page, size and sort are ignored, so an export always starts from
    the newest matching entry. Like
    the screen, the export is rechecked after it is built and then audited with
    a count only.
    """
    try:
        service = runtime()
        actor = _principal(request, service.store)
        if request.GET:
            return _error(ErrorCode.INVALID, 400, query_string=True)
        parameters = request.POST.copy()
        parameters.pop("csrfmiddlewaretoken", None)
        fmt = parameters.pop("format", ["csv"])
        zone_name = parameters.pop("timezone", ["UTC"])
        for name in ("through", "page", "size", "sort"):
            parameters.pop(name, None)
        if len(fmt) != 1 or len(zone_name) != 1:
            raise ValueError("Invalid log export choice.")
        fmt, zone = export_choice(fmt[0], zone_name[0])
        query = LogQuery.parse(parameters)
        with transaction.atomic():
            configuration = SystemConfiguration.objects.first()
            if configuration is None or configuration.restore_review_required:
                return _error(ErrorCode.UNAVAILABLE, 503)
            rows = export_rows(query)
        body = export_body(rows, fmt, zone)
        response = HttpResponse(
            body.encode("utf-8"),
            headers=download_headers(
                export_file_name(fmt, datetime.now(UTC)),
                content_type=EXPORT_FORMATS[fmt],
            ),
        )
        with transaction.atomic():
            current = _principal(request, service.store, final=True)
            if current.identity != actor.identity:
                raise PermissionError("Log reader changed.")
            record_action(
                Action.SYSTEM_LOGS_EXPORTED,
                actor_kind=ActorKind.PORTAL_USER,
                actor_id=current.identity,
                context={"outcome": Outcome.SUCCEEDED, "count": len(rows)},
            )
        return response
    except PermissionError:
        return _error(ErrorCode.DENIED, 403)
    except DatabaseError:
        return _error(ErrorCode.UNAVAILABLE, 503)
    except UNAVAILABLE:
        return _error(ErrorCode.UNAVAILABLE, 503)
    except UnknownZone:
        return _error(ErrorCode.INVALID, 400, message=ZONE_MESSAGE)
    except NothingShown:
        return _error(ErrorCode.INVALID, 400, message=NOTHING_MESSAGE)
    except ValueError:
        return _error(ErrorCode.INVALID, 400)

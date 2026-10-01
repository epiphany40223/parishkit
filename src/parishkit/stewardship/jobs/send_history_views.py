"""Admin-only, read-only history of the Family email sends (#432).

One row per send of the current campaign (``send_history``), newest first,
counted with the live progress panel's rules. The page is read in one
``read_transaction`` snapshot, takes no lock (in particular not the global
work-order lock the sending workers take) and records one audited Outgoing
mail view, like the panel.
"""

from dataclasses import replace

from django.db import DatabaseError, transaction
from django.shortcuts import render
from django.views.decorators.http import require_safe

from parishkit.stewardship.accounts.authentication import runtime
from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.audit.schemas import Action, ActorKind, Outcome
from parishkit.stewardship.audit.services import record_action
from parishkit.stewardship.campaigns.work_locks import read_transaction
from parishkit.stewardship.web.contracts import ErrorCode, filters
from parishkit.stewardship.web.tables import WINDOW_SIZES, paginate

from .delivery_views import UNAVAILABLE, _database_error, _error, _principal
from .ownership import database_now
from .send_history import count_row, list_sends, mark_live
from .send_progress import read_send

# Sends per page. A campaign has a handful (the invitation and each reminder,
# in Testing and in Production), so one page usually holds them all. Every
# shown send is counted, so there is no "All": at most 100 per page.
PAGE_SIZE = 25
SIZES = tuple(str(size) for size in WINDOW_SIZES)


def _load(parameters):
    """List the sends and count the shown page in one lock-free snapshot.

    Returns the template context, or None when the system is unavailable
    (no configuration yet, or a restore still under review).
    """
    with read_transaction():
        configuration = SystemConfiguration.objects.select_related(
            "current_campaign"
        ).first()
        if configuration is None or configuration.restore_review_required:
            return None
        campaign = configuration.current_campaign
        if parameters.get("size", str(PAGE_SIZE)) not in SIZES:
            raise ValueError("Unsupported table page size.")
        table = replace(
            paginate(
                list_sends(campaign.pk) if campaign else [],
                parameters,
                default=PAGE_SIZE,
            ),
            sizes=WINDOW_SIZES,
            allow_all=False,
        )
        if campaign is not None and table.rows:
            mode = configuration.mode
            # Only Production mail can be paused (see send_progress_views).
            paused = mode == "production" and campaign.delivery_paused
            now = database_now()
            rows = [
                count_row(campaign, sent, mode=mode, paused=paused, now=now)
                for sent in table.rows
            ]
            # Only the send the progress panel shows links to it, so read
            # the panel's choice when any shown send is in progress.
            if any(row.current and row.send.active for row in rows):
                cycle = campaign.production_cycle if mode == "production" else 0
                rows = mark_live(rows, read_send(campaign.pk, mode, cycle, now))
            table = replace(table, rows=rows)
        return {"campaign": campaign, "table": table}


@require_safe
def family_email_sends(request):
    """The Family email sends page: one audited view of a bounded page.

    As the progress panel, the session and the restore review are checked
    again after rendering, so an Admin signed out or demoted, or a restore
    begun, while the page rendered withholds it.
    """
    try:
        # Only an Administrator gets as far as having the query validated.
        service = runtime()
        actor = _principal(request, service.store)
        # The sends have one fixed order (newest first), so no sort option.
        parameters = filters(request.GET, allowed={"page", "size"})
        context = _load(parameters)
        if context is None:
            return _error(ErrorCode.UNAVAILABLE, 503)
        response = render(request, "stewardship/family-email-sends.html", context)
        with transaction.atomic():
            current = _principal(request, service.store, final=True)
            if current.identity != actor.identity:
                raise PermissionError("Send history reader changed.")
            if SystemConfiguration.objects.filter(
                restore_review_required=True
            ).exists():
                return _error(ErrorCode.UNAVAILABLE, 503)
            record_action(
                Action.DELIVERY_VIEWED,
                actor_kind=ActorKind.PORTAL_USER,
                actor_id=current.identity,
                context={
                    "outcome": Outcome.SUCCEEDED,
                    "count": len(context["table"].rows),
                },
            )
        response["Cache-Control"] = "no-store"
        return response
    except PermissionError:
        return _error(ErrorCode.DENIED, 403)
    except DatabaseError as error:
        return _database_error(error)
    except UNAVAILABLE:
        return _error(ErrorCode.UNAVAILABLE, 503)
    except ValueError:
        return _error(ErrorCode.INVALID, 400)

"""Admin-only, read-only Family email history page (#432).

One row per send of the current campaign (``send_history``), newest first,
counted with the live progress panel's rules. The page is read
(``send_reads.read_history``) in one ``read_transaction`` snapshot, takes no
lock (in particular not the global work-order lock the sending workers take)
and records one audited Outgoing mail view, like the panel.
"""

from django.db import DatabaseError, transaction
from django.shortcuts import render
from django.views.decorators.http import require_safe

from parishkit.stewardship.accounts.authentication import runtime
from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.audit.schemas import Action, ActorKind, Outcome
from parishkit.stewardship.audit.services import record_action
from parishkit.stewardship.web.contracts import ErrorCode, filters

from .delivery_views import UNAVAILABLE, _database_error, _error, _principal
from .send_reads import read_history


@require_safe
def family_email_sends(request):
    """The Family email history page: one audited view of a bounded page.

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
        context = read_history(parameters)
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

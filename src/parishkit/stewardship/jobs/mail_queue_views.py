"""Admin-only, read-only Mail queue page and its polled status (#1562, #413).

The page counts every email still waiting to go out, of every kind
(``mail_queue``), and follows the Family send in progress
(``send_progress``) in its Family send panel, or says that none is and
summarises the most recent one; it replaced the Family email progress page.
While the system is available, live-status-v1.js re-reads the status
fragment every few seconds, so a send that starts while the page is open
appears by itself. Neither read takes the global work-order lock: the counts
are read in ``read_transaction``, so watching the launch send never slows it
down. The page records one audited view; the fragment, polled for as long as
the page is open, records none and never renews the Admin's idle time.
"""

from django.db import DatabaseError, transaction
from django.http import HttpResponse
from django.shortcuts import render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views.decorators.http import require_safe

from parishkit.stewardship.accounts.authentication import runtime
from parishkit.stewardship.accounts.role_refusal import SHARED_REFUSALS
from parishkit.stewardship.accounts.role_refusal import response as role_response
from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.audit.schemas import Action, ActorKind, Outcome
from parishkit.stewardship.audit.services import record_action
from parishkit.stewardship.campaigns.work_locks import read_transaction
from parishkit.stewardship.web.contracts import ErrorCode, filters
from parishkit.stewardship.web.stat_tiles import tile

from .delivery_metadata import other_mode
from .delivery_reads import read_sending_holds
from .delivery_views import UNAVAILABLE, _database_error, _error, _principal
from .ownership import database_now
from .send_reads import audited_count, read_mail_queue

# How often an open page re-reads the status while emails remain, in ms.
POLL_MILLISECONDS = 5000
# How long an open page keeps checking without anything changing, in ms: the
# limit restarts whenever a check brings new counts, so a page opened up to
# an hour before a send keeps following it to the end.
GIVE_UP_MILLISECONDS = 3 * 60 * 60 * 1000


def _announcement(sent):
    """The short sentence screen readers hear when the coarse status changes."""
    if sent is None:
        return ""
    if not sent.active:
        return _("No Family email send is in progress.")
    if sent.paused:
        return _("Family email send paused.")
    if sent.counts.held:
        return _("Family email send held.")
    if sent.percent is None:
        return _("Family email send in progress; the total is not known yet.")
    return _("Family email send in progress, %(percent)s%% done.") % {
        "percent": sent.percent // 25 * 25
    }


def _paused_line(queue, paused):
    """Paused's explanation: whether live delivery is paused now.

    Only Production mail can be paused, so in Testing it says so instead.
    It is drawn on every read, so the tile never changes height when the
    pause starts or ends.
    """
    if queue.mode != "production":
        return _("Testing email is never paused")
    return _("Live delivery is paused") if paused else _("Live delivery is not paused")


def _tiles(queue, paused):
    """The Mail queue's four headline figures (ui-conventions "Stat tiles")."""
    return (
        tile("queued", _("Queued"), queue.column("queued")),
        tile("sending", _("Sending now"), queue.column("sending")),
        tile("retry-wait", _("Waiting to retry"), queue.column("retry_wait")),
        tile(
            "paused",
            _("Paused"),
            queue.column("paused"),
            details=(_paused_line(queue, paused),),
        ),
    )


def _load():
    """Read the queue and the current send (``send_reads.read_mail_queue``).

    Returns the template context, or None when the system is unavailable
    (no configuration yet, or a restore still under review). ``follow``
    keeps an open page checking whenever the system is available, whether
    or not anything is queued or a send is in progress, so a page left open
    shows the next emails by itself. ``upcoming`` (a Production send about
    to start) only changes what the idle Family send panel says.
    """
    data = read_mail_queue()
    if data is None:
        return None
    queue = data["queue"]
    return data | {
        "tiles": _tiles(queue, data["paused"]),
        "other_mode": other_mode(queue.mode),
        "follow": True,
        "announcement": _announcement(data["send"]),
        "poll_interval": POLL_MILLISECONDS,
        "give_up": GIVE_UP_MILLISECONDS,
    }


def _read(request, template, *, page):
    """Admit a current Administrator, read the send, render, then recheck.

    Rendering happens outside the read snapshot. Before releasing the HTML
    the session and the restore review are checked again, so an Admin
    signed out or demoted, or a restore begun, while the page rendered
    withholds it. Only the page is audited.
    """
    try:
        filters(request.GET, allowed=set())
        service = runtime()
        actor = _principal(request, service.store)
        context = _load()
        if context is None:
            return _error(ErrorCode.UNAVAILABLE, 503)
        if page:
            context["status_url"] = reverse("admin:mail_queue_status")
            # Sent mail's sending limit note, drawn once per page load
            # outside the polled region, so it never appears or vanishes
            # under the pointer; the fragment never reads it.
            with read_transaction():
                context["holds"] = read_sending_holds(database_now())
            response = render(request, template, context)
        else:
            # The fragment needs no Admin chrome, so it skips the context
            # processors (and their queries) a full page render runs.
            response = HttpResponse(render_to_string(template, context))
        with transaction.atomic():
            current = _principal(request, service.store, final=True)
            if current.identity != actor.identity:
                raise PermissionError("Mail queue reader changed.")
            # As delivery_views._page: a restore review that began while the
            # page rendered withholds it.
            if SystemConfiguration.objects.filter(
                restore_review_required=True
            ).exists():
                return _error(ErrorCode.UNAVAILABLE, 503)
            if page:
                record_action(
                    Action.DELIVERY_VIEWED,
                    actor_kind=ActorKind.PORTAL_USER,
                    actor_id=current.identity,
                    context={
                        "outcome": Outcome.SUCCEEDED,
                        "count": audited_count(context["queue"]),
                    },
                )
        response["Cache-Control"] = "no-store"
        return response
    except SHARED_REFUSALS as error:
        # A role that lacks the page, or an ended session: the shared
        # refusal (#1924, #2062), not this page's own error.
        return role_response(error)
    except PermissionError:
        return _error(ErrorCode.DENIED, 403)
    except DatabaseError as error:
        return _database_error(error)
    except UNAVAILABLE:
        return _error(ErrorCode.UNAVAILABLE, 503)
    except ValueError:
        return _error(ErrorCode.INVALID, 400)


@require_safe
def mail_queue(request):
    """The Mail queue page: one audited view, then passive polls."""
    return _read(request, "stewardship/mail-queue.html", page=True)


@require_safe
def mail_queue_status(request):
    """Passive status fragment the open page polls; never audited."""
    return _read(request, "stewardship/mail-queue-status.html", page=False)

"""Admin-only, read-only Family email progress page and its polled status (#413).

The page shows the Family send in progress (``send_progress``), or says that
none is and summarises the most recent one. While there is a current
campaign, live-status-v1.js re-reads the status fragment every few seconds,
so a send that starts while the page is open appears by itself. Neither
read takes the global work-order lock: the counts are read in
``read_transaction``, so watching the launch send never slows it down. The
page records one audited view; the fragment, polled for as long as the page
is open, records none and never renews the Admin's idle time.
"""

from django.db import DatabaseError, transaction
from django.http import HttpResponse
from django.shortcuts import render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views.decorators.http import require_safe

from parishkit.stewardship.accounts.authentication import runtime
from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.audit.schemas import Action, ActorKind, Outcome
from parishkit.stewardship.audit.services import record_action
from parishkit.stewardship.campaigns.work_locks import read_transaction
from parishkit.stewardship.web.contracts import ErrorCode, filters

from .delivery_views import UNAVAILABLE, _database_error, _error, _principal
from .ownership import database_now
from .send_progress import progress, read_send, upcoming

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


def _audited_count(sent):
    """The emails the audited view showed: the total, or those counted so far."""
    if sent is None:
        return 0
    return sent.done + sent.counts.remaining if sent.total is None else sent.total


def _load():
    """Read the current send in one read-only snapshot, without any lock.

    Returns the template context, or None when the system is unavailable
    (no configuration yet, or a restore still under review). ``follow``
    keeps an open page checking while there is a current campaign, whether
    or not a send is in progress, so a page left open switches to the next
    send by itself. ``upcoming`` (a Production send about to start) only
    changes what the idle page says.
    """
    with read_transaction():
        configuration = SystemConfiguration.objects.select_related(
            "current_campaign"
        ).first()
        if configuration is None or configuration.restore_review_required:
            return None
        campaign = configuration.current_campaign
        production = configuration.mode == "production"
        # Only Production mail can be paused; use the pause control itself,
        # since messages are held one by one as workers reach them.
        paused = bool(production and campaign and campaign.delivery_paused)
        sent = soon = None
        if campaign is not None:
            cycle = campaign.production_cycle if production else 0
            now = database_now()
            counts = read_send(campaign.pk, configuration.mode, cycle, now)
            sent = None if counts is None else progress(counts, paused=paused)
            if sent is None or not sent.active:
                soon = upcoming(campaign, configuration.mode, cycle, now)
        return {
            "campaign": campaign,
            "testing": not production,
            "paused": paused,
            "send": sent,
            "upcoming": bool(soon),
            "follow": campaign is not None,
            "announcement": _announcement(sent),
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
            context["status_url"] = reverse("admin:family_email_progress_status")
            response = render(request, template, context)
        else:
            # The fragment needs no Admin chrome, so it skips the context
            # processors (and their queries) a full page render runs.
            response = HttpResponse(render_to_string(template, context))
        with transaction.atomic():
            current = _principal(request, service.store, final=True)
            if current.identity != actor.identity:
                raise PermissionError("Progress reader changed.")
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
                        "count": _audited_count(context["send"]),
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


@require_safe
def family_email_progress(request):
    """The Family email progress page: one audited view, then passive polls."""
    return _read(request, "stewardship/family-email-progress.html", page=True)


@require_safe
def family_email_progress_status(request):
    """Passive status fragment the open page polls; never audited."""
    return _read(request, "stewardship/family-email-progress-status.html", page=False)

"""Publish to ParishSoft: what waits, Check ParishSoft and its plan (#528).

The page (reports spec, "Publish to ParishSoft"; ADM-09) is the
Administrator's. This first slice reads and checks, and writes nothing to
ParishSoft: Publish, Run again and the command line come in later slices.

- While the deployment's ``parishsoft_publication`` setting is off the page
  says so and offers no button, and every post is refused
  (``publication_off``).
- Check ParishSoft posts to the page itself and queues one preflight
  (``publication_tasks.request_preflight``) under a key drawn with the page,
  so a repeated click returns the same check. As Refresh now does on Refresh
  from ParishSoft (``accounts.refresh_views``), the sign-in remembers the
  check, never the address, and the page shows its live status below the
  button through the task's passive status fragment; when it ends, the page
  redraws in place with the plan.
- The current campaign's newest plan, whoever made it, is shown with its
  counts and a table of its rows, which sorts and pages in place.

Viewing records ``census_publication_viewed`` with the number of plan rows.
"""

from uuid import UUID, uuid4

from django.core.exceptions import ObjectDoesNotExist
from django.db import DatabaseError, transaction
from django.http import HttpResponseRedirect
from django.template.loader import render_to_string
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from parishkit.stewardship.accounts.authentication import denial, runtime
from parishkit.stewardship.accounts.policy import Capability, allows
from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.accounts.sessions import authenticated_admin
from parishkit.stewardship.audit.schemas import Action, ActorKind, Outcome
from parishkit.stewardship.audit.services import record_action
from parishkit.stewardship.jobs.views import PUBLICATION_CHECK, _task_read
from parishkit.stewardship.observability import (
    Event,
    current_correlation,
    emit_failure,
)
from parishkit.stewardship.responses.publication_tasks import request_preflight
from parishkit.stewardship.storage import StorageInvariantError
from parishkit.stewardship.web.contracts import filters
from parishkit.stewardship.web.refusals import (
    UserFacingUnavailable,
    unavailable_response,
)
from parishkit.stewardship.web.responses import campaign_response
from parishkit.stewardship.web.tables import paginate, table_parameters

from . import census_publication as publication
from .census_change_views import _error
from .export_views import SAFE_FAILURES
from .family_links import link_families
from .read_admission import admit_report_read

# The session key remembering the check this sign-in last requested, so the
# page shows it after the Post/Redirect/Get (navigation rule 5).
RUN_KEY = "stewardship_publication_check"


def _principal(request, store, *, read_only=False):
    """The signed-in Administrator who may publish; anyone else is denied."""
    principal = authenticated_admin(
        request, store=store, activity=not read_only, read_only=read_only
    )
    if not allows(principal, Capability.PUBLISH_CENSUS):
        raise PermissionError("Publish to ParishSoft is unavailable.")
    return principal


def _audit(principal, campaign_id, outcome, count):
    """Record the page view as a count of plan rows, never a name or value."""
    with transaction.atomic():
        system = SystemConfiguration.objects.select_related(
            "active_configuration__parish"
        ).get()
        record_action(
            Action.CENSUS_PUBLICATION_VIEWED,
            actor_kind=ActorKind.PORTAL_USER,
            actor_id=principal.identity,
            subject_id=campaign_id,
            parish_id=system.active_configuration.parish.pk,
            campaign_id=campaign_id,
            context={"outcome": outcome, "count": count},
        )


def _run(request):
    """The live status of the check this sign-in last requested, or {}.

    Read as Refresh from ParishSoft reads its run (``refresh_views._run``):
    unaudited, pointing the status region's poller at the task's passive
    status fragment, which never renews idle time. A check that can no
    longer be read is forgotten rather than shown as an error.
    """
    task_id = request.session.get(RUN_KEY)
    if not isinstance(task_id, str):
        return {}
    result, context = _task_read(request, task_id)
    if context is None:
        if result.status_code in {403, 404}:
            del request.session[RUN_KEY]
        return {}
    return context | {
        "run_task_id": task_id,
        "region_id": PUBLICATION_CHECK,
        "publication_check": True,
        "read_unconfirmed": publication.read_unconfirmed(task_id),
        "status_url": reverse("admin:background_task_status", args=[task_id])
        + "?view=publication",
    }


def _check(request, service, principal, campaign_id):
    """Queue Check ParishSoft under the page's key and show it on this page.

    The page draws the button unavailable for each refusal here, so only a
    stale page meets one; it is answered in place with its reason.
    """
    if not publication.enabled():
        # Every post is refused while the switch is off, before anything is
        # read (reports spec, "Publishing switched off").
        raise publication.OFF.refusal()
    filters(request.POST, allowed={"request_key", "csrfmiddlewaretoken"})
    if request.FILES:
        raise ValueError("Unexpected upload.")
    key = UUID(request.POST.get("request_key", ""))
    if key.version != 4:
        raise ValueError("Invalid check key.")
    waiting = publication.waiting(campaign_id)
    gate = publication.check_gate(
        campaign_id, enabled=True, waiting_changes=waiting["changes"]
    )
    if gate is not None:
        raise gate.refusal()
    try:
        task = request_preflight(
            service.store,
            principal.identity,
            request_key=key,
            correlation_id=current_correlation(),
            enabled=publication.enabled(),
        )
    except PermissionError:
        # The page's gate passed, but other campaign work took the campaign
        # before the request was admitted (a race): refuse it as the gate
        # does, rather than as a denied page.
        raise publication.HELD_CAMPAIGN.refusal() from None
    request.session[RUN_KEY] = str(task.run_id)
    return HttpResponseRedirect(
        reverse("admin:census_publication") + f"#{PUBLICATION_CHECK}"
    )


def _paging(request):
    """The plan table's page, size and sort, refused when malformed."""
    values = filters(request.GET, allowed=set(table_parameters()))
    paginate([], values, sorting=publication.SORTING)
    return values


def _render(request, campaign_id, paging, run):
    """Read what waits, the gate and the newest plan; return the page bytes."""
    on = publication.enabled()
    waiting = publication.waiting(campaign_id) if on else None
    gate = publication.check_gate(
        campaign_id, enabled=on, waiting_changes=waiting and waiting["changes"]
    )
    plan = publication.newest_plan(campaign_id) if on else None
    view = publication.plan_view(plan) if plan is not None else None
    table = (
        paginate(view["rows"], paging, sorting=publication.SORTING)
        if view is not None
        else None
    )
    if table is not None:
        # Each shown Family name opens its Timeline (#1570): one read of
        # the page's Family record ids.
        link_families(campaign_id, table.rows)
    return (
        render_to_string(
            "stewardship/census-publication.html",
            {
                "enabled": on,
                "waiting": waiting,
                "gate": gate,
                "view": view,
                "table": table,
                "request_key": uuid4(),
                **run,
            },
            request=request,
        ).encode(),
        len(view["rows"]) if view is not None else 0,
    )


@require_http_methods(["GET", "HEAD", "POST"])
def page(request, campaign_id):
    """Publish to ParishSoft: the page, and its Check ParishSoft (a POST)."""
    finish, handed_off = None, False
    try:
        service = runtime()
        principal = _principal(request, service.store)
        admit_report_read(campaign_id)
        if request.method == "POST":
            try:
                return _check(request, service, principal, campaign_id)
            except UserFacingUnavailable as error:
                return unavailable_response(error)
            except ValueError:
                return _error(campaign_id, status=400)
        try:
            paging = _paging(request)
        except ValueError:
            return _error(campaign_id, status=400)
        run = _run(request) if publication.enabled() else {}
        finalized, count = False, 0

        def finish(completed):
            """Audit the view once, after releasing the read transaction."""
            nonlocal finalized
            if finalized:
                return
            finalized = True
            try:
                _audit(
                    principal,
                    campaign_id,
                    Outcome.SUCCEEDED if completed else Outcome.FAILED,
                    count,
                )
            except (DatabaseError, StorageInvariantError) as error:
                emit_failure(error, event=Event.REPORT_AUDIT_FAILED)

        def authorize(guard):
            """A role change while the page is produced ends it."""
            nonlocal principal
            fresh = _principal(request, service.store, read_only=True)
            if fresh.identity != principal.identity:
                raise PermissionError("Publish to ParishSoft access changed.")
            principal = fresh
            admit_report_read(campaign_id)

        def content():
            """Only detached authorized data reaches the template."""
            nonlocal count
            body, count = _render(request, campaign_id, paging, run)
            return iter((body,))

        response = campaign_response(
            request,
            [campaign_id],
            authorize=authorize,
            open_content=content,
            on_close=finish,
        )
        if response.status_code == 503 and not response.streaming:
            return _error(campaign_id, status=503)
        handed_off = response.status_code == 200 and response.streaming
        return response
    except (PermissionError, ObjectDoesNotExist):
        return denial()
    except (*SAFE_FAILURES, StorageInvariantError):
        return _error(campaign_id, status=503)
    except ValueError as error:
        emit_failure(error, event=Event.REPORT_SHAPING_FAILED)
        return _error(campaign_id, status=503)
    finally:
        if finish is not None and not handed_off:
            finish(False)

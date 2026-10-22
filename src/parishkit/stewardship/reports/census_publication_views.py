"""Update ParishSoft: what waits, Check ParishSoft, its plan and its run.

The page (reports spec, "Update ParishSoft"; ADM-09, renamed from Publish
to ParishSoft by #1688) is the Administrator's.

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
- Update N changes opens the shared review dialog as a confirmation: its
  review and Apply post to ``confirmation/`` (``confirmation``), naming the plan,
  which calls ``confirm_and_publish`` after the shared newest-plan check.
  Run again's review and Apply post to ``retries/``
  (``retries``), which calls ``rerun_publication``. The run's live status
  then shows in the plan's region, and each written change's result in the
  table once it ends.

Viewing records ``census_publication_viewed`` with the number of plan rows;
the dialog's posts record what their services record.
"""

import logging
from uuid import UUID, uuid4

from django.core.exceptions import ObjectDoesNotExist
from django.db import DatabaseError, transaction
from django.http import HttpResponseRedirect
from django.shortcuts import render
from django.template.loader import render_to_string
from django.urls import reverse
from django.views.decorators.http import require_http_methods, require_POST

from parishkit.stewardship.accounts.authentication import denial, runtime
from parishkit.stewardship.accounts.policy import Capability
from parishkit.stewardship.accounts.role_refusal import require
from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.accounts.sessions import (
    authenticated_admin,
    automatic_read,
    sign_in_instant,
)
from parishkit.stewardship.audit.schemas import Action, ActorKind, Outcome
from parishkit.stewardship.audit.services import record_action
from parishkit.stewardship.jobs.models import NONTERMINAL_STATES, TaskRun
from parishkit.stewardship.jobs.storage import TaskRetryConflict
from parishkit.stewardship.jobs.views import (
    PUBLICATION_CHECK,
    UPDATE_RUN,
    _task_read,
)
from parishkit.stewardship.observability import (
    Event,
    current_correlation,
    emit_failure,
)
from parishkit.stewardship.responses.publication_models import (
    PublicationConfirmation,
)
from parishkit.stewardship.responses.publication_plans import confirmation_counts
from parishkit.stewardship.responses.publication_tasks import (
    confirm_and_publish,
    request_preflight,
    rerun_publication,
)
from parishkit.stewardship.responses.publication_worker import PublicationDisabled
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
# The counts Update's review shows and Apply binds (confirm_plan's).
COUNTS = ("write_entities", "write_fields", "blocked_entities")


def _principal(request, store, *, read_only=False, passive=False):
    """The signed-in Administrator who may update; anyone else is denied.

    ``passive`` (the page's own automatic re-read, #1985) admits the same
    Administrator by the same checks without renewing the sign-in.
    """
    principal = authenticated_admin(
        request,
        store=store,
        activity=not read_only and not passive,
        read_only=read_only,
    )
    return require(principal, Capability.PUBLISH_CENSUS)


def _audit(principal, campaign_id, outcome, count, *, automatic=False):
    """Record the page view as a count of plan rows, never a name or value.

    ``automatic`` marks the page's own re-read (#1985), as a report's
    automatic view is marked (#1620): the view is audited either way.
    """
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
            context={"outcome": outcome, "count": count}
            | ({"automatic": True} if automatic else {}),
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


def _update_run(request, campaign_id):
    """The newest plan's latest update run's live status, or {} for none.

    Read unaudited before the page is produced, as the check's is, and drawn
    in the plan's region (``UPDATE_RUN``), polled with ``?view=update`` so a
    poll words the run as the page does.
    """
    plan = publication.newest_plan(campaign_id)
    run = publication.latest_run(plan) if plan is not None else None
    if run is None:
        return {}
    result, context = _task_read(request, str(run.pk))
    if context is None:
        return {}
    return {
        "update_status": context
        | {
            "region_id": UPDATE_RUN,
            "update_run": True,
            "status_url": reverse("admin:background_task_status", args=[run.pk])
            + "?view=update",
        },
        "update_task_id": run.pk,
    }


def _check(request, service, principal, campaign_id):
    """Queue Check ParishSoft under the page's key and show it on this page.

    The page draws the button unavailable for each refusal here, so only a
    stale page meets one; it is answered in place with its reason.
    """
    if not publication.enabled():
        # Every post is refused while the switch is off, before anything is
        # read (reports spec, "Updating switched off").
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
        reverse("admin:update_parishsoft") + f"#{PUBLICATION_CHECK}"
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
            "stewardship/update-parishsoft.html",
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
    """Update ParishSoft: the page, and its Check ParishSoft (a POST)."""
    finish, handed_off = None, False
    try:
        service = runtime()
        # The page re-reads itself once its update run settles, and when it
        # is shown again (#1985): such a read is passive, so it never extends
        # the sign-in, and its audited view is marked automatic.
        automatic = automatic_read(request)
        principal = _principal(request, service.store, passive=automatic)
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
        run = (
            _run(request) | _update_run(request, campaign_id)
            if publication.enabled()
            else {}
        )
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
                    automatic=automatic,
                )
            except (DatabaseError, StorageInvariantError) as error:
                emit_failure(error, event=Event.REPORT_AUDIT_FAILED)

        def authorize(guard):
            """A role change while the page is produced ends it."""
            nonlocal principal
            fresh = _principal(request, service.store, read_only=True)
            if fresh.identity != principal.identity:
                raise PermissionError("Update ParishSoft access changed.")
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


def _step_fields(request, confirm_fields):
    """The step's action and plan, refusing unexpected fields and uploads.

    A review posts its action and the plan the page showed; Apply also posts
    the review's hidden fields (``confirm_fields``). The plan travels in the
    post, not the address, so the dialog (outside every region, which a
    redraw never removes) keeps one Apply address whichever plan is newest.
    """
    action = request.POST.get("action", "")
    if action not in {"preview", "confirm"}:
        raise ValueError("Unknown step.")
    allowed = {"action", "plan", "csrfmiddlewaretoken"}
    if action == "confirm":
        allowed |= confirm_fields
    filters(request.POST, allowed=allowed)
    if request.FILES:
        raise ValueError("Unexpected upload.")
    return action, UUID(request.POST.get("plan", ""))


def _key(request, name):
    """A version 4 key the review drew, from the posted field ``name``."""
    key = UUID(request.POST.get(name, ""))
    if key.version != 4:
        raise ValueError("Invalid key.")
    return key


def _not_offered(run):
    """The refusal for a step the plan no longer offers.

    An unfinished run is running; otherwise the plan has moved on (another
    Administrator confirmed it, or its run completed).
    """
    if run is not None and run.state in NONTERMINAL_STATES:
        return publication.RUNNING.refusal()
    return publication.CHANGED.refusal()


def _update_step(request, service, principal, campaign_id):
    """Update's review (a confirmation) or Apply, for the campaign's newest plan.

    The review re-checks what the page drew (the newest plan, not yet
    confirmed, in Production, nothing unfinished, the campaign free and the
    plan less than an hour old) and draws the confirmation with a new key.
    Apply checks the same, requires the counts shown to equal the plan's,
    and calls ``confirm_and_publish`` under the review's key, so a repeated
    Apply returns the same run; the database checks every rule again.
    """
    action, plan_id = _step_fields(request, {"request_key", *COUNTS})
    plan = publication.newest_plan_check(campaign_id, plan_id)
    counts = confirmation_counts(plan.pk)
    if action == "confirm":
        key = _key(request, "request_key")
        shown = {name: int(request.POST.get(name, "")) for name in COUNTS}
        replay = PublicationConfirmation.objects.filter(
            actor_id=principal.identity, request_key=key, plan_id=plan.pk
        ).exists()
    else:
        replay = False
    if not replay:
        run = publication.latest_run(plan)
        offer = publication.offer(plan, counts, run=run)
        if offer.kind != "update":
            raise _not_offered(run)
        if offer.gate is not None:
            raise offer.gate.refusal()
    if action == "preview":
        return {
            "update": {
                "preview": True,
                "plan": plan,
                "counts": counts,
                "title": publication.update_question(counts),
                "key": uuid4(),
            }
        }
    if shown != {name: counts[name] for name in COUNTS}:
        raise publication.CHANGED.refusal()
    try:
        confirm_and_publish(
            service.store,
            principal.identity,
            plan.pk,
            correlation_id=current_correlation(),
            enabled=publication.enabled(),
            session_id=request.portal_session.pk,
            authenticated_at=sign_in_instant(request),
            request_key=key,
            **shown,
        )
    except PublicationDisabled:
        # Switched off between the page's check and the service's.
        raise publication.OFF.refusal() from None
    except DatabaseError as error:
        refusal = publication.confirm_refusal(error, plan)
        if refusal is error:
            # Not a refusal: an outage, logged here and answered as one (503).
            emit_failure(error, level=logging.ERROR, name_class=True)
            raise
        raise refusal from None
    except PermissionError:
        # The services' own admission refused what the page's checks passed
        # a moment before (a race): said as the page would now say it.
        raise _raced(request, service, plan) from None
    except ValueError:
        # The key is bound to another intent: a stale dialog.
        raise publication.CHANGED.refusal() from None
    return {"update": {"started": True}}


def _raced(request, service, plan):
    """The refusal for a service refusing what the page's checks passed.

    The Administrator is checked again first, so a lost role is still a
    denial; otherwise the plan's region is worked out again and its reason
    given: an unfinished run, the campaign held, Testing mode, every Member
    finished, or, failing a better word, that something changed.
    """
    _principal(request, service.store, read_only=True)
    run = publication.latest_run(plan)
    offer = publication.offer(plan, confirmation_counts(plan.pk), run=run)
    if offer.gate is not None:
        return offer.gate.refusal()
    if run is not None and run.state in NONTERMINAL_STATES:
        return publication.RUNNING.refusal()
    if run is not None and offer.kind is None:
        return publication.FINISHED.refusal()
    return publication.CHANGED.refusal()


def _rerun_step(request, service, principal, campaign_id):
    """Run again's review (a confirmation) or Apply, for the newest plan.

    Offered only once the plan's run task failed and a Member has no final
    outcome (``census_publication.offer``), never in Testing mode. Apply
    calls ``rerun_publication`` with the review's key as
    its command id, so a repeated Apply returns the same retry.
    """
    action, plan_id = _step_fields(request, {"command_key"})
    plan = publication.newest_plan_check(campaign_id, plan_id)
    run = publication.latest_run(plan)
    key = _key(request, "command_key") if action == "confirm" else None
    replay = (
        key is not None
        and run is not None
        and TaskRun.objects.filter(root_id=run.root_id, retry_command_id=key).exists()
    )
    if not replay:
        offer = publication.offer(plan, confirmation_counts(plan.pk), run=run)
        if offer.kind != "rerun":
            if run is not None and run.state in NONTERMINAL_STATES:
                raise publication.RUNNING.refusal()
            raise publication.FINISHED.refusal()
        if offer.gate is not None:
            raise offer.gate.refusal()
    if action == "preview":
        return {
            "rerun": {
                "preview": True,
                "plan": plan,
                "title": publication.rerun_question(offer.unfinished),
                "closing": publication.past_window(plan),
                "key": uuid4(),
            }
        }
    try:
        rerun_publication(
            service.store,
            principal.identity,
            plan.pk,
            command_id=key,
            correlation_id=current_correlation(),
            enabled=publication.enabled(),
        )
    except PublicationDisabled:
        # Switched off between the page's check and the service's.
        raise publication.OFF.refusal() from None
    except (PermissionError, TaskRetryConflict):
        # Another Administrator's Run again, or the run's last Member
        # finished, between the page's check and the service's. Any other
        # storage invariant is a programming error, answered as an outage.
        raise _raced(request, service, plan) from None
    return {"rerun": {"started": True}}


def _dialog(request, campaign_id, step):
    """Answer one of the review dialog's posts (a review or Apply) for a plan.

    Every post is refused while the switch is off. A refusal the page would
    have drawn (``UserFacingUnavailable``) is the closed JSON refusal the
    dialog shows below its buttons; a malformed post is a 400. The answer
    is ``update-parishsoft-review.html``: a review the dialog shows, or,
    after Apply, a plain fragment, after which the page is read again in
    place and shows the run's status.
    """
    try:
        service = runtime()
        principal = _principal(request, service.store)
        admit_report_read(campaign_id)
        try:
            if not publication.enabled():
                raise publication.OFF.refusal()
            context = step(request, service, principal, campaign_id)
        except UserFacingUnavailable as error:
            return unavailable_response(error)
        except ValueError:
            return _error(campaign_id, status=400)
        response = render(request, "stewardship/update-parishsoft-review.html", context)
        response["Cache-Control"] = "no-store"
        return response
    except (PermissionError, ObjectDoesNotExist):
        return denial()
    except (*SAFE_FAILURES, StorageInvariantError):
        return _error(campaign_id, status=503)


@require_POST
def confirmation(request, campaign_id):
    """Update N changes: its confirmation's review and Apply (#1688)."""
    return _dialog(request, campaign_id, _update_step)


@require_POST
def retries(request, campaign_id):
    """Run again: its confirmation's review and Apply (#1688)."""
    return _dialog(request, campaign_id, _rerun_step)

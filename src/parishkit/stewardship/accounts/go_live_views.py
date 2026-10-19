"""The Go live page's Check step, Testing cleanup and Testing submissions.

Check (#1585 slice 2) shows passive readiness evidence and starts the
irreversible Testing cleanup from the shared review dialog; Testing cleanup
follows it until #1585 slice 3 replaces it with the Prepare step.
"""

from uuid import UUID, uuid4

from django.core import signing
from django.core.exceptions import ObjectDoesNotExist
from django.http import HttpResponseRedirect
from django.shortcuts import render
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_http_methods

from parishkit.stewardship.campaigns.cleanup_preview import (
    TESTING_FAMILY_SORTING,
    cleanup_families,
)
from parishkit.stewardship.campaigns.domain import Percentage
from parishkit.stewardship.campaigns.production_models import (
    ProductionTransitionRequest,
)
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.jobs.admission import _scope
from parishkit.stewardship.source.readiness import source_readiness
from parishkit.stewardship.storage import StaleRecordError
from parishkit.stewardship.web.contracts import PageWindow, expected_version, filters
from parishkit.stewardship.web.refusals import (
    UserFacingGone,
    refused_step,
    unexpected_fields,
)
from parishkit.stewardship.web.tables import window_table

from . import admin_navigation, go_live_check
from .activation_progress import _sign as sign_links_control
from .activation_progress import control as links_control
from .admin_caller import AdminCaller
from .admin_editing import editable_configuration, principal
from .authentication import runtime
from .content_models import ContentVersion
from .go_live_check import remember_check
from .go_live_commands import SALT as COMMAND_SALT
from .go_live_commands import (
    configured_origin,
    origin_check,
    start_cleanup,
    verify_preview,
)
from .go_live_inputs import collect_inputs
from .go_live_progress import control, progress
from .integration_views import ERRORS, _checked
from .setup_views import _closed, error_response

PROBLEMS = {
    "initial_schedule_required": _("Save exactly one initial invitation schedule."),
    "page_reference_unavailable": _("Resolve the selected campaign page revisions."),
    "mail_template_unavailable": _(
        "Select an available template for every mail schedule."
    ),
    "ministry_mapping_unavailable": _(
        "Review Ministries missing from the current source."
    ),
    "financial_mapping_incomplete": _(
        "Complete financial periods, fund mappings and sharing options."
    ),
    "integration_configuration_incomplete": _(
        "Complete the required integration settings."
    ),
    "admin_recipient_required": _(
        "Configure at least one explicit Administrator email address."
    ),
    "tenant_unavailable": _("Check the configured ParishSoft organization."),
    "full_refresh_required": _("Complete a full ParishSoft refresh for this campaign."),
    "source_scope_changed": _(
        "Refresh the source for this campaign and its selected financial periods."
    ),
    "full_refresh_stale": _(
        "The last full refresh is too old. Run another full refresh."
    ),
    "testing_delivery_unresolved": _(
        "Wait for or resolve all unfinished and uncertain Testing deliveries."
    ),
    "production_delivery_unresolved": _(
        "Resolve uncertain or already allocated live mail before continuing."
    ),
    "family_population_unavailable": _(
        "Wait for the current source and Family eligibility population to agree."
    ),
    "cleanup_already_started": _("A go-live cleanup already owns this campaign."),
    "campaign_work_held": _(
        "Another campaign operation currently holds this workflow."
    ),
    "other_campaign_live": _("Another campaign is scheduled or active."),
    "configuration_pending": _("Wait for the pending configuration change to finish."),
    "parishsoft_check_required": _(
        "Verify the current ParishSoft credential and organization."
    ),
    "google_workspace_check_required": _(
        "Verify the current Google Workspace credential and sender settings."
    ),
    "slack_check_required": _("Verify the current Slack credential and channel."),
    "family_email_unsendable": _(
        "Fix the invitation or reminder that Pages and emails marks “Can't be "
        "sent until fixed”: its Family code or link is only inside markup that "
        "sending removes."
    ),
    # A test of unsaved text from a scheduled email's page (#446) counts as a
    # test of the email it started from; any later save changes the
    # configuration, so a new test is needed then.
    "family_test_mail_required": _(
        "Preview and successfully send a selected Family email "
        "using the current configuration. A test of unsaved text sent from a "
        "scheduled email counts as a test of the email it started from; "
        "saving any change afterwards means sending a new test."
    ),
    "family_test_mail_pending": _("Wait for the pending Family email test to finish."),
    "campaign_closed": _("The campaign closing instant has passed. Review its dates."),
}
# The page that fixes each readiness problem, by its Admin URL name; a
# problem that only needs waiting (a running test or refresh, other
# campaign work) has none. Test emails link to each email's own page.
FIXES = {
    "initial_schedule_required": "schedule_settings",
    "page_reference_unavailable": "content_catalog",
    "mail_template_unavailable": "schedule_settings",
    "ministry_mapping_unavailable": "campaign_settings",
    "financial_mapping_incomplete": "campaign_settings",
    "integration_configuration_incomplete": "integrations",
    "admin_recipient_required": "users",
    "tenant_unavailable": "integrations",
    "full_refresh_required": "source_refresh",
    "source_scope_changed": "source_refresh",
    "full_refresh_stale": "source_refresh",
    "testing_delivery_unresolved": "deliveries",
    "production_delivery_unresolved": "deliveries",
    "campaign_work_held": "background",
    "parishsoft_check_required": "integrations",
    "google_workspace_check_required": "integrations",
    "slack_check_required": "integrations",
    "family_email_unsendable": "content_catalog",
    "campaign_closed": "schedule_settings",
}


# Every Check step action, by its form's ``action``, with its other fields.
_CHECK_FIELDS = {
    "verify": set(),
    "review": set(),
    "confirm": {"preview_token"},
    "retry_stop": {"control"},
}
# The Start go-live dialog's refusals (go-live-start-review.html) and their
# statuses; the refusal log records each by its key (``refused_step``).
_START_REFUSALS = {
    "started": 409,
    "stopping": 409,
    "stop_failed": 409,
    "closed": 409,
    "changed": 409,
    "origin_unverified": 409,
}
# How often the Check step re-reads itself while nothing is running: the
# readiness checks change only when the Administrator fixes something.
_IDLE_INTERVAL_MS = 30000
# The longest the shared watcher follows a page (live-status-v1.js).
_WATCH_MS = 3 * 60 * 60 * 1000


@require_http_methods(["GET", "HEAD", "POST"])
def check(request, campaign_id):
    """The Go live page's Check step (#1585): passive GET, every POST in place.

    GET draws the step from durable state and the session's kept public web
    address check, so a reload returns to the same state; it sends no mail,
    creates no cleanup intent and does not extend the sign-in's idle time.
    Each POST answers a redirect to this address or to the step in
    progress, or a fragment of the Start go-live dialog, never a page:

    - ``verify`` runs the DNS and origin check, keeps its result in the
      session (``go_live_check.remember_check``) and redirects here.
    - ``review`` answers the Start go-live dialog's review, holding the
      signed preview Apply posts, or a refusal the dialog shows as drawn.
    - ``confirm`` (Apply, the acknowledgement that cleanup cannot be undone)
      runs the public web address check again, starts Testing cleanup and
      redirects to the step in progress.
    - ``retry_stop`` retries the failed disposal of a stopped go-live's
      Family links and redirects here.
    """
    try:
        filters(request.GET, allowed=set())
        service = runtime()
        if request.method != "POST":
            _closed(request, set())
            context = _check_context(request, service, campaign_id)
            response = render(request, "stewardship/go-live-check.html", context)
            return _checked(request, service, response)
        action = request.POST.get("action")
        if action not in _CHECK_FIELDS:
            # No sources: an unknown action value, not an unexpected field.
            raise unexpected_fields()
        _closed(request, {"action", *_CHECK_FIELDS[action]})
        if action in {"review", "confirm"}:
            return _start(request, service, campaign_id, action)
        if action == "verify":
            actor = principal(request, service)
            inputs, verified, _token = verify_preview(request, service, campaign_id)
            origin, profile = configured_origin()
            remember_check(
                request,
                campaign_id,
                actor.identity,
                inputs,
                verified,
                origin,
                profile.value,
            )
        else:
            _retry_stop(request, service, campaign_id, request.POST.get("control", ""))
        return _checked(
            request, service, HttpResponseRedirect(reverse("admin:go_live"), status=303)
        )
    except ObjectDoesNotExist:
        return error_response(LookupError("Readiness configuration is unavailable."))
    except ERRORS as error:
        return error_response(error)


def _retry_stop(request, service, campaign_id, token):
    """Retry stopping go-live: retry the failed disposal ``token`` names, once.

    A forged control is refused (``BadSignature``, a 400). When the control
    names the failure Check shows now, the links page's own control is
    minted now and the disposal retried through its owner, which re-checks
    the Administrator, the request and the task version. Otherwise it was
    already retried (a resend after a lost answer) or has finished: nothing
    is done, and Check shows how it stands now.
    """
    actor = principal(request, service)
    signing.Signer(salt=go_live_check.RETRY_SALT).unsign_object(token)
    stopped = go_live_check.progress(request, service, campaign_id)
    if stopped.state != "stop_failed" or token != stopped.retry:
        return
    preparation, task = stopped.failed
    links_control(
        request,
        service,
        campaign_id,
        stopped.request.pk,
        token=sign_links_control(
            actor, stopped.request, "retry_cleanup", preparation=preparation, task=task
        ),
    )


def _check_context(request, service, campaign_id):
    """The Check step's template context, read now (``go_live_check``)."""
    stage = go_live_check.progress(request, service, campaign_id)
    context = {"stage": stage}
    if stage.state == "idle":
        caller = AdminCaller.from_request(request)
        preview = collect_inputs(caller, service, campaign_id)
        actor = principal(request, service, passive=True)
        verified = go_live_check.kept_check(
            request, campaign_id, actor.identity, preview
        )
        state = go_live_check.check_state(
            preview.problems, preview.target_state, verified
        )
        counts = preview.families.counts
        context |= {
            "preview": preview,
            "campaign": preview.campaign,
            "problems": _problems(preview),
            "counts": counts,
            "email_eligibility": Percentage(counts.email_eligible, counts.active),
            "no_email": Percentage(counts.no_eligible_email, counts.active),
            "inventory": sorted(preview.cleanup.inventory.counts.items()),
            "origin_verified": verified,
            "warnings": _warnings(),
        }
    else:
        state = stage.state
        context["campaign"] = stage.request.campaign
    context |= {
        "state": state,
        "wizard": go_live_check.bar(state, stage.in_progress_url),
        # Work that moves by itself is followed closely; otherwise the step
        # only re-reads now and then, for a fix made in another tab.
        "live_interval": None
        if state in {"started", "stopping"}
        else _IDLE_INTERVAL_MS,
        "live_limit": _WATCH_MS,
    }
    return context


def _warnings():
    """What Check warns about without blocking: the last backup and debug logging.

    ``backup_at`` is when the newest backup completed (None before any);
    ``debug`` whether this process logs debug detail, which System health
    can turn off.
    """
    from parishkit.stewardship.jobs.backup_models import BackupRun
    from parishkit.stewardship.observability import debug_logging_enabled

    return {
        "backup_at": BackupRun.objects.order_by("-completed_at", "-id")
        .values_list("completed_at", flat=True)
        .first(),
        "debug": debug_logging_enabled(),
    }


def _problems(preview):
    """Each readiness problem as ``{"text", "links"}``, in order.

    ``links`` are ``(url, label)`` pairs for the pages that fix it: none for
    a problem that only needs waiting, and for a missing test email one per
    selected Family email, named by its subject rather than a number
    (#1585), each opening its Preview and test email page.
    """
    lines = []
    for code in preview.problems:
        if code == "family_test_mail_required":
            links = [
                (
                    reverse("admin:campaign_mail", args=[template]),
                    _("Preview and send a test of “%(subject)s”")
                    % {"subject": subject},
                )
                for template, subject in _family_emails(preview)
            ]
        else:
            name = FIXES.get(code)
            links = (
                [(reverse(f"admin:{name}"), admin_navigation.PAGES[name].label)]
                if name
                else []
            )
        lines.append({"text": PROBLEMS[code], "links": links})
    return lines


def _family_emails(preview):
    """The selected Family emails as ``(record id, subject)``, by subject.

    The subject names the email the Administrator wrote, as Pages and
    emails lists it; a revision without one falls back to "Family email".
    """
    templates = preview.configuration.family_templates
    subjects = dict(
        ContentVersion.objects.filter(record_id__in=templates).values_list(
            "record_id", "subject"
        )
    )
    return sorted(
        (
            (template, subjects.get(UUID(str(template))) or _("Family email"))
            for template in templates
        ),
        key=lambda item: str(item[1]),
    )


def _start(request, service, campaign_id, action):
    """The Start go-live dialog's Review (``review``) or Apply (``confirm``).

    Review re-reads readiness and answers the dialog's review with a signed
    preview of these exact inputs, or a refusal; Apply re-reads readiness,
    runs the public web address check again and starts Testing cleanup from
    that preview, redirecting to the step in progress. The kept check
    (``go_live_check.kept_check``) only redraws the page on a reload and
    gates Review: a DNS record can change after it without changing the
    inputs digest, so Apply never trusts it, and a failure there is refused
    in the dialog with the check's reason. The preview's age never refuses
    Apply: the signature is checked and the binding signed again, so the
    dialog may stay open as long as nothing it shows changes.
    ``start_cleanup`` then repeats every check under the work lock.
    """
    try:
        actor = principal(request, service)
        if action == "confirm" and _sent_before(campaign_id, request):
            # The same Apply again (a resend after a lost answer): its key
            # makes it happen once, and start_cleanup returns that request.
            return _started(request, service, campaign_id, request.POST)
        stage = go_live_check.progress(request, service, campaign_id)
        if stage.state != "idle":
            return _in_progress(request, service, actor, stage)
        inputs = collect_inputs(AdminCaller.from_request(request), service, campaign_id)
        if inputs.target_state == "closed":
            return _start_answer(request, service, {"refused": "closed"})
        verified = go_live_check.kept_check(
            request, campaign_id, actor.identity, inputs
        )
        if inputs.problems or verified is not True:
            return _start_answer(request, service, {"refused": "changed"})
        origin, profile = configured_origin()
        if action == "review":
            token = signing.dumps(
                {
                    "actor": str(actor.identity),
                    "campaign": str(campaign_id),
                    "key": str(uuid4()),
                    "digest": inputs.digest,
                    "origin": origin,
                    "profile": profile.value,
                },
                salt=COMMAND_SALT,
            )
            return _start_answer(
                request, service, {"preview": inputs, "token": token, "stage": stage}
            )
        binding = _binding(request.POST)
        if binding.get("digest") != inputs.digest:
            return _start_answer(request, service, {"refused": "changed"})
        if not origin_check(origin, profile):
            # Kept as failed, so the reloaded step offers the check again.
            remember_check(
                request,
                campaign_id,
                actor.identity,
                inputs,
                False,
                origin,
                profile.value,
            )
            return _start_answer(request, service, {"refused": "origin_unverified"})
        return _started(request, service, campaign_id, request.POST)
    except StaleRecordError:
        # Lost to another Start, or the inputs moved under the work lock:
        # say which.
        stage = go_live_check.progress(request, service, campaign_id)
        if stage.state != "idle":
            return _in_progress(request, service, principal(request, service), stage)
        return _start_answer(request, service, {"refused": "changed"})


def _in_progress(request, service, actor, stage):
    """Answer a Start made while go-live is not idle, by what it is doing.

    Go-live this Administrator started (in another tab, say) is no refusal:
    the answer redirects to the step in progress, which the dialog opens
    (``data-review-redirects`` for Review, ``data-review-redraw="leave"``
    for Apply). Another Administrator's is the "started" refusal; a stop
    still running or failed is refused in that state's own words.
    """
    if stage.state == "started" and stage.request.initiated_by_id == actor.identity:
        return _checked(
            request, service, HttpResponseRedirect(stage.in_progress_url, status=303)
        )
    return _start_answer(request, service, {"refused": stage.state, "stage": stage})


def _binding(values):
    """The Start go-live preview Apply posted, signature checked, age not.

    A tampered or foreign preview is refused (``BadSignature``, a 400).
    """
    binding = signing.loads(values.get("preview_token", ""), salt=COMMAND_SALT)
    if type(binding) is not dict:
        raise ValueError("Invalid go-live preview.")
    return binding


def _sent_before(campaign_id, request):
    """Whether this exact Apply already started a go-live request."""
    try:
        key = UUID(str(_binding(request.POST).get("key")))
    except ValueError:
        return False
    return ProductionTransitionRequest.objects.filter(
        campaign_id=campaign_id, request_key=key
    ).exists()


def _started(request, service, campaign_id, values):
    """Start Testing cleanup from Apply's preview; redirect to the step in progress.

    The binding is signed again, so its five-minute lifetime never reaches
    the Administrator; the DNS and origin check it once bounded has just
    run again (``_start``), and ``start_cleanup`` still re-reads and
    compares every input it binds under the work lock.
    """
    status = start_cleanup(
        request,
        service,
        campaign_id,
        preview_token=signing.dumps(_binding(values), salt=COMMAND_SALT),
        acknowledge=True,
    )
    return _checked(
        request,
        service,
        HttpResponseRedirect(
            reverse("admin:go_live_cleanup", args=[status.request_id]), status=303
        ),
    )


def _start_answer(request, service, start):
    """Draw the Start go-live dialog's fragment; a refusal with its status."""
    status = _START_REFUSALS.get(start.get("refused"), 200)
    response = render(
        request,
        "stewardship/go-live-start-review.html",
        {"start": start},
        status=status,
    )
    if status != 200:
        # The answer carries its own explanation; keep it as rendered.
        response.stewardship_safe_error = True
        refused_step(response, start["refused"])
    return _checked(request, service, response)


@require_http_methods(["GET", "HEAD", "POST"])
def cleanup_status(request, campaign_id, request_id):
    """Passive progress and exact signed controls never expose direct deletion."""
    try:
        filters(request.GET, allowed=set())
        _closed(request, {"control"})
        service = runtime()
        if request.method == "POST":
            control(
                request,
                service,
                campaign_id,
                request_id,
                token=request.POST.get("control", ""),
            )
            return _checked(
                request,
                service,
                HttpResponseRedirect(
                    reverse("admin:go_live_cleanup", args=[request_id])
                ),
            )
        context = progress(request, service, campaign_id, request_id)
        status = context["status"]
        context["completion"] = Percentage(
            status.processed_count, status.inventory_total
        )
        # The page follows itself (live-status-v1.js) until cleanup settles.
        context["live_pending"] = status.state in {
            "cleanup_queued",
            "cleanup_running",
            "cleanup_retry_wait",
        } or (context["cancelling"] and status.state != "cancelled")
        # The second step of going live (#196).
        admin_navigation.place(request, flow="go_live", step="cleanup")
        response = render(request, "stewardship/go-live-cleanup.html", context)
        return _checked(request, service, response)
    except ObjectDoesNotExist:
        return error_response(LookupError("Cleanup request is unavailable."))
    except ERRORS as error:
        return error_response(error)


@require_http_methods(["GET", "HEAD"])
def testing_families(request, campaign_id):
    """Admin-only current Testing Family identities, with bounded server pagination."""
    try:
        parameters = filters(request.GET, allowed={"page", "size", "sort"})
        sort = TESTING_FAMILY_SORTING.parse(parameters)
        window = PageWindow(
            expected_version(parameters.get("page", "1")),
            expected_version(parameters.get("size", "50")),
        )
        service = runtime()
        # Kept on the work lock: passive admission and the cleanup inventory
        # run inside this block and refuse under a read-only snapshot.
        with work_transaction():
            principal(request, service, passive=True)
            configuration = editable_configuration(service)
            scope = _scope(campaign_id)
            if (
                scope.campaign is None
                or configuration.current_campaign_id != campaign_id
                or configuration.mode != "testing"
                or scope.campaign.state != "draft"
            ):
                # Not a permission problem: signing in again cannot help, so
                # refuse with a plain 410 notice rather than the generic 403
                # "sign in again" page (#867). Go live's Check step, the only page
                # that links here, itself renders only for the Testing draft.
                raise UserFacingGone(
                    _("Testing submissions are only available before go-live."),
                    fix=_(
                        "Going live removes the Testing submissions. Live "
                        "responses are under Review work and Reports and queries."
                    ),
                )
            source = source_readiness(scope)
            source_id = (
                source.current_id
                if source.reason in {"ready", "full_refresh_stale"}
                else None
            )
            window, rows, has_next, total = cleanup_families(
                campaign_id, source_id=source_id, window=window, sort=sort
            )
        # A sub-page of Go live's Check step: its trail leads back there,
        # and it draws no step indicator (one step bar per page, #1585).
        response = render(
            request,
            "stewardship/go-live-families.html",
            {
                "campaign": scope.campaign,
                "table": window_table(
                    window,
                    rows,
                    has_next,
                    total=total,
                    sorting=TESTING_FAMILY_SORTING,
                    sort=sort,
                ),
            },
        )
        return _checked(request, service, response)
    except ERRORS as error:
        return error_response(error)

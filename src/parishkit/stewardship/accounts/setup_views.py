"""The original-login setup workflow; step saves are temporary, never activation."""

from uuid import UUID

from django.db import DatabaseError
from django.http import HttpResponseRedirect
from django.shortcuts import render
from django.utils.translation import gettext_lazy as _
from django.views.decorators.debug import sensitive_post_parameters
from django.views.decorators.http import require_http_methods

from parishkit.config import ConfigError
from parishkit.stewardship.storage import StaleRecordError
from parishkit.stewardship.web.contracts import expected_version, filters
from parishkit.stewardship.web.refusals import annotate, stale_page, unexpected_fields

from .access_gate import status_page
from .admin_editing import error_response
from .authentication import runtime
from .limiting import LimiterUnavailable
from .setup_checks import start_check
from .setup_drafts import save_section, view_draft
from .setup_forms import STEP_FORMS, form_values, initial_values
from .setup_policy import SetupState
from .setup_staging import _admit, begin_setup, cancel_setup
from .setup_wizard import BY_KEY, PAGES, continue_after, wizard_for

ERRORS = (
    ConfigError,
    DatabaseError,
    LimiterUnavailable,
    PermissionError,
    ValueError,
    StaleRecordError,
    LookupError,
)


def _closed(request, fields, query=frozenset()):
    """Reject extra/repeated fields before any form can echo an unowned value.

    ``query`` names the few single-valued GET parameters a page accepts.
    """
    filters(request.GET, allowed=query)
    if (
        request.FILES
        or set(request.POST) - {*fields, "csrfmiddlewaretoken"}
        or any(len(values) != 1 for _, values in request.POST.lists())
    ):
        raise unexpected_fields(
            request.POST, request.FILES, allowed={*fields, "csrfmiddlewaretoken"}
        )


def _checked(request, service, response, draft=None):
    """Do not release rendered draft values after revocation or a concurrent edit."""
    _admit(request, service, activity=False)
    if draft is not None:
        current = view_draft(request, service, draft.status.attempt_id)
        if current.status != draft.status:
            raise StaleRecordError("Setup changed while rendering.")
    response["Cache-Control"] = "no-store"
    if response.status_code == 400:
        response.stewardship_safe_error = True
    return response


def _context(draft, current=None):
    """Render the stepper, the current attempt's safe deadlines and its state."""
    wizard = wizard_for(draft, current)
    return {
        "draft": draft,
        "wizard": wizard,
        "expired": draft is not None and draft.status.state == SetupState.EXPIRED,
    }


# Closed, static explanations for a wizard page that cannot open. They never
# echo exception text; the stepper names the step that fixes a prerequisite.
PAGE_ERRORS = (
    (
        PermissionError,
        _(
            "This setup page is not available to this sign-in. The setup attempt "
            "may have ended, or it belongs to a different sign-in."
        ),
    ),
    (
        StaleRecordError,
        _("Setup changed while this page was loading. Reload the page to continue."),
    ),
    # ConfigError is a ValueError: match it (a missing prerequisite) first.
    ((LookupError, ConfigError), _("Complete the earlier setup steps first.")),
    (
        ValueError,
        _(
            "This page address is not valid, or this step does not apply to the "
            "settings you saved."
        ),
    ),
)


def page_error(request, error, current=None, *, fallback=error_response):
    """Explain a wizard page that cannot open instead of returning a JSON body.

    Only a browser GET/HEAD of an HTML wizard page is converted; posts and the
    JSON status endpoints keep the closed JSON error. The status code is always
    the one the JSON response would have used, so clients and tests see the
    same outcome. The stepper is shown only when this sign-in still owns a
    live draft, and the page is released only after re-admitting the sign-in.
    """
    response = fallback(error)
    if request.method not in {"GET", "HEAD"}:
        return response
    message = next(
        (text for kinds, text in PAGE_ERRORS if isinstance(error, kinds)),
        _("Setup is temporarily unavailable. Try again in a moment."),
    )
    # A user-facing refusal (web.refusals) explains itself more precisely.
    refusal = getattr(error, "refusal", None)
    if refusal is not None:
        message = refusal.message
    wizard = None
    try:
        service = runtime()
        wizard = wizard_for(view_draft(request, service), current)
        if wizard is not None:
            # The stepper reflects this sign-in's draft: re-admit before release.
            _admit(request, service, activity=False)
    except ERRORS:
        wizard = None
    page = render(
        request,
        "stewardship/setup-unavailable.html",
        {
            "message": message,
            "refusal": refusal,
            "wizard": wizard,
            "blocked": wizard.step(current) if wizard else None,
        },
        status=response.status_code,
    )
    page["Cache-Control"] = "no-store"
    page.stewardship_safe_error = True
    # The refusal log's reason and class, which the JSON answer carried
    # (#1444).
    return annotate(page, error)


@require_http_methods(["GET", "HEAD", "POST"])
def setup(request):
    """GET is passive; only an explicit CSRF-protected start creates an attempt."""
    try:
        service = runtime()
        if request.method == "POST":
            action = request.POST.get("action")
            fields = {"action"} if action == "start" else {"action", "attempt"}
            if action == "check":
                fields.add("version")
            _closed(request, fields)
            if action == "start":
                attempt = begin_setup(request, service)
                destination = (
                    PAGES[0].url
                    if attempt.state == SetupState.COLLECTING
                    else "/admin/setup"
                )
            elif action == "cancel":
                cancel_setup(request, service, UUID(request.POST.get("attempt", "")))
                destination = "/admin/setup"
            elif action == "check":
                # Check parish data, or Check again (#2201): the page itself
                # shows the check and re-reads it in place until it answers.
                start_check(
                    request,
                    service,
                    UUID(request.POST.get("attempt", "")),
                    expected_version=expected_version(request.POST.get("version")),
                )
                from django.urls import reverse

                destination = reverse("admin:setup_source") + "#setup-source-panel"
            else:
                raise ValueError("Unknown setup action.")
            return _checked(request, service, HttpResponseRedirect(destination))
        _closed(request, set())
        draft = view_draft(request, service)
        if draft is not None and draft.status.state == SetupState.FROZEN:
            # A confirmed setup finishes in the background; its owner sees
            # the "Finishing setup" progress page instead of the wizard.
            return _checked(
                request, service, HttpResponseRedirect("/admin/setup/finishing")
            )
        response = render(request, "stewardship/setup.html", _context(draft))
        return _checked(request, service, response, draft)
    except ConfigError:
        # An absent marker on a non-bootstrap deployment is not permission to
        # restart initial setup. Keep the established fail-closed recovery page.
        return status_page(request, kind="setup", admin=True)
    except ERRORS as error:
        return page_error(request, error)


@sensitive_post_parameters("candidate")
@require_http_methods(["GET", "HEAD", "POST"])
def setup_step(request, step):
    """Save one public step without activating policy, provider settings or YAML.

    The Slack step also takes the Slack bot token, which it stages rather
    than saves (``setup_slack_step``, #1578); its page form adds that field.
    The optional Off-site backups step (#2200) names the saved delegated
    mailbox user in its folder help and is closed where the local
    environment does not reach Google Drive (``_backup_options``).
    """
    try:
        if step not in STEP_FORMS:
            raise LookupError("Unknown setup step.")
        service = runtime()
        form_type = STEP_FORMS[step]
        options = {}
        if step == "slack":
            from . import setup_slack_step

            form_type = setup_slack_step.SetupSlackStepForm
        _closed(request, {*form_type.base_fields, "version"})
        draft = view_draft(request, service)
        if draft is None or draft.status.state != SetupState.COLLECTING:
            return _checked(request, service, HttpResponseRedirect("/admin/setup"))
        if step == "slack":
            options["saved"] = setup_slack_step.saved_channel(draft)
        elif step == "backup":
            options = _backup_options(draft)
        form = form_type(
            request.POST if request.method == "POST" else None,
            initial=initial_values(step, draft.sections.get(step, {})),
            **options,
        )
        if step == "parish" and draft.source_task_id is not None:
            form.fields["timezone"].disabled = True
            form.fields["timezone"].help_text = (
                "The source load fixes this setup's timezone. "
                "Cancel and start a new setup to change it."
            )
        if request.method == "POST":
            version = expected_version(request.POST.get("version"))
            if version != draft.status.version:
                raise stale_page()
            if form.is_valid() and step == "slack":
                if setup_slack_step.save(request, service, draft, form, version):
                    return _checked(
                        request, service, continue_after(request, service, step)
                    )
            elif form.is_valid():
                save_section(
                    request,
                    service,
                    draft.status.attempt_id,
                    step=step,
                    values=form_values(form),
                    expected_version=version,
                )
                return _checked(
                    request, service, continue_after(request, service, step)
                )
            status = 400
        else:
            status = 200
        context = _context(draft, step)
        response = render(
            request,
            "stewardship/setup-step.html",
            context
            # A wizard heading uses its stepper label (one name per page).
            | {"form": form, "step": step, "step_label": BY_KEY[step].label}
            | _slack_context(draft, context["wizard"], options)
            | (_backup_context(form, options) if step == "backup" else {}),
            status=status,
        )
        return _checked(request, service, response, draft)
    except ERRORS as error:
        return page_error(request, error, step)


def _backup_options(draft):
    """The Off-site backups step form's options (#2200).

    ``mailbox`` is the delegated mailbox user saved on Outgoing email
    settings, which the folder must let add files, and ``drive_closed``
    whether this is the local environment without its Google Drive terms,
    where the step cannot turn copies on (as Test access cannot there).
    """
    from .integration_views import _drive_in_local

    return {
        "mailbox": draft.sections.get("mail", {}).get("delegated_email"),
        "drive_closed": _drive_in_local(),
    }


def _backup_context(form, options):
    """What the Off-site backups step's page draws besides its form (#2200).

    ``backup_on`` is whether the box is ticked as drawn (the posted value on
    a refused save), which picks the primary's label before the page's
    script runs; ``backup_mailbox`` the saved delegated mailbox user the
    Google steps name; ``backup_missing`` the complete gate's hint.
    """
    from .setup_forms import BACKUP_MISSING

    return {
        "backup_on": bool(form["enabled"].value()),
        "backup_mailbox": options["mailbox"],
        "backup_missing": BACKUP_MISSING,
    }


def _slack_context(draft, wizard, options):
    """What the Slack step's page says about a saved token, if anything.

    ``slack_saved`` is whether a token is staged, ``slack_saved_channel``
    the channel it is sealed with (the page's note follows the channel field
    against it, as the token's requirement does), and
    ``slack_on`` whether Slack is saved on, which offers Turn off Slack
    notifications (#1499). Other steps get nothing.
    """
    if "saved" not in options:
        return {}
    return {
        "slack_saved": options["saved"] is not None,
        "slack_saved_channel": options["saved"],
        "slack_on": bool(draft.sections.get("slack", {}).get("enabled")),
    }


@require_http_methods(["GET", "HEAD"])
def setup_source(request):
    """Check parish data (#2201): explain and offer the quick check.

    The check itself is a POST to the setup overview (action ``check``),
    which answers with this page again; while the check waits, the page
    re-reads its panel in place until the installer answers.
    """
    try:
        _closed(request, set())
        service = runtime()
        draft = view_draft(request, service)
        if draft is None or draft.status.state != SetupState.COLLECTING:
            return _checked(request, service, HttpResponseRedirect("/admin/setup"))
        context = _context(draft, "source")
        context["source_step"] = context["wizard"].step("source")
        context |= source_check_context(draft.status.attempt_id)
        response = render(request, "stewardship/setup-source.html", context)
        return _checked(request, service, response, draft)
    except ERRORS as error:
        return page_error(request, error, "source")


# What the Check parish data page says for a failed check, by its stable
# error code (#2201, admin-portal spec "Checking the parish data during
# setup"). ``{organization}`` is the chosen organization as the
# organization name rule writes it.
CHECK_ERRORS = {
    "parishsoft_key_refused": _(
        "ParishSoft did not accept the saved key. Paste the key again, or a "
        "different one, at ParishSoft connection."
    ),
    "parishsoft_organization_not_reached": _(
        "The saved key no longer reaches %(organization)s. Choose another "
        "organization or enter a different key at ParishSoft connection."
    ),
    "parishsoft_no_families": _(
        "ParishSoft has no Families for %(organization)s. Check at ParishSoft "
        "connection that it is your parish's organization."
    ),
    "parishsoft_check_unreadable": _(
        "ParishSoft's answer could not be read. Try again in a few minutes; if "
        "it keeps happening, tell your IT contact."
    ),
    "parishsoft_unavailable": _(
        "ParishSoft could not be reached. Try again in a few minutes."
    ),
}
# The failures a different key or organization fixes, which link to
# ParishSoft connection.
CHECK_FIXED_AT_CONNECTION = frozenset(
    {
        "parishsoft_key_refused",
        "parishsoft_organization_not_reached",
        "parishsoft_no_families",
    }
)


def source_check_context(attempt_id):
    """What the Check parish data page shows of the attempt's newest check.

    Reads the attempt's newest check (``setup_checks.current_check``) and
    names the staged key's chosen organization, by the name the check read
    or else the lookup's, as the organization name rule writes it; the page
    context itself is ``check_page``'s.
    """
    from parishkit.stewardship.campaigns.work_locks import work_transaction
    from parishkit.stewardship.source.organization_names import organization_label

    from .setup_checks import _staged_key, current_check
    from .setup_lookups import chosen_name
    from .setup_models import SetupAttempt

    with work_transaction():
        attempt = SetupAttempt.objects.get(pk=attempt_id)
        check = current_check(attempt)
        credential = _staged_key(attempt)
        organization = (credential.settings if credential else {}).get(
            "organization_id"
        )
        name = None
        if organization is not None:
            name = (
                check.organization_name
                if check is not None and check.current and check.organization_name
                else chosen_name(attempt_id, organization)
            )
    return check_page(
        check, organization_label(organization, name) if organization else None
    )


# The states Check parish data draws, each in the one status slot.
CHECK_STATES = ("initial", "waiting", "ready", "failed", "stale")


def check_page(check, label):
    """The Check parish data page's context for ``check`` (or None).

    ``check_shown`` is the state drawn: "initial" before any check,
    "waiting" while it waits, "ready" for a current ready check, "failed"
    for a current failed one, and "stale" for a check of an earlier
    connection. The page draws every other state as an invisible copy in
    the same slot, so ``check_error_reserve`` is the longest failure
    message, for that copy. A failed check adds its message and whether
    ParishSoft connection is the fix.
    """
    if check is None:
        shown = "initial"
    elif check.waiting:
        shown = "waiting"
    elif not check.current:
        shown = "stale"
    else:
        shown = "ready" if check.state == "ready" else "failed"
    messages = [
        str(message % {"organization": label or ""})
        for message in CHECK_ERRORS.values()
    ]
    context = {
        "check": check,
        "organization_label": label,
        "check_shown": shown,
        "check_states": CHECK_STATES,
        "check_error_reserve": max(messages, key=len),
    }
    if shown == "failed":
        context["check_error"] = CHECK_ERRORS[check.error_code] % {
            "organization": label or ""
        }
        context["check_fix"] = check.error_code in CHECK_FIXED_AT_CONNECTION
    return context

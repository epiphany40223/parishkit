"""The original-login setup workflow; step saves are temporary, never activation."""

from uuid import UUID

from django.db import DatabaseError
from django.http import HttpResponseRedirect
from django.shortcuts import render
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_http_methods

from parishkit.config import ConfigError
from parishkit.stewardship.storage import StaleRecordError
from parishkit.stewardship.web.contracts import expected_version, filters

from .access_gate import status_page
from .admin_editing import error_response
from .authentication import runtime
from .limiting import LimiterUnavailable
from .setup_drafts import save_section, view_draft
from .setup_forms import FORMS, STEPS, form_values, initial_values
from .setup_policy import SetupState
from .setup_source import start_source_load
from .setup_staging import _admit, begin_setup, cancel_setup
from .setup_wizard import PAGES, continue_after, wizard_for

ERRORS = (
    ConfigError,
    DatabaseError,
    LimiterUnavailable,
    PermissionError,
    ValueError,
    StaleRecordError,
    LookupError,
)


def _closed(request, fields):
    """Reject extra/repeated fields before any form can echo an unowned value."""
    filters(request.GET, allowed=set())
    if (
        request.FILES
        or set(request.POST) - {*fields, "csrfmiddlewaretoken"}
        or any(len(values) != 1 for _, values in request.POST.lists())
    ):
        raise ValueError("Invalid setup fields.")


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
    return {
        "draft": draft,
        "wizard": wizard_for(draft, current),
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


# Credential pages require a Google sign-in less than five minutes old, and a
# new sign-in ends this attempt; say so plainly rather than a generic denial.
CREDENTIAL_PAGES = frozenset({"parishsoft", "google_workspace", "slack_credential"})
FRESH_SIGN_IN = _(
    "Credentials can be entered only within five minutes of signing in with "
    "Google, and only by the sign-in that started this setup. Signing in again "
    "ends this setup attempt and clears its temporary settings, so a new "
    "attempt starts from the first step. Have every credential ready before "
    "you sign in."
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
    if isinstance(error, PermissionError) and current in CREDENTIAL_PAGES:
        message = FRESH_SIGN_IN
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
            "wizard": wizard,
            "blocked": wizard.step(current) if wizard else None,
        },
        status=response.status_code,
    )
    page["Cache-Control"] = "no-store"
    page.stewardship_safe_error = True
    return page


@require_http_methods(["GET", "HEAD", "POST"])
def setup(request):
    """GET is passive; only an explicit CSRF-protected start creates an attempt."""
    try:
        service = runtime()
        if request.method == "POST":
            action = request.POST.get("action")
            fields = {"action"} if action == "start" else {"action", "attempt"}
            if action == "load":
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
            elif action == "load":
                task = start_source_load(
                    request,
                    service,
                    UUID(request.POST.get("attempt", "")),
                    expected_version=expected_version(request.POST.get("version")),
                )
                from django.urls import reverse

                destination = reverse("admin:setup_source_progress", args=[task.run_id])
            else:
                raise ValueError("Unknown setup action.")
            return _checked(request, service, HttpResponseRedirect(destination))
        _closed(request, set())
        draft = view_draft(request, service)
        response = render(request, "stewardship/setup.html", _context(draft))
        return _checked(request, service, response, draft)
    except ConfigError:
        # An absent marker on a non-bootstrap deployment is not permission to
        # restart initial setup. Keep the established fail-closed recovery page.
        return status_page(request, kind="setup", admin=True)
    except ERRORS as error:
        return page_error(request, error)


@require_http_methods(["GET", "HEAD", "POST"])
def setup_step(request, step):
    """Save one public step without activating policy, provider settings or YAML."""
    try:
        if step not in FORMS:
            raise LookupError("Unknown setup step.")
        service = runtime()
        form_type = FORMS[step]
        _closed(request, {*form_type.base_fields, "version"})
        draft = view_draft(request, service)
        if draft is None or draft.status.state != SetupState.COLLECTING:
            return _checked(request, service, HttpResponseRedirect("/admin/setup"))
        form = form_type(
            request.POST if request.method == "POST" else None,
            initial=initial_values(step, draft.sections.get(step, {})),
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
                raise StaleRecordError("Reload this setup form.")
            if form.is_valid():
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
        response = render(
            request,
            "stewardship/setup-step.html",
            _context(draft, step)
            | {"form": form, "step": step, "step_label": STEPS[step]},
            status=status,
        )
        return _checked(request, service, response, draft)
    except ERRORS as error:
        return page_error(request, error, step)


@require_http_methods(["GET", "HEAD"])
def setup_source(request):
    """Explain and offer the one source load; the load itself is a hub POST."""
    try:
        _closed(request, set())
        service = runtime()
        draft = view_draft(request, service)
        if draft is None or draft.status.state not in {
            SetupState.COLLECTING,
            SetupState.LOADING,
        }:
            return _checked(request, service, HttpResponseRedirect("/admin/setup"))
        context = _context(draft, "source")
        context["source_step"] = context["wizard"].step("source")
        response = render(request, "stewardship/setup-source.html", context)
        return _checked(request, service, response, draft)
    except ERRORS as error:
        return page_error(request, error, "source")

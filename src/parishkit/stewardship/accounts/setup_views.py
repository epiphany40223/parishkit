"""The original-login setup workflow; step saves are temporary, never activation."""

from uuid import UUID

from django.db import DatabaseError
from django.http import HttpResponseRedirect
from django.shortcuts import render
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


def _context(draft):
    """Render only declared step labels and the current attempt's safe deadlines."""
    return {
        "draft": draft,
        "steps": [
            {"key": key, "label": label, "saved": key in draft.sections}
            for key, label in STEPS.items()
        ]
        if draft
        else [],
        "expired": draft is not None and draft.status.state == SetupState.EXPIRED,
    }


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
                    "/admin/setup/parish"
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
        return error_response(error)


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
                return _checked(request, service, HttpResponseRedirect("/admin/setup"))
            status = 400
        else:
            status = 200
        response = render(
            request,
            "stewardship/setup-step.html",
            _context(draft) | {"form": form, "step": step, "step_label": STEPS[step]},
            status=status,
        )
        return _checked(request, service, response, draft)
    except ERRORS as error:
        return error_response(error)

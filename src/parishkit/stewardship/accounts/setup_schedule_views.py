"""Original-login mail schedules and atomic temporary campaign-date correction."""

from django.http import HttpResponseRedirect
from django.shortcuts import render
from django.views.decorators.http import require_http_methods

from parishkit.config import ConfigError
from parishkit.stewardship.storage import StaleRecordError
from parishkit.stewardship.web.contracts import expected_version

from .authentication import runtime
from .schedule_forms import ScheduleForm, Schedules, ScheduleWindow
from .setup_content_values import CONTENT_STEPS
from .setup_content_views import _draft
from .setup_drafts import save_sections
from .setup_formsets import closed_formset
from .setup_views import ERRORS, _checked, _context, error_response


class SetupScheduleWindow(ScheduleWindow):
    """Dates may change with their mail; the first campaign keeps the parish zone."""

    def __init__(self, *args, **kwargs):
        """Only the campaign's already-admitted original timezone is displayed."""
        super().__init__(*args, editable=True, **kwargs)
        self.fields["timezone"].disabled = True


def revised_schedules(previous, formset):
    """Apply explicit formset operations without writing or inventing omissions."""
    records = {row["id"]: row for row in previous}
    for operation in formset.patch():
        identifier = operation["id"]
        if operation["operation"] == "remove":
            records.pop(identifier)
        else:
            records[identifier] = {"id": identifier, "values": operation["values"]}
    return list(records.values())


@require_http_methods(["GET", "HEAD", "POST"])
def setup_schedules(request):
    """Validate the entire schedule collection before one atomic temporary save."""
    try:
        closed_formset(
            request,
            prefix="schedules",
            fields=(*ScheduleForm.base_fields, "DELETE"),
            extra_fields=(f"window-{name}" for name in ScheduleWindow.base_fields),
        )
        service = runtime()
        draft, campaign = _draft(request, service)
        data = request.POST if request.method == "POST" else None
        previous = draft.sections.get("schedules", {"records": []})["records"]
        window = SetupScheduleWindow(data, prefix="window", previous=campaign)
        selected = (
            window.values() if data is not None and window.is_valid() else campaign
        )
        schedules = Schedules(
            data,
            prefix="schedules",
            previous=previous,
            templates=[
                draft.sections[step]
                for step in CONTENT_STEPS
                if draft.sections.get(step, {}).get("values")
            ],
            campaign_id=draft.status.attempt_id,
            campaign=selected,
        )
        status, collection_error = 200, False
        if request.method == "POST":
            version = expected_version(request.POST.get("version"))
            if version != draft.status.version:
                raise StaleRecordError("Reload the first-campaign schedules.")
            if window.is_valid() and schedules.is_valid():
                try:
                    save_sections(
                        request,
                        service,
                        draft.status.attempt_id,
                        updates={
                            "campaign": draft.sections["campaign"]
                            | {"campaign": selected},
                            "schedules": {
                                "records": revised_schedules(previous, schedules)
                            },
                        },
                        expected_version=version,
                    )
                except (ConfigError, ValueError):
                    collection_error = True
                else:
                    return _checked(
                        request, service, HttpResponseRedirect("/admin/setup/schedules")
                    )
            status = 400
        response = render(
            request,
            "stewardship/setup-schedules.html",
            _context(draft)
            | {
                "window": window,
                "schedules": schedules,
                "collection_error": collection_error,
                "campaign_name": campaign["name"],
            },
            status=status,
        )
        return _checked(request, service, response, draft)
    except ERRORS as error:
        return error_response(error)

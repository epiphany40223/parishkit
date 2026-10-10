"""The ParishSoft refresh schedule editor (#632) with sample stored schedules.

Each page is the real settings template with the real editor. The editor's
live check is answered by the real server code too: a test routes the
check's address (``CHECK``) to ``check_answer``, which reads the posted rows
with the same form the save uses and renders the same answer template, so
the browser exercises exactly the server's rules without a database.
"""

from datetime import timedelta
from uuid import uuid4

from django.http import QueryDict
from django.template.loader import render_to_string

from parishkit.stewardship.accounts.integration_forms import (
    InlineCredentialForm,
    IntegrationForm,
)
from parishkit.stewardship.accounts.refresh_schedule_forms import (
    PRESETS,
    ScheduleEditor,
    editor_view,
    presets_payload,
)
from parishkit.stewardship.source.schedule_preview import durations, summarize

ZONE = "America/New_York"
CHECK = "/admin/system/integrations/parishsoft/schedule-check/"
# Production's schedule before the editor: listed full times and quarter-hour
# quick updates.
PRODUCTION = {
    "organization_id": "12345",
    "full_refresh": "daily",
    "nightly_time": "00:00",
    "full_refresh_times": ["00:00", "08:00", "12:00", "16:00", "20:00"],
    "delta_refresh": "quarter_hour",
}
# A kept full time at 23:50 runs too close to its neighbours: its problems
# are shown but Save stays available until the schedule itself changes.
CLOSE = {
    "organization_id": "12345",
    "nightly_time": "00:00",
    "full_refresh_times": ["00:00", "23:50"],
    "delta_refresh": "hourly",
}
STORED = {"/refresh-schedule": PRODUCTION, "/refresh-schedule-close": CLOSE}


def _reader(now):
    """The preview without a database: typical durations, no email windows."""

    def read(document, *, now, timezone):
        return summarize(
            document,
            timezone=timezone,
            today=now.date(),
            windows=(),
            measured=durations({}),
            margin=timedelta(minutes=30),
        )

    return read


def _schedule(editor, now):
    """The editor's template context, as the view builds it."""
    return {
        "schedule": editor_view(
            editor, timezone=ZONE, now=now, preview_reader=_reader(now)
        ),
        "presets": PRESETS,
        "presets_payload": presets_payload(),
    }


def check_answer(stored, body, now):
    """The live check's answer to a posted ``body`` (URL-encoded rows)."""
    editor = ScheduleEditor(QueryDict(body or ""), stored=stored)
    return render_to_string(
        "stewardship/refresh-schedule-check.html", _schedule(editor, now)
    )


def components(context, admin, now):
    """The settings page for each sample stored schedule."""
    responses = {}
    for path, stored in STORED.items():
        form = IntegrationForm(
            "parishsoft",
            initial=stored | {"base_digest": "a" * 64},
            stored=stored,
        )
        responses[path] = (
            "text/html",
            render_to_string(
                "stewardship/integration-settings.html",
                context
                | {
                    "admin_chrome": admin,
                    "target": "parishsoft",
                    "label": "ParishSoft",
                    "summary": None,
                    "configured": True,
                    "credential": InlineCredentialForm(
                        "parishsoft", initial={"intent": "synthetic-intent"}
                    ),
                    "refresh_key": uuid4(),
                    "form": form,
                }
                | _schedule(form.schedule, now),
            ),
        )
    return responses

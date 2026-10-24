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
    ScheduleEditor,
    basic_context,
    editor_view,
)
from parishkit.stewardship.source.refresh_rules import stored_settings
from parishkit.stewardship.source.schedule_preview import durations, summarize

ZONE = "America/New_York"
CHECK = "/admin/system/integrations/parishsoft/schedule-check/"
# Production's schedule before the editor: listed full times and quarter-hour
# quick updates, which no longer run and are not shown (#1147). It opens in
# Advanced: no Basic choice gives its full times.
PRODUCTION = {
    "organization_id": "12345",
    "full_refresh": "daily",
    "nightly_time": "00:00",
    "full_refresh_times": ["00:00", "08:00", "12:00", "16:00", "20:00"],
    "delta_refresh": "quarter_hour",
}
# A kept full time at 23:50 (rule 4) runs too close to 00:00 (rule 1): its
# problems are shown but Save stays available until the schedule itself
# changes. Rules 2 and 3 sit between them, so the pointer tests have rows
# below rule 1's long message.
CLOSE = {
    "organization_id": "12345",
    "nightly_time": "00:00",
    "full_refresh_times": ["00:00", "06:00", "12:00", "23:50"],
    "delta_refresh": "hourly",
}
# A schedule saved from Basic (#1122): "Nightly only", so it opens in Basic.
BASIC = (
    {"organization_id": "12345"}
    | stored_settings(
        {
            "rules": [{"kind": "full", "at": "02:00"}],
            "skips": [],
            "skip_around_family_emails": True,
        }
    )
    | {"refresh_schedule_mode": "basic"}
)
STORED = {
    "/refresh-schedule": PRODUCTION,
    "/refresh-schedule-close": CLOSE,
    "/refresh-schedule-basic": BASIC,
}


def _reader(now):
    """The preview without a database: typical durations, no email windows."""

    def read(document, *, now, timezone):
        return summarize(
            document,
            timezone=timezone,
            today=now.date(),
            windows=(),
            measured=durations([]),
            margin=timedelta(minutes=30),
        )

    return read


def _schedule(editor, now):
    """The editor's template context, as the view builds it."""
    return {
        "schedule": editor_view(
            editor, timezone=ZONE, now=now, preview_reader=_reader(now)
        ),
    } | basic_context()


def check_answer(stored, body, now):
    """The live check's answer to a posted ``body`` (URL-encoded rows)."""
    editor = ScheduleEditor(QueryDict(body or ""), stored=stored)
    return render_to_string(
        "stewardship/refresh-schedule-check.html", _schedule(editor, now)
    )


# The organization the sample pages name (#1554).
ORGANIZATION = "St. Example Parish (12345)"


def components(context, admin, now):
    """The settings page for each sample stored schedule.

    ``/refresh-schedule-gated`` is Production's schedule with Save waiting
    for a change too, as the view draws it (#1330). Every page names the
    organization as text, which has no field (#1554).
    """
    responses = {}
    extra = dict.fromkeys(("/refresh-schedule-gated",))
    for path, stored in (STORED | extra).items():
        stored = stored or PRODUCTION
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
                    "organization": ORGANIZATION,
                    "credential": InlineCredentialForm(
                        "parishsoft",
                        initial={"intent": "synthetic-intent"},
                        organization=ORGANIZATION,
                    ),
                    "refresh_key": uuid4(),
                    "form": form,
                    "change_gate": path.endswith("-gated"),
                }
                | _schedule(form.schedule, now),
            ),
        )
    return responses

"""New and Edit report email (#1786, delivery plan item 6), drawn offline.

New, blank, and Edit of a converted Weekly report (with a past send), from
the real editor form and template with synthetic users; the Next sends
table is drawn from the report's values by the real table builder. The
live table's endpoint is not served here (a test routes it), so the table
keeps what the page drew. Edit is also served as the calendar opens it for
one send, with its second send skipped, and New as a refused Review draws
it, for a one-time send whose time has passed.
"""

import re
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID

from django.template.loader import render_to_string

from parishkit.stewardship.reports import report_email_editor as editor
from parishkit.stewardship.reports.report_blocks import REGISTRY
from parishkit.stewardship.reports.report_blocks.registry import AFTER_BUTTON

NEW = "/report-email-new"
EDIT = "/report-email-edit"
EDIT_SKIPPED = "/report-email-edit-skipped"
NEW_REFUSED = "/report-email-new-refused"
# A refused Review of New: a one-time send whose time has passed, so the
# refusal concerns the date and the time together.
PASSED = {
    "action": "preview",
    "name": "Staff requests",
    "subject": "",
    "sends": "once",
    "once_date": "2054-10-19",
    "time": "07:00",
    "editor_mode": "basic",
    "as_of": "send_time",
    "recipient": ["group:administrators"],
    "block": ["campaign_totals"],
    "html": "",
    "text": "",
    "zone": "America/New_York",
}
NOW = datetime(2054, 10, 20, 12, 0, tzinfo=UTC)
CAMPAIGN = {
    "timezone": "America/New_York",
    "start_date": "2054-10-01",
    "end_date": "2054-10-31",
}
REPORT = UUID(int=301)
USERS = [
    {
        "id": str(UUID(int=11)),
        "name": "Pat Doe",
        "email": "pat@example.org",
        "role": "staff",
    },
    {
        "id": str(UUID(int=12)),
        "name": "Chris Admin",
        "email": "chris@example.org",
        "role": "administrator",
    },
]
WEEKLY = {
    "campaign_id": str(UUID(int=5)),
    "kind": "report",
    "name": "Weekly report",
    "subject": "",
    "intro_html": "",
    "intro_text": "",
    "recipients": [{"group": "administrators"}],
    "schedule": {
        "type": "repeat",
        "frequency": "weekly",
        "weekdays": [0],
        "from": "campaign_start",
        "until": "first_send_after_campaign_end",
        "time": "08:00:00",
    },
    "as_of": "send_time",
    "blocks": [
        {"block": "additional_information", "version": 1, "options": {}},
        {"block": "response_funnel", "version": 1, "options": {}},
    ],
    "content_required": True,
    "skip_dates": [],
    "paused_ranges": [],
    "slot_basis": "send_date",
}


def _rows(saved):
    """The Blocks rows and the blocks not in the report."""
    keys = [entry["block"] for entry in (saved or {}).get("blocks", [])]
    rows = [
        {
            "key": key,
            "label": REGISTRY[key].label,
            "help": REGISTRY[key].help,
            "last": REGISTRY[key].placement == AFTER_BUTTON,
        }
        for key in keys
    ]
    available = [
        {
            "key": key,
            "label": block.label,
            "help": block.help,
            "last": block.placement == AFTER_BUTTON,
        }
        for key, block in REGISTRY.items()
        if key not in keys
    ]
    return rows, available


def _page(context, admin, saved, data=None):
    """The editor for ``saved`` (None: New), refused for ``data`` if given."""
    from django.http import QueryDict

    from parishkit.stewardship.reports.report_email_editor_views import (
        _date_time_error,
    )

    posted = None
    if data is not None:
        posted = QueryDict(mutable=True)
        for name, value in data.items():
            posted.setlist(name, value if isinstance(value, list) else [value])
    form = editor.EditorForm(
        posted,
        campaign=CAMPAIGN,
        campaign_id=UUID(int=5),
        saved=saved,
        others=[],
        users=USERS,
        now=NOW,
    )
    if posted is None:
        form.initial = editor.initial(saved, CAMPAIGN, NOW)
    else:
        assert not form.is_valid()
    choices = editor.recipient_choices(USERS)
    rows, available = _rows(saved)
    chips = [
        {
            "value": editor.entry_value(entry),
            "label": editor.chip_label(editor.entry_value(entry), choices),
        }
        for entry in (saved or {}).get("recipients", [])
    ]
    values = saved or editor.blank_values(CAMPAIGN, UUID(int=5))
    # The view's own choice of what Next sends draws: nothing on New's
    # blank form (#1972).
    drawn = editor.drawn_values(form, saved, CAMPAIGN, UUID(int=5))
    moment = editor.moment(saved, CAMPAIGN, NOW) if saved else None
    return render_to_string(
        "stewardship/report-email-entry.html",
        context
        | {
            "admin_chrome": admin,
            "campaign": SimpleNamespace(
                active_configuration=SimpleNamespace(name="2054 Stewardship")
            ),
            "values": CAMPAIGN,
            "new": saved is None,
            "report_id": REPORT if saved else None,
            "saved": saved,
            "form": form,
            "choices": choices,
            "leader_addresses": ["lee@example.org"],
            "chips": chips,
            "blocks": rows,
            "available_blocks": available,
            "base_digest": "0" * 64,
            "campaign_zone": CAMPAIGN["timezone"],
            "post_url": "/admin/mail/report-emails/new/",
            "list_url": "/admin/mail/report-emails/",
            "cancel_url": "/admin/mail/report-emails/",
            "preview_url": "/admin/mail/report-emails/schedule-preview/",
            "drawn_at": moment.isoformat() if moment else None,
            # Edit's Next sends table takes boxes for Skip and Send selected
            # sends; no send has Production work here.
            "sends_table": drawn
            and editor.sends_table(
                drawn,
                CAMPAIGN,
                NOW,
                bulk=saved is not None,
                worked=(lambda key: False) if saved else None,
            ),
            "as_of_labels": editor.AS_OF_LABELS,
            "visual": values["intro_html"],
            "date_time_error": _date_time_error(form),
            "sent": False,
            "busy": False,
            "editor_mode": form["editor_mode"].value() or "basic",
            "sends": [],
        },
    )


def review_answer():
    """Review's answer for a new report that sends after the campaign ends.

    The real review template, from the real description of the Weekly
    report, with the after-campaign question and its tick.
    """
    choices = editor.recipient_choices(USERS)
    return render_to_string(
        "stewardship/report-email-review.html",
        {
            "kind": "write",
            "change": {"dropped_skips": []},
            "before": None,
            "after": editor.describe(WEEKLY, CAMPAIGN, NOW, choices),
            "preview": "synthetic-preview",
            "after_campaign": datetime(2054, 11, 2, 12, 0, tzinfo=UTC),
            "end_date": CAMPAIGN["end_date"],
        },
    )


def slots(saved=WEEKLY):
    """The slot keys of the report's next sends, in order."""
    table = editor.sends_table(saved, CAMPAIGN, NOW)
    return [row["slot"] for row in table["rows"]]


def named_send(page):
    """The slot key of Edit's second Next sends box, as the calendar names it."""
    return re.findall(r'name="send" value="([^"]+)"', page)[1]


def skipped():
    """A daily report (five sends ahead) with its second send skipped."""
    daily = WEEKLY | {
        "name": "Daily report",
        "schedule": {"type": "repeat", **editor.PRESETS["daily"], "time": "07:00:00"},
        "as_of": "previous_day_end",
    }
    return daily | {"skip_dates": [slots(daily)[1]]}


def changed_table():
    """The live table's answer on Edit once the schedule differs from the
    saved one: the selection column, but no boxes."""
    return render_to_string(
        "stewardship/report-email-schedule-preview.html",
        {
            "sends_table": editor.sends_table(
                WEEKLY | {"schedule": WEEKLY["schedule"] | {"time": "09:00:00"}},
                CAMPAIGN,
                NOW,
                bulk=True,
            )
        },
    )


def skip_answer(kind, sends):
    """Review's answer for Skip (or, with ``kind`` "unskip", Send) selected
    sends: the real review template, naming each ``(slot key, ISO
    instant)`` of ``sends``."""
    return render_to_string(
        "stewardship/report-email-review.html",
        {
            "kind": kind,
            "change": {
                "name": "Daily report",
                "sends": [
                    {"send": key, "due_at": datetime.fromisoformat(at)}
                    for key, at in sends
                ],
            },
            "preview": "synthetic-preview",
        },
    )


def components(context, admin):
    """The editor's New and Edit pages, Edit opened from the calendar for
    its second send (``?send=``, served as its own exact address), Edit with
    a skipped send, and New refused for a passed one-time send."""
    edit = _page(context, admin, WEEKLY)
    return {
        NEW: ("text/html", _page(context, admin, None)),
        NEW_REFUSED: ("text/html", _page(context, admin, None, PASSED)),
        EDIT: ("text/html", edit),
        EDIT_SKIPPED: ("text/html", _page(context, admin, skipped())),
        f"{EDIT}?send={named_send(edit)}": ("text/html", edit),
    }

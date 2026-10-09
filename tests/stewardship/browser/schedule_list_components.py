"""Dates and mail schedules' list and its New and Edit page (#878).

Rendered from the real templates with the views' own helpers, so the browser
checks need no database:

- ``LIST``: a sent invitation, two upcoming reminders and a weekly digest,
  sorted by When either way (``?sort=-when``, ``?sort=when``). Its Delete
  posts to ``DELETE``, which answers as the server does: a redirect to the
  change's status page, here already Applied, after which the dialog redraws
  the table from the list's refresh address, which shows Reminder 1 gone.
- ``BULK``: the same list whose refresh address shows both reminders gone,
  for Delete selected.
- ``REFUSED`` and ``FAILED``: lists whose Delete is refused with the server's
  explanation, or recorded but not applied.
- ``NEW`` and ``EDIT``: New scheduled email, and Edit scheduled email for
  Reminder 1; Review and save answers with the review page. ``EDIT_DIGEST``
  is Edit scheduled email for the weekly digest. The campaign is in New
  York's zone; the pages show and take times in the browser's (#558).
- ``EDIT_UNCHANGED``: Edit scheduled email for Reminder 1 whose Review and
  save is refused as the server refuses a save that changes nothing: the
  page again, with its values as posted and the in-place message.

``POSTS`` lists the fixture server's answers to the pages' POSTs; it is
filled in by ``components``, before the server starts.
"""

import json
from datetime import UTC, datetime
from uuid import uuid4

from django.http import QueryDict
from django.template.loader import render_to_string

from parishkit.stewardship.accounts.schedule_changes import describe
from parishkit.stewardship.accounts.schedule_entry_views import (
    UNCHANGED,
    RepeatRule,
    _summary,
    entry_form,
    entry_times,
)
from parishkit.stewardship.accounts.schedule_forms import (
    email_names,
    schedule_order,
    window_text,
)
from parishkit.stewardship.accounts.schedule_table import SORTING, schedule_rows
from parishkit.stewardship.web.tables import whole_table

from ..campaign_factory import campaign, schedule
from ..content_factory import content

# Between the invitation (October 1, 2054) and the reminders, so the list
# shows a sent row next to upcoming ones.
NOW = datetime(2054, 10, 5, 12, tzinfo=UTC)
LIST = "/schedule-list"
BULK = "/schedule-list-bulk"
REFUSED = "/schedule-list-refused"
FAILED = "/schedule-list-failed"
NEW = "/schedule-new"
EDIT = "/schedule-edit"
EDIT_DIGEST = "/schedule-edit-digest"
EDIT_UNCHANGED = "/schedule-edit-unchanged"
DELETE = "/schedule-list/deletion"
APPLIED = "/schedule-change-applied"
NOT_APPLIED = "/schedule-change-failed"
# The refusal's own explanation, which the dialog shows.
REFUSAL = "Reminders need an initial invitation."
POSTS = {}


def _data():
    """The campaign, its emails and its saved schedules, in sending order."""
    owner = campaign()
    emails = [
        content(owner["id"], kind="email", slot=kind, subject=f"{kind} mail")
        for kind in ("initial", "reminder", "daily_digest", "weekly_digest")
    ]
    # A second reminder email with the same subject, so the list names the
    # two apart by the start of their IDs.
    emails.append(
        content(owner["id"], kind="email", slot="reminder", subject="reminder mail")
    )
    by_kind = {row["values"]["slot"]: row for row in emails}

    def saved(kind, **values):
        """A saved schedule of ``kind`` that sends that type's email."""
        email = by_kind[kind]
        return schedule(
            owner["id"],
            kind=kind,
            template_version=email["id"],
            subject=email["values"]["subject"],
            **values,
        )

    rows = sorted(
        [
            saved("weekly_digest", date=None, weekday=0),
            saved("reminder", date="2054-10-20"),
            saved("initial"),
            saved("reminder", date="2054-10-10"),
        ],
        key=schedule_order,
    )
    return owner, emails, rows


def _list(values, owner, emails, rows, *, sort="when", **extra):
    """The list page for ``rows`` (Initial invitation sent, with results)."""
    names = email_names([row for row in emails if row["values"]["kind"] == "email"])
    summary = {rows[0]["id"]: {"delivered": 1031, "failed": 2}}
    described = schedule_rows(rows, owner["values"], summary, NOW, names)
    context = {
        "campaign": {"pk": owner["id"], "active_configuration": owner["values"]},
        "values": owner["values"],
        "editable": False,
        "base_digest": "a" * 64,
        "table": whole_table(described, sorting=SORTING, sort=sort),
        "refresh_url": LIST,
        "new_url": NEW,
        "delete_url": DELETE,
        "window_text": window_text(owner["values"]),
    } | extra
    return (
        "text/html",
        render_to_string("stewardship/schedule-settings.html", values | context),
    )


def _entry(values, owner, emails, rows, *, schedule_row=None, post_url):
    """New scheduled email, or Edit scheduled email for ``schedule_row``."""
    context = {
        "campaign": {"pk": owner["id"], "active_configuration": owner["values"]},
        "values": owner["values"],
        "entry": entry_form(None, emails, rows, schedule_row),
        "repeat": None if schedule_row else RepeatRule(prefix="repeat"),
        "new": schedule_row is None,
        "schedule": schedule_row,
        "base_digest": "a" * 64,
        "post_url": post_url,
        "list_url": LIST,
        "window_text": window_text(owner["values"]),
    } | entry_times(owner["values"], rows, schedule_row, NOW, bound=False)
    return (
        "text/html",
        render_to_string("stewardship/schedule-entry.html", values | context),
    )


def _unchanged(values, owner, emails, rows, schedule_row):
    """Edit for ``schedule_row`` refused as posted unchanged (see the docstring).

    It is posted as the Los Angeles page shows it: 9:00 AM in New York is
    6:00 AM there.
    """
    data = QueryDict(mutable=True)
    data.update(
        {
            "schedule-date": schedule_row["values"]["date"],
            "schedule-time": "06:00",
            "schedule-template_version": schedule_row["values"]["template_version"],
        }
    )
    entry = entry_form(data, emails, rows, schedule_row)
    assert entry.is_valid(), entry.errors
    entry.add_error(None, UNCHANGED)
    context = {
        "campaign": {"pk": owner["id"], "active_configuration": owner["values"]},
        "values": owner["values"],
        "entry": entry,
        "repeat": None,
        "new": False,
        "schedule": schedule_row,
        "base_digest": "a" * 64,
        "post_url": f"{EDIT_UNCHANGED}/review",
        "list_url": LIST,
        "window_text": window_text(owner["values"]),
        # The view's own refusal summary (_entry_page).
        "errors": _summary(entry, None),
    } | entry_times(owner["values"], rows, schedule_row, NOW, bound=True)
    return render_to_string("stewardship/schedule-entry.html", values | context)


def _status(values, state, **receipt):
    """A configuration change's status page in ``state``."""
    return (
        "text/html",
        render_to_string(
            "stewardship/configuration-request.html",
            values | {"receipt": {"request_id": uuid4(), "state": state, **receipt}},
        ),
    )


def components(context, admin):
    """Every page above, and the POST answers (see the module docstring)."""
    values = context | {"admin_chrome": admin}
    owner, emails, rows = _data()
    initial, first, _second, digest = rows
    reminders = [row for row in rows if row["values"]["kind"] == "reminder"]
    responses = {
        LIST: _list(values, owner, emails, rows, refresh_url=f"{LIST}-one-deleted"),
        f"{LIST}?sort=-when": _list(values, owner, emails, rows, sort="-when"),
        f"{LIST}?sort=when": _list(values, owner, emails, rows),
        f"{LIST}-one-deleted": _list(
            values,
            owner,
            emails,
            [row for row in rows if row["id"] != first["id"]],
            refresh_url=f"{LIST}-one-deleted",
        ),
        BULK: _list(
            values,
            owner,
            emails,
            rows,
            refresh_url=f"{BULK}-deleted",
            delete_url=f"{BULK}/deletion",
        ),
        f"{BULK}-deleted": _list(
            values,
            owner,
            emails,
            [initial, digest],
            refresh_url=f"{BULK}-deleted",
        ),
        REFUSED: _list(values, owner, emails, rows, delete_url=f"{REFUSED}/deletion"),
        FAILED: _list(values, owner, emails, rows, delete_url=f"{FAILED}/deletion"),
        APPLIED: _status(values, "applied"),
        NOT_APPLIED: _status(values, "failed", failure_code="stale_base"),
        NEW: _entry(values, owner, emails, rows, post_url=f"{NEW}/review"),
        EDIT: _entry(
            values, owner, emails, rows, schedule_row=first, post_url=f"{EDIT}/review"
        ),
        EDIT_DIGEST: _entry(
            values, owner, emails, rows, schedule_row=digest, post_url=f"{EDIT}/review"
        ),
        EDIT_UNCHANGED: _entry(
            values,
            owner,
            emails,
            rows,
            schedule_row=first,
            post_url=f"{EDIT_UNCHANGED}/review",
        ),
    }
    review = render_to_string(
        "stewardship/schedule-preview.html",
        values
        | {
            "campaign": {"pk": owner["id"], "active_configuration": owner["values"]},
            "changes": [
                {
                    "label": "Reminder",
                    "operation": "add",
                    "before": None,
                    "after": describe(reminders[0]["values"], owner["values"]),
                    "impact": {},
                }
            ],
            "window_changes": {},
            "blocking": 0,
            "preview": "synthetic-preview",
            "post_url": LIST,
        },
    )
    POSTS.update(
        {
            DELETE: (303, APPLIED, ""),
            f"{BULK}/deletion": (303, APPLIED, ""),
            f"{REFUSED}/deletion": (
                400,
                None,
                json.dumps(
                    {
                        "errors": [{"code": "invalid"}],
                        "refusal": {
                            "message": REFUSAL,
                            "fix": "Nothing was deleted.",
                            "link": None,
                        },
                    }
                ),
            ),
            f"{FAILED}/deletion": (303, NOT_APPLIED, ""),
            f"{NEW}/review": (200, None, review),
            f"{EDIT}/review": (200, None, review),
            f"{EDIT_UNCHANGED}/review": (
                400,
                None,
                _unchanged(values, owner, emails, rows, first),
            ),
        }
    )
    return responses

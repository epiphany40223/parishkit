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
  Reminder 1; Review changes answers with the review fragment for the
  shared review dialog (#750), whose Apply posts to ``APPLY`` (Applied at
  once), then returns to ``LIST``. ``EDIT_DIGEST`` is Edit scheduled email
  for the weekly digest. The campaign is in New York's zone; the pages show
  and take times in the browser's (#558).
  Edit also offers the schedule's own email (#446), filled in from Reminder
  1's email; the reminder type has two emails to choose from.
  ``EDIT_EMAIL_REFUSED``: the same Edit, whose Review answers 400 with the
  page drawn again with the box ticked and ``EMAIL_REFUSAL`` on the email, as
  the view refuses an email the whole configuration rejects;
  ``EDIT_INITIAL``: Edit for the Initial invitation, whose email's plain
  text is generated from its HTML.
- ``NEW_REPEAT``: New, whose Review answers with the review of 30 repeated
  reminders (the most one rule adds), a long review; ``NEW_BLOCKED``: New,
  whose review is blocked by sends in progress, so it has no preview;
  ``NEW_PENDING``: New, whose Apply posts to ``APPLY_PENDING``, whose change
  stays Applying; ``NEW_INVALID``: New, whose Review answers 400 with the page
  drawn again with the send date's error and the summary, as the server does;
  ``NEW_STALE``: New, whose Review answers 409 with a refusal in the server's
  words (``STALE_REFUSAL``).
- ``DATES``: the date-change review (Campaign settings' proposed end date,
  ``DATES_END``, with every schedule's editor); its Review answers with the
  review for the shared review dialog (#750), whose Apply posts to ``LIST``
  (Applied at once) and then returns there. ``DATES_INVALID``: the same page,
  whose Review answers 400 with the page drawn again with Reminder 2 outside
  the campaign, its editor open and the view's summary; ``DATES_STALE``: the
  same page, whose Review answers 409 (``STALE_REFUSAL``).

``POSTS`` lists the fixture server's answers to the pages' POSTs; it is
filled in by ``components``, before the server starts.
"""

import json
from datetime import UTC, datetime
from uuid import uuid4

from django.http import QueryDict
from django.template.loader import render_to_string

from parishkit.stewardship.accounts import schedule_email
from parishkit.stewardship.accounts.schedule_changes import (
    EMAIL_DOES_NOT_FIT,
    describe,
)
from parishkit.stewardship.accounts.schedule_entry_views import (
    RepeatRule,
    _summary,
    entry_form,
    entry_times,
)
from parishkit.stewardship.accounts.schedule_forms import (
    Schedules,
    ScheduleWindow,
    email_names,
    schedule_order,
    window_text,
)
from parishkit.stewardship.accounts.schedule_table import (
    SORTING,
    attach,
    schedule_rows,
)
from parishkit.stewardship.accounts.schedule_views import _summary as dates_summary
from parishkit.stewardship.web.tables import whole_table

from ..campaign_factory import campaign, schedule
from ..content_factory import content
from ..test_schedule_forms import data_for

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
EDIT_EMAIL_REFUSED = "/schedule-edit-email-refused"
EDIT_INITIAL = "/schedule-edit-initial"
# The refusal EDIT_EMAIL_REFUSED's answer draws on the email.
EMAIL_REFUSAL = str(EMAIL_DOES_NOT_FIT)
NEW_REPEAT = "/schedule-new-repeat"
NEW_BLOCKED = "/schedule-new-blocked"
NEW_PENDING = "/schedule-new-pending"
NEW_INVALID = "/schedule-new-invalid"
NEW_STALE = "/schedule-new-stale"
APPLY = "/schedule-list/apply"
APPLY_PENDING = "/schedule-list/apply-pending"
PENDING = "/schedule-change-pending"
STALE_REFUSAL = "Another Administrator changed the schedules meanwhile."
# The send date's refusal NEW_INVALID's answer draws at the field.
DATE_REFUSAL = "Choose a date within the campaign."
DELETE = "/schedule-list/deletion"
APPLIED = "/schedule-change-applied"
NOT_APPLIED = "/schedule-change-failed"
DATES = "/schedule-dates"
DATES_INVALID = "/schedule-dates-invalid"
DATES_STALE = "/schedule-dates-stale"
# The end date Campaign settings proposes; the saved one is October 31.
DATES_END = "2054-10-25"
# The refusal's own explanation, which the dialog shows.
REFUSAL = "Reminders need an initial invitation."
POSTS = {}
# A stale page's refusal, in the server's words.
POSTS_STALE = (
    409,
    None,
    json.dumps(
        {
            "errors": [{"code": "stale_version"}],
            "refusal": {
                "message": STALE_REFUSAL,
                "fix": "Reload the page to start again.",
            },
        }
    ),
)


def _data():
    """The campaign, its emails and its saved schedules, in sending order."""
    owner = campaign()
    emails = [
        content(owner["id"], kind="email", slot=kind, subject=f"{kind} mail")
        for kind in ("initial", "reminder", "daily_digest", "weekly_digest")
    ]
    # A second reminder email with the same subject, so the list names the
    # two apart by the start of their IDs, and its own text (and hand-written
    # plain text), so Edit's own email can be seen to fill from it (#446).
    access = " {{ family_code }} {{ family_url }}"
    emails.append(
        content(
            owner["id"],
            kind="email",
            slot="reminder",
            subject="reminder mail",
            html="<p>A second reminder." + access + "</p>",
            text="Hand-written: a second reminder." + access,
        )
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


def _entry(
    values, owner, emails, rows, *, schedule_row=None, post_url, apply_url=APPLY
):
    """New scheduled email, or Edit scheduled email for ``schedule_row``."""
    toggle, email, sources = (
        schedule_email.editor(None, emails, schedule_row, owner["id"])
        if schedule_row
        else (None, None, None)
    )
    context = {
        "email_toggle": toggle,
        "email": email,
        "email_sources": sources,
        "campaign": {"pk": owner["id"], "active_configuration": owner["values"]},
        "values": owner["values"],
        "entry": entry_form(None, emails, rows, schedule_row),
        "repeat": None if schedule_row else RepeatRule(prefix="repeat"),
        "new": schedule_row is None,
        "schedule": schedule_row,
        "base_digest": "a" * 64,
        "post_url": post_url,
        "list_url": LIST,
        "apply_url": apply_url,
        "window_text": window_text(owner["values"]),
    } | entry_times(owner["values"], rows, schedule_row, NOW, bound=False)
    return (
        "text/html",
        render_to_string("stewardship/schedule-entry.html", values | context),
    )


def _email_refused(values, owner, emails, rows, schedule_row):
    """Edit refused for its edited email, drawn again as ``_entry_page`` does.

    The box is ticked and the email's fields hold what was posted; the
    view's own refusal (``schedule_email.refuse``) and summary mark it.
    """
    data = QueryDict(mutable=True)
    data.update(
        {
            "edit_email": "on",
            "subject": "Refused",
            "html": "<p>Refused. {{ family_code }} {{ family_url }}</p>",
            "generate_text": "on",
        }
    )
    toggle, email, sources = schedule_email.editor(
        data, emails, schedule_row, owner["id"]
    )
    assert email.is_valid(), email.errors
    schedule_email.refuse(email, EMAIL_DOES_NOT_FIT)
    entry = entry_form(None, emails, rows, schedule_row)
    context = {
        "email_toggle": toggle,
        "email": email,
        "email_sources": sources,
        "campaign": {"pk": owner["id"], "active_configuration": owner["values"]},
        "values": owner["values"],
        "entry": entry,
        "repeat": None,
        "new": False,
        "schedule": schedule_row,
        "base_digest": "a" * 64,
        "post_url": f"{EDIT_EMAIL_REFUSED}/review",
        "list_url": LIST,
        "apply_url": APPLY,
        "window_text": window_text(owner["values"]),
        "errors": _summary(entry, None, email),
    } | entry_times(owner["values"], rows, schedule_row, NOW, bound=False)
    return render_to_string("stewardship/schedule-entry.html", values | context)


def _invalid(values, owner, emails, rows):
    """New refused for its send date, drawn again as ``_entry_page`` draws it.

    Posted as a reminder on November 15, after the campaign ends, with the
    view's own summary of the error.
    """
    reminder = next(row for row in emails if row["values"]["slot"] == "reminder")
    data = QueryDict(mutable=True)
    data.update(
        {
            "schedule-kind": "reminder",
            "schedule-date": "2054-11-15",
            "schedule-time": "06:00",
            "schedule-template_version": reminder["id"],
        }
    )
    entry = entry_form(data, emails, rows)
    repeat = RepeatRule(data, prefix="repeat")
    assert entry.is_valid() and repeat.is_valid(), entry.errors
    entry.add_error("date", DATE_REFUSAL)
    context = {
        "campaign": {"pk": owner["id"], "active_configuration": owner["values"]},
        "values": owner["values"],
        "entry": entry,
        "repeat": repeat,
        "new": True,
        "schedule": None,
        "base_digest": "a" * 64,
        "post_url": f"{NEW_INVALID}/review",
        "list_url": LIST,
        "apply_url": APPLY,
        "window_text": window_text(owner["values"]),
        "errors": _summary(entry, repeat),
    } | entry_times(owner["values"], rows, None, NOW, bound=True)
    return render_to_string("stewardship/schedule-entry.html", values | context)


def _dates(values, owner, emails, rows, *, post_url, data=None):
    """The date-change review with ``DATES_END`` proposed, as ``_page`` draws it.

    With ``data`` the forms are bound and validated, and the page is the
    refused Review's answer, with the view's own summary.
    """
    window = ScheduleWindow(
        data,
        previous=owner["values"],
        editable=True,
        prefix="window",
        proposed=None if data else {"end_date": DATES_END},
    )
    schedules = Schedules(
        data,
        previous=rows,
        templates=list(emails),
        campaign_id=owner["id"],
        campaign=owner["values"],
        prefix="schedules",
        email_links=True,
    )
    if data is not None:
        window.is_valid()
        schedules.is_valid()
    context = {
        "campaign": {"pk": owner["id"], "active_configuration": owner["values"]},
        "window": window,
        "schedules": schedules,
        "schedule_rows": attach(schedules, owner["values"], {}, NOW),
        "base_digest": "a" * 64,
        "editable": True,
        "post_url": post_url,
        "list_url": LIST,
    }
    if data is not None:
        context["errors"] = dates_summary(window, schedules)
    return render_to_string("stewardship/schedule-reconcile.html", values | context)


def _dates_refused(values, owner, emails, rows):
    """``DATES_INVALID``'s answer: Reminder 2 moved after the campaign ends."""
    data = data_for(rows)
    data.update(
        {
            "base_digest": "a" * 64,
            "window-timezone": owner["values"]["timezone"],
            "window-start_date": owner["values"]["start_date"],
            "window-end_date": DATES_END,
            "schedules-2-date": "2054-11-15",
        }
    )
    return _dates(
        values, owner, emails, rows, post_url=f"{DATES_INVALID}/review", data=data
    )


def _review(owner, changes, *, new=True, blocking=0):
    """The review dialog's fragment for ``changes`` (schedule-review.html)."""
    return render_to_string(
        "stewardship/schedule-review.html",
        {
            "campaign": {"pk": owner["id"], "active_configuration": owner["values"]},
            "changes": changes,
            "window_changes": {},
            "blocking": blocking,
            "preview": None if blocking else "synthetic-preview",
            "new": new,
        },
    )


def _dates_review(owner, first):
    """The date-change review's fragment: the new end date and a re-planned
    reminder."""
    return render_to_string(
        "stewardship/schedule-review.html",
        {
            "campaign": {"pk": owner["id"], "active_configuration": owner["values"]},
            "changes": [
                {
                    "label": "Reminder",
                    "operation": "update",
                    "before": describe(first["values"], owner["values"]),
                    "after": describe(first["values"], owner["values"]),
                    "impact": {"cancellable": 3, "occurrences": 1},
                }
            ],
            "window_changes": {"end_date"},
            "before_window": owner["values"],
            "after_window": owner["values"] | {"end_date": DATES_END},
            "blocking": 0,
            "preview": "synthetic-preview",
            "dates": True,
        },
    )


def _added(owner, values):
    """One added schedule's change, as ``build_preview`` describes it."""
    return {
        "label": "Reminder",
        "operation": "add",
        "before": None,
        "after": describe(values, owner["values"]),
        "impact": {},
    }


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
        EDIT_EMAIL_REFUSED: _entry(
            values,
            owner,
            emails,
            rows,
            schedule_row=first,
            post_url=f"{EDIT_EMAIL_REFUSED}/review",
        ),
        EDIT_INITIAL: _entry(
            values, owner, emails, rows, schedule_row=initial, post_url=f"{EDIT}/review"
        ),
        PENDING: _status(values, "validating"),
        NEW_REPEAT: _entry(
            values, owner, emails, rows, post_url=f"{NEW_REPEAT}/review"
        ),
        NEW_BLOCKED: _entry(
            values, owner, emails, rows, post_url=f"{NEW_BLOCKED}/review"
        ),
        NEW_PENDING: _entry(
            values,
            owner,
            emails,
            rows,
            post_url=f"{NEW}/review",
            apply_url=APPLY_PENDING,
        ),
        NEW_INVALID: _entry(
            values, owner, emails, rows, post_url=f"{NEW_INVALID}/review"
        ),
        NEW_STALE: _entry(values, owner, emails, rows, post_url=f"{NEW_STALE}/review"),
        DATES: (
            "text/html",
            _dates(values, owner, emails, rows, post_url=f"{DATES}/review"),
        ),
        DATES_INVALID: (
            "text/html",
            _dates(values, owner, emails, rows, post_url=f"{DATES_INVALID}/review"),
        ),
        DATES_STALE: (
            "text/html",
            _dates(values, owner, emails, rows, post_url=f"{DATES_STALE}/review"),
        ),
    }
    review = _review(owner, [_added(owner, reminders[0]["values"])])
    edit_review = _review(
        owner,
        [
            {
                "label": "Reminder",
                "operation": "update",
                "before": describe(first["values"], owner["values"]),
                "after": describe(
                    first["values"] | {"time": "13:30:00"}, owner["values"]
                ),
                "impact": {"cancellable": 3, "occurrences": 1, "outboxes": 3},
            }
        ],
        new=False,
    )
    # One rule adds at most 30 reminders: one a day from October 2.
    repeated = _review(
        owner,
        [
            _added(owner, reminders[0]["values"] | {"date": f"2054-10-{day:02d}"})
            for day in range(2, 32)
        ],
    )
    blocked = _review(
        owner,
        [
            {
                "label": "Reminder",
                "operation": "update",
                "before": describe(first["values"], owner["values"]),
                "after": describe(first["values"], owner["values"]),
                "impact": {"blocking": 2},
            }
        ],
        new=False,
        blocking=2,
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
            f"{EDIT}/review": (200, None, edit_review),
            f"{NEW_REPEAT}/review": (200, None, repeated),
            f"{NEW_BLOCKED}/review": (200, None, blocked),
            f"{NEW_INVALID}/review": (
                400,
                None,
                _invalid(values, owner, emails, rows),
            ),
            f"{NEW_STALE}/review": POSTS_STALE,
            f"{EDIT_EMAIL_REFUSED}/review": (
                400,
                None,
                _email_refused(values, owner, emails, rows, first),
            ),
            f"{DATES}/review": (200, None, _dates_review(owner, first)),
            f"{DATES_INVALID}/review": (
                400,
                None,
                _dates_refused(values, owner, emails, rows),
            ),
            f"{DATES_STALE}/review": POSTS_STALE,
            # The date-change review's Apply confirms on the list.
            LIST: (303, APPLIED, ""),
            APPLY: (303, APPLIED, ""),
            APPLY_PENDING: (303, PENDING, ""),
        }
    )
    return responses

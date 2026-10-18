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
  once), then returns to ``LIST``. New's answer adds one reminder that
  sends once, so it is the confirmation (#1356); Edit's is a full review.
  ``EDIT_DIGEST`` is Edit scheduled email
  for the weekly digest. The campaign is in New York's zone; the pages show
  and take times in the browser's (#558).
  Edit also offers the schedule's own email (#446), filled in from Reminder
  1's email; the reminder type has two emails to choose from.
  ``EDIT_EMAIL_REFUSED``: the same Edit, whose Review answers 400 with the
  page drawn again with the box ticked and ``EMAIL_REFUSAL`` on the email, as
  the view refuses an email the whole configuration rejects;
  ``EDIT_INITIAL``: Edit for the Initial invitation, whose email's plain
  text is generated from its HTML.
  Every New and Edit page's "Send a test of this text" (#446) posts to
  ``TEST_SEND``, which each test answers itself (``page.route``);
  ``EDIT_CLOSED`` is Edit while the campaign admits no test mail, so the
  button is drawn unavailable with ``NOT_OPEN``.
  ``EDIT_SERIES``: Edit for Reminder 1 as a reminder of a remembered series
  (#469) holding both reminders ("Part of" its rule, and the scope choice);
  its Review answers with the review of The whole series at 10:30 AM, its
  series lines drawn by the view's own ``_edit_notes`` from
  ``series_edits.plan``.
  ``EDIT_SERIES_GATED``: the same Edit with The whole series drawn
  unavailable (``GATED_REASON``), whose Review answers 400 with the page
  drawn again with "This and following" refused at the choice
  (``DAY_MOVED``), as the view refuses a time that moves a day.
  ``EDIT_PASSED``: Edit for Reminder 1 whose Apply posts to
  ``APPLY_PASSED``, which answers 409 with the server's refusal of a
  reviewed send time that has since passed (``send_time_passed``, #1464).
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
- ``CALENDAR``: the read-only Campaign calendar (#469) of the same schedules
  plus a daily digest at 7:00 AM, the invitation sent, with the weekly digest
  expanded to each Monday and the daily one to each day through November 1
  (the day after the campaign closes), its first four days already past.
  Each campaign day's Add (#1521) opens ``NEW`` with ``?date=``; ``ADD_ON``
  (``NEW?date=2054-10-12``) is that New with October 12 filled in, as the
  view draws it, whose Cancel and Apply return to ``CALENDAR`` on October
  (``CALENDAR_OCTOBER``). The calendar shows one month (#1656); ``MONTHS``
  are its addresses with ``?month=``, each the same page (the script reads
  the month).
- Edit's Delete (#1656): ``EDIT`` posts it to ``EDIT_DELETE``, answered with
  the review of removing Reminder 1; ``EDIT_SERIES`` to ``SERIES_DELETE``,
  answered with the review of deleting its whole series; ``EDIT_INITIAL``
  to ``INITIAL_DELETE``, refused with ``REFUSAL`` in the server's words.
  ``EDIT_FROM_CALENDAR`` is ``EDIT`` opened from the calendar's October,
  whose Cancel and Apply return there.

``POSTS`` lists the fixture server's answers to the pages' POSTs; it is
filled in by ``components``, before the server starts.
"""

import json
from datetime import UTC, datetime, time
from uuid import uuid4

from django import forms
from django.http import QueryDict
from django.template.loader import render_to_string

from parishkit.stewardship.accounts import schedule_email
from parishkit.stewardship.accounts.campaign_mail import NOT_OPEN as CLOSED
from parishkit.stewardship.accounts.schedule_calendar_views import (
    WEEK,
    calendar_sends,
)
from parishkit.stewardship.accounts.schedule_changes import (
    EMAIL_DOES_NOT_FIT,
    describe,
)
from parishkit.stewardship.accounts.schedule_entry_views import (
    DAY_MOVED,
    SERIES_DELETED,
    RepeatRule,
    _edit_notes,
    _sends_once,
    _summary,
    entry_form,
    entry_times,
    series_context,
)
from parishkit.stewardship.accounts.schedule_forms import (
    OUTSIDE_CAMPAIGN,
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
from parishkit.stewardship.accounts.series_edits import plan, rule_text
from parishkit.stewardship.web.refusals import send_time_passed
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
EDIT_CLOSED = "/schedule-edit-closed"
EDIT_SERIES = "/schedule-edit-series"
EDIT_SERIES_GATED = "/schedule-edit-series-gated"
EDIT_PASSED = "/schedule-edit-passed"
APPLY_PASSED = "/schedule-list/apply-passed"
# The refusal of an Apply whose reviewed send time has passed (#1464), in
# the server's words, as error_response answers the dialog's script.
PASSED_REFUSAL = send_time_passed(LIST).refusal.as_dict()
# Both reminders' series (#469): every day of October 2054.
SERIES_RULE = {"frequency": "daily", "start": "2054-10-01", "end": "2054-10-31"}
GATED_REASON = "A reminder of this series has already sent."
DAY_MOVED_TEXT = str(DAY_MOVED)
# Where "Send a test of this text" posts (#446), and the reason it is
# unavailable on EDIT_CLOSED.
TEST_SEND = "/schedule-email-test"
NOT_OPEN = str(CLOSED)
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
CALENDAR = "/schedule-calendar"
# New opened by the calendar's Add on October 12, 2054 (#1521).
ADD_DAY = "2054-10-12"
ADD_ON = f"{NEW}?date={ADD_DAY}"
CALENDAR_OCTOBER = f"{CALENDAR}?month=2054-10"
MONTHS = [
    CALENDAR_OCTOBER,
    f"{CALENDAR}?month=2054-11",
    # Outside the campaign's months: the script opens the nearer end.
    f"{CALENDAR}?month=2054-01",
    f"{CALENDAR}?month=2060-01",
]
EDIT_DELETE = f"{EDIT}/deletion"
SERIES_DELETE = f"{EDIT_SERIES}/deletion"
INITIAL_DELETE = f"{EDIT_INITIAL}/deletion"
EDIT_FROM_CALENDAR = f"{EDIT}?calendar=2054-10"
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


def _calendar(values, owner, emails, rows):
    """The Campaign calendar for ``rows`` and a daily digest (invitation sent)."""
    names = email_names([row for row in emails if row["values"]["kind"] == "email"])
    summary = {rows[0]["id"]: {"delivered": 1031, "failed": 2}}
    email = next(row for row in emails if row["values"]["slot"] == "daily_digest")
    daily = schedule(
        owner["id"],
        kind="daily_digest",
        date=None,
        time="07:00:00",
        template_version=email["id"],
        subject=email["values"]["subject"],
    )
    rows = sorted([*rows, daily], key=schedule_order)
    context = {
        "campaign": {"pk": owner["id"], "active_configuration": owner["values"]},
        "values": owner["values"],
        "sends": calendar_sends(rows, owner["values"], summary, NOW, names),
        "weekdays": WEEK,
        "list_url": LIST,
        "add_url": NEW,
    }
    return (
        "text/html",
        render_to_string("stewardship/schedule-calendar.html", values | context),
    )


def _entry(
    values,
    owner,
    emails,
    rows,
    *,
    schedule_row=None,
    post_url,
    apply_url=APPLY,
    closed=None,
    series=None,
    refusals=None,
    scope_error=None,
    on=None,
    list_url=LIST,
    delete_url=EDIT_DELETE,
):
    """New scheduled email, or Edit scheduled email for ``schedule_row``.

    ``closed`` is the reason the campaign admits no test mail now, as
    ``schedule_email.test_context`` gives it. ``series`` is the remembered
    series ``schedule_row`` belongs to (#469), drawn as the view draws it,
    with ``refusals`` its scopes known to be unavailable; ``scope_error``
    draws the page refused with "This and following" chosen and that error
    at the choice, with its summary. ``on`` is New's send date from the
    calendar's Add on a day (#1521), ``list_url`` where Cancel and Apply
    return, and ``delete_url`` where Edit's Delete posts its review (#1656).
    """
    entry = entry_form(None, emails, rows, schedule_row, on=on)
    scope_data = QueryDict("series-scope=following") if scope_error else None
    part_of = series_context(entry, series, scope_data, refusals)
    errors = {}
    if scope_error:
        part_of["form"].is_valid()
        part_of["form"].add_error(
            "scope", forms.ValidationError(scope_error, code="series_edit_refused")
        )
        errors = {"errors": _summary(entry, None, None, part_of["form"])}
    toggle, email, sources = schedule_email.editor(
        None, emails, schedule_row, owner["id"]
    )
    context = (
        {
            "email_toggle": toggle,
            "email": email,
            "email_sources": sources,
            "email_test": {
                "url": TEST_SEND,
                "no_email": schedule_email.NO_EMAIL,
                "closed": closed,
                "unknown": False,
            },
            "campaign": {"pk": owner["id"], "active_configuration": owner["values"]},
            "values": owner["values"],
            "entry": entry,
            "repeat": None if schedule_row else RepeatRule(prefix="repeat"),
            "series": part_of,
            "new": schedule_row is None,
            "schedule": schedule_row,
            "base_digest": "a" * 64,
            "post_url": post_url,
            "list_url": list_url,
            "apply_url": apply_url,
            "delete_url": delete_url,
            "window_text": window_text(owner["values"]),
        }
        | entry_times(owner["values"], rows, schedule_row, NOW, bound=False)
        | errors
    )
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
    schedule_email.refuse(email, EMAIL_DOES_NOT_FIT, "candidate_refused")
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
    entry.add_error("date", forms.ValidationError(DATE_REFUSAL, code=OUTSIDE_CAMPAIGN))
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


def _review(owner, changes, *, new=True, blocking=0, series_notes=(), deleting=None):
    """The review dialog's fragment for ``changes`` (schedule-review.html).

    A New review adding one schedule that sends once is the view's
    confirmation (``_sends_once``, #1356), as the server draws it.
    ``deleting`` is Edit's Delete's scope (#1656).
    """
    reviewed = {
        "campaign": {"pk": owner["id"], "active_configuration": owner["values"]},
        "changes": changes,
        "window_changes": {},
        "blocking": blocking,
        "preview": None if blocking else "synthetic-preview",
        "new": new,
        "series_notes": series_notes,
        "deleting": deleting,
    }
    return render_to_string(
        "stewardship/schedule-review.html",
        reviewed | {"confirmation": new and _sends_once(reviewed, None)},
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
    series = {
        "id": str(uuid4()),
        "values": {
            "campaign_id": owner["id"],
            "rule": SERIES_RULE,
            "schedules": sorted(row["id"] for row in reminders),
        },
    }
    responses = {
        LIST: _list(values, owner, emails, rows, refresh_url=f"{LIST}-one-deleted"),
        f"{LIST}?sort=-when": _list(values, owner, emails, rows, sort="-when"),
        f"{LIST}?sort=when": _list(values, owner, emails, rows),
        # Only the sent invitation, which is read-only: rows, none
        # selectable, and no reason given for that (#1332).
        f"{LIST}-sent-only": _list(
            values, owner, emails, [initial], refresh_url=f"{LIST}-sent-only"
        ),
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
        CALENDAR: _calendar(values, owner, emails, rows),
        **{month: _calendar(values, owner, emails, rows) for month in MONTHS},
        APPLIED: _status(values, "applied"),
        NOT_APPLIED: _status(values, "failed", failure_code="stale_base"),
        NEW: _entry(values, owner, emails, rows, post_url=f"{NEW}/review"),
        ADD_ON: _entry(
            values,
            owner,
            emails,
            rows,
            post_url=f"{NEW}/review",
            on=ADD_DAY,
            list_url=CALENDAR_OCTOBER,
        ),
        EDIT_FROM_CALENDAR: _entry(
            values,
            owner,
            emails,
            rows,
            schedule_row=first,
            post_url=f"{EDIT}/review",
            list_url=CALENDAR_OCTOBER,
        ),
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
            values,
            owner,
            emails,
            rows,
            schedule_row=initial,
            post_url=f"{EDIT}/review",
            delete_url=INITIAL_DELETE,
        ),
        EDIT_CLOSED: _entry(
            values,
            owner,
            emails,
            rows,
            schedule_row=first,
            post_url=f"{EDIT}/review",
            closed=NOT_OPEN,
        ),
        EDIT_SERIES: _entry(
            values,
            owner,
            emails,
            rows,
            schedule_row=first,
            post_url=f"{EDIT_SERIES}/review",
            series=series,
            delete_url=SERIES_DELETE,
        ),
        EDIT_SERIES_GATED: _entry(
            values,
            owner,
            emails,
            rows,
            schedule_row=first,
            post_url=f"{EDIT_SERIES_GATED}/review",
            series=series,
            refusals={"series": GATED_REASON},
        ),
        EDIT_PASSED: _entry(
            values,
            owner,
            emails,
            rows,
            schedule_row=first,
            post_url=f"{EDIT_PASSED}/review",
            apply_url=APPLY_PASSED,
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
    # "The whole series" from Reminder 1 at 10:30 AM here (1:30 PM in New
    # York): its lines drawn by the view's own words from the command line's
    # plan.
    outcome = plan(
        series,
        {row["id"]: row["values"] for row in rows},
        set(),
        scope="series",
        time_of_day="13:30:00",
    )
    series_review = _review(
        owner,
        [_added(owner, row["values"] | {"time": "13:30:00"}) for row in reminders],
        new=False,
        series_notes=_edit_notes(
            outcome, first["values"]["date"], time(10, 30), changed_email=False
        ),
    )
    # Edit's Delete (#1656): Reminder 1 removed, or its whole series, with
    # the view's own line on the series.
    removed = [
        {
            "label": "Reminder",
            "operation": "remove",
            "before": describe(row["values"], owner["values"]),
            "after": None,
            "impact": {"cancellable": 3, "occurrences": 1},
        }
        for row in reminders
    ]
    delete_review = _review(owner, removed[:1], new=False, deleting="this")
    series_delete = _review(
        owner,
        removed,
        new=False,
        deleting="series",
        series_notes=[SERIES_DELETED % {"rule": rule_text(SERIES_RULE)}],
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
            EDIT_DELETE: (200, None, delete_review),
            SERIES_DELETE: (200, None, series_delete),
            INITIAL_DELETE: (
                400,
                None,
                json.dumps(
                    {
                        "errors": [{"code": "invalid"}],
                        "refusal": {
                            "message": REFUSAL,
                            "fix": "Nothing was deleted. Change the other "
                            "schedules first, then try again.",
                            "link": None,
                        },
                    }
                ),
            ),
            f"{EDIT}/review": (200, None, edit_review),
            f"{EDIT_SERIES}/review": (200, None, series_review),
            f"{EDIT_PASSED}/review": (200, None, edit_review),
            APPLY_PASSED: (
                409,
                None,
                json.dumps(
                    {"errors": [{"code": "stale_version"}], "refusal": PASSED_REFUSAL}
                ),
            ),
            f"{EDIT_SERIES_GATED}/review": (
                400,
                None,
                _entry(
                    values,
                    owner,
                    emails,
                    rows,
                    schedule_row=first,
                    post_url=f"{EDIT_SERIES_GATED}/review",
                    series=series,
                    refusals={"series": GATED_REASON},
                    scope_error=DAY_MOVED_TEXT,
                )[1],
            ),
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

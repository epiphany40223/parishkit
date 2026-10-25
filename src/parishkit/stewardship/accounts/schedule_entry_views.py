"""New, Edit and Delete for one or a few mail schedules (#878).

Dates and mail schedules lists the saved schedules (``schedule_views``); these
views act on them:

- **New scheduled email** and **Edit scheduled email** are one page with one
  schedule's fields, plus, on New, Sends (Once or Repeating) for reminders. Review
  changes builds the whole set of schedules (the saved ones unchanged, plus
  this one added or changed, or one reminder per repeat date) and reviews it
  in the shared review dialog (UX conventions, "Review dialog", #750), whose
  Apply confirms on Dates and mail schedules and returns there.
  Inside the campaign wizard the page saves directly instead, with its
  action row's Save and go back (#1404 PR 4b, ``campaign_wizard.save_step``).
  The page shows and takes send times in the browser's time zone, which the
  page script posts as ``zone``; ``schedule_local`` converts them to and
  from the campaign-local values a schedule keeps (#558).
- **Edit this schedule's email**, on New and Edit, edits the schedule's own
  email (``schedule_email``, #446): a changed email is added as a new email
  that only this schedule (on New, every reminder its repeat adds) sends, in
  the same reviewed change.
  Its **Send a test of this text** sends the unsaved text as a test, saving
  nothing (``schedule_email_test``).
- **Reminder series** (#469): New's Sends Repeating also remembers its rule as a
  series (``_remember``), and Edit of a series' reminder offers "this and
  following" and "the whole series" for a time or email change
  (``SeriesScope``, ``_series_edit``), planned by the command line's own
  ``series_edits.plan``. With those two, **Change the repeat** (#1808) also
  gives the series a new rule (``RepeatRule`` on Edit, ``rule_scopes``,
  ``_new_rule``), checked by the command line's own
  ``admin_changes.checked_rule`` and planned by the same plan. Delivery
  never reads a series.
- **Delete** answers the list's confirmation dialog: it removes the chosen
  schedules in one configuration change. Edit's own Delete (#1656) posts
  the same route with ``action=preview`` instead and is answered with the
  review dialog's fragment (``_delete_review``): this schedule alone, or for
  a series' reminder the whole series (the command line's series delete),
  confirmed on Dates and mail schedules like every review.
- **Add these schedules and continue**, inside the campaign wizard on a
  campaign with no schedule saved (#1404 PR 6), adds the suggested starting
  schedules (``schedule_suggestions``) in one change, saved directly
  (``schedule_suggested``).

Neither path has validation of its own. Each turns its request into the
change document the schedule commands take (``admin_changes.form_data``),
binds it to the date-change review's own forms and runs the same
``schedule_changes.build_preview``: the formset's field and collection rules,
the whole-configuration candidate, the blocking-work count and the signed
intent. A save then shows that review in the dialog, confirmed on Dates and
mail schedules; a delete records the signed intent at once through
``admin_editing.confirm_intent``, which rechecks the applied configuration and
the campaign's work under the work lock, as a confirmation always does.

Both refuse a schedule that has run or has work prepared (``schedule_table``'s
read-only statuses), so a sent row can be neither changed nor removed here,
even from an old link.
"""

import json
import re
from datetime import date, time
from uuid import UUID

from django import forms
from django.core import signing
from django.db import DatabaseError
from django.http import HttpResponseRedirect
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone
from django.utils.formats import date_format
from django.utils.translation import gettext_lazy as _
from django.utils.translation import ngettext
from django.views.decorators.http import require_http_methods

from parishkit.config import ConfigError
from parishkit.stewardship.campaigns.intervals import local_day
from parishkit.stewardship.campaigns.recurrence import (
    RULE_ERRORS,
    canonical_rule,
    expand,
)
from parishkit.stewardship.campaigns.report_presets import STARTING_POINTS
from parishkit.stewardship.campaigns.work_locks import (
    read_transaction,
    work_transaction,
)
from parishkit.stewardship.storage import StaleRecordError
from parishkit.stewardship.time_entry import canonical
from parishkit.stewardship.wall_clock import weekday_choices
from parishkit.stewardship.web.admin_forms import AdminForm
from parishkit.stewardship.web.contracts import filters
from parishkit.stewardship.web.dates import UnknownZone, browser_zone, format_time
from parishkit.stewardship.web.presentation import parish_date
from parishkit.stewardship.web.refusals import (
    UserFacingDenied,
    UserFacingError,
    UserFacingMissing,
    UserFacingStale,
    stale_page,
    unexpected_fields,
    with_default_code,
)

from . import (
    admin_navigation,
    campaign_wizard,
    schedule_email,
    schedule_local,
    schedule_suggestions,
    schedule_views,
    suggested_reports,
)
from .admin_editing import (
    confirm_intent,
    database_now,
    direct_key,
    error_response,
    form_action,
    principal,
)
from .authentication import runtime
from .campaign_mail import admits_test_mail
from .content_forms import sample_banner, sample_render
from .content_views import _records
from .limiting import LimiterUnavailable
from .policy import Capability, allows
from .schedule_changes import (
    EMAIL_DOES_NOT_FIT,
    NO_CHANGES,
    build_preview,
    confirm_scope,
    preview_salt,
)
from .schedule_forms import (
    REPEATED_HOUR,
    ZONE_MISSING,
    ScheduleForm,
    Schedules,
    ScheduleWindow,
    email_usage,
    saved_send_attrs,
    schedule_labels,
    schedule_order,
    window_text,
)
from .schedule_preview import work_summary
from .schedule_reads import campaign_schedules, schedule_state
from .schedule_table import SORTING, schedule_rows
from .series_edits import SCOPES, SeriesEditError, cut, plan, rule_text
from .series_schema import SECTION as SERIES
from .sessions import authenticated_admin
from .users_schema import uses_users

ERRORS = (
    ConfigError,
    DatabaseError,
    LimiterUnavailable,
    PermissionError,
    ValueError,
    LookupError,
    StaleRecordError,
    signing.BadSignature,
)
# One repeat rule adds at most this many reminders (the page script's limit,
# REPEAT_LIMIT in ui-v1.js). A campaign holds at most Schedules.max_num
# schedules, so a delete names at most that many.
REPEAT_LIMIT = 30
SCHEDULE_LIMIT = Schedules.max_num
# The one schedule's fields on the page, and the repeat rule's. The rule's
# fields are posted only so a refused page can show them again; the dates it
# gave are posted as ``repeat-dates``.
ENTRY = "schedule"
ENTRY_FIELDS = (
    "kind",
    "date",
    "time",
    "template_version",
    # The moment an Edit page drew its schedule from (#558, ScheduleForm).
    "drawn_at",
)
REPEAT_FIELDS = (
    "sends",
    "frequency",
    "weekdays",
    "by",
    "day",
    "ordinal",
    "month_weekday",
    "start",
    "until",
    "dates",
)
REPEAT_MULTIPLE = {"repeat-weekdays", "repeat-dates"}
# The complete gate's words for each field still missing (#2025; UX
# conventions, "Prerequisite gates"): the page's button waits for a whole
# schedule, its hint naming the first field missing, as the date-change
# review's New schedule pane does.
MISSING_HINTS = {
    "kind": _("Choose a mail type."),
    # A date typed but refused (outside the campaign, before the window
    # check shows its own words after a pause): data-empty-hint says the
    # empty case.
    "date": _("Enter a send date within the campaign."),
    "time": _("Enter a send time that can be read, for example 9:00 AM."),
    "template_version": _("Choose the email to send."),
}
DATE_EMPTY = _("Enter the send date.")
# New's hint while Initial invitation is chosen in a campaign that already
# has one (#2025): the server's one_initial rule refuses a second, so the
# button waits from the moment the type is chosen. One not yet sent can be
# changed from the list; one sent (or being prepared) cannot.
INITIAL_SCHEDULED = _(
    "This campaign already has an Initial invitation. Change it from Dates "
    "and mail schedules instead."
)
INITIAL_SENT = _(
    "This campaign's Initial invitation has already been sent or is being "
    "prepared, so it can't have another."
)
# Edit's choice of which reminders of a series change (#469), posted as
# ``series-scope``.
SCOPE = "series-scope"
# A series edit's time moves a reminder to another day in the campaign's
# zone, which its series' rule would no longer give (#469).
DAY_MOVED = _(
    "At this time, some reminders of this series would send on another day. "
    "Choose another time, or This reminder only."
)
# A posted series scope for a reminder that is no longer in a series.
NOT_IN_SERIES = _("This reminder is no longer part of a series.")
# The page's words for each refusal of a series edit (series_edits.plan's
# reason codes; its own messages are the command line's), and for any other.
SERIES_REFUSALS = {
    "all_sent": _(
        "Every reminder of this series has already sent or is being prepared."
    ),
    "read_only_series": _(
        "A reminder of this series after its first unsent one has already sent "
        "or is being prepared, so the whole series can't change together. Open "
        "a reminder after it and choose \u201cThis and following\u201d."
    ),
    "read_only_following": _(
        "A reminder of this series from this one on has already sent or is "
        "being prepared. Open a reminder after it and choose \u201cThis and "
        "following\u201d."
    ),
    "day_moved": DAY_MOVED,
}
# A series edit's new time is already past for another reminder (#469).
TIME_PASSED = _(
    "At this time, the reminder of this series on %(date)s would send at "
    "%(time)s, which has already passed, so it would send at once. Choose a "
    "later time, or change that reminder on its own."
)
SERIES_REFUSED = _("This change can't be made to the series. Reload the page.")
# Change the repeat (#1808): the rule fields an Edit of a series' reminder
# posts, as ``repeat-<name>`` (RepeatRule's, without New's Sends and dates).
RULE_FIELDS = (
    "frequency",
    "weekdays",
    "by",
    "day",
    "ordinal",
    "month_weekday",
    "start",
    "until",
)
# The page's words for a new rule the command line's own check refuses
# (admin_changes.RuleRefused), and for one starting before the reminders it
# changes (series_edits.plan's ``rule_start``), each at the rule's field.
RULE_SKIPS = _(
    "The day of the month is not in %(months)s, so the series would skip a "
    "month. Choose a day every month in the range has, or a shorter range."
)
RULE_COUNT = _(
    "A series repeats on 1 to %(limit)s dates; this rule gives %(count)s. "
    "Change its dates or how often it repeats."
)
RULE_START = _(
    "Start the new repeat on or after %(date)s, the first reminder it "
    "changes; the earlier reminders stay in this series."
)
# A new rule's date whose send time has already passed (#1808): the page
# holds such a rule, so only a crafted post meets this; said at From.
RULE_PASSED = _(
    "The reminder on %(date)s at %(time)s would send at a time that has "
    "already passed, so it would send at once. Start the new repeat later."
)
# Edit's refusal of a Change the repeat that changes nothing (the page holds
# Review then; only a request that bypasses the page script meets it).
UNCHANGED_RULE = _(
    "Nothing has changed. Change the time, email or repeat, then choose Review changes."
)
# A Change the repeat posted with This reminder only (an old or crafted
# page: the box is drawn only for the other two).
RULE_SCOPE = _(
    "Choose \u201cThis and following\u201d or \u201cThe whole series\u201d to "
    "change the repeat."
)
# A repeat on New whose date's send time has already passed (#1948). The page
# script never offers such a date, so only an old page or a crafted post
# meets this; an added reminder in the past would send at once.
REPEAT_PASSED = _(
    "The reminder on %(date)s at %(time)s would send at a time that has "
    "already passed, so it would send at once. Choose a later first date or "
    "a later send time."
)
# Another reminder from the cut date whose time has passed without sending
# (#469): a series change would add it again on its past date, so it would
# send at once. Only the page refuses this; the command line allows it.
MISSED = _(
    "The reminder of this series on %(date)s has passed without sending, so "
    "changing it with the others would send it at once. Change it on its own, "
    "or open a later reminder and choose \u201cThis and following\u201d."
)
# The browser's IANA time zone, which ui-v1.js fills in (#558).
ZONE = "zone"
# New scheduled email's address parameter: the day the Campaign
# calendar's Add on a day opened it for (#1521), an ISO date in the browser's
# zone. That New fills it in as the send date and returns to the calendar.
ADD_ON = "date"
# Edit's address parameter (#1656), which New also takes (#1808): the calendar month
# (``2054-11``) it was opened from, or in the week view (#1808) the week's
# Sunday (``2054-10-11``), which its Cancel and Apply return to. New's Add
# from the week view carries it too, so New returns to that week.
FROM_CALENDAR = "calendar"
# The Campaign calendar's Move… (#1521) posts to Edit with ``move=yes``,
# the new send date, the browser's zone and the configuration version only;
# Edit fills in the rest of its form from the saved schedule (``_moved``).
MOVE = "move"
MOVE_FIELDS = {
    "action",
    "csrfmiddlewaretoken",
    "base_digest",
    ZONE,
    MOVE,
    f"{ENTRY}-date",
}
# Why a send the calendar offers no Move… for is refused anyway (an old
# page or a crafted request): a missed send's time has passed.
NOT_MOVABLE = _(
    "This scheduled email can't be moved from the calendar: its send time has "
    "passed without sending."
)
# Edit's line when "This reminder only" takes a reminder off the dates its
# series' rule gives (#1521), which ``series_schema.upkeep`` then applies.
LEAVES_SERIES = _(
    "This reminder leaves its series (%(rule)s), since the series does not "
    "send on its new day."
)
# A calendar move whose kept time falls in the hour the campaign's clocks
# repeat on the new day (#1521): the Move dialog has only a date, so the
# refusal says what it can do there, at the date.
MOVE_REPEATED_HOUR = _(
    "On this day the send time falls in the hour that repeats when clocks fall "
    "back in the campaign's time zone, so it can't be saved exactly. Choose "
    "another day, or change the time on its Edit page."
)
# A calendar move to a day whose kept time is already past (Administrator,
# PR #2095): the moved send would be due at once, so it would send right
# after Apply. The calendar offers no such day; an old page or a crafted
# request meets this. Unlike the ordinary Edit, a move never goes back.
MOVE_PASSED = _(
    "This day's send time has already passed, so the email would send at "
    "once. Choose a later day."
)
# A year and month as the calendar's addresses name one (``?month=``).
YEAR_MONTH = re.compile(r"\d{4}-(0[1-9]|1[0-2])")
# Edit's Delete (#1656): which reminders of a series' reminder it removes;
# the fields its review posts, and the review's line on a series.
DELETE_SCOPES = ("this", "series")
DELETE_FIELDS = {
    "csrfmiddlewaretoken",
    "base_digest",
    "schedule_id",
    "action",
    "scope",
    # The browser's zone, which the review's send times are shown in
    # (#1129, #1949), as on Edit's own review.
    ZONE,
}
SERIES_DELETED = _(
    "The whole series is deleted: %(rule)s. Its reminders that already sent "
    "stay as ordinary reminders."
)
# Edit's refusal of a save that changes nothing. The page's change gate
# keeps Review unavailable until something changed, so only a request that
# bypasses the page script meets it, in the review dialog (#750).
UNCHANGED = _(
    "Nothing has changed. Change the date, time or email, then choose Review changes."
)
# The signed review of an edited email carries its text; the Pages and emails
# editor refuses a preview this large too (content_views._preview).
PREVIEW_LIMIT = 256_000
TOO_LARGE = _("This email is too large to save. Shorten it, then try again.")
# Why a direct save (#1404 PR 4b) saved nothing while send work blocks the
# change: the review dialog shows the same count with no Apply.
BLOCKED = _(
    "Email from these schedules is being sent right now, or has an uncertain "
    "result, so nothing was saved."
)
BLOCKED_FIX = _("Wait for it to finish, or resolve it on Sent mail, then save again.")


# The id of each repeat rule control (components/repeat-rule.html), whose
# reserved error line is "<id>_error"; the weekdays' line is the group's.
RULE_IDS = {
    "frequency": "repeat-frequency",
    "weekdays": "repeat-weekdays",
    "by": "repeat-by",
    "day": "repeat-day",
    "ordinal": "repeat-ordinal",
    "month_weekday": "repeat-month-weekday",
    "start": "repeat-from",
    "until": "repeat-until",
}
# Each rule error's closed code, for the refusal log (#1437).
RULE_CODES = {
    "dates": "repeat_dates",
    "order": "repeat_order",
    "span": "repeat_span",
    "weekdays": "repeat_weekdays",
    "monthly": "repeat_monthly",
    "frequency": "repeat_frequency",
}


def rule_place(error, data):
    """Where a repeat rule's ``error`` is shown: ``(fields, line field)``.

    The same places as the page script's repeatErrorPlace (ui-v1.js,
    #1915): the fields marked, and the field whose reserved line holds the
    message, the last of them (UX conventions "Field errors and live
    checks": an error about two fields marks both and is said once, after
    them). A missing date marks whichever date is empty, or both.
    """
    if error == "dates":
        fields = tuple(name for name in ("start", "until") if not data.get(name))
        fields = fields or ("start", "until")
    elif error in ("order", "span"):
        fields = ("start", "until")
    elif error == "monthly":
        by_day = data.get("by") != "weekday"
        fields = ("day",) if by_day else ("ordinal", "month_weekday")
    else:  # weekdays, frequency
        fields = (error,)
    return fields, fields[-1]


class RepeatRule(AdminForm):
    """New scheduled email's Sends and repeat rule, for reminders only.

    Sends (``once`` or ``repeat``; UX conventions, "Schedules", Once or
    Repeating, #2022) is posted only while the page shows it, for a
    Reminder; missing, it is Once. ``clean`` gives ``repeat``, whether the
    page repeats. The page script expands the rule into dates
    (``window.ParishRecurrence``) and posts the dates that fit as ``dates``;
    only Sends and ``dates`` decide what is saved. The rule's own fields are
    kept loose (any choice the page offers, or nothing) because they are only
    drawn again on a refused page, never trusted, except that a rule giving
    no date is refused at the field the page marks for it (``refuse_rule``,
    #1915).
    """

    sends = forms.ChoiceField(
        required=False,
        label=_("Sends"),
        choices=[("once", _("Once")), ("repeat", _("Repeating"))],
    )
    frequency = forms.ChoiceField(
        required=False,
        label=_("How often"),
        choices=[
            ("weekly", _("Every week")),
            ("daily", _("Every day")),
            ("monthly", _("Every month")),
        ],
    )
    weekdays = forms.MultipleChoiceField(
        required=False,
        label=_("On these days"),
        choices=[(str(index), day) for index, day in weekday_choices()],
    )
    by = forms.ChoiceField(
        required=False,
        label=_("Repeat on"),
        choices=[
            ("day", _("A day of the month")),
            ("weekday", _("A weekday of the month")),
        ],
    )
    # In the page script's words for a day out of range (#1915).
    day = forms.IntegerField(
        required=False,
        min_value=1,
        max_value=31,
        error_messages={
            key: RULE_ERRORS["monthly"] for key in ("invalid", "min_value", "max_value")
        },
    )
    ordinal = forms.ChoiceField(
        required=False,
        choices=[
            ("1", _("First")),
            ("2", _("Second")),
            ("3", _("Third")),
            ("4", _("Fourth")),
            ("-1", _("Last")),
        ],
    )
    month_weekday = forms.ChoiceField(
        required=False,
        choices=[(str(index), day) for index, day in weekday_choices()],
    )
    start = forms.DateField(
        required=False, error_messages={"invalid": RULE_ERRORS["dates"]}
    )
    until = forms.DateField(
        required=False, error_messages={"invalid": RULE_ERRORS["dates"]}
    )
    # Plain text values, checked below: the page script writes them.
    dates = forms.Field(required=False, widget=forms.MultipleHiddenInput)

    # Fields a rule error marks beyond the field that holds it (refuse_rule).
    rule_marks = ()
    # The rule errors' words, for the sizes of the reserved error lines.
    words = RULE_ERRORS

    @property
    def marks(self):
        """Each marked rule field's name -> the id of the line describing it.

        A field with its own error is described by its own line; one that
        ``refuse_rule`` marks with another, by that field's line. The
        template draws ``aria-invalid`` and ``aria-describedby`` from it.
        """
        found = {name: name for name in RULE_IDS if name in self.errors}
        found |= dict(self.rule_marks)
        return {name: f"{RULE_IDS[line]}_error" for name, line in found.items()}

    def refuse_rule(self):
        """Refuse a rule ``expand`` refuses at its fields; True if refused.

        The page script holds Save while the rule gives no date, so this
        answers only a request that skips it, in the same words and at the
        same fields (#1915).
        """
        rule = _rule_fields(self.cleaned_data)
        # A monthly weekday choice that is not a number is a monthly error.
        error = expand(rule).get("error") if rule is not None else "monthly"
        if not error:
            return False
        fields, line = rule_place(error, self.cleaned_data)
        self.add_error(
            line, forms.ValidationError(RULE_ERRORS[error], code=RULE_CODES[error])
        )
        self.rule_marks = tuple((name, line) for name in fields)
        return True

    def clean(self):
        """Add ``repeat``: True when Sends is Repeating."""
        cleaned = super().clean()
        cleaned["repeat"] = cleaned.get("sends") == "repeat"
        return cleaned

    def clean_dates(self):
        """The posted dates: distinct calendar days, at most REPEAT_LIMIT."""
        values = list(self.cleaned_data.get("dates") or [])
        if len(values) > REPEAT_LIMIT or len(set(values)) != len(values):
            raise forms.ValidationError(
                _("One repeat adds at most %(limit)s reminders."),
                params={"limit": REPEAT_LIMIT},
                code="too_many_dates",
            )
        try:
            return sorted(date.fromisoformat(value) for value in values)
        except ValueError:
            raise forms.ValidationError(
                _("Choose the dates again."), code="invalid_dates"
            ) from None


class SeriesScope(AdminForm):
    """Edit's choice of which reminders of a remembered series change (#469).

    "This reminder only" is the ordinary Edit; the other two change the
    reminder's series from it, or from the series' first reminder that has
    not sent (``series_edits.plan``). It is drawn only for a series' reminder.
    """

    scope = forms.ChoiceField(
        required=False,
        label=_("Apply this edit to"),
        choices=[
            ("this", _("This reminder only")),
            ("following", _("This and following")),
            ("series", _("The whole series")),
        ],
        widget=forms.RadioSelect,
        help_text=_(
            "\u201cThis reminder only\u201d changes just this one; the other "
            "two change the send time, email or repeat."
        ),
    )
    # Change the repeat (#1808): drawn only while another scope than This
    # reminder only is chosen (schedule-series-scope.html), and then the
    # repeat rule's fields (``RepeatRule``, prefix ``repeat``) give the
    # series' new rule.
    rule = forms.BooleanField(
        required=False,
        label=_("Change the repeat"),
        help_text=_(
            "With \u201cThis and following\u201d or \u201cThe whole series\u201d, "
            "change the days this series sends on. Sent or prepared reminders "
            "stay."
        ),
    )
    # The rest of the help, in the "i" beside the legend (#2130;
    # schedule-series-scope.html draws this field itself).
    scope.tip = _(
        "Choose \u201cThis reminder only\u201d to change just this reminder; "
        "moving it to a day its series does not repeat on takes it out of the "
        "series. The other choices change every reminder they cover. Reminders "
        "that sent or are being prepared don't change."
    )

    def clean_scope(self):
        """The scope chosen; "this" when none is posted."""
        return self.cleaned_data.get("scope") or "this"


def _series_of(configuration, campaign, schedule):
    """The applied ``schedule_series`` record holding ``schedule``, or None."""
    if schedule is None or schedule["values"]["kind"] != "reminder":
        return None
    rows = configuration.active_configuration.canonical_document["sections"]
    return next(
        (
            row
            for row in rows.get(SERIES, [])
            if row["values"]["campaign_id"] == str(campaign.pk)
            and schedule["id"] in row["values"]["schedules"]
        ),
        None,
    )


def series_context(
    entry, record, data=None, refusals=None, *, rule=None, scopes=None, mailings=""
):
    """Edit's "Part of" and scope for a series' reminder, or None (#469).

    ``record`` is the reminder's series (``_series_of``), ``data`` the post
    and ``refusals`` the scopes already known to be unavailable
    (``scope_refusals``). Returns ``{"text", "form", "options", "rule",
    "scoped", "changing", "mailings"}``: the rule in plain words, the bound
    ``SeriesScope`` and each choice to draw (``value``, ``label``,
    ``checked``, the ``reason`` it is unavailable, or None, and for Change
    the repeat its ``rule_scopes`` entry as ``scope``); Change the repeat's
    rule form (``rule``, a ``RepeatRule``), whether a scope other than This
    reminder only is chosen (``scoped``) and the box is ticked
    (``changing``), so a page drawn again shows them at once, and the saved
    mailings its date list checks against (``mailings``, JSON). The send
    date is the reminder's own only for This reminder only, so the other
    scopes grey ``entry``'s date in place, unsent (conditional fields,
    ``data-enabled-when``); its help says so.
    """
    if record is None:
        return None
    field = entry.fields["date"]
    field.widget.attrs["data-enabled-when"] = f"{SCOPE}=this"
    field.help_text = _(
        "Used only with \u201cThis reminder only\u201d. Otherwise each "
        "reminder keeps its own date, and this box shows this reminder's."
    )
    form = SeriesScope(data, prefix="series", initial={"scope": "this"})
    chosen = form["scope"].value() or "this"
    return {
        "text": rule_text(record["values"]["rule"]),
        "form": form,
        "options": [
            {
                "value": value,
                "label": label,
                "checked": value == chosen,
                "reason": (refusals or {}).get(value),
                "scope": (scopes or {}).get(value),
            }
            for value, label in form.fields["scope"].choices
        ],
        "rule": rule,
        "scoped": chosen != "this",
        "changing": chosen != "this" and bool(form["rule"].value()),
        "mailings": mailings,
    }


def rule_scopes(record, saved, schedule, read_only):
    """Each series scope's first date and room for Change the repeat (#1808).

    Returns ``{scope: {"from", "min", "min_hint", "room"}}`` for each scope
    ``series_edits.cut`` admits from ``schedule``. ``from`` is the date the
    scope's changes start on (its cut date), which the rule's From starts
    at; ``min`` is that date again when earlier reminders stay in the
    series, since the plan refuses a new rule starting before it then
    (``rule_start``), with its words in ``min_hint``, else empty; ``room``
    is how many schedules are still free once the unsent reminders the
    change replaces (from the cut date on) are removed, for the page's date
    list. The server plans and checks again on Review.
    """
    schedules = {row["id"]: row["values"] for row in saved}
    members = record["values"]["schedules"]
    result = {}
    for scope in SCOPES:
        try:
            _anchor, cut_date = cut(
                record, schedules, read_only, scope=scope, start=schedule["id"]
            )
        except SeriesEditError:
            continue
        stays = any(schedules[member]["date"] < cut_date for member in members)
        replaced = sum(
            1
            for member in members
            if member not in read_only and schedules[member]["date"] >= cut_date
        )
        result[scope] = {
            "from": cut_date,
            "min": cut_date if stays else "",
            "min_hint": RULE_START % {"date": parish_date(date.fromisoformat(cut_date))}
            if stays
            else "",
            "room": SCHEDULE_LIMIT - len(saved) + replaced,
        }
    return result


def rule_initial(rule, start):
    """A saved series rule as Change the repeat's fields start (#1808).

    ``rule`` is the canonical rule; ``start`` the From the page fills in
    (the first date the scope changes, ``rule_scopes``), never the rule's
    own start, which may be a date long past that a New repeat left out.
    The monthly choice the rule does not use starts as New's would.
    """
    monthly = rule.get("monthly") or {}
    return {
        "frequency": rule["frequency"],
        "weekdays": [str(day) for day in rule.get("weekdays", [])],
        "by": "weekday" if "ordinal" in monthly else "day",
        "day": monthly.get("day", 1),
        "ordinal": str(monthly.get("ordinal", 1)),
        "month_weekday": str(monthly.get("weekday", 0)),
        "start": start,
        "until": rule["end"],
    }


def rule_mailings(saved, record, read_only, campaign_zone, now):
    """The saved mailings Change the repeat's dates are checked against (#1808).

    Every saved invitation and reminder (``mailings``) except the series'
    own unsent reminders, which the change replaces, as JSON.
    """
    replaced = {
        member for member in record["values"]["schedules"] if member not in read_only
    }
    kept = [row for row in saved if row["id"] not in replaced]
    return json.dumps(mailings(kept, None, campaign_zone, now))


def _saved(state, campaign):
    """The campaign's saved schedules, in sending order."""
    return sorted(campaign_schedules(state[0], campaign.pk), key=schedule_order)


def _read_only(saved, campaign):
    """IDs of the saved schedules that have run or have work prepared."""
    rows = schedule_rows(
        saved,
        campaign.active_configuration.values,
        work_summary(campaign.pk),
        timezone.now(),
    )
    return {row.id for row in rows if row.read_only}


def _changeable(saved, campaign, identifiers):
    """Refuse an unknown or read-only schedule among ``identifiers``.

    Called inside the view's transaction. The table offers no action on
    either, so this is reached only from an old page or link. Returns the
    read-only schedule IDs, which Edit's series scopes reuse (#469).
    """
    known = {row["id"] for row in saved}
    if set(identifiers) - known:
        raise UserFacingMissing(
            _("That scheduled email no longer exists."),
            fix=_("Return to Dates and mail schedules to see the current list."),
            link=reverse("admin:schedule_settings"),
            link_label=_("Dates and mail schedules"),
        )
    read_only = _read_only(saved, campaign)
    if set(identifiers) & read_only:
        raise UserFacingDenied(
            _(
                "That scheduled email has already run, or is being prepared, "
                "so it can't be changed or deleted."
            ),
            fix=_("Return to Dates and mail schedules to see its status."),
            link=reverse("admin:schedule_settings"),
            link_label=_("Dates and mail schedules"),
        )
    return read_only


def review(
    service,
    actor,
    state,
    campaign,
    editable,
    entries,
    *,
    base_digest,
    content=(),
    series=(),
    series_changes=(),
    key=None,
    zone=None,
    added_reports=(),
):
    """Bind a change document to the review's forms and build the review.

    ``entries`` are the change document's schedules (``admin_changes``): a
    saved ``id`` with the fields it changes, an ``id`` with ``delete``, or the
    fields of a new schedule. The window is posted unchanged. Returns
    ``(context, window, schedules, identifiers)``: ``context`` is
    ``build_preview``'s review, or None with the errors on the bound forms,
    and ``identifiers`` names each form (a saved ID, or ``new<n>``).
    ``content`` lists the content records the change adds (an edited email,
    ``schedule_email.copy``); the forms offer them as saved emails, so an
    entry may send one. ``series`` and ``series_changes`` are
    ``build_preview``'s reminder series to remember and series operations
    (#469). ``key`` signs the review with a direct save's request key
    (#1404 PR 4b, ``admin_editing.direct_key``). ``zone`` is
    the browser's posted zone, which the review's send times are shown in
    (#1129); the command line passes none. ``added_reports`` are report
    email rows added in the same change (``build_preview``'s, #2038).
    """
    # The schedule commands' own binding, so the page and the command line
    # cannot read the same change differently.
    from parishkit.stewardship.admin_changes import form_data

    values = campaign.active_configuration.values
    saved = campaign_schedules(state[0], campaign.pk)
    data, identifiers = form_data(
        {"window": {}, "schedules": entries},
        saved,
        values,
        editable=editable,
        base_digest=base_digest,
    )
    window = ScheduleWindow(data, prefix="window", previous=values, editable=editable)
    schedules = Schedules(
        data,
        prefix="schedules",
        templates=[
            *_records(state[0], campaign.pk),
            *({"id": row["id"], "values": row["values"]} for row in content),
        ],
        campaign_id=campaign.pk,
        campaign=values,
        previous=saved,
    )
    context = build_preview(
        service,
        actor,
        state,
        campaign,
        window,
        schedules,
        base_digest=base_digest,
        salt=preview_salt(campaign.pk),
        content=content,
        series=series,
        series_changes=series_changes,
        request_key=key,
        zone=zone,
        added_reports=added_reports,
    )
    return context, window, schedules, identifiers


def _messages(errors):
    """The plain messages of a form's (or formset's) error list."""
    return [str(message) for message in errors]


def _coded(errors):
    """``(message, code)`` for each of a list of ``ValidationError``s.

    ``errors`` is an error list's ``as_data()``; the code travels with each
    message, so the refusal log can say why a carried error was refused
    (#1437).
    """
    return [(str(message), error.code) for error in errors for message in error]


def _carry_errors(entry, window, schedules, identifiers, targets):
    """Show the review's errors on the page's one schedule form.

    ``targets`` maps a reviewed form's identifier to the date it stands for
    (a repeat date) or None (the page's own schedule). A target's field
    errors go on the matching field, except a repeat date's own date error,
    which names that date at the top; every other form's errors, the
    formset's rules between schedules and the window's catch-all go at the
    top too, so nothing the server found is hidden.
    """

    def add(field, message, code):
        """Add one message, once, keeping its error's closed code."""
        existing = entry.errors.get(field or "__all__", [])
        if message not in existing:
            entry.add_error(field, forms.ValidationError(message, code=code))

    labels = schedule_labels(schedules.previous)
    for index, form in enumerate(schedules.forms):
        identifier = identifiers[index]
        for field, errors in form.errors.as_data().items():
            for message, code in _coded(errors):
                if identifier in targets:
                    day = targets[identifier]
                    if day is not None and field == "date":
                        add(None, f"{parish_date(day)}: {message}", code)
                    elif field in entry.fields and not entry.fields[field].disabled:
                        add(field, message, code)
                    else:
                        add(None, message, code)
                else:
                    name = labels[index] if index < len(labels) else ""
                    add(None, f"{name}: {message}" if name else message, code)
    for message, code in _coded(schedules.non_form_errors().as_data()):
        add(None, message, code)
    for message, code in _coded(window.non_field_errors().as_data()):
        add(None, message, code)


def entry_form(
    data,
    templates,
    saved,
    schedule=None,
    kept=None,
    test_mail=True,
    drawn=None,
    on=None,
):
    """The page's one schedule form: blank for New, filled in for Edit.

    It offers the campaign's emails labelled by the schedules that send them,
    with their preview, test and edit links (#446). For Edit it carries the
    saved values, so its mail type is fixed, and the schedule's name for
    the legend (``schedule_label``). The saved
    date and time are campaign-local; the page script shows them in the
    browser's zone (``entry_times``), and ``kept`` is the saved time in that
    zone, so a legacy time with seconds posted back unchanged still reads.
    ``templates`` are the campaign's content records and ``saved`` its saved
    schedules in sending order. ``test_mail`` false (the campaign admits no
    test mail, see ``campaign_mail.admits_test_mail``) replaces each test link
    with a note saying why, so the page never links a test that cannot work
    (#923). ``drawn`` is the moment Edit draws the schedule from (its
    ``drawn_at`` field, posted back; ``schedule_local.instant``). ``on``
    is New's send date from the calendar's Add on a day (#1521), drawn as
    a reminder on that day.
    """
    form = ScheduleForm(
        data,
        prefix=ENTRY,
        templates=templates,
        usage=email_usage(saved),
        links=True,
        test_mail=test_mail,
        initial=schedule["values"] | {"id": schedule["id"], "drawn_at": drawn or ""}
        if schedule
        # A day from the calendar is drawn as a reminder on that date: the
        # page script clears (and hides) the date of a row with no mail
        # type, so a date alone would be lost. The Initial invitation also
        # sends on a date, so choosing it keeps the day.
        else ({"kind": "reminder", "date": on} if on else None),
        browser_times=True,
    )
    # Edit's legend names the schedule (Reminder 1, schedule-entry.html), so
    # the reader sees which one they are changing.
    form.schedule_label = (
        dict(
            zip((row["id"] for row in saved), schedule_labels(saved), strict=True)
        ).get(schedule["id"])
        if schedule
        else None
    )
    # The address names the schedule; a posted ID is never read.
    del form.fields["id"]
    # Edit posted as shown keeps the saved date, which the browser's window
    # check (#1787) judges then; ``drawn`` is the saved send's moment.
    if schedule and drawn:
        form.fields["date"].widget.attrs.update(
            saved_send_attrs(schedule["values"], drawn)
        )
    if kept is not None:
        form.fields["time"].keep(kept)
    # The page's complete gate (#2025) names what is missing; the send date
    # is optional to the form (ScheduleSet.clean asks for it by mail type),
    # so it is required in the page only. A disabled date (Repeating, a
    # series' greyed date) asks for nothing; one hidden but enabled (no mail
    # type yet) is never submitted over, since the required Mail type holds
    # the button first, so the browser's own check never stops a Review.
    for name, hint in MISSING_HINTS.items():
        form.fields[name].widget.attrs["data-missing-hint"] = hint
    form.fields["date"].widget.attrs["data-empty-hint"] = DATE_EMPTY
    form.fields["date"].widget.attrs["required"] = True
    return form


def initial_taken(saved, campaign):
    """New's words while the campaign already has its Initial invitation.

    Empty when it has none. Otherwise the hint the page's button waits with
    once Initial invitation is chosen (#2025): a sent or prepared one cannot
    be changed, so only an unsent one points at Dates and mail schedules.
    """
    initial = {row["id"] for row in saved if row["values"]["kind"] == "initial"}
    if not initial:
        return ""
    return INITIAL_SENT if initial & _read_only(saved, campaign) else INITIAL_SCHEDULED


def entry_times(values, saved, schedule, now, *, bound):
    """The moments the page script shows in the browser's zone (#558).

    ``values`` is the campaign's configuration and ``saved`` its saved
    schedules. Returns the page's context: the saved invitations and
    reminders (``mailings_json``) and the campaign's first and last moments
    (``window_start``, ``window_end``) for the repeat rule's checks, the
    schedules still free (``room``) of the campaign's ``limit``, and for an
    unbound Edit page the schedule's own moment (``local_from``), which the
    script fills the fields from. A bound page shows what was posted, which
    is already in the browser's zone, so it has none.
    """
    zone = values["timezone"]
    return {
        "mailings_json": json.dumps(mailings(saved, schedule, zone, now)),
        "window_start": local_day(
            date.fromisoformat(values["start_date"]), zone
        ).interval.start.isoformat(),
        "window_end": local_day(
            date.fromisoformat(values["end_date"]), zone
        ).interval.end.isoformat(),
        "room": SCHEDULE_LIMIT - len(saved),
        "limit": SCHEDULE_LIMIT,
        "local_from": schedule_local.instant(schedule["values"], zone, now).isoformat()
        if schedule and not bound
        else "",
    }


def _fields(new):
    """Every field the page may post, for ``form_action``'s closed check."""
    names = {
        "base_digest",
        ZONE,
        schedule_email.EDIT,
        *schedule_email.FIELDS,
        *(f"{ENTRY}-{name}" for name in ENTRY_FIELDS),
    }
    if new:
        names.update(f"repeat-{name}" for name in REPEAT_FIELDS)
    else:
        # Change the repeat (#1808) posts its box and the rule's fields.
        names.update({SCOPE, MOVE, "series-rule"})
        names.update(f"repeat-{name}" for name in RULE_FIELDS)
    return names


def _days(entry, repeat):
    """The send dates typed on a valid page, in the browser's zone.

    One per repeat date when the page repeats; otherwise the one date field.
    Repeating dates are also what ``_targets`` names.
    """
    if repeat is not None and repeat.cleaned_data["repeat"]:
        return repeat.cleaned_data["dates"]
    return [entry.cleaned_data.get("date")]


def _entries(entry, repeat, schedule, *, zone, campaign_zone, now):
    """The change document's schedules for a valid page: one, or one per date.

    Each typed date and time (in the browser's ``zone``) becomes the
    campaign-local values a schedule keeps (``schedule_local.to_campaign``);
    an edited schedule whose date and time were left as shown keeps its saved
    values. Raises ``UnknownZone`` for a blank or unknown zone, and ``ValueError``
    for a date too far out to convert.
    """
    data = entry.cleaned_data
    repeating = repeat is not None and repeat.cleaned_data["repeat"]
    result = []
    for day in _days(entry, repeat):
        # Family mail has no weekday (#2102); the record keeps it as null.
        sent_on, clock, weekday = schedule_local.to_campaign(
            day,
            data["time"],
            None,
            zone=zone,
            campaign_zone=campaign_zone,
            now=now,
            saved=schedule["values"] if schedule is not None else None,
            drawn=data.get("drawn_at"),
        )
        fields = {
            "date": sent_on.isoformat() if sent_on else None,
            "time": clock.isoformat(timespec="seconds"),
            "weekday": weekday,
            "template_version": data["template_version"],
        }
        if schedule is not None:
            fields = {"id": schedule["id"], **fields}
        else:
            fields["kind"] = "reminder" if repeating else data["kind"]
        result.append(fields)
    return result


def _targets(entries, schedule, days):
    """Which reviewed form stands for which entry (see ``_carry_errors``).

    The changed schedule keeps its ID; added ones are ``new<n>`` in order,
    each standing for its repeat date (as typed, in the browser's zone) when
    ``days`` lists them.
    """
    if schedule is not None:
        return {schedule["id"]: None}
    return {
        f"new{offset}": days[offset] if days else None for offset in range(len(entries))
    }


def _check_repeat(entry, repeat):
    """Repeating applies to reminders only and needs at least one date."""
    if repeat is None or not repeat.cleaned_data.get("repeat"):
        return
    if entry.cleaned_data.get("kind") != "reminder":
        repeat.add_error(
            "sends",
            forms.ValidationError(_("Only reminders repeat."), code="reminders_only"),
        )
    elif not repeat.cleaned_data.get("dates") and not repeat.refuse_rule():
        # A valid rule none of whose dates can be added.
        repeat.add_error(
            "sends",
            forms.ValidationError(
                _(
                    "This rule gives no date that can be added. Change the rule, "
                    "or choose Once to send on one date."
                ),
                code="no_dates",
            ),
        )


def _rule(repeat):
    """The canonical rule New's Repeating fields give, or None (#469).

    The page script expands the same fields into the posted dates; here
    they are read only to remember the rule, so anything incomplete or
    invalid is simply None.
    """
    rule = _rule_fields(repeat.cleaned_data)
    return None if rule is None else canonical_rule(rule)


def _rule_fields(data):
    """The rule New's cleaned Repeating fields describe, as ``expand`` takes it.

    None when a monthly weekday choice is not a number (only a request built
    by hand sends one).
    """
    frequency = data.get("frequency")
    rule = {
        "frequency": frequency,
        "start": data["start"].isoformat() if data.get("start") else None,
        "end": data["until"].isoformat() if data.get("until") else None,
    }
    try:
        if frequency == "weekly":
            rule["weekdays"] = [int(value) for value in data.get("weekdays") or []]
        elif frequency == "monthly" and data.get("by") == "weekday":
            rule["monthly"] = {
                "ordinal": int(data.get("ordinal")),
                "weekday": int(data.get("month_weekday")),
            }
        elif frequency == "monthly":
            rule["monthly"] = {"day": data.get("day")}
    except (TypeError, ValueError):
        return None
    return rule


def _remember(repeat, entries, document):
    """New's Repeating rule as ``build_preview``'s series to remember, or none (#469).

    The rule is remembered, holding exactly the reminders the repeat adds,
    when it is valid, every reminder's campaign-local date is a day it gives
    (a browser far from the campaign's zone can move a send to another
    campaign day) and the configuration ``document`` is in the users form,
    the only form that can hold a series. Otherwise the reminders are added
    as before, unremembered.
    """
    rule = _rule(repeat)
    if rule is None or not uses_users(document):
        return []
    given = set(expand(rule)["dates"])
    if any(row["date"] not in given for row in entries):
        return []
    return [{"rule": rule, "added": list(range(len(entries)))}]


def _refuse_at(form, field, message, code):
    """Refuse Change the repeat's rule at one of its fields (#1808).

    The field is marked by its own line, as ``RepeatRule.refuse_rule``
    marks a rule ``expand`` refuses, so the summary links to it.
    """
    form.add_error(field, forms.ValidationError(message, code=code))
    form.rule_marks = ((field, field),)


def _new_rule(form):
    """Change the repeat's new rule, checked as the command line checks one.

    ``form`` is the posted ``RepeatRule`` (#1808). A rule ``expand``
    refuses is refused at its fields (``refuse_rule``); then the command
    line's own repeat check (``admin_changes.checked_rule``: no month
    skipped for want of its day, 1 to REPEAT_LIMIT dates) decides, in the
    page's words at the day of the month or Until. Returns the canonical
    rule, or None with the refusal on ``form``.
    """
    from parishkit.stewardship.admin_changes import RuleRefused, checked_rule

    if form.refuse_rule():
        return None
    try:
        return checked_rule(_rule_fields(form.cleaned_data))[0]
    except RuleRefused as error:
        if error.reason == "missing":
            months = ", ".join(
                date_format(date.fromisoformat(f"{month}-01"), "F Y")
                for month in error.months
            )
            _refuse_at(form, "day", RULE_SKIPS % {"months": months}, "repeat_skips")
        else:
            _refuse_at(
                form,
                "until",
                RULE_COUNT % {"limit": REPEAT_LIMIT, "count": error.count},
                "repeat_count",
            )
        return None


def _page_refusal(error):
    """The page's own translated words for a refused series edit (#469).

    ``series_edits.plan`` words its refusals for the command line; the page
    maps each reason code to plain words that name what it offers.
    """
    return SERIES_REFUSALS.get(error.reason, SERIES_REFUSED)


def scope_refusals(record, saved, schedule, campaign, read_only, now):
    """The series scopes the page can already tell are unavailable, and why.

    Returns ``{scope: message}`` (UX conventions, "Prerequisite gates"): a
    scope ``series_edits.plan`` refuses even with nothing changed (a
    reminder from the cut date has sent or is being prepared), or one whose
    reminders from the cut date include another reminder whose time has
    already passed without sending, which any change would add again on its
    past date and so send at once (``MISSED``). The reminder being edited
    keeps the ordinary Edit's meaning. Only the page refuses that; the
    command line keeps its documented behaviour.
    """
    zone = campaign.active_configuration.values["timezone"]
    schedules = {row["id"]: row["values"] for row in saved}
    result = {}
    for scope in SCOPES:
        try:
            plan(record, schedules, read_only, scope=scope, start=schedule["id"])
            _anchor, cut_date = cut(
                record, schedules, read_only, scope=scope, start=schedule["id"]
            )
        except SeriesEditError as error:
            result[scope] = _page_refusal(error)
            continue
        missed = sorted(
            schedules[member]["date"]
            for member in record["values"]["schedules"]
            if member != schedule["id"]
            and member not in read_only
            and schedules[member]["date"] >= cut_date
            and schedule_local.instant(schedules[member], zone, now) <= now
        )
        if missed:
            result[scope] = MISSED % {
                "date": parish_date(date.fromisoformat(missed[0]))
            }
    return result


def _repeat_passed(entries, campaign_zone, zone, now):
    """Words refusing New's repeat when a reminder it adds is already past.

    ``entries`` are the campaign-local schedules ``_entries`` built; the
    first whose send moment is at or before ``now`` is named in the
    browser's ``zone``. Returns None when every reminder is still ahead.
    """
    for row in entries:
        moment = schedule_local.instant(row, campaign_zone, now)
        if moment <= now:
            shown = moment.astimezone(browser_zone(zone))
            return REPEAT_PASSED % {
                "date": parish_date(shown.date()),
                "time": format_time(shown.time()),
            }
    return None


class TimePassed(ValueError):
    """A series edit's new time would put another reminder in the past (#469).

    ``shown`` is that reminder's date and time in the browser's words
    (``{"date", "time"}``), so Change the repeat (#1808) can say it at From.
    """

    def __init__(self, message, shown):
        """Keep the message and the reminder's date and time."""
        super().__init__(message)
        self.shown = shown


def _refuse_passed(added, schedule, campaign_zone, zone, now):
    """Refuse a series edit that adds another reminder at a time already past.

    A new send time can move one of today's unsent reminders before now (a
    9:00 AM reminder moved to 7:00 AM at 8:00 AM), and an added reminder
    whose time has passed is due at once, so it would send right after
    Apply. Only the reminder being edited may be added in the past, as on
    the ordinary Edit: the one added on its own date (once). The message
    names the date and new time in the browser's ``zone``. The saved-time
    check (``scope_refusals``) covers reminders already missed; this one
    covers the new times. The command line keeps its documented behaviour.
    """
    own = schedule["values"]["date"]
    for row in added:
        if row["date"] == own:
            own = None  # the edited reminder's own re-add, allowed once
            continue
        moment = schedule_local.instant(row, campaign_zone, now)
        if moment <= now:
            shown = moment.astimezone(browser_zone(zone))
            words = {
                "date": parish_date(shown.date()),
                "time": format_time(shown.time()),
            }
            raise TimePassed(TIME_PASSED % words, words)


def _series_edit(
    entry,
    schedule,
    record,
    saved,
    campaign,
    *,
    scope,
    zone,
    now,
    email,
    read_only,
    rule=None,
):
    """The change document for "this and following" or "the whole series" (#469).

    ``record`` is the reminder's series, ``saved`` the campaign's saved
    schedules, ``read_only`` the ids that have run or have work prepared and
    ``email`` the email every changed reminder sends (an edited email's new
    id, or the one chosen). A send time left as the page showed it, and the
    reminder's own email, leave each reminder's own; otherwise the browser
    time is converted on each date the edit changes (the unsent members from
    the cut date on), as a repeat's dates are, and one that would move such a
    reminder to another campaign day is refused (``DAY_MOVED``). Members
    before the cut date keep their times, so they are not checked. With
    Change the repeat's new ``rule`` (#1808, canonical and already checked
    by ``_new_rule``) the changed dates are the rule's instead: each is a
    calendar day, and a changed time is converted on it and must keep it.
    The plan itself is the command line's (``series_edits.plan``). Returns
    ``(entries, series, series_changes, notes)``: the schedules to delete
    and add, the series to remember (``build_preview``'s ``series``), the
    operations on the existing record and the review's lines on the series
    (``_edit_notes``). Raises ``TimePassed`` (``_refuse_passed``),
    ``SeriesEditError``, ``UnknownZone`` or ``schedule_local.RepeatedHour``.
    """
    campaign_zone = campaign.active_configuration.values["timezone"]
    schedules = {row["id"]: row["values"] for row in saved}
    _anchor, cut_date = cut(
        record, schedules, read_only, scope=scope, start=schedule["id"]
    )
    clock = entry.cleaned_data["time"]
    times = None
    retimed = clock != schedule_local.in_browser(
        schedule["values"], zone, campaign_zone, now
    )
    # A new rule's dates are calendar days, so the time (changed or not) is
    # converted on each, as a New repeat's is: a browser whose day differs
    # from the campaign's is then refused (DAY_MOVED) rather than saving
    # other moments than the page's date list showed (#1808 review).
    if retimed or rule is not None:
        times = {}
        for day, wanted in _changed_days(
            record, schedules, read_only, cut_date, rule, zone, campaign_zone, now
        ):
            moved, converted, _weekday = schedule_local.to_campaign(
                day, clock, None, zone=zone, campaign_zone=campaign_zone, now=now
            )
            if moved.isoformat() != wanted:
                raise SeriesEditError(str(DAY_MOVED), "day_moved")
            times.setdefault(wanted, converted.isoformat(timespec="seconds"))
    changed_email = email != schedule["values"]["template_version"]
    outcome = plan(
        record,
        schedules,
        read_only,
        scope=scope,
        start=schedule["id"],
        rule=rule,
        times=times,
        template_version=email if changed_email else None,
    )
    entries = [{"id": member, "delete": True} for member in outcome["remove"]]
    entries.extend(
        {"kind": "reminder", **row, "weekday": None} for row in outcome["add"]
    )
    series = (
        [outcome["series"] | {"added": list(range(len(outcome["add"])))}]
        if outcome["series"] is not None
        else []
    )
    _refuse_passed(outcome["add"], schedule, campaign_zone, zone, now)
    notes = _edit_notes(
        outcome,
        cut_date,
        clock if retimed else None,
        changed_email,
        rule=rule,
    )
    return entries, series, outcome["changes"], notes


def _changed_days(
    record, schedules, read_only, cut_date, rule, zone, campaign_zone, now
):
    """The days a series edit's new time is converted on, as ``(day, date)``.

    ``day`` is the browser-local day the time is typed for and ``date`` the
    campaign date (ISO) the converted time must keep. Without a new rule
    they are the unsent members from the cut date, each on the day it shows
    in the browser's ``zone``; with Change the repeat's ``rule`` (#1808)
    they are the rule's dates, calendar days read in both zones alike.
    """
    if rule is not None:
        return [(date.fromisoformat(text), text) for text in expand(rule)["dates"]]
    return [
        (
            schedule_local.instant(schedules[member], campaign_zone, now)
            .astimezone(browser_zone(zone))
            .date(),
            schedules[member]["date"],
        )
        for member in record["values"]["schedules"]
        if member not in read_only and schedules[member]["date"] >= cut_date
    ]


def _edit_notes(outcome, cut_date, clock, changed_email, rule=None):
    """The review's plain lines on what a series edit does to its series.

    ``clock`` is the new send time as typed (this computer's time), or None
    when the time is unchanged; ``changed_email`` says whether the email
    changed; ``rule`` is Change the repeat's new rule (#1808), or None. A
    split names both parts: the reminders before the cut date that stay in
    the series, and the changed ones that become a new series. A new rule
    also says how many reminders stay as they are and, without a split,
    the series' new rule.
    """
    if outcome["series"] is None:
        return []
    day = parish_date(date.fromisoformat(cut_date))
    notes = []
    if clock is not None:
        notes.append(
            _(
                "From %(date)s, every reminder of this series sends at %(time)s "
                "(this computer's time)."
            )
            % {"date": day, "time": format_time(clock)}
        )
    if changed_email:
        notes.append(
            _("From %(date)s, every reminder of this series sends the chosen email.")
            % {"date": day}
        )
    for change in outcome["changes"]:
        notes.append(
            _("Reminders before %(date)s stay in this series: %(rule)s.")
            % {"date": day, "rule": rule_text(change["values"]["rule"])}
        )
    if outcome["changes"]:
        notes.append(
            _("From %(date)s, the changed reminders are a new series: %(rule)s.")
            % {"date": day, "rule": rule_text(outcome["series"]["rule"])}
        )
    elif rule is not None:
        notes.append(
            _("This series now repeats: %(rule)s.")
            % {"rule": rule_text(outcome["series"]["rule"])}
        )
    if rule is not None and outcome["series"]["keep"]:
        count = len(outcome["series"]["keep"])
        notes.append(
            ngettext(
                "%(count)s reminder of this series stays as it is.",
                "%(count)s reminders of this series stay as they are.",
                count,
            )
            % {"count": count}
        )
    return notes


def _leaves_series(record, entries):
    """Edit's review line when this reminder leaves its series (#1521).

    ``record`` is the reminder's series (``_series_of``) and ``entries``
    the one changed schedule of a "This reminder only" Edit. The line is
    there when its new campaign-local date is not one its series' rule
    gives, the same test ``series_schema.upkeep`` takes it out by.
    """
    if record is None or not entries or not entries[0].get("date"):
        return []
    rule = record["values"]["rule"]
    if entries[0]["date"] in expand(rule)["dates"]:
        return []
    return [LEAVES_SERIES % {"rule": rule_text(rule)}]


def _moved(posted, schedule, saved, campaign, now):
    """The Campaign calendar's Move… (#1521) as Edit's own form would post it.

    ``posted`` holds only ``MOVE_FIELDS``: the new send date in the
    browser's zone. The send time is the saved one in that zone (so 9:00 AM
    stays 9:00 AM on the new day), the email is the saved one, and
    ``drawn_at`` is the saved send's moment, as a freshly drawn Edit page
    would post them; Edit then reviews it exactly as if the Administrator
    had changed only the date there. A send the calendar offers no Move…
    for (a missed send) is refused. A blank or
    unknown zone keeps the campaign-local time, so Edit's own zone refusal
    answers it.
    """
    values = campaign.active_configuration.values
    row = next(
        row
        for row in schedule_rows(saved, values, work_summary(campaign.pk), now)
        if row.id == schedule["id"]
    )
    if not row.movable:
        raise UserFacingDenied(
            NOT_MOVABLE, fix=_("Open its Edit page to change it instead.")
        )
    drawn = schedule_local.instant(schedule["values"], values["timezone"], now)
    try:
        clock = schedule_local.in_browser(
            schedule["values"], posted.get(ZONE, ""), values["timezone"], now
        )
    except UnknownZone:
        clock = time.fromisoformat(schedule["values"]["time"])
    moved = posted.copy()
    del moved[MOVE]
    moved[f"{ENTRY}-time"] = canonical(clock)
    moved[f"{ENTRY}-template_version"] = schedule["values"]["template_version"]
    moved[f"{ENTRY}-drawn_at"] = drawn.isoformat()
    return moved


def _series_notes(reviewed):
    """The review's line on a repeat New remembers as a series (#469)."""
    return [
        _("Kept together as a series: %(rule)s.") % {"rule": rule_text(row["rule"])}
        for row in reviewed.get("series", ())
    ]


def _summary(entry, repeat, email=None, scope=None, rule=None):
    """The page's error summary: every message, linked to its field.

    The shared summary (components/errors.html) takes focus when the refused
    page loads, so the reader hears what to fix and can jump to each field.
    ``email`` is the schedule's email form; it counts only while in use
    (bound). ``scope`` is Edit's series scope (#469), linked to its first
    choice, since a choice group has no control of its own id. The repeat
    rule's controls are drawn with their own ids (``RULE_IDS``, not Django's
    ``id_repeat-...``); the weekdays link to their first box, and an error
    that marks several fields (a last date before the first) to the first
    of them (#1915). ``rule`` is Edit's Change the repeat rule (#1808),
    drawn with the same controls.
    """
    errors = []
    for form in (entry, repeat, email, scope, rule):
        if form is None or not form.is_bound:
            continue
        for field, messages in form.errors.items():
            target = None if field == "__all__" else form[field].auto_id
            # A choice group links to its first choice: Edit's scope, and
            # New's Sends (components/choice-pair.html's radio ids).
            if form is scope and field == "scope" and target:
                target = f"{target}_0"
            if form is repeat and field == "sends":
                target = "repeat-sends_0"
            if form in (repeat, rule) and field in RULE_IDS:
                marked = [name for name, line in form.rule_marks if line == field]
                target = RULE_IDS[marked[0] if marked else field]
                if target == RULE_IDS["weekdays"]:
                    target = "repeat-weekday-0"
            errors.extend(
                {"message": message, "field_id": target} for message in messages
            )
    return errors


def _entry_page(request, context, *, status=200):
    """Render New or Edit scheduled email; a refusal gets its summary."""
    if status == 400:
        context = context | {
            "errors": _summary(
                context["entry"],
                context["repeat"],
                context.get("email"),
                (context.get("series") or {}).get("form"),
                (context.get("series") or {}).get("rule"),
            )
        }
    response = render(
        request, "stewardship/schedule-entry.html", context, status=status
    )
    if status == 400:
        response.stewardship_safe_error = True
        # The refusal log's code for this page's own refusal (#1444).
        with_default_code(response)
    return response


def _kept(posted, schedule, values, now):
    """A posted Edit page's saved time in the browser's zone, or None.

    Only a POST needs it (``entry_form``'s ``kept``); a missing or unknown
    zone is refused later, with the page's own message.
    """
    if posted is None or schedule is None:
        return None
    try:
        return schedule_local.in_browser(
            schedule["values"],
            posted.get(ZONE, ""),
            values["timezone"],
            now,
            posted.get(f"{ENTRY}-drawn_at"),
        )
    except UnknownZone:
        return None


def added_on(parameters, name=ADD_ON):
    """The day New was opened for by the calendar's Add on a day, or None.

    ``parameters`` are the address's checked ones (``filters``). The day is
    an ISO date (``2054-10-12``), the browser-local day of the grid cell;
    anything else is refused as an unknown address (a ``ValueError``, as
    ``filters`` refuses). Whether it falls inside the campaign is the
    review's to say, at the field, as for a typed date. ``name`` is the
    parameter read, for the calendar's own ``week`` (#1808).
    """
    text = parameters.get(name)
    if text is None:
        return None
    try:
        day = date.fromisoformat(text)
    except ValueError:
        day = None
    # fromisoformat also reads "20541012" and other ISO forms; only the
    # form the date field shows is the calendar's.
    if day is None or day.isoformat() != text:
        raise ValueError("Unknown filter input.")
    return text


def year_month(parameters, name):
    """The calendar month (``2054-11``) the address's ``name`` names, or None.

    ``parameters`` are the address's checked ones (``filters``). Anything
    but a four-digit year and a two-digit month is refused as an unknown
    address (a ``ValueError``, as ``filters`` refuses). Whether the month is
    one of the campaign's is the calendar script's to say, since the months
    it draws follow the browser's zone.
    """
    text = parameters.get(name)
    if text is not None and not YEAR_MONTH.fullmatch(text):
        raise ValueError("Unknown filter input.")
    return text


def calendar_place(parameters, name):
    """The calendar month (``2054-11``) or week (``2054-10-11``) ``name`` names.

    Edit's (and New's) ``?calendar=`` is the month the calendar showed or,
    in its week view (#1808), a day of the week shown; None when absent.
    Anything else is refused as an unknown address (a ``ValueError``).
    """
    text = parameters.get(name)
    if text is None or YEAR_MONTH.fullmatch(text):
        return text
    return added_on(parameters, name)


def calendar_url(place):
    """The Campaign calendar showing ``place``: a month, or a week's day (#1808)."""
    calendar = reverse("admin:schedule_calendar")
    if YEAR_MONTH.fullmatch(place):
        return f"{calendar}?month={place}"
    return f"{calendar}?view=week&week={place}"


def return_url(on, place):
    """Where New or Edit's Cancel goes and its Apply returns (#1521, #1656).

    ``on`` is New's day from the calendar's Add, ``place`` the calendar
    month or week (#1808) the page was opened from (``calendar_place``):
    the page returns to the calendar showing that place, else that day's
    month; otherwise it returns to Dates and mail schedules.
    """
    if on is None and place is None:
        return reverse("admin:schedule_settings")
    return calendar_url(place or on[:7])


def _entry(request, campaign_id, schedule_id=None):
    """New (``schedule_id`` None) or Edit scheduled email, GET or POST.

    A POST previews (``review``) and answers with the shared review dialog's
    fragment (``schedule-review.html``; UX conventions, "Review dialog",
    #750), whose Apply posts the signed preview to Dates and mail schedules.
    A preview refused for the values posted (a field, or a rule between
    schedules) is this page again with its errors (status 400), which the
    page script marks on the form it already shows; any other refusal (a
    stale page, a read-only schedule, an unchanged Edit) is a plain refusal
    the dialog shows. The posted date and time are in the browser's zone
    (``zone``) and are converted to the campaign's before the review, so its
    rules compare the moments the Admin chose. A ticked "Edit this
    schedule's email" with changed text adds the new email to the same
    review, and the schedule sends it (``schedule_email``); on New with
    Sends Repeating, every reminder the rule adds sends that one new email, and the
    rule is remembered as a series (``_remember``). On Edit of a series'
    reminder, "this and following" or "the whole series" (``SeriesScope``)
    plans the series edit instead (``_series_edit``); the send date is not
    posted then, so the date the page showed stands in for it. With Change
    the repeat ticked (#1808), its rule fields give the new rule, checked as
    the command line checks one (``_new_rule``) and planned with it; a
    refused rule is the page again with the refusal at its field. New opened by
    the Campaign calendar's Add on a day (``?date=``, ``added_on``, #1521)
    has that send date filled in, and its Cancel and Apply return to the
    calendar on that day's month; Edit opened from the calendar
    (``?calendar=<month>``, #1656) returns to that month, and either opened
    from its week view (``?calendar=<day>``, #1808) to that week
    (``return_url``).
    The page posts to its own path, without the parameters. Edit also draws
    Delete, whose review is ``_delete_review``'s.

    Inside the campaign wizard the page posts ``save`` instead (#1404 PR 4b):
    the same review, signed with the save's request key and recorded at once
    by ``campaign_wizard.save_step`` once the work transaction has ended,
    answered with the change's status, which the page follows; send work
    that blocks the change refuses it (``BLOCKED``).
    """
    service = runtime()
    actor = principal(request, service)
    parameters = filters(
        request.GET,
        allowed={ADD_ON, FROM_CALENDAR} if schedule_id is None else {FROM_CALENDAR},
    )
    on = added_on(parameters)
    place = calendar_place(parameters, FROM_CALENDAR)
    if request.FILES:
        raise unexpected_fields(request.FILES, allowed=())
    new = schedule_id is None
    # The page posts a preview, whose review confirms on the list, or inside
    # the campaign wizard a direct save.
    action = (
        form_action(
            request.POST,
            preview_fields=_fields(new),
            # Change the repeat (#1808) posts its weekdays as New does.
            multiple_fields=REPEAT_MULTIPLE if new else frozenset({"repeat-weekdays"}),
            direct=True,
        )
        if request.method == "POST"
        else None
    )
    if action == "confirm":
        # No sources: an unknown action value, not an unexpected field.
        raise unexpected_fields()
    # The calendar's Move… (#1521): only its own few fields, as a preview.
    moving = action is not None and MOVE in request.POST
    if moving and (
        action != "preview"
        or request.POST.get(MOVE) != "yes"
        or set(request.POST) - MOVE_FIELDS
    ):
        raise unexpected_fields(request.POST, allowed=MOVE_FIELDS)
    key = direct_key(request.POST) if action == "save" else None
    # A repeat of a direct save is answered with its first change's status
    # before the form is checked again (#1486).
    if key is not None and (
        answered := campaign_wizard.repeated_save(request, actor, key)
    ):
        return answered
    posted = request.POST if request.method == "POST" else None
    now = timezone.now()
    with work_transaction() if posted is not None else read_transaction():
        state, campaign, editable = schedule_state(service, campaign_id, page=True)
        values = campaign.active_configuration.values
        saved = _saved(state, campaign)
        schedule = None
        read_only = set()
        if not new:
            read_only = _changeable(saved, campaign, {str(schedule_id)})
            schedule = next(row for row in saved if row["id"] == str(schedule_id))
        if moving:
            posted = _moved(posted, schedule, saved, campaign, now)
        record = _series_of(state[0], campaign, schedule)
        refusals = (
            scope_refusals(record, saved, schedule, campaign, read_only, now)
            if record is not None
            else {}
        )
        scoped = posted is not None and posted.get(SCOPE) in SCOPES
        if scoped and record is None:
            # A page drawn while the reminder was in a series (#469).
            raise UserFacingError(
                NOT_IN_SERIES, fix=_("Reload the page to see it as it is now.")
            )
        if scoped:
            posted = _with_shown_date(posted, schedule, values["timezone"], now)
        records = _records(state[0], campaign.pk)
        test_mail = admits_test_mail(state[0].mode, campaign)
        entry = entry_form(
            posted,
            records,
            saved,
            schedule,
            kept=_kept(posted, schedule, values, now),
            test_mail=test_mail,
            drawn=schedule_local.instant(
                schedule["values"], values["timezone"], now
            ).isoformat()
            if schedule
            else None,
            on=on,
        )
        repeat = RepeatRule(posted, prefix="repeat") if new else None
        # Change the repeat (#1808): the series' rule as its fields start,
        # with From at the first date the default scope changes.
        scopes, rule_form, rule_list = {}, None, ""
        if record is not None:
            scopes = rule_scopes(record, saved, schedule, read_only)
            saved_rule = record["values"]["rule"]
            start = next(
                (
                    scopes[name]["from"]
                    for name in ("following", "series")
                    if name in scopes
                ),
                saved_rule["start"],
            )
            rule_form = RepeatRule(
                posted, prefix="repeat", initial=rule_initial(saved_rule, start)
            )
            rule_list = rule_mailings(saved, record, read_only, values["timezone"], now)
        part_of = series_context(
            entry,
            record,
            posted,
            refusals,
            rule=rule_form,
            scopes=scopes,
            mailings=rule_list,
        )
        scope = part_of["form"] if part_of else None
        # The schedule's own email (#446): on New, of the posted mail type.
        toggle, email, sources = schedule_email.editor(
            posted, records, schedule, campaign.pk
        )
        context = {
            "campaign": campaign,
            "values": values,
            "entry": entry,
            "repeat": repeat,
            "email_toggle": toggle,
            "email": email,
            "email_sources": sources,
            # Send a test of this text (#446, schedule_email_test).
            "email_test": schedule_email.test_context(test_mail, campaign.pk),
            "new": new,
            "schedule": schedule,
            # Edit of a series' reminder (#469): its rule and the scope.
            "series": part_of,
            "base_digest": state[0].active_configuration.digest,
            "post_url": request.path,
            # Where Cancel goes and Apply returns: the calendar's month or
            # week for its Add and its links (#1656, #1808), else the list.
            "list_url": return_url(on, place),
            # Edit's Delete (#1656) posts its review here.
            "delete_url": reverse("admin:schedule_delete"),
            # The review dialog's Apply confirms on the list, then returns.
            "apply_url": reverse("admin:schedule_settings"),
            "window_text": window_text(values),
            # New waits while a second Initial invitation is chosen (#2025).
            "initial_taken": initial_taken(saved, campaign) if new else "",
        } | entry_times(values, saved, schedule, now, bound=posted is not None)
        admin_navigation.place(request, flow="change", step="edit")
        if posted is None:
            return _entry_page(request, context)
        valid = (
            entry.is_valid()
            & (repeat is None or repeat.is_valid())
            & (scope is None or scope.is_valid())
            & (rule_form is None or rule_form.is_valid())
            & (not email.is_bound or email.is_valid())
        )
        if valid:
            _check_repeat(entry, repeat)
            valid = repeat is None or not repeat.errors
        if not valid:
            return _entry_page(request, context, status=400)
        try:
            entries = _entries(
                entry,
                repeat,
                schedule,
                zone=posted.get(ZONE, ""),
                campaign_zone=values["timezone"],
                now=now,
            )
        except UnknownZone:
            entry.add_error(
                None, forms.ValidationError(ZONE_MISSING, code="zone_missing")
            )
            return _entry_page(request, context, status=400)
        except schedule_local.RepeatedHour:
            # A move has only its date to change (#1521).
            entry.add_error(
                "date" if moving else "time",
                forms.ValidationError(
                    MOVE_REPEATED_HOUR if moving else REPEATED_HOUR,
                    code="repeated_hour",
                ),
            )
            return _entry_page(request, context, status=400)
        except ValueError:
            # Only a date far outside any campaign cannot be converted.
            entry.add_error(
                "date",
                forms.ValidationError(
                    _("Choose a date within the campaign (%(window)s).")
                    % {"window": window_text(values)},
                    code="outside_campaign",
                ),
            )
            return _entry_page(request, context, status=400)
        passed = (
            _repeat_passed(entries, values["timezone"], posted.get(ZONE, ""), now)
            if repeat is not None and repeat.cleaned_data["repeat"]
            else None
        )
        if passed:
            repeat.add_error(
                "sends", forms.ValidationError(passed, code="repeat_time_passed")
            )
            return _entry_page(request, context, status=400)
        if (
            moving
            and schedule_local.instant(entries[0], values["timezone"], now) <= now
        ):
            entry.add_error(
                "date", forms.ValidationError(MOVE_PASSED, code="move_time_passed")
            )
            return _entry_page(request, context, status=400)
        added = (
            schedule_email.copy(
                email,
                records,
                entry.cleaned_data["template_version"],
                campaign_id=campaign.pk,
                # Every entry has the one mail type (a repeat's are reminders).
                kind=schedule["values"]["kind"] if schedule else entries[0]["kind"],
            )
            if email.is_bound
            else None
        )
        series, series_changes, notes = [], [], None
        chosen = scope.cleaned_data["scope"] if scope is not None else "this"
        if chosen in refusals:
            # Drawn unavailable with its reason; a post that names it anyway
            # (an old page) is refused at the choice, changing nothing.
            scope.add_error(
                "scope",
                forms.ValidationError(refusals[chosen], code="series_edit_refused"),
            )
            return _entry_page(request, context, status=400)
        new_rule = None
        if scope is not None and scope.cleaned_data["rule"]:
            if chosen == "this":
                # The box is drawn only for the other scopes (an old page).
                scope.add_error(
                    "scope", forms.ValidationError(RULE_SCOPE, code="rule_scope")
                )
                return _entry_page(request, context, status=400)
            # The command line's own rule check, at the rule's fields.
            new_rule = _new_rule(rule_form)
            if new_rule is None:
                return _entry_page(request, context, status=400)
        if chosen != "this":
            try:
                entries, series, series_changes, notes = _series_edit(
                    entry,
                    schedule,
                    record,
                    saved,
                    campaign,
                    scope=chosen,
                    zone=posted.get(ZONE, ""),
                    now=now,
                    email=added["id"]
                    if added is not None
                    else entry.cleaned_data["template_version"],
                    read_only=read_only,
                    rule=new_rule,
                )
            except TimePassed as error:
                if new_rule is not None:
                    # A new rule's date already past: said at From (#1808).
                    _refuse_at(
                        rule_form,
                        "start",
                        RULE_PASSED % error.shown,
                        "repeat_time_passed",
                    )
                else:
                    entry.add_error(
                        "time",
                        forms.ValidationError(str(error), code="series_time_passed"),
                    )
                return _entry_page(request, context, status=400)
            except SeriesEditError as error:
                if error.reason == "rule_start" and new_rule is not None:
                    # A new rule from before the cut date while earlier
                    # reminders stay: said at From (#1808).
                    day = parish_date(date.fromisoformat(scopes[chosen]["from"]))
                    _refuse_at(
                        rule_form,
                        "start",
                        RULE_START % {"date": day},
                        "repeat_rule_start",
                    )
                    return _entry_page(request, context, status=400)
                scope.add_error(
                    "scope",
                    forms.ValidationError(
                        _page_refusal(error), code="series_edit_refused"
                    ),
                )
                return _entry_page(request, context, status=400)
            except schedule_local.RepeatedHour:
                entry.add_error(
                    "time", forms.ValidationError(REPEATED_HOUR, code="repeated_hour")
                )
                return _entry_page(request, context, status=400)
        elif added is not None:
            # Every entry sends the one new email (a repeat's reminders share
            # it); nothing else changes.
            entries = [row | {"template_version": added["id"]} for row in entries]
        if repeat is not None and repeat.cleaned_data["repeat"]:
            series = _remember(repeat, entries, service.store.active().document())
        reviewed, window, schedules, identifiers = review(
            service,
            actor,
            state,
            campaign,
            editable,
            entries,
            base_digest=request.POST.get("base_digest"),
            content=[added] if added else (),
            series=series,
            series_changes=series_changes,
            key=key,
            zone=posted.get(ZONE),
        )
        if reviewed is None and str(NO_CHANGES) in _messages(window.non_field_errors()):
            # Only Edit can post its schedule as saved. Its change gate keeps
            # Review unavailable then, so this is a safeguard for a request
            # that bypasses the page script: a plain refusal, which the
            # review dialog shows (UX conventions, "Review dialog", #750).
            raise UserFacingError(
                UNCHANGED_RULE if new_rule is not None else UNCHANGED,
                fix=_("Nothing was changed."),
            )
        if reviewed is None and str(EMAIL_DOES_NOT_FIT) in _messages(
            window.non_field_errors()
        ):
            # The whole candidate refused the edited email: say so there.
            schedule_email.refuse(email, EMAIL_DOES_NOT_FIT, "candidate_refused")
            return _entry_page(request, context, status=400)
        if reviewed is None:
            repeating = repeat is not None and repeat.cleaned_data["repeat"]
            days = _days(entry, repeat) if repeating else None
            targets = _targets(entries, schedule, days)
            if chosen != "this":
                # A series edit adds reminders, each standing for its date.
                targets = {
                    f"new{offset}": date.fromisoformat(row["date"])
                    for offset, row in enumerate(
                        row for row in entries if "delete" not in row
                    )
                }
            _carry_errors(entry, window, schedules, identifiers, targets)
            return _entry_page(request, context, status=400)
        if added is not None:
            if len(reviewed["preview"] or "") > PREVIEW_LIMIT:
                schedule_email.refuse(email, TOO_LARGE, "too_large")
                return _entry_page(request, context, status=400)
            if key is None:
                reviewed = reviewed | {"email": _sample(service, campaign, added)}
        if key is None:
            reviewed = reviewed | {
                "series_notes": _series_notes(reviewed)
                + (_leaves_series(record, entries) if chosen == "this" else [])
                if notes is None
                else notes
            }
            # The review dialog's fragment, never a page: its Apply confirms
            # on Dates and mail schedules (the form's dialog names it), and
            # the dialog then returns there. A New schedule that sends once,
            # with no email of its own, would only repeat the form: a
            # confirmation.
            return render(
                request,
                "stewardship/schedule-review.html",
                reviewed
                | {
                    "new": new,
                    "confirmation": new
                    and added is None
                    and _sends_once(reviewed, repeat),
                },
            )
        if not reviewed["preview"]:
            # Send work blocks the change: the dialog would offer no Apply.
            raise UserFacingStale(
                BLOCKED,
                fix=BLOCKED_FIX,
                link=reverse("admin:deliveries"),
                link_label=_("Sent mail"),
                reason="send_work_blocks",
            )
    # A direct save, recorded now that the work transaction has ended.
    return campaign_wizard.save_step(
        request,
        service,
        actor,
        token=reviewed["preview"],
        salt=preview_salt(campaign_id),
        current_scope=lambda service: confirm_scope(service, campaign_id, page=True),
        campaign_id=campaign_id,
    )


def _with_shown_date(posted, schedule, campaign_zone, now):
    """``posted`` with the send date the Edit page showed (#469).

    A series scope greys the date, so the browser does not post it; the
    one schedule form still reads one, and the series edit keeps each
    reminder's own date anyway. A blank or unknown zone leaves the post as
    it is, for the page's own zone refusal.
    """
    try:
        zone = browser_zone(posted.get(ZONE, ""))
    except UnknownZone:
        return posted
    shown = schedule_local.instant(schedule["values"], campaign_zone, now)
    posted = posted.copy()
    posted[f"{ENTRY}-date"] = shown.astimezone(zone).date().isoformat()
    return posted


def _sends_once(reviewed, repeat):
    """Whether a review only adds one schedule that sends once (#1356).

    Such a review shows nothing the New page does not: no current schedule,
    no sent or planned work (every count is 0), and a send time the reader
    just typed. A Repeating New (its generated reminders), anything else the
    change touches, or sends that block it keep the full review.
    """
    changes = reviewed["changes"]
    # Repeating keeps the full review even when its rule yields one date.
    repeating = repeat is not None and repeat.cleaned_data["repeat"]
    return (
        not repeating
        and len(changes) == 1
        and changes[0]["operation"] == "add"
        and bool(changes[0]["after"]["date"])
        and not reviewed["window_changes"]
        and not reviewed.get("series")
        and not reviewed["blocking"]
        and bool(reviewed["preview"])
    )


def _sample(service, campaign, added):
    """The new email as the review shows it: a fictional sample.

    Filled in with the parish's values and made-up Family values, with the
    campaign banner the real send would carry, exactly as the Pages and
    emails review shows a proposed email.
    """
    from parishkit.stewardship.jobs.campaign_mail_values import document_parish

    values = campaign.active_configuration.values
    return sample_render(
        added["values"],
        parish=document_parish(service.store.active().document()),
        campaign=values,
        banner=sample_banner(values, added["values"]["slot"]),
    )


def mailings(saved, schedule, campaign_zone, now):
    """The saved invitations and reminders, for the repeat rule's checks.

    Each is ``{"kind", "at"}``, ``at`` the UTC moment it sends (ISO 8601),
    which the page script compares repeat dates with in the browser's zone.
    The schedule being edited is left out (Repeating is offered on New only,
    so it is None there).
    """
    return [
        {
            "kind": row["values"]["kind"],
            "at": schedule_local.instant(row["values"], campaign_zone, now).isoformat(),
        }
        for row in saved
        if row["values"]["kind"] in {"initial", "reminder"}
        and row["values"]["date"]
        and (schedule is None or row["id"] != schedule["id"])
    ]


def _recheck(request, service):
    """Refuse a response whose access was revoked while it was built."""
    if not allows(
        authenticated_admin(request, store=service.store, read_only=True),
        Capability.CONFIGURE,
    ):
        raise PermissionError("Schedule access was revoked.")


@require_http_methods(["GET", "HEAD", "POST"])
def schedule_new(request, campaign_id):
    """New scheduled email: one schedule, or a repeating reminder."""
    try:
        response = _entry(request, campaign_id)
        _recheck(request, runtime())
        response["Cache-Control"] = "no-store"
        return response
    except ERRORS as error:
        return error_response(error)


@require_http_methods(["GET", "HEAD", "POST"])
def schedule_edit(request, campaign_id, schedule_id):
    """Edit scheduled email: the New page filled in from one saved schedule."""
    try:
        response = _entry(request, campaign_id, schedule_id)
        _recheck(request, runtime())
        response["Cache-Control"] = "no-store"
        return response
    except ERRORS as error:
        return error_response(error)


def _chosen(parameters):
    """The schedule IDs a delete names: 1–SCHEDULE_LIMIT distinct UUIDs.

    Each way a list can be wrong has its own refusal, so the dialog says
    what happened: nothing chosen, one schedule twice (a stale or crafted
    page), or more than a campaign can hold.
    """
    allowed = {"csrfmiddlewaretoken", "base_digest", "schedule_id"}
    if set(parameters) - allowed or any(
        len(values) != 1 for name, values in parameters.lists() if name != "schedule_id"
    ):
        raise unexpected_fields(parameters, allowed=allowed, multiple={"schedule_id"})
    values = parameters.getlist("schedule_id")
    if not values:
        raise UserFacingError(
            _("Choose at least one scheduled email to delete."),
            fix=_("Reload the page and try again."),
        )
    if len(values) > SCHEDULE_LIMIT:
        raise UserFacingError(
            _("Choose at most %(limit)s scheduled emails to delete at once.")
            % {"limit": SCHEDULE_LIMIT},
            fix=_("Nothing was deleted. Reload the page and choose again."),
        )
    if len(set(values)) != len(values):
        raise UserFacingError(
            _("The same scheduled email was chosen more than once."),
            fix=_("Nothing was deleted. Reload the page and choose again."),
        )
    return [str(UUID(value)) for value in values]


def _refusal(window, schedules, *, fallback=None, fix=None):
    """A refused delete, with every rule it would break, as one message.

    ``fallback`` and ``fix`` replace the delete's own wording for another
    change refused the same way (the suggested schedules, #1404 PR 6).
    """
    messages = [
        *_messages(schedules.non_form_errors()),
        *_messages(window.non_field_errors()),
        *(
            message
            for form in schedules.forms
            for messages in form.errors.values()
            for message in _messages(messages)
        ),
    ]
    return UserFacingError(
        " ".join(dict.fromkeys(messages))
        or fallback
        or _("These scheduled emails can't be deleted together."),
        fix=fix
        or _("Nothing was deleted. Change the other schedules first, then try again."),
    )


def _delete_entries(schedule, record, saved, read_only, scope):
    """What Edit's Delete removes (#1656): ``(entries, series_changes, notes)``.

    ``scope`` "this" removes ``schedule`` alone; a series' reminder then
    leaves its series through the series upkeep, as a delete from the list
    does. "series" removes every reminder of ``record`` (the reminder's
    series) that has not sent or been prepared, and the series record, with
    the command line's own plan (``series_edits.plan``, scope ``delete``);
    the sent ones stay as ordinary reminders. ``notes`` are the review's
    plain lines on what else the delete means.
    """
    identifier = schedule["id"]
    if scope == "this":
        return [{"id": identifier, "delete": True}], [], []
    if record is None:
        # A page drawn while the reminder was in a series.
        raise UserFacingError(
            NOT_IN_SERIES, fix=_("Reload the page to see it as it is now.")
        )
    outcome = plan(
        record,
        {row["id"]: row["values"] for row in saved},
        read_only,
        scope="delete",
    )
    entries = [{"id": member, "delete": True} for member in outcome["remove"]]
    note = SERIES_DELETED % {"rule": rule_text(record["values"]["rule"])}
    return entries, outcome["changes"], [note]


def _delete_review(request, service, actor, campaign_id):
    """Review Edit's Delete of one schedule, or of its whole series (#1656).

    The post names one schedule (``schedule_id``), ``action=preview`` and,
    for a series' reminder, ``scope`` ("this", the default, or "series",
    ``_delete_entries``). The schedule must still be changeable
    (``_changeable``: not run and no work prepared), as on Edit. The change
    is reviewed exactly as every schedule change is (``review``), and the
    answer is the review dialog's fragment, whose Apply confirms on Dates and
    mail schedules; sends that block it give a review without Apply. A rule
    the remaining schedules would break is a plain refusal the dialog shows,
    and nothing is recorded until Apply.
    """
    posted = request.POST
    if set(posted) - DELETE_FIELDS or any(
        len(values) != 1 for _name, values in posted.lists()
    ):
        raise unexpected_fields(posted, allowed=DELETE_FIELDS)
    scope = posted.get("scope", "this")
    if posted["action"] != "preview" or scope not in DELETE_SCOPES:
        # No sources: an unknown action or scope value, not an unknown field.
        raise unexpected_fields()
    identifier = str(UUID(posted.get("schedule_id", "")))
    with work_transaction():
        state, campaign, editable = schedule_state(service, campaign_id, page=True)
        saved = _saved(state, campaign)
        read_only = _changeable(saved, campaign, {identifier})
        schedule = next(row for row in saved if row["id"] == identifier)
        record = _series_of(state[0], campaign, schedule)
        entries, series_changes, notes = _delete_entries(
            schedule, record, saved, read_only, scope
        )
        reviewed, window, schedules, _forms = review(
            service,
            actor,
            state,
            campaign,
            editable,
            entries,
            base_digest=posted.get("base_digest"),
            series_changes=series_changes,
            zone=posted.get(ZONE),
        )
    if reviewed is None:
        raise _refusal(window, schedules)
    response = render(
        request,
        "stewardship/schedule-review.html",
        reviewed
        | {
            "new": False,
            "deleting": scope,
            "series_notes": notes,
        },
    )
    _recheck(request, service)
    return response


@require_http_methods(["POST"])
def schedule_delete(request, campaign_id):
    """Delete the chosen schedules in one change, as the dialog confirmed.

    The change is built and signed as the review builds it, then recorded at
    once (``confirm_intent``: the applied configuration and the campaign's
    work are rechecked under the work lock). A refusal changes nothing. The
    answer is the change's status page, which the dialog follows until the
    change is applied. Edit's Delete posts ``action=preview`` instead, and
    is answered with its review (``_delete_review``, #1656).
    """
    try:
        service = runtime()
        actor = principal(request, service)
        filters(request.GET, allowed=set())
        if request.FILES:
            raise unexpected_fields(request.FILES, allowed=())
        if "action" in request.POST:
            return _delete_review(request, service, actor, campaign_id)
        identifiers = _chosen(request.POST)
        listing = reverse("admin:schedule_settings")
        with work_transaction():
            state, campaign, editable = schedule_state(service, campaign_id, page=True)
            saved = _saved(state, campaign)
            _changeable(saved, campaign, set(identifiers))
            reviewed, window, schedules, _forms = review(
                service,
                actor,
                state,
                campaign,
                editable,
                [{"id": identifier, "delete": True} for identifier in identifiers],
                base_digest=request.POST.get("base_digest"),
            )
        if reviewed is None:
            raise _refusal(window, schedules)
        if reviewed["preview"] is None:
            raise UserFacingDenied(
                _(
                    "Email from these schedules is being sent right now, or has an "
                    "uncertain result, so they can't be deleted yet."
                ),
                fix=_(
                    "Wait for it to finish, or resolve it on the Sent mail "
                    "page, then try again."
                ),
            )
        receipt = confirm_intent(
            request,
            service,
            actor,
            token=reviewed["preview"],
            salt=preview_salt(campaign_id),
            link=listing,
            current_scope=lambda service: confirm_scope(
                service, campaign_id, page=True
            ),
        )
        admin_navigation.remember_origin(request, receipt.request_id, path=listing)
        _recheck(request, service)
        return HttpResponseRedirect(
            reverse("admin:configuration_request", args=[receipt.request_id])
        )
    except ERRORS as error:
        return error_response(error)


# Why Add these schedules and continue saved nothing because the campaign
# no longer has a set to suggest (a schedule was saved, or its Initial
# invitation email removed, since the page was drawn).
NOTHING_TO_SUGGEST = _("The suggested schedules no longer apply, so nothing was saved.")
NOTHING_TO_SUGGEST_FIX = _(
    "Reload the page to see the campaign's schedules, then add any you need."
)
# The fix when the suggested set breaks a schedule rule (an email that no
# longer fits its mail type, say): the schedules can still be added one by one.
NOT_ADDED_FIX = _(
    "Nothing was saved. Add the schedules one at a time with New scheduled email."
)


def _report_rows(configuration, campaign, entries):
    """The configuration rows of the ticked suggested report emails (#2038).

    ``entries`` are their ``create`` entries (``SuggestedReports.entries``),
    checked and built by the Report emails page's own code, which refuses a
    value New report email would refuse, naming its field.
    """
    if not entries:
        return []
    from parishkit.stewardship.reports.report_email_changes import change_rows
    from parishkit.stewardship.reports.report_email_editor import portal_users
    from parishkit.stewardship.reports.report_email_list import report_records

    patch, due, _changes = change_rows(
        entries,
        {
            row["id"]: row["values"]
            for row in report_records(configuration, campaign.pk)
        },
        campaign.active_configuration.values,
        database_now(),
        campaign_id=campaign.pk,
        users=portal_users(configuration),
    )
    # Both starting points repeat, so no send time is ever returned. A
    # one-time starting point would return one, which Apply must sign so it
    # refuses a time that has passed (#1464); this save signs none, so
    # refuse rather than save such a report unchecked.
    if due:
        raise RuntimeError("a suggested report email has a one-time send time")
    return patch


@require_http_methods(["POST"])
def schedule_suggested(request, campaign_id):
    """Add the suggested starting schedules directly (#1404 PR 6).

    Dates and mail schedules' **Add these schedules and continue**, inside
    the campaign wizard on a campaign with no schedule saved. The form posts
    only the page's version (``base_digest``) and request key; the server
    works the set out again from the applied values
    (``schedule_suggestions.entries``), so the change is exactly what the
    page listed for that version. The suggested report emails the page
    offers beside them (#2038, ``suggested_reports``) post their ticks and
    times: each ticked report becomes a ``report-email preview`` ``create``
    entry, checked as New report email checks one
    (``report_email_changes.change_rows``), and its row joins the same
    change, so the Family schedules and the reports apply in one
    configuration version or not at all. A ticked report without a readable
    time is refused at its time field: the page drawn again, status 400,
    with nothing saved. It is reviewed as New scheduled email
    reviews its schedules (``review``), signed with the save's request key
    (``admin_editing.direct_key``), and recorded at once by
    ``campaign_wizard.save_step``, which rechecks under the work lock that
    the wizard still applies. A repeat of the same submit is answered with
    the first change's status. Refused, with nothing saved: a stale page,
    setup that has ended, a campaign with a schedule saved meanwhile (or
    whose Initial invitation time has passed) or send work that blocks it.
    The answer is the change's status, which the page follows.
    """
    try:
        service = runtime()
        actor = principal(request, service)
        filters(request.GET, allowed=set())
        if request.FILES:
            raise unexpected_fields(request.FILES, allowed=())
        values = filters(
            request.POST,
            allowed={
                "csrfmiddlewaretoken",
                "base_digest",
                "request_key",
                *suggested_reports.fields(STARTING_POINTS),
            },
        )
        key = direct_key(request.POST)
        if answered := campaign_wizard.repeated_save(request, actor, key):
            return answered
        with work_transaction():
            state, campaign, editable = schedule_state(service, campaign_id, page=True)
            if campaign_wizard.current() is None:
                raise campaign_wizard.setup_ended()
            # Asked before the page's version: saving any schedule changes
            # the version, so a stale page would otherwise always hide this
            # more specific answer.
            entries = schedule_suggestions.entries(
                campaign.active_configuration.values,
                _records(state[0], campaign_id),
                timezone.now(),
            )
            if not editable or campaign_schedules(state[0], campaign_id) or not entries:
                raise UserFacingStale(
                    NOTHING_TO_SUGGEST,
                    fix=NOTHING_TO_SUGGEST_FIX,
                    reason="stale_page",
                )
            if values.get("base_digest") != state[0].active_configuration.digest:
                raise stale_page()
            ticked = schedule_views.suggested_form(
                request.POST, state[0], campaign, database_now()
            )
            # A starting point no longer offered (a report of its name was
            # saved since) is refused rather than silently left out.
            allowed = {
                "csrfmiddlewaretoken",
                "base_digest",
                "request_key",
                *suggested_reports.fields(ticked.keys),
            }
            if set(request.POST) - allowed:
                raise unexpected_fields(request.POST, allowed=allowed)
            refused = None
            if not ticked.is_valid():
                # The page drawn again with the refusal at its fields, which
                # the page script places (ui-v1.js, showFieldErrors); answered
                # once access is rechecked, after the transaction.
                refused = schedule_views._list(
                    request,
                    state,
                    campaign,
                    editable,
                    SORTING.parse({}),
                    reports_form=ticked,
                )
                refused.status_code = 400
                refused.stewardship_safe_error = True
                with_default_code(refused)
            else:
                reviewed, window, schedules, _forms = review(
                    service,
                    actor,
                    state,
                    campaign,
                    editable,
                    entries,
                    base_digest=values["base_digest"],
                    key=key,
                    added_reports=_report_rows(state[0], campaign, ticked.entries()),
                )
                if reviewed is None:
                    raise _refusal(
                        window,
                        schedules,
                        fallback=_("The suggested schedules don't fit the campaign."),
                        fix=NOT_ADDED_FIX,
                    )
                if not reviewed["preview"]:
                    # A draft with no schedule has no send work to block the
                    # change; should any, refuse it as New scheduled email does.
                    raise UserFacingStale(
                        BLOCKED,
                        fix=BLOCKED_FIX,
                        link=reverse("admin:deliveries"),
                        link_label=_("Sent mail"),
                        reason="send_work_blocks",
                    )
        if refused is not None:
            _recheck(request, service)
            return refused
        # Recorded once the work transaction has ended.
        response = campaign_wizard.save_step(
            request,
            service,
            actor,
            token=reviewed["preview"],
            salt=preview_salt(campaign_id),
            current_scope=lambda service: confirm_scope(
                service, campaign_id, page=True
            ),
            campaign_id=campaign_id,
        )
        _recheck(request, service)
        return response
    except ERRORS as error:
        return error_response(error)

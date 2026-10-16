"""The ParishSoft settings page's refresh schedule editor (#632).

The page edits the one refresh schedule as rule rows ("every 2 hours from
08:00 to 18:00", "at 02:00"), skip rows and the switch "Skip refreshes
around Family emails" (see the admin-portal spec, "ParishSoft refresh
schedule settings"). It has Basic and Advanced modes (#1122; ui-conventions
spec, "Basic and Advanced modes"): Basic offers a few choices
(``BASIC_CHOICES``), each exactly one set of rule rows, which the page
script writes into the Advanced rows. The rules are applied in one place,
the server: the page script only adds and removes rows, writes the Basic
choices and reads times; every check, the preview and the cost come from
here, through the shared validator (``refresh_rules.check_schedule``) and
the preview functions (``schedule_preview``), for the live check and the
save alike. The mode is presentation only: it is stored beside the rules
(``MODE``) and chooses which panel the page opens in, never what runs.

The editor offers full refreshes only. A stored rule of the "quick" kind
(quick updates, which ParishSoft's empty change list makes useless, #1122)
still runs and is shown read-only with one action that removes them all,
through the usual review; the capability itself goes with #1147.

Two rules keep Production's refresh times unchanged until an Administrator
changes the schedule itself:

- the page shows a stored schedule as rules (``refresh_rules.converted_rules``,
  which reproduces today's times exactly), and a posted schedule whose rows
  (in any order), skips and switch are what the page showed is not a change:
  saving other settings keeps the stored schedule keys exactly as they are;
- problems on such an unchanged schedule (a kept off-quarter-hour time too
  close to another) are shown at their rows but never block the page's other
  settings; they block only a change to the schedule.

A request without the editor's rows (the management form) is treated as an
unchanged schedule, so a save that never showed the editor cannot change it.
"""

import json
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from django import forms
from django.forms import formset_factory
from django.utils.translation import gettext, ngettext, ngettext_lazy
from django.utils.translation import gettext_lazy as _

from parishkit.stewardship.source.cadence import MAX_DAILY_TIMES, refresh_settings
from parishkit.stewardship.source.refresh_rules import (
    DAY,
    MAX_RULES,
    MAX_SKIPS,
    SPACING,
    STEPS,
    _skipped,
    check_schedule,
    clock,
    converted_rules,
    daily_times,
    minutes,
    stored_settings,
    valid_shape,
)
from parishkit.stewardship.wall_clock import WEEKDAYS, hhmm, wall_hhmm
from parishkit.stewardship.web.admin_forms import AdminForm, AdminFormSet
from parishkit.stewardship.web.time_entry_fields import FlexibleTimeField

from .refresh_schedule_grid import HOURS, grid_rows, legend
from .source_cadence_schema import CADENCE_SETTINGS

RULES = "rules"
SKIPS = "skips"
SWITCH = "skip_around_family_emails"
# The stored settings key (and the posted toggle's name) of the mode the
# schedule was last saved in: "basic" or "advanced" (source_cadence_schema).
MODE = "refresh_schedule_mode"
MODES = ("basic", "advanced")
# The Basic panel's controls. They are posted while Basic is shown, but
# the server reads only the Advanced rows they were written into.
BASIC = "refresh_basic"
BASIC_HOURS = "refresh_basic_hours"
MANAGEMENT = ("TOTAL_FORMS", "INITIAL_FORMS", "MIN_NUM_FORMS", "MAX_NUM_FORMS")
RULE_FIELDS = ("kind", "shape", "every", "start", "last", "at")
SKIP_FIELDS = ("shape", "at", "start", "last")
# The editor's row caps are the stored document's (refresh_rules.MAX_RULES
# and MAX_SKIPS, which explain the bound), so the page can show any schedule
# saved since #936 in full and save it back. One stored earlier with more
# rows still reads (MAX_STORED_ROWS), but must be shortened to be saved here.
RULE_ROWS = MAX_RULES
SKIP_ROWS = MAX_SKIPS

# "every [15 minutes]", "every [hour]", "every [2 hours]": the rule intervals
# refresh_rules.STEPS allows, worded to read as a sentence in the row.
STEP_LABELS = {
    15: _("15 minutes"),
    30: _("30 minutes"),
    60: _("hour"),
    120: _("2 hours"),
    180: _("3 hours"),
    240: _("4 hours"),
    360: _("6 hours"),
    480: _("8 hours"),
    720: _("12 hours"),
}
STEP_CHOICES = tuple((step, STEP_LABELS[step]) for step in STEPS)
# The kind is a hidden field (#1122): its labels are never shown, so they
# are plain and untranslated.
KIND_CHOICES = (("full", "full"), ("quick", "quick"))
RULE_SHAPES = (("every", _("every")), ("at", _("at")))
SKIP_SHAPES = (("at", _("at")), ("range", _("from")))
QUARTER_HOUR_MESSAGE = _("Use :00, :15, :30 or :45.")


@dataclass(frozen=True)
class BasicChoice:
    """One Basic choice: its key, name, plain-words description and rules.

    "every" is one choice with an hours list (``EVERY_HOURS``): its rules
    depend on the hours chosen (``basic_rules``), so ``rules`` is empty.
    """

    key: str
    name: str
    description: str
    rules: tuple = ()


# "Every [N] hours": the hour counts Basic offers, each a whole number of
# repeats a day from midnight, and the one chosen when none is yet.
EVERY_HOURS = (2, 3, 4, 6, 8, 12)
DEFAULT_EVERY_HOURS = 4

# The Basic choices (#1122; admin-portal spec, "Schedule editor"), in their
# order. Each is exactly one Advanced rule set of full refreshes: choosing
# one replaces the rules and clears the skips; none changes the switch.
# Quick updates are not offered while ParishSoft's change list returns
# nothing (#1122, #1147).
BASIC_CHOICES = (
    BasicChoice(
        "nightly",
        _("Nightly only"),
        _("One full refresh a night, at 02:00."),
        ({"kind": "full", "at": "02:00"},),
    ),
    BasicChoice(
        "every",
        _("Every few hours"),
        _("A full refresh every few hours, starting at 00:00."),
    ),
    BasicChoice(
        "business_hours",
        _("Business hours"),
        _("A full refresh at 02:00, then every 2 hours from 08:00 to 18:00."),
        (
            {"kind": "full", "at": "02:00"},
            {"kind": "full", "every": 120, "from": "08:00", "to": "18:00"},
        ),
    ),
)
BASIC_KEYS = frozenset(choice.key for choice in BASIC_CHOICES)


def basic_rules(key, hours=None):
    """The rules of one Basic choice ("every" also takes its ``hours``)."""
    if key == "every":
        return (
            {
                "kind": "full",
                "every": hours * 60,
                "from": "00:00",
                "to": clock(DAY - hours * 60),
            },
        )
    return next(choice.rules for choice in BASIC_CHOICES if choice.key == key)


def _basic_sets():
    """Every Basic rule set: ``(key, hours, rules)``, hours only for "every"."""
    for choice in BASIC_CHOICES:
        if choice.key == "every":
            for hours in EVERY_HOURS:
                yield choice.key, hours, basic_rules("every", hours)
        else:
            yield choice.key, None, choice.rules


def basic_choice(document):
    """The Basic choice equal to a schedule document, as ``(key, hours)``, or None.

    Equal means the same rules in any order and no skips; the switch is
    outside both modes, so it does not matter. A document with a quick
    rule never matches: Basic offers only full refreshes.
    """
    if document is None or document["skips"]:
        return None
    rules = _sorted_rows(document["rules"])
    for key, hours, choice_rules in _basic_sets():
        if rules == _sorted_rows(choice_rules):
            return key, hours
    return None


def rule_initial(rule):
    """A stored rule as its row's initial values."""
    if "at" in rule:
        return {"kind": rule["kind"], "shape": "at", "at": rule["at"]}
    return {
        "kind": rule["kind"],
        "shape": "every",
        "every": rule["every"],
        "start": rule["from"],
        "last": rule["to"],
    }


def skip_initial(skip):
    """A stored skip as its row's initial values."""
    if "at" in skip:
        return {"shape": "at", "at": skip["at"]}
    return {"shape": "range", "start": skip["from"], "last": skip["to"]}


def basic_payload():
    """The Basic rule sets as the page script writes and matches them.

    Each is ``{"key", "hours", "rules"}`` with the rows' initial values, so
    the script has no copy of the choices' rules: this is their one source.
    """
    return [
        {"key": key, "hours": hours, "rules": [rule_initial(rule) for rule in rules]}
        for key, hours, rules in _basic_sets()
    ]


def basic_context():
    """What the editor's Basic panel draws: its choices, hours and payload."""
    return {
        "basic_choices": BASIC_CHOICES,
        "every_hours": EVERY_HOURS,
        "basic_payload": basic_payload(),
    }


def _off_quarter(value):
    """Whether ``HH:MM`` text is off the quarter hour."""
    return minutes(value) % SPACING != 0


class _Row(AdminForm):
    """Shared row behavior: kept times and the quarter-hour rule at each field."""

    def __init__(self, *args, kept=frozenset(), **kwargs):
        """``kept`` are full times stored off the quarter hour (see the module)."""
        super().__init__(*args, **kwargs)
        self.kept = frozenset(kept)
        for name in ("at", "start", "last"):
            self.fields[name].widget.attrs["data-schedule-time"] = name

    def _ignore(self, used):
        """Forget errors of the fields the row's shape does not use.

        The page script disables (and so does not send) the fields a shape
        hides; a value posted there anyway says nothing about the schedule.
        """
        for name in set(self.fields) - {"kind", "shape", *used}:
            self._errors.pop(name, None)

    def _time(self, name, *, allow_kept=False):
        """The ``HH:MM`` value of a time field, checked for the quarter hour.

        A missing time is reported at the field; a time off the quarter hour
        too, unless ``allow_kept`` and it is a kept time.
        """
        value = hhmm(self.cleaned_data.get(name))
        if value is None:
            if name not in self.errors:
                self.add_error(
                    name,
                    forms.ValidationError(
                        gettext("Enter a time."), code="time_missing"
                    ),
                )
            return None
        if _off_quarter(value) and not (allow_kept and value in self.kept):
            self.add_error(
                name, forms.ValidationError(QUARTER_HOUR_MESSAGE, code="quarter_hour")
            )
            return None
        return value


class RuleForm(_Row):
    """One rule row: "every [interval] from [time] to [time]" or "at [time]".

    The kind is a hidden field: every row the page offers is a full refresh,
    and a stored quick rule is shown read-only and posted back as it is.
    """

    kind = forms.ChoiceField(
        label=_("Kind"),
        choices=KIND_CHOICES,
        initial="full",
        # A setting, though hidden: the change gate counts it (ui-v1.js).
        widget=forms.HiddenInput(attrs={"data-change-setting": True}),
    )
    shape = forms.ChoiceField(label=_("Repeat"), choices=RULE_SHAPES, initial="at")
    every = forms.TypedChoiceField(
        label=_("Interval"),
        choices=STEP_CHOICES,
        coerce=int,
        required=False,
        initial=60,
    )
    start = FlexibleTimeField(label=_("From"), required=False)
    last = FlexibleTimeField(label=_("To"), required=False)
    at = FlexibleTimeField(label=_("At"), required=False)

    def clean(self):
        """The rule the row describes, as ``refresh_rules`` stores it (``rule``)."""
        data = super().clean()
        self.rule = None
        kind, shape = data.get("kind"), data.get("shape")
        if kind is None or shape is None:
            return data
        self._ignore(("at",) if shape == "at" else ("every", "start", "last"))
        if shape == "at":
            # A kept off-quarter-hour time stays allowed as a single full time.
            at = self._time("at", allow_kept=kind == "full")
            if at is not None:
                self.rule = {"kind": kind, "at": at}
            return data
        if data.get("every") in (None, "") and "every" not in self.errors:
            self.add_error(
                "every",
                forms.ValidationError(gettext("Choose how often."), code="required"),
            )
        start, last = self._time("start"), self._time("last")
        if start is not None and last is not None and last < start:
            self.add_error(
                None,
                forms.ValidationError(
                    gettext(
                        "A rule can't cross midnight. Use two rules: one up to 23:45 "
                        "and one from 00:00."
                    ),
                    code="crosses_midnight",
                ),
            )
        elif start is not None and last is not None and not self.errors:
            self.rule = {
                "kind": kind,
                "every": data["every"],
                "from": start,
                "to": last,
            }
        return data


class SkipForm(_Row):
    """One skip row: "Skip at [time]" or "Skip from [time] to [time]"."""

    shape = forms.ChoiceField(label=_("Skip"), choices=SKIP_SHAPES, initial="at")
    at = FlexibleTimeField(label=_("At"), required=False)
    start = FlexibleTimeField(label=_("From"), required=False)
    last = FlexibleTimeField(label=_("To"), required=False)

    def clean(self):
        """The skip the row describes, as ``refresh_rules`` stores it (``skip``)."""
        data = super().clean()
        self.skip = None
        shape = data.get("shape")
        if shape is not None:
            self._ignore(("at",) if shape == "at" else ("start", "last"))
        if shape == "at":
            # A kept time may be skipped by name, like any other time.
            at = self._time("at", allow_kept=True)
            if at is not None:
                self.skip = {"at": at}
        elif shape == "range":
            start = self._time("start", allow_kept=True)
            last = self._time("last", allow_kept=True)
            if start is not None and last is not None and last <= start:
                self.add_error(
                    None,
                    forms.ValidationError(
                        gettext(
                            "A skip can't cross midnight, and its end (which is not "
                            "skipped) must be after its start. Use two skips: one "
                            "that ends at 23:45, with a skip at 23:45, and one from "
                            "00:00."
                        ),
                        code="crosses_midnight",
                    ),
                )
            elif start is not None and last is not None and not self.errors:
                self.skip = {"from": start, "to": last}
        return data


class RowSet(AdminFormSet):
    """Every posted row is checked: an unchanged blank row is a problem too.

    Django leaves an unchanged extra row unvalidated (``empty_permitted``);
    here every row the page posts says something about the schedule, so a
    blank one is reported at its fields instead of being dropped.
    """

    def __init__(self, *args, kept=frozenset(), **kwargs):
        """Pass the kept times to each row."""
        self.kept = frozenset(kept)
        super().__init__(*args, **kwargs)

    def _construct_form(self, i, **kwargs):
        """Validate every row; give it the kept times."""
        return super()._construct_form(i, empty_permitted=False, kept=self.kept)

    @property
    def empty_form(self):
        """The row the page script clones for "Add a rule" or "Add a skip"."""
        form = self.form(
            auto_id=self.auto_id,
            prefix=self.add_prefix("__prefix__"),
            empty_permitted=True,
            use_required_attribute=False,
            renderer=self.renderer,
            kept=self.kept,
        )
        self.add_fields(form, None)
        return form


class RuleRows(RowSet):
    """The rule rows, refusing more than ``RULE_ROWS`` in plain words."""

    default_error_messages = {
        "too_many_forms": ngettext_lazy(
            "A schedule has at most %(num)d rule.",
            "A schedule has at most %(num)d rules.",
            "num",
        )
    }


class SkipRows(RowSet):
    """The skip rows, refusing more than ``SKIP_ROWS`` in plain words."""

    default_error_messages = {
        "too_many_forms": ngettext_lazy(
            "A schedule has at most %(num)d skip.",
            "A schedule has at most %(num)d skips.",
            "num",
        )
    }


Rules = formset_factory(
    RuleForm,
    formset=RuleRows,
    extra=0,
    max_num=RULE_ROWS,
    validate_max=True,
    absolute_max=RULE_ROWS,
)
Skips = formset_factory(
    SkipForm,
    formset=SkipRows,
    extra=0,
    max_num=SKIP_ROWS,
    validate_max=True,
    absolute_max=SKIP_ROWS,
)


def posted_names():
    """Every field name the editor may post, for the page's closed field check."""
    names = {SWITCH, MODE, BASIC, BASIC_HOURS}
    for prefix, fields, most in (
        (RULES, RULE_FIELDS, RULE_ROWS),
        (SKIPS, SKIP_FIELDS, SKIP_ROWS),
    ):
        names |= {f"{prefix}-{name}" for name in MANAGEMENT}
        names |= {
            f"{prefix}-{index}-{name}" for index in range(most) for name in fields
        }
    return frozenset(names)


def _canonical(document):
    """A schedule document with its rows in a fixed order, for comparison.

    The order of rows never changes what a schedule runs, so rows the page
    showed, posted back in another order, are no change.
    """
    return {
        "rules": _sorted_rows(document["rules"]),
        "skips": _sorted_rows(document["skips"]),
        SWITCH: document[SWITCH],
    }


def _sorted_rows(rows):
    """Schedule rows in a fixed order, since their order changes nothing."""
    return sorted(rows, key=lambda row: json.dumps(row, sort_keys=True))


def kept_times(shown):
    """The full times stored off the quarter hour, which the page keeps.

    Only a "Full at" row of the schedule as shown can hold one (an existing
    schedule's listed times); an Administrator may keep them but not add new
    ones (``check_schedule``'s ``kept``).
    """
    return frozenset(
        rule["at"]
        for rule in shown["rules"]
        if rule["kind"] == "full" and "at" in rule and _off_quarter(rule["at"])
    )


@dataclass(frozen=True)
class RowProblem:
    """One problem: where it is shown, its words and its short form.

    ``scope`` is "rules" or "skips" with the row ``index``, or None for the
    schedule as a whole. ``short`` is the phrase the line beside Save lists.
    """

    scope: str | None
    index: int | None
    text: str
    short: str
    # An entry the time field itself refuses as it is typed (the shared
    # time entry's reading line says so): not repeated under the row.
    at_field: bool = False
    # The validator's code ("too_close", "no_full", ...), or "" for a row's
    # own error.
    code: str = ""

    @property
    def anchor(self):
        """The id of the row (or the editor) the problem links to."""
        return f"{self.scope}-{self.index}" if self.scope else "refresh-schedule"


def _series(values):
    """``["a", "b", "c"]`` as "a, b and c"."""
    values = list(values)
    if len(values) < 2:
        return "".join(values)
    return gettext("%(first)s and %(last)s") % {
        "first": ", ".join(values[:-1]),
        "last": values[-1],
    }


def _times_words(values):
    """Sorted ``HH:MM`` times in words, with long even runs as ranges.

    Four or more times an equal step apart read as "every 15 minutes from
    00:15 to 07:45", so a review of a quarter-hour schedule stays short.
    """
    numbers = [minutes(value) for value in values]
    parts, start = [], 0
    while start < len(numbers):
        end = start + 1
        if end < len(numbers):
            step = numbers[end] - numbers[start]
            while end + 1 < len(numbers) and numbers[end + 1] - numbers[end] == step:
                end += 1
            if end - start + 1 >= 4:
                parts.append(
                    gettext("every %(step)s from %(start)s to %(end)s")
                    % {
                        "step": STEP_LABELS.get(step)
                        or gettext("%(count)d minutes") % {"count": step},
                        "start": clock(numbers[start]),
                        "end": clock(numbers[end]),
                    }
                )
                start = end + 1
                continue
        parts.append(clock(numbers[start]))
        start += 1
    return _series(parts)


def _generated(rule):
    """The times (minutes) one rule gives before skips and coverage."""
    if "at" in rule:
        return {minutes(rule["at"])}
    return set(range(minutes(rule["from"]), minutes(rule["to"]) + 1, rule["every"]))


def _noun(kind):
    """ "full refresh" or "quick update"."""
    return gettext("full refresh") if kind == "full" else gettext("quick update")


def _skip_words(skip):
    """A skip row in words: "the skip at 12:30" or "the skip from 12:00 to 13:00"."""
    if "at" in skip:
        return gettext("the skip at %(time)s") % {"time": skip["at"]}
    return gettext("the skip from %(start)s to %(end)s") % {
        "start": skip["from"],
        "end": skip["to"],
    }


def schedule_problems(document, *, kept):
    """Every problem of a valid-shaped ``document``, placed at its row.

    Runs the shared validator (``check_schedule``) and places each of its
    problems where the admin-portal spec says: spacing at the row giving
    the later time, an off-quarter-hour time at a row giving it, a skip that
    matches nothing or removes the last full time at that skip.
    """
    result = daily_times(document)
    settings = stored_settings(document) if result.full else {"refresh_rules": document}
    rules = document["rules"]
    generated = [_generated(rule) for rule in rules]
    full = {minutes(value) for value in result.full}

    def giving(value):
        """The first row giving the daily time ``value`` as its final kind."""
        kind = "full" if minutes(value) in full else "quick"
        for index, rule in enumerate(rules):
            if rule["kind"] == kind and minutes(value) in generated[index]:
                return index
        return next(
            (i for i, times in enumerate(generated) if minutes(value) in times), None
        )

    found = []
    for problem in check_schedule(settings, kept=kept):
        start = len(found)
        if problem.code == "too_close":
            earlier, later = problem.times
            gap = (minutes(later) - minutes(earlier)) % DAY
            kind = "full" if minutes(earlier) in full else "quick"
            found.append(
                RowProblem(
                    RULES,
                    giving(later),
                    gettext(
                        "%(later)s is only %(gap)d minutes after the %(earlier)s "
                        "%(noun)s; refreshes must be at least 15 minutes apart."
                    )
                    % {
                        "later": later,
                        "gap": gap,
                        "earlier": earlier,
                        "noun": _noun(kind),
                    },
                    gettext("%(later)s is too close to %(earlier)s")
                    % {"later": later, "earlier": earlier},
                )
            )
        elif problem.code == "off_quarter":
            (value,) = problem.times
            found.append(
                RowProblem(
                    RULES,
                    giving(value),
                    gettext("%(time)s is not on the quarter hour. ") % {"time": value}
                    + str(QUARTER_HOUR_MESSAGE),
                    gettext("%(time)s is not on the quarter hour") % {"time": value},
                )
            )
        elif problem.code == "unmatched_skip":
            skip = document["skips"][problem.index]
            found.append(
                RowProblem(
                    SKIPS,
                    problem.index,
                    gettext("This matches no refresh."),
                    gettext("%(skip)s matches no refresh")
                    % {"skip": _skip_words(skip)},
                )
            )
        elif problem.code == "no_full":
            found.extend(_no_full(document))
        elif problem.code == "too_many":
            found.append(
                RowProblem(
                    None,
                    None,
                    gettext(
                        "This gives more than %(most)d refreshes a day, the most "
                        "there is room for 15 minutes apart."
                    )
                    % {"most": MAX_DAILY_TIMES},
                    gettext("too many refreshes a day"),
                )
            )
        # "rules_mismatch" cannot arise: the lists checked are the rules' own.
        found[start:] = [replace(item, code=problem.code) for item in found[start:]]
    return found


def _no_full(document):
    """Where "no full refresh a day" is shown: at each skip that removed one.

    Without any full rule at all, at the editor as a whole.
    """
    full = set()
    for rule in document["rules"]:
        if rule["kind"] == "full":
            full |= _generated(rule)
    found = []
    for index, skip in enumerate(document["skips"]):
        removed = sorted(value for value in full if _skipped(skip, value))
        if not removed:
            continue
        times = _series(clock(value) for value in removed)
        text = (
            gettext(
                "This skips %(times)s, the only full refresh; keep at least one "
                "full refresh a day."
            )
            if len(full) == 1
            else gettext(
                "This skips the full refreshes at %(times)s; keep at least one "
                "full refresh a day."
            )
        )
        found.append(
            RowProblem(
                SKIPS,
                index,
                text % {"times": times},
                gettext("%(skip)s leaves no full refresh")
                % {"skip": _skip_words(skip)},
            )
        )
    if not found:
        found.append(
            RowProblem(
                None,
                None,
                gettext("Add a rule: keep at least one full refresh a day."),
                gettext("add a full refresh"),
            )
        )
    return found


TIME_FIELDS = frozenset({"at", "start", "last"})
# The codes of the form's own time checks (#1437), which the row shows: any
# other error at a time field is the time entry's own, shown at the field.
ROW_CODES = frozenset({"time_missing", "quarter_hour"})


def _row_errors(scope, index, form):
    """A row's own errors (an unread or missing time, a range crossing
    midnight) as problems shown at the row, each naming its field.

    A time the parser cannot read carries the parser's code; the time
    entry already shows that refusal at its field, so it is marked
    ``at_field`` and only the line beside Save repeats it.
    """
    row = (
        gettext("Rule %(number)d") if scope == RULES else gettext("Skip %(number)d")
    ) % {"number": index + 1}
    found = []
    for name, errors in form.errors.as_data().items():
        for error in errors:
            message = " ".join(error.messages)
            text = (
                message
                if name == "__all__"
                else gettext("%(field)s: %(message)s")
                % {"field": form.fields[name].label, "message": message}
            )
            found.append(
                RowProblem(
                    scope,
                    index,
                    text,
                    f"{row}: {text}",
                    at_field=name in TIME_FIELDS and error.code not in ROW_CODES,
                )
            )
    return found


class ScheduleEditor:
    """The refresh schedule's rows, switch and verdict for one settings page.

    ``stored`` are the ParishSoft integration's stored settings. Unbound,
    the editor shows them as rules; bound (``data``), it reads the posted
    rows with the same forms whichever request posted them: the live check
    and the save.
    """

    def __init__(self, data=None, *, stored):
        """Read the stored schedule and, when posted, the edited one."""
        self.stored = stored
        self.shown = converted_rules(stored)
        self.converted = "refresh_rules" not in stored
        self.kept = kept_times(self.shown)
        self.posted = data is not None and f"{RULES}-TOTAL_FORMS" in data
        bound = data if self.posted else None
        self.rules = Rules(
            bound,
            prefix=RULES,
            initial=None
            if self.posted
            else [rule_initial(r) for r in self.shown["rules"]],
            kept=self.kept,
        )
        self.skips = Skips(
            bound,
            prefix=SKIPS,
            initial=None
            if self.posted
            else [skip_initial(s) for s in self.shown["skips"]],
            kept=self.kept,
        )
        self.switch = bound.get(SWITCH) == "on" if self.posted else self.shown[SWITCH]
        self.posted_mode = bound.get(MODE) if self.posted else None
        self._checked = None

    def _rows_valid(self):
        """Whether every rule and skip row reads (the formsets validate)."""
        return self.rules.is_valid() and self.skips.is_valid()

    def document(self):
        """The edited ``refresh_rules`` document, or None while a row is unread.

        Unposted, the schedule as shown.
        """
        if not self.posted:
            return self.shown
        if not self._rows_valid():
            return None
        document = {
            "rules": [form.rule for form in self.rules.forms],
            "skips": [form.skip for form in self.skips.forms],
            SWITCH: self.switch,
        }
        return document if valid_shape(document) else None

    @property
    def changed(self):
        """Whether the posted schedule differs from the one the page showed."""
        if not self.posted:
            return False
        document = self.document()
        return document is None or _canonical(document) != _canonical(self.shown)

    def problems(self):
        """Every problem, placed at its row; computed once.

        Row errors (an unread time, a range crossing midnight) come first, as
        the line beside Save names them; then the validator's.
        """
        if self._checked is not None:
            return self._checked
        found = []
        if self.posted:
            for scope, formset in ((RULES, self.rules), (SKIPS, self.skips)):
                if not formset.is_valid():
                    for message in formset.non_form_errors():
                        found.append(RowProblem(None, None, message, message))
                for index, form in enumerate(formset.forms):
                    found.extend(_row_errors(scope, index, form))
            if not self.rules.forms:
                found.append(
                    RowProblem(
                        None,
                        None,
                        gettext("Add a rule: keep at least one full refresh a day."),
                        gettext("add a rule"),
                    )
                )
        found.extend(self._validator_problems())
        self._checked = found
        return found

    def _validator_problems(self):
        """The shared validator's problems, at the rows as numbered on the page.

        While some rows cannot be read (a half-typed time), the rest are
        still checked, so their problems do not vanish and come back with
        each keystroke elsewhere; "no full refresh" waits until every rule
        reads, since the unread one may be it.
        """
        document = self.document()
        if document is not None:
            return schedule_problems(document, kept=self.kept)
        if not self.posted or not self.rules.forms:
            return []
        rules = [
            (index, form.rule)
            for index, form in enumerate(self.rules.forms)
            if getattr(form, "rule", None) is not None
        ]
        skips = [
            (index, form.skip)
            for index, form in enumerate(self.skips.forms)
            if getattr(form, "skip", None) is not None
        ]
        readable = {
            "rules": [rule for _, rule in rules],
            "skips": [skip for _, skip in skips],
            SWITCH: self.switch,
        }
        if not valid_shape(readable):
            return []
        places = {RULES: [i for i, _ in rules], SKIPS: [i for i, _ in skips]}
        unread = len(rules) < len(self.rules.forms)
        return [
            replace(problem, index=places[problem.scope][problem.index])
            if problem.scope
            else problem
            for problem in schedule_problems(readable, kept=self.kept)
            if not (unread and problem.code in ("no_full", "too_many"))
        ]

    @property
    def blocking(self):
        """Whether the schedule's problems block saving: only a changed one's."""
        return self.changed and bool(self.problems())

    def is_valid(self):
        """Whether the schedule may be saved with the page's other settings."""
        return not self.blocking

    def settings(self):
        """The stored schedule settings this save writes.

        An unchanged schedule keeps exactly the stored keys (none, for an
        existing schedule that never stored them); a changed one is derived
        from its rules (``stored_settings``), so nothing posted can set the
        stored lists directly.
        """
        if not self.changed:
            return {
                name: self.stored[name]
                for name in CADENCE_SETTINGS
                if name in self.stored
            }
        if self.blocking:
            raise ValueError("A schedule with problems cannot be saved.")
        document = self.document()
        # Basic only when it was saved from Basic and Basic can show it.
        basic = self.posted_mode == "basic" and basic_choice(document) is not None
        return stored_settings(document) | {MODE: "basic" if basic else "advanced"}

    @property
    def basic(self):
        """The Basic choice equal to the edited schedule, ``(key, hours)``, or None."""
        return basic_choice(self.document())

    @property
    def mode(self):
        """The mode the editor is drawn in: "basic" or "advanced".

        Advanced whenever the schedule has no Basic equivalent (a custom
        schedule, a quick rule, a row that cannot be read); else the mode
        posted (a refused save drawn again) or, unposted, the mode last
        saved, and Basic when none was saved (ui-conventions spec, "Which
        mode opens").
        """
        if self.basic is None:
            return "advanced"
        wanted = self.posted_mode if self.posted else self.stored.get(MODE)
        return "advanced" if wanted == "advanced" else "basic"

    @property
    def has_quick(self):
        """Whether a rule row is a quick rule (shown read-only, with Remove)."""
        formset = self.rules
        return any(form["kind"].value() == "quick" for form in formset.forms)

    def row_problems(self, scope):
        """Each row's problems, but those its time fields show themselves."""
        found = {}
        for problem in self.problems():
            if problem.scope == scope and not problem.at_field:
                found.setdefault(problem.index, []).append(problem.text)
        return found

    def general_problems(self):
        """Problems of the schedule as a whole, said in the line beside Save."""
        return [p.text for p in self.problems() if p.scope is None]

    def rows(self, scope):
        """``(form, problems)`` for each row of ``scope``, for the template."""
        formset = self.rules if scope == RULES else self.skips
        problems = self.row_problems(scope)
        return [(form, problems.get(index, [])) for index, form in enumerate(formset)]

    def notes(self):
        """Each rule row's note: a kept off-quarter-hour full time says so."""
        document = self.document()
        if document is None:
            return {}
        return {
            index: gettext("Kept from the earlier schedule (not on the quarter hour).")
            for index, rule in enumerate(document["rules"])
            if rule["kind"] == "full" and "at" in rule and rule["at"] in self.kept
        }

    def daily(self):
        """The daily times the edited rules give, or None while a row is unread."""
        document = self.document()
        return None if document is None else daily_times(document)


def _duration(value):
    """A timedelta as "1 h 34 min" or "12 min" (to the minute)."""
    total = round(value.total_seconds() / 60)
    hours, rest = divmod(total, 60)
    if hours:
        return gettext("%(hours)d h %(minutes)d min") % {
            "hours": hours,
            "minutes": rest,
        }
    return gettext("%(minutes)d min") % {"minutes": rest}


def _length(value):
    """A run length as "7.3 minutes"."""
    return gettext("%(minutes)s minutes") % {
        "minutes": f"{value.total_seconds() / 60:.1f}"
    }


WINDOW_WORDS = {
    "reminder_preparing": _("reminder being prepared"),
    "reminder_sending": _("reminder being sent"),
    "initial_sending": _("invitation being sent"),
}


def _time_line(entry, timezone):
    """One preview line: (text, struck) for a time on a preview day."""
    noun = _noun(entry.kind)
    if entry.status == "covered":
        return (
            gettext("%(time)s %(noun)s covered by the %(by)s full refresh")
            % {"time": entry.time, "noun": noun, "by": entry.by},
            True,
        )
    if entry.status == "skipped":
        return (
            gettext("%(time)s %(noun)s skipped: %(cause)s")
            % {"time": entry.time, "noun": noun, "cause": WINDOW_WORDS[entry.window]},
            True,
        )
    if entry.status == "joined":
        return (
            gettext("%(time)s %(noun)s runs with the %(by)s one: clocks go forward")
            % {"time": entry.time, "noun": noun, "by": entry.by},
            True,
        )
    text = f"{entry.time} {noun}"
    if entry.nightly:
        text = gettext("%(time)s nightly full refresh") % {"time": entry.time}
    if entry.shift == "forward":
        text += gettext(": runs at %(time)s, clocks go forward") % {
            "time": wall_hhmm(entry.due_at, timezone)
        }
    elif entry.shift == "back":
        text += gettext(": runs once, clocks go back")
    elif entry.nightly and entry.window:
        text += gettext(": runs even during the %(cause)s") % {
            "cause": WINDOW_WORDS[entry.window]
        }
    return text, False


def _day_counts(full, quick, spent, *, with_quick):
    """One preview day's counts: "3 full refreshes, about 22 min of ParishSoft time".

    Quick updates are counted only for a schedule that still has a quick
    rule (``with_quick``); the editor offers none (#1122).
    """
    values = {
        "full": ngettext("%(count)d full refresh", "%(count)d full refreshes", full)
        % {"count": full},
        "time": _duration(spent),
    }
    if not with_quick:
        return gettext("%(full)s, about %(time)s of ParishSoft time") % values
    values["quick"] = ngettext(
        "%(count)d quick update", "%(count)d quick updates", quick
    ) % {"count": quick}
    return gettext("%(full)s, %(quick)s, about %(time)s of ParishSoft time") % values


def preview_context(preview, *, timezone, today, daily):
    """What the per-day list and the summary show for a ``SchedulePreview``."""
    days = []
    with_quick = bool(daily.quick)
    for index, day in enumerate(preview.days):
        full, quick = len(day.runs("full")), len(day.runs("quick"))
        spent, _share = preview.cost.per_day[index]
        days.append(
            {
                "day": day.day,
                "weekday": WEEKDAYS[day.day.weekday()],
                "today": day.day == today,
                "change": day.change,
                "counts": _day_counts(full, quick, spent, with_quick=with_quick),
                "times": [
                    {
                        "text": text,
                        "struck": struck,
                        "kind": entry.kind,
                        "due_at": (
                            entry.due_at.astimezone(UTC).isoformat()
                            if entry.due_at is not None and not struck
                            else None
                        ),
                    }
                    for entry in day.times
                    for text, struck in (_time_line(entry, timezone),)
                ],
            }
        )
    cost, durations = preview.cost, preview.cost.durations
    typical = gettext(" (typical; not yet measured)")
    summary = (
        gettext(
            "About %(time)s of ParishSoft time a day (%(share)s of the day): "
            "%(full)s of about %(full_length)s%(full_typical)s and %(quick)s of "
            "about %(quick_length)s%(quick_typical)s."
        )
        if with_quick
        else gettext(
            "About %(time)s of ParishSoft time a day (%(share)s of the day): "
            "%(full)s of about %(full_length)s%(full_typical)s."
        )
    ) % {
        "time": _duration(cost.average),
        "share": f"{cost.share * 100:.1f}%",
        "full": ngettext(
            "%(count)d full refresh", "%(count)d full refreshes", len(daily.full)
        )
        % {"count": len(daily.full)},
        "full_length": _length(durations.full),
        "full_typical": "" if durations.full_measured else typical,
        "quick": ngettext(
            "%(count)d quick update", "%(count)d quick updates", len(daily.quick)
        )
        % {"count": len(daily.quick)},
        "quick_length": _length(durations.quick),
        "quick_typical": "" if durations.quick_measured else typical,
    }
    fresh = preview.freshness
    margin = round(fresh.margin.total_seconds() / 60)
    alarm = gettext(
        "If a full refresh is more than %(minutes)d minutes late, Administrators "
        "are alerted."
    ) % {"minutes": margin}
    if fresh.longest is None:
        freshness = gettext("Fewer than two full refreshes run in the next seven days.")
    else:
        freshness = gettext(
            "New ParishSoft data arrives at least every %(wait)s "
            "(%(start)s to %(end)s)."
        ) % {
            "wait": _wait(fresh.longest),
            "start": wall_hhmm(fresh.start, timezone),
            "end": wall_hhmm(fresh.end, timezone),
        }
    return {
        "days": days,
        "line": gettext("About %(time)s of ParishSoft time a day.")
        % {"time": _duration(cost.average)},
        "summary": summary,
        "warning": cost.warning,
        "freshness": f"{freshness} {alarm}",
        "estimated_windows": bool(preview.windows),
    }


def _wait(value):
    """The longest wait in words: "8 hours", "1 day" or "2 h 30 min"."""
    hours, rest = divmod(round(value.total_seconds() / 60), 60)
    if rest:
        return _duration(value)
    if hours % 24 == 0:
        days = hours // 24
        return ngettext("%(count)d day", "%(count)d days", days) % {"count": days}
    return ngettext("%(count)d hour", "%(count)d hours", hours) % {"count": hours}


def status_line(editor, line=None):
    """The line beside Save, and whether the schedule blocks saving.

    Returns ``(problems, text)``: ``problems`` are ``(anchor, short)`` pairs
    the line links to (empty when there are none), and ``text`` the words
    around them, or the cost line when nothing is wrong. Problems of the
    schedule as a whole have no row to link to: the line says them in full
    after the links (``EditorView.general``), and the count includes them.
    """
    problems = editor.problems()
    links = [(p.anchor, p.short) for p in problems if p.scope is not None]
    if not problems:
        return [], line or ""
    count = len(problems)
    if editor.changed:
        text = ngettext(
            "Fix %(count)d problem before saving:",
            "Fix %(count)d problems before saving:",
            count,
        ) % {"count": count}
    else:
        text = ngettext(
            "Your current schedule has %(count)d problem, which blocks only a "
            "change to the schedule; saving other settings keeps it as it is:",
            "Your current schedule has %(count)d problems, which block only a "
            "change to the schedule; saving other settings keeps it as it is:",
            count,
        ) % {"count": count}
    return links, text


@dataclass
class EditorView:
    """Everything the editor's template and the live check draw."""

    editor: ScheduleEditor
    timezone: str
    preview: dict | None = None
    links: list = field(default_factory=list)
    status: str = ""

    @property
    def nightly(self):
        """The nightly full time the edited rules give, or None."""
        daily = self.editor.daily()
        return daily.full[0] if daily is not None and daily.full else None

    @property
    def blocking(self):
        """Whether the schedule's problems keep Save unavailable."""
        return self.editor.blocking

    @property
    def general(self):
        """Problems of the schedule as a whole, said in full beside Save.

        They are never drawn above the rows: a problem appearing there would
        push the rows, Add and Save under the pointer (#736).
        """
        return self.editor.general_problems()

    def _rows(self, scope):
        """Each row of ``scope`` with its number, messages and note.

        A quick rule row also carries ``quick``: its words and the hidden
        fields that post it back unchanged (``_quick_row``).
        """
        formset = self.editor.rules if scope == RULES else self.editor.skips
        problems = self.editor.row_problems(scope)
        notes = self.editor.notes() if scope == RULES else {}
        return [
            {
                "form": form,
                "index": index,
                "number": index + 1,
                "problems": problems.get(index, []),
                "note": notes.get(index),
                "quick": _quick_row(form) if scope == RULES else None,
            }
            for index, form in enumerate(formset.forms)
        ]

    def _empty(self, scope):
        """The blank row the page script clones, with ``__prefix__`` names."""
        formset = self.editor.rules if scope == RULES else self.editor.skips
        return {
            "form": formset.empty_form,
            "index": "__prefix__",
            "number": 0,
            "problems": [],
            "note": None,
        }

    @property
    def empty_rule_row(self):
        """The rule row "Add a rule" clones."""
        return self._empty(RULES)

    @property
    def empty_skip_row(self):
        """The skip row "Add a skip" clones."""
        return self._empty(SKIPS)

    @property
    def rule_rows(self):
        """The rule rows, for the template."""
        return self._rows(RULES)

    @property
    def skip_rows(self):
        """The skip rows, for the template."""
        return self._rows(SKIPS)

    @property
    def mode(self):
        """The mode the editor is drawn in (``ScheduleEditor.mode``)."""
        return self.editor.mode

    @property
    def basic(self):
        """The Basic panel's choice and hours as drawn: ``{"key", "hours"}``.

        The choice equal to the schedule, or none chosen; the hours list
        shows the matching count, else ``DEFAULT_EVERY_HOURS``.
        """
        key, hours = self.editor.basic or (None, None)
        return {"key": key, "hours": hours or DEFAULT_EVERY_HOURS}

    @property
    def has_quick(self):
        """Whether the rows include a stored quick rule (``ScheduleEditor``)."""
        return self.editor.has_quick

    @property
    def text_lists(self):
        """The full daily times as "Edit as text" shows them."""
        daily = self.editor.daily()
        if daily is None:
            return None
        return {"full": ", ".join(daily.full)}


def _quick_row(form):
    """A quick rule row's words and hidden fields, or None for a full rule.

    The editor no longer offers quick updates (#1122), so a stored quick
    rule is drawn read-only: its words ("Quick update every hour from 00:00
    to 23:00") and ``(name, value)`` hidden fields that post it back as it
    is, until "Remove quick updates" takes it out.
    """
    if form["kind"].value() != "quick":
        return None
    shape = form["shape"].value()
    used = ("kind", "shape", "at") if shape == "at" else RULE_FIELDS[:5]
    fields = [
        (form[name].html_name, "" if form[name].value() is None else form[name].value())
        for name in used
    ]
    values = dict(fields)
    prefix = form.prefix + "-"
    value = {name.removeprefix(prefix): text for name, text in values.items()}
    if shape == "at":
        words = gettext("Quick update at %(time)s") % {"time": value["at"]}
    else:
        # Raw posted text (a refused save drawn again): never trusted as a
        # number, so a crafted value is shown as sent instead of failing.
        every = str(value["every"])
        step = STEP_LABELS.get(int(every), every) if every.isdecimal() else every
        words = gettext("Quick update every %(step)s from %(start)s to %(end)s") % {
            "step": step,
            "start": value["start"],
            "end": value["last"],
        }
    return {"words": words, "fields": fields}


def editor_view(editor, *, timezone, now, preview_reader):
    """Compute the editor's preview, summary and status line.

    ``preview_reader`` is ``schedule_preview.schedule_preview`` (or a stand-in
    in tests); it is called only with a document of the closed shape.
    """
    view = EditorView(editor, timezone)
    document = editor.document()
    daily = editor.daily()
    if document is not None and daily.full:
        preview = preview_reader(document, now=now, timezone=timezone)
        today = now.astimezone(ZoneInfo(timezone)).date()
        view.preview = preview_context(
            preview, timezone=timezone, today=today, daily=daily
        )
        # The seven-day grid (slice 4) draws the same days as the list: each
        # day's dict carries its 96 cells, and the legend names the shadings.
        rows = grid_rows(preview, timezone=timezone, now=now)
        for shown, row in zip(view.preview["days"], rows, strict=True):
            shown["cells"] = row["cells"]
        view.preview["legend"] = legend(rows)
        # The dot of a stored quick rule is named only while one runs: the
        # editor offers none (#1122).
        view.preview["other_mark"] = (
            gettext("Quick update")
            if any(cell["quick"] for row in rows for cell in row["cells"])
            else None
        )
        view.preview["hours"] = HOURS
    view.links, view.status = status_line(
        editor, view.preview["line"] if view.preview else None
    )
    return view


def _whole_hour_offsets(timezone):
    """Whether ``timezone``'s offset from UTC is a whole number of hours all year."""
    zone = ZoneInfo(timezone)
    year = datetime.now(UTC).year
    return all(
        datetime(year, month, 1, tzinfo=zone).utcoffset() % timedelta(hours=1)
        == timedelta()
        for month in (1, 7)
    )


def _has_daylight_saving(timezone):
    """Whether ``timezone``'s offset changes during the year."""
    zone = ZoneInfo(timezone)
    year = datetime.now(UTC).year
    return (
        len({datetime(year, month, 1, tzinfo=zone).utcoffset() for month in (1, 7)}) > 1
    )


def differences(stored, document, *, timezone):
    """The review's list of what a changed schedule changes, in words.

    The full and quick times added and removed, the nightly time if it
    moves, the switch if it changes, and, for an existing schedule, each
    difference the background-processing spec's "Stored schedule and
    upgrade" names. Never the stored settings themselves.
    """
    shown = converted_rules(stored)
    before, after = daily_times(shown), daily_times(document)
    found = []
    for old, new, added_words, removed_words in (
        (
            before.full,
            after.full,
            gettext("Full refreshes added: %(times)s."),
            gettext("Full refreshes removed: %(times)s."),
        ),
        (
            before.quick,
            after.quick,
            gettext("Quick updates added: %(times)s."),
            gettext("Quick updates removed: %(times)s."),
        ),
    ):
        added = sorted(set(new) - set(old))
        removed = sorted(set(old) - set(new))
        if added:
            found.append(added_words % {"times": _times_words(added)})
        if removed:
            found.append(removed_words % {"times": _times_words(removed)})
    schedule = refresh_settings(stored)
    old_nightly = schedule["nightly_time"]
    if after.full and after.full[0] != old_nightly:
        found.append(
            gettext(
                "The nightly full refresh, the one that runs even while Family "
                "emails are being sent, moves from %(old)s to %(new)s."
            )
            % {"old": old_nightly, "new": after.full[0]}
        )
    if document[SWITCH] != shown[SWITCH]:
        found.append(
            gettext(
                "Turns on skipping refreshes around Family emails: refreshes other "
                "than the nightly one are skipped while a reminder is being "
                "prepared and while a Family email is being sent."
            )
            if document[SWITCH]
            else gettext(
                "Turns off skipping refreshes around Family emails: every "
                "refresh runs, even while a Family email is being sent."
            )
        )
    if "refresh_rules" not in stored:
        found.extend(_upgrade_differences(schedule, timezone))
    if not found:
        found.append(
            gettext(
                "The refresh times stay the same; only the rules that describe "
                "them change."
            )
        )
    return found


def _upgrade_differences(schedule, timezone):
    """The differences of changing an existing schedule, per the upgrade spec."""
    found = []
    frequency, delta = schedule["frequency"], schedule["delta_refresh"]
    if frequency in ("hourly", "quarter_hour"):
        found.append(
            gettext(
                "Full refreshes every %(step)s become listed times. Each now "
                "records its own time, so the refresh due when this change takes "
                "effect may run once more, and every full refresh except the "
                "nightly one waits for a Family email send to finish."
            )
            % {
                "step": gettext("hour")
                if frequency == "hourly"
                else gettext("15 minutes")
            }
        )
    hourly = "hourly" in (frequency, delta)
    if hourly and not _whole_hour_offsets(timezone):
        found.append(
            gettext(
                "The parish's time zone, %(zone)s, is not a whole number of hours "
                "from UTC, so hourly refreshes move from the UTC hour to the "
                "parish's own hour."
            )
            % {"zone": timezone}
        )
    repeating = {"hourly", "quarter_hour"} & {frequency, delta}
    if repeating and _has_daylight_saving(timezone):
        found.append(
            gettext(
                "Hourly and quarter-hour refreshes follow UTC today, so on the day "
                "clocks go back the repeated hour runs twice. As listed parish "
                "times they run once, at the earlier time."
            )
        )
    return found

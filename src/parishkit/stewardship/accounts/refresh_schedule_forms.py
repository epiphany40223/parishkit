"""The ParishSoft settings page's refresh schedule editor (#632).

The page edits the one refresh schedule as rule rows ("Full every 2 hours
from 08:00 to 18:00", "Quick at 07:00"), skip rows and the switch "Skip
refreshes around Family emails" (see the admin-portal spec, "ParishSoft
refresh schedule settings"). The rules are applied in one place, the
server: the page script only adds and removes rows, fills presets and reads
times; every check, the preview and the cost come from here, through the
shared validator (``refresh_rules.check_schedule``) and the preview
functions (``schedule_preview``), for the live check and the save alike.

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
from django.forms import BaseFormSet, formset_factory
from django.utils.translation import gettext, ngettext, ngettext_lazy
from django.utils.translation import gettext_lazy as _

from parishkit.stewardship.source.cadence import MAX_DAILY_TIMES, refresh_settings
from parishkit.stewardship.source.refresh_rules import (
    DAY,
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
from parishkit.stewardship.web.time_entry_fields import FlexibleTimeField

from .source_cadence_schema import CADENCE_SETTINGS

RULES = "rules"
SKIPS = "skips"
SWITCH = "skip_around_family_emails"
MANAGEMENT = ("TOTAL_FORMS", "INITIAL_FORMS", "MIN_NUM_FORMS", "MAX_NUM_FORMS")
RULE_FIELDS = ("kind", "shape", "every", "start", "last", "at")
SKIP_FIELDS = ("shape", "at", "start", "last")
# The editor's own row caps, below the stored document's bound (refresh_rules.MAX_ROWS):
# every row's fields are posted with the page's other settings, and the
# whole request must stay within the server's DATA_UPLOAD_MAX_NUMBER_FIELDS
# (500), or it is refused before any form reads it. 64 rules and 16 skips
# post at most 64 * 6 + 16 * 4 + 9 = 457 editor names (posted_names), with
# room for the token, the action and the page's other settings; a test pins
# the sum. More rules than 64 never matter: "every" rules give any number of
# times. Raising these to MAX_ROWS (96) is not safe: 96 rules alone post
# 576 names. The page shows a stored document in full and saves it back, so
# these caps must not go below what stored documents hold: this page saves
# at most these, and a converted schedule has far fewer rows (at most
# MAX_LEGACY_FULL_TIMES full times plus a few rules). A document stored
# with more rows by another path (valid_shape allows MAX_ROWS) could not be
# saved back from this page.
RULE_ROWS = 64
SKIP_ROWS = 16

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
KIND_CHOICES = (("full", _("Full")), ("quick", _("Quick")))
RULE_SHAPES = (("every", _("every")), ("at", _("at")))
SKIP_SHAPES = (("at", _("at")), ("range", _("from")))
QUARTER_HOUR_MESSAGE = _("Use :00, :15, :30 or :45.")


@dataclass(frozen=True)
class Preset:
    """One preset: its key, name, what it includes, and its rules."""

    key: str
    name: str
    description: str
    rules: tuple


# The admin-portal spec's presets, in its order. Each fills the rules and
# clears the skips; none changes the switch.
PRESETS = (
    Preset(
        "nightly",
        _("Nightly only"),
        _("Full at 02:00; no quick updates."),
        ({"kind": "full", "at": "02:00"},),
    ),
    Preset(
        "nightly_hourly",
        _("Nightly, then quick updates hourly"),
        _(
            "Full at 02:00; quick every hour from 00:00 to 23:00 (23 quick "
            "updates; the 02:00 one is covered by the full refresh). The default."
        ),
        (
            {"kind": "full", "at": "02:00"},
            {"kind": "quick", "every": 60, "from": "00:00", "to": "23:00"},
        ),
    ),
    Preset(
        "every_2_hours",
        _("Every 2 hours"),
        _("Full every 2 hours from 00:00 to 22:00; no quick updates."),
        ({"kind": "full", "every": 120, "from": "00:00", "to": "22:00"},),
    ),
    Preset(
        "every_4_hours",
        _("Every 4 hours"),
        _("Full every 4 hours from 00:00 to 20:00; no quick updates."),
        ({"kind": "full", "every": 240, "from": "00:00", "to": "20:00"},),
    ),
    Preset(
        "business_hours",
        _("Business hours"),
        _(
            "Full at 02:00 and every 2 hours from 08:00 to 18:00; quick every "
            "hour from 07:00 to 19:00 (7 quick updates, at 07:00, 09:00, 11:00, "
            "13:00, 15:00, 17:00 and 19:00; the others are covered by the full "
            "refreshes)."
        ),
        (
            {"kind": "full", "at": "02:00"},
            {"kind": "full", "every": 120, "from": "08:00", "to": "18:00"},
            {"kind": "quick", "every": 60, "from": "07:00", "to": "19:00"},
        ),
    ),
)


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


def presets_payload():
    """The presets as the page script fills them: each one's rows' initial values."""
    return [
        {"key": preset.key, "rules": [rule_initial(rule) for rule in preset.rules]}
        for preset in PRESETS
    ]


def _hhmm(value):
    """A cleaned ``time`` as ``HH:MM`` text, or None."""
    return None if value is None else value.strftime("%H:%M")


def _off_quarter(value):
    """Whether ``HH:MM`` text is off the quarter hour."""
    return minutes(value) % SPACING != 0


class _Row(forms.Form):
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
        value = _hhmm(self.cleaned_data.get(name))
        if value is None:
            if name not in self.errors:
                self.add_error(name, gettext("Enter a time."))
            return None
        if _off_quarter(value) and not (allow_kept and value in self.kept):
            self.add_error(name, QUARTER_HOUR_MESSAGE)
            return None
        return value


class RuleForm(_Row):
    """One rule row: "[Full | Quick] every [interval] from [time] to [time]",
    or "[Full | Quick] at [time]"."""

    kind = forms.ChoiceField(label=_("Kind"), choices=KIND_CHOICES, initial="full")
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
            self.add_error("every", gettext("Choose how often."))
        start, last = self._time("start"), self._time("last")
        if start is not None and last is not None and last < start:
            self.add_error(
                None,
                gettext(
                    "A rule can't cross midnight. Use two rules: one up to 23:45 "
                    "and one from 00:00."
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
                    gettext(
                        "A skip can't cross midnight, and its end (which is not "
                        "skipped) must be after its start. Use two skips: one "
                        "that ends at 23:45, with a skip at 23:45, and one from "
                        "00:00."
                    ),
                )
            elif start is not None and last is not None and not self.errors:
                self.skip = {"from": start, "to": last}
        return data


class RowSet(BaseFormSet):
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
    names = {SWITCH}
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
        "rules": sorted(document["rules"], key=lambda r: json.dumps(r, sort_keys=True)),
        "skips": sorted(document["skips"], key=lambda r: json.dumps(r, sort_keys=True)),
        SWITCH: document[SWITCH],
    }


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
                gettext("Add a Full rule: keep at least one full refresh a day."),
                gettext("add a full refresh"),
            )
        )
    return found


TIME_FIELDS = frozenset({"at", "start", "last"})


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
                    at_field=name in TIME_FIELDS and error.code is not None,
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
        return stored_settings(self.document())

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


WEEKDAYS = (
    _("Monday"),
    _("Tuesday"),
    _("Wednesday"),
    _("Thursday"),
    _("Friday"),
    _("Saturday"),
    _("Sunday"),
)
WINDOW_WORDS = {
    "reminder_preparing": _("reminder being prepared"),
    "reminder_sending": _("reminder being sent"),
    "initial_sending": _("invitation being sent"),
}


def _wall(instant, timezone):
    """An instant as the parish's ``HH:MM``."""
    return instant.astimezone(ZoneInfo(timezone)).strftime("%H:%M")


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
            "time": _wall(entry.due_at, timezone)
        }
    elif entry.shift == "back":
        text += gettext(": runs once, clocks go back")
    elif entry.nightly and entry.window:
        text += gettext(": runs even during the %(cause)s") % {
            "cause": WINDOW_WORDS[entry.window]
        }
    return text, False


def preview_context(preview, *, timezone, today, daily):
    """What the per-day list and the summary show for a ``SchedulePreview``."""
    days = []
    for index, day in enumerate(preview.days):
        full, quick = len(day.runs("full")), len(day.runs("quick"))
        spent, _share = preview.cost.per_day[index]
        days.append(
            {
                "day": day.day,
                "weekday": WEEKDAYS[day.day.weekday()],
                "today": day.day == today,
                "change": day.change,
                "counts": gettext(
                    "%(full)s, %(quick)s, about %(time)s of ParishSoft time"
                )
                % {
                    "full": ngettext(
                        "%(count)d full refresh", "%(count)d full refreshes", full
                    )
                    % {"count": full},
                    "quick": ngettext(
                        "%(count)d quick update", "%(count)d quick updates", quick
                    )
                    % {"count": quick},
                    "time": _duration(spent),
                },
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
    summary = gettext(
        "About %(time)s of ParishSoft time a day (%(share)s of the day): "
        "%(full)s of about %(full_length)s%(full_typical)s and %(quick)s of "
        "about %(quick_length)s%(quick_typical)s."
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
            "start": _wall(fresh.start, timezone),
            "end": _wall(fresh.end, timezone),
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
        """Each row of ``scope`` with its number, messages and note."""
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
    def text_lists(self):
        """The full and quick daily times as "Edit as text" shows them."""
        daily = self.editor.daily()
        if daily is None:
            return None
        return {"full": ", ".join(daily.full), "quick": ", ".join(daily.quick)}


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

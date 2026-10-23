"""Configuration change commands of the Admin automation command line (ADM-11 PR 4).

``schedule preview`` and ``schedule confirm`` change mail schedules and the
draft campaign dates as the Dates and mail schedules page does, through the same
functions (``accounts.schedule_changes``, ``admin_editing.confirm_intent``),
and ``config request cancel`` cancels a live end-date change that has not
applied (#944):

- ``schedule preview`` admits the caller as the page's form post does
  (recording activity, so it needs a full-scope session), binds the operator's
  change document to the page's own forms, and prints the page's review with
  ``preview.token``, the same signed binding the page embeds. It changes
  nothing.
- ``schedule confirm`` takes that token (or one the page signed: tokens are
  bound to the Administrator, not the channel) and records the configuration
  request exactly as the page's **Confirm** does, rechecking authority, the
  applied configuration, the campaign's work and expiry. In the request's own
  durable transaction it records one ``admin_cmd_schedule_confirm`` event
  whose subject is the automation session. A repeated confirm returns the
  original request and records nothing new.

The change document (default, pending Administrator confirmation) is JSON::

    {"window": {"start_date": "...", "end_date": "...", "timezone": "...",
                "overlap_confirmed": true},
     "schedules": [{"id": "<saved>", "time": "10:00:00"},
                   {"id": "<saved>", "delete": true},
                   {"kind": "reminder", "date": "...", "time": "...",
                    "template_version": "<uuid>"},
                   {"repeat": {"frequency": "weekly", "start": "...",
                               "end": "...", "weekdays": [0, 3]},
                    "time": "...", "template_version": "<uuid>"},
                   {"series": "<id>", "scope": "following",
                    "from": "<reminder id>", "time": "..."},
                   {"series": "<id>", "delete": true}]}

A saved schedule named by ``id`` keeps every field the document leaves out;
``delete`` removes it; an entry without ``id`` adds a schedule. Report
emails are not schedules here: ``report-email preview`` changes them, and
a digest's ``comparison`` and ``skip`` members are gone with the digests
(#1786). Saved
schedules the document does not name stay as they are: as on the page,
omission never removes one. A ``repeat`` entry (#469) adds one ordinary
reminder per date its rule gives (``campaigns.recurrence``), each with the
entry's ``time`` and ``template_version``, exactly as if the document listed
them one by one. With ``"remember": true`` the same change also remembers
the rule as a reminder series holding exactly those reminders
(``schedule_changes.build_preview``'s ``series``), for later series edits;
delivery never reads a series. A ``series`` entry edits a remembered series
(``accounts.series_edits``): ``"scope": "following"`` from its reminder
``from`` ("this and following"), ``"scope": "series"`` (the whole series,
from its first reminder that has not sent), each with an optional new
``repeat`` rule, ``time`` and ``template_version``; or ``"delete": true``,
which removes its unsent reminders and the series. Either way it only adds
and removes ordinary reminders, through the same forms, review and
confirmation; a reminder that sent stays as it is. ``window`` (any of its
fields) changes the campaign's dates only while they may still change; for a
live campaign (scheduled or open in Production, #912) only ``end_date`` may
change, and confirming it binds the exceptional end-date intent as the page
does.
"""

import json
from dataclasses import dataclass
from datetime import date
from uuid import UUID

from django.db import DatabaseError
from django.http import QueryDict

from .admin_reads import NotAvailable, ReadModel, _held, _recheck, config_request
from .campaigns.recurrence import RULE_ERRORS, canonical_rule, expand

# The page's limits: a signed preview of at most 256,000 characters, and a
# formset of at most 101 rows.
TOKEN_LIMIT = 256_000
CHANGES_LIMIT = 256_000
ROW_LIMIT = 101
WINDOW_FIELDS = ("timezone", "start_date", "end_date", "overlap_confirmed")
ROW_FIELDS = ("kind", "date", "time", "weekday", "template_version")
# A repeat entry's members besides its rule (#469). It always adds
# reminders, so it takes no kind, and its rule gives the dates.
REPEAT_FIELDS = ("time", "template_version")
# The most reminders one repeat adds, as on New scheduled email
# (schedule_entry_views.REPEAT_LIMIT).
REPEAT_LIMIT = 30
# A series entry's members (#469): which series, how much of it, from where.
SERIES_FIELDS = ("series", "scope", "from", "repeat", *REPEAT_FIELDS)
# The internal key naming an added entry in field errors: ``new<n>``, or
# ``new<n>.<date>`` for one date of a repeat. A document cannot carry it,
# since parse_changes refuses unknown members before adding it.
LABEL = "_label"
# What each field of a change document may hold, as JSON.
FIELD_TYPES = {
    "timezone": (str,),
    "start_date": (str,),
    "end_date": (str,),
    "overlap_confirmed": (bool,),
    "kind": (str,),
    "date": (str, type(None)),
    "time": (str,),
    "weekday": (int, type(None)),
    "template_version": (str,),
}


class FieldProblem(dict):
    """One problem in an error's ``fields``, with what the refusal log may keep.

    In the error document it is the plain ``{"field", "code", "message"}``
    dict. The refusal log (#1403; admin-automation spec, Refusal entry)
    records a problem's field name only from one of these, and only its
    ``logged`` name: a server-owned name in the identifier shape, such as
    ``window-start_date`` or ``schedules-time``, never a key or id the
    caller sent. ``unexpected`` marks a problem about a key or id the caller
    sent that the command does not have; the log counts those
    (``unexpected_fields``) and never names them. A plain dict problem
    gives the log only its code.
    """

    def __init__(self, problem, *, logged=None, unexpected=False):
        """Keep the document's problem and the refusal log's view of it."""
        super().__init__(problem)
        self.logged = logged
        self.unexpected = unexpected


def unexpected_problem(field, message):
    """An ``invalid`` problem about a key or id the caller sent that the
    command does not have; the refusal log counts it and never names it."""
    return FieldProblem(
        {"field": field, "code": "invalid", "message": message}, unexpected=True
    )


class InvalidChange(ValueError):
    """The change is not valid; nothing changed (``invalid``, exit 1).

    ``fields`` lists each problem as ``{"field", "code", "message"}``:
    ``code`` is the closed ``web.contracts.ErrorCode`` value (``required``
    or ``invalid``); the field is
    ``window.<name>``, ``schedules.<id>.<name>`` for a saved schedule,
    ``schedules.new<n>.<name>`` for the n-th added one (from 0), or
    ``window``/``schedules`` for a problem between fields; the message is the
    page's own.
    """

    def __init__(self, fields):
        """Keep the field problems the error document lists."""
        super().__init__("The change is not valid.")
        self.fields = fields


def _typed(name, value):
    """Whether ``value`` has a type the field ``name`` accepts (never a bool int)."""
    if isinstance(value, bool) and bool not in FIELD_TYPES[name]:
        return False
    return isinstance(value, FIELD_TYPES[name])


def _repeat(entry, label):
    """The reminder entries a ``repeat`` entry adds, one per date of its rule.

    The rule must be valid in every member (``canonical_rule`` admits no
    stray types), skip no month for want of its day, and give from one to
    REPEAT_LIMIT dates; otherwise this is a ``ValueError`` with the page's
    message where it has one. Unlike the page's panel, which leaves out the
    dates that do not fit, every date is added: automation should not drop a
    date silently, so a date the forms refuse refuses the whole change, named
    ``<label>.<date>``, and so does a month the rule would skip.
    """
    if (
        set(entry) - {"repeat", "remember", *REPEAT_FIELDS}
        or not all(_typed(name, entry[name]) for name in REPEAT_FIELDS if name in entry)
        or entry.get("remember", True) is not True
    ):
        raise ValueError(
            'A repeat takes only its rule, time, template_version and "remember": true.'
        )
    fields = {name: entry[name] for name in REPEAT_FIELDS if name in entry}
    return [
        {"kind": "reminder", "date": day, **fields, LABEL: f"{label}.{day}"}
        for day in _rule(entry["repeat"])[1]
    ]


def _rule(value):
    """A repeat rule's canonical form and dates, or the page's ``ValueError``.

    The rule must be valid in every member (``canonical_rule`` admits no
    stray types), skip no month for want of its day, and give from one to
    REPEAT_LIMIT dates.
    """
    rule = canonical_rule(value)
    if rule is None:
        # canonical_rule refuses exactly the rules expand names an error for.
        raise ValueError(RULE_ERRORS[expand(value)["error"]])
    expanded = expand(rule)
    if expanded["missing"]:
        months = ", ".join(
            date.fromisoformat(f"{month}-01").strftime("%B %Y")
            for month in expanded["missing"]
        )
        raise ValueError(
            f"The repeat's day of the month is not in {months}; choose a day "
            "every month in the range has, or a shorter range."
        )
    dates = expanded["dates"]
    if not 1 <= len(dates) <= REPEAT_LIMIT:
        raise ValueError(
            f"A repeat adds from 1 to {REPEAT_LIMIT} reminders; this one gives "
            f"{len(dates)} dates."
        )
    return rule, dates


def _series(entry, label):
    """A ``series`` entry's edit, checked for shape, as ``series_edits.plan`` takes it.

    ``{"series", "delete": true}`` deletes the series; otherwise ``scope``
    is ``"following"`` (with the reminder ``from``) or ``"series"`` (no
    ``from``), with an optional ``repeat`` rule (checked as a repeat's is),
    ``time`` and ``template_version``. Returns the entry's series id, scope,
    from, canonical rule (or None), time, email and ``LABEL``.
    """
    from .accounts.series_edits import SCOPES

    if "delete" in entry:
        if entry["delete"] is not True or set(entry) != {"series", "delete"}:
            raise ValueError('A series is deleted by its id and "delete": true alone.')
        scope = "delete"
    else:
        scope = entry.get("scope")
        if (
            set(entry) - set(SERIES_FIELDS)
            or scope not in SCOPES
            or ("from" in entry) != (scope == "following")
            or not isinstance(entry.get("from", ""), str)
            or not all(
                _typed(name, entry[name]) for name in REPEAT_FIELDS if name in entry
            )
        ):
            raise ValueError(
                'A series entry takes "scope" ("following" with "from", or '
                '"series"), and optionally repeat, time and template_version.'
            )
    if not isinstance(entry["series"], str):
        raise ValueError("A series is named by its id.")
    return {
        "series": entry["series"],
        "scope": scope,
        "start": entry.get("from"),
        "rule": _rule(entry["repeat"])[0] if "repeat" in entry else None,
        "time_of_day": entry.get("time"),
        "template_version": entry.get("template_version"),
        LABEL: label,
    }


def parse_changes(text):
    """Parse and check the shape of a change document; values are the forms' job.

    Refuses (``ValueError``, ``invalid``) anything but an object with
    ``window`` and ``schedules``, unknown or mistyped members, a ``delete``
    without a saved ``id``, a repeated ``id``, a ``kind`` on a saved
    schedule (its mail type never changes), an empty new entry, a repeat
    ``_repeat`` refuses, and more rows than the page takes, before or after
    the repeats are expanded, and a ``series`` entry ``_series`` refuses or
    a series named twice. Returns ``{"window": dict, "schedules": list,
    "series": list, "edits": list}``: each repeat is replaced by its
    reminders, each series entry moves to ``edits`` (``_series``; planned
    against the applied records by ``series_entries``), and each
    added entry carries its ``LABEL``. ``series`` lists each repeat to
    remember as ``{"rule", "added"}``: its canonical rule and the positions
    of its reminders among the document's added entries, which is the order
    the page's form set adds them in (``build_preview``'s ``series``).
    """
    if len(text) > CHANGES_LIMIT:
        raise ValueError("The change document is too long.")
    try:
        value = json.loads(text)
    except ValueError:
        raise ValueError("The change document is not JSON.") from None
    if not isinstance(value, dict) or set(value) - {"window", "schedules"}:
        raise ValueError("The change document has unknown members.")
    window = value.get("window", {})
    rows = value.get("schedules", [])
    if (
        not isinstance(window, dict)
        or set(window) - set(WINDOW_FIELDS)
        or not all(_typed(name, item) for name, item in window.items())
    ):
        raise ValueError("The change document's window is not valid.")
    if not isinstance(rows, list) or len(rows) > ROW_LIMIT:
        raise ValueError("The change document's schedules are not valid.")
    seen = set()
    entries = []
    series = []
    edits = []
    added = 0
    for row in rows:
        if isinstance(row, dict) and "series" in row:
            edit = _series(row, f"new{added}")
            if edit["series"] in {item["series"] for item in edits}:
                raise ValueError("A series is named twice.")
            edits.append(edit)
            added += 1
            continue
        if isinstance(row, dict) and "repeat" in row:
            reminders = _repeat(row, f"new{added}")
            if row.get("remember"):
                # Added entries so far, saved ones aside: this repeat's
                # reminders come next among the change's added schedules.
                first = sum("id" not in entry for entry in entries)
                series.append(
                    {
                        "rule": canonical_rule(row["repeat"]),
                        "added": list(range(first, first + len(reminders))),
                    }
                )
            entries.extend(reminders)
            added += 1
            continue
        if (
            not isinstance(row, dict)
            or set(row) - {"id", "delete", *ROW_FIELDS}
            or not all(_typed(name, row[name]) for name in ROW_FIELDS if name in row)
        ):
            raise ValueError("A schedule in the change document is not valid.")
        if "id" in row:
            if not isinstance(row["id"], str) or row["id"] in seen:
                raise ValueError("A schedule is named twice or not by its id.")
            if "kind" in row:
                # A saved schedule's mail type never changes (as on the page).
                raise ValueError("A saved schedule's kind cannot change.")
            seen.add(row["id"])
        elif all(
            value is None or value == "" or value is False for value in row.values()
        ):
            # The page's form set skips a new row with only empty or default
            # values, so it would add nothing; and it would shift a
            # remembered repeat's positions (build_preview's ``series``).
            raise ValueError("A new schedule needs its fields.")
        if "delete" in row and (
            row["delete"] is not True or "id" not in row or set(row) != {"id", "delete"}
        ):
            raise ValueError("Only a saved schedule can be deleted, by id alone.")
        if "id" not in row:
            row = row | {LABEL: f"new{added}"}
            added += 1
        entries.append(row)
    if len(entries) > ROW_LIMIT:
        raise ValueError("The change document's schedules are not valid.")
    return {"window": window, "schedules": entries, "series": series, "edits": edits}


def series_entries(parsed, configuration, saved, campaign):
    """``parsed`` with each series edit planned against the applied records (#469).

    ``saved`` is the campaign's saved schedule records and ``campaign`` the
    applied campaign record. Each edit's reminders to remove become
    ``{"id", "delete": true}`` entries and its reminders to add become added
    entries labelled ``<label>.<date>``, both after the document's own
    entries, so the repeats' positions stay as they are; its series is added
    to ``series`` with the positions of those added reminders, and its other
    operations on the existing record are ``series_changes``. A series that
    is not the campaign's, a reminder of it the document also names, or an
    edit ``series_edits.plan`` refuses is ``InvalidChange`` on the entry.
    """
    from django.utils import timezone

    from .accounts.schedule_forms import schedule_order
    from .accounts.schedule_preview import work_summary
    from .accounts.schedule_table import schedule_rows
    from .accounts.series_edits import SeriesEditError, plan
    from .admin_reads import campaign_series

    result = parsed | {"series_changes": []}
    if not parsed["edits"]:
        return result
    known = {row["id"]: row for row in campaign_series(configuration, campaign.pk)}
    # The page's read-only statuses: run, running or prepared ahead.
    read_only = {
        row.id
        for row in schedule_rows(
            sorted(saved, key=schedule_order),
            campaign.active_configuration.values,
            work_summary(campaign.pk),
            timezone.now(),
        )
        if row.read_only
    }
    named = {entry["id"] for entry in parsed["schedules"] if "id" in entry}
    entries = list(parsed["schedules"])
    series = list(parsed["series"])
    schedules = {row["id"]: row["values"] for row in saved}
    for edit in parsed["edits"]:
        try:
            record = known.get(edit["series"])
            if record is None:
                raise SeriesEditError("This campaign has no series with this id.")
            if named & set(record["schedules"]):
                raise SeriesEditError(
                    "A reminder of this series is also changed on its own in "
                    "this document; change the series and its reminders in "
                    "separate changes."
                )
            outcome = plan(
                {"id": record["id"], "values": record},
                schedules,
                read_only,
                scope=edit["scope"],
                start=edit["start"],
                rule=edit["rule"],
                time_of_day=edit["time_of_day"],
                template_version=edit["template_version"],
            )
        except SeriesEditError as error:
            field = f"schedules.{edit[LABEL]}"
            raise InvalidChange(
                [{"field": field, "code": "invalid", "message": str(error)}]
            ) from None
        entries.extend({"id": member, "delete": True} for member in outcome["remove"])
        # This edit's reminders come next among the change's added schedules.
        first = sum("id" not in entry for entry in entries)
        entries.extend(
            {"kind": "reminder", **values, LABEL: f"{edit[LABEL]}.{values['date']}"}
            for values in outcome["add"]
        )
        if outcome["series"] is not None:
            series.append(
                outcome["series"]
                | {"added": list(range(first, first + len(outcome["add"])))}
            )
        result["series_changes"].extend(outcome["changes"])
    if len(entries) > ROW_LIMIT:
        raise ValueError("The change document's schedules are not valid.")
    return result | {"schedules": entries, "series": series}


def _text(value):
    """A value as the browser posts it: empty for None."""
    return "" if value is None else str(value)


def _window_initial(campaign):
    """The window's current values, as the page's form starts with them."""
    initial = {name: campaign[name] for name in ("timezone", "start_date", "end_date")}
    financial = campaign["financial"]
    if financial is not None:
        initial["overlap_confirmed"] = bool(financial["overlap_confirmed"])
    return initial


def form_data(changes, previous, campaign, *, editable, base_digest, end_only=False):
    """The page's posted form for this change document.

    ``previous`` is the campaign's saved schedule records and ``campaign``
    its applied values. Saved schedules are posted in the page's order
    (``schedule_order``), each with its own id, then the added ones, so the
    page's forms read exactly what a browser would have posted. A window
    change while the dates may not change is refused as the page refuses one
    (``StaleRecordError``); values equal to the current ones are no change.
    ``end_only`` (a live campaign, #912) admits a change to the end date
    alone.
    Returns ``(data, identifiers)``, the latter naming each form row for
    field errors.
    """
    from .accounts.schedule_forms import schedule_order
    from .storage import StaleRecordError

    rows = sorted(previous, key=schedule_order)
    saved = {row["id"] for row in rows}
    named = {entry["id"]: entry for entry in changes["schedules"] if "id" in entry}
    if set(named) - saved:
        raise InvalidChange(
            [
                unexpected_problem(
                    f"schedules.{identifier}",
                    "No saved schedule of this campaign has this id.",
                )
                for identifier in sorted(set(named) - saved)
            ]
        )
    added = [entry for entry in changes["schedules"] if "id" not in entry]
    data = QueryDict(mutable=True)
    data["action"] = "preview"
    data["base_digest"] = base_digest
    data["schedules-TOTAL_FORMS"] = str(len(rows) + len(added))
    data["schedules-INITIAL_FORMS"] = str(len(rows))
    identifiers = []
    for index, row in enumerate(rows):
        entry = named.get(row["id"], {})
        values = row["values"] | {
            name: entry[name] for name in ROW_FIELDS if name in entry
        }
        data[f"schedules-{index}-id"] = row["id"]
        for name in ROW_FIELDS:
            data[f"schedules-{index}-{name}"] = _text(values.get(name))
        if entry.get("delete"):
            data[f"schedules-{index}-DELETE"] = "on"
        identifiers.append(row["id"])
    for offset, entry in enumerate(added):
        index = len(rows) + offset
        for name in ROW_FIELDS:
            data[f"schedules-{index}-{name}"] = _text(entry.get(name))
        # parse_changes labels each added entry (a repeat's by its date);
        # the pages' own entries are numbered here.
        identifiers.append(entry.get(LABEL, f"new{offset}"))
    initial = _window_initial(campaign)
    window = initial | changes["window"]
    if not editable:
        changed = {name for name in window if window[name] != initial.get(name)}
        # A live campaign's end date alone may still change (#912).
        if changed - ({"end_date"} if end_only else set()):
            raise StaleRecordError("Campaign dates are structurally locked.")
        if end_only:
            # The page posts its one open window field, changed or not.
            data["window-end_date"] = window["end_date"]
        return data, identifiers
    if set(window) - set(initial):
        raise InvalidChange(
            [
                {
                    "field": "window.overlap_confirmed",
                    "code": "invalid",
                    "message": "This campaign has no financial period.",
                }
            ]
        )
    for name in ("timezone", "start_date", "end_date"):
        data[f"window-{name}"] = window[name]
    if window.get("overlap_confirmed"):
        data["window-overlap_confirmed"] = "on"
    return data, identifiers


def _problem(field, item, logged=None):
    """One field problem: the closed error code and the page's message.

    Django's ``required`` is ``ErrorCode.REQUIRED``; every other form
    error is ``ErrorCode.INVALID``. ``logged`` is the server-owned name the
    refusal log may record for it (see ``FieldProblem``), if any.
    """
    from .web.contracts import ErrorCode

    code = ErrorCode.REQUIRED if item["code"] == "required" else ErrorCode.INVALID
    return FieldProblem(
        {"field": field, "code": code.value, "message": str(item["message"])},
        logged=logged,
    )


def form_problems(prefix, errors, logged):
    """Each problem of one form's ``errors``, at ``prefix.<name>`` (or
    ``prefix`` for the form as a whole), one per message.

    ``logged`` is the server-owned name the refusal log records in place of
    ``prefix``, which may hold a row's id: the field's name is the form's
    own, so ``logged-<name>`` names it without the id.
    """
    return [
        _problem(
            prefix if name == "__all__" else f"{prefix}.{name}",
            item,
            logged if name == "__all__" else f"{logged}-{name}",
        )
        for name, messages in errors.get_json_data().items()
        for item in messages
    ]


def field_errors(window, schedules, identifiers):
    """Every problem the page's forms found, with the page's messages."""
    # A schedule row's identifier is its id or the change's own label, so
    # the refusal log names its fields as "schedules-<name>" without it.
    problems = form_problems("window", window.errors, "window")
    for index, form in enumerate(schedules.forms):
        problems += form_problems(
            f"schedules.{identifiers[index]}", form.errors, "schedules"
        )
    problems.extend(
        _problem("schedules", item, "schedules")
        for item in schedules.non_form_errors().get_json_data()
    )
    return problems


@dataclass(frozen=True)
class SchedulePreview(ReadModel):
    """The page's review of a schedule change, and the token that confirms it.

    ``version`` is the applied configuration's digest the change was made
    against. ``window`` holds the dates and timezone ``before`` and
    ``after`` and the names of the campaign values that ``changed``. Each
    change names its schedule ``id``, its ``operation`` and ``kind``, its
    civil values and first resolved send times ``before`` and ``after``
    (None when added or removed), and its ``impact``: the page's counts of
    work already done, replaced, failed and blocking. ``blocking`` is their
    total of blocking work; while it is above zero, as on the page, there is
    no ``preview`` to confirm. Otherwise ``preview.token`` is the signed
    binding ``schedule confirm`` takes, valid for fifteen minutes.
    ``series`` lists each reminder series the change remembers (``id``,
    ``rule`` and its reminders' ``schedules``), empty when none (#469).
    ``series_changes`` lists each existing series it updates or removes
    (``id``, ``operation`` and, for an update, its new ``rule`` and
    ``schedules``), empty when none.
    """

    campaign_id: UUID
    version: str
    window: dict
    changes: list
    blocking: int
    series: list
    series_changes: list
    preview: dict | None


# The impact counts the page shows for each change.
IMPACT = ("delivered", "cancellable", "failed", "blocking", "occurrences", "outboxes")


def _side(value):
    """One side of a change: civil values and resolved send times, or None."""
    if value is None:
        return None
    return {
        "kind": value["kind"],
        "date": value["date"],
        "time": value["time"],
        "weekday": value["weekday"],
        "template_version": value["template_version"],
        "subject": value["subject"],
        "comparison": value.get("comparison", False),
        # The digest sends it skips (#1658), empty when none.
        "skip_dates": value.get("skip_dates", []),
        "timezone": value["timezone"],
        "resolved": [
            {"key": slot["key"], "due_at": slot["due_at"]}
            for slot in value["resolved_slots"]
        ],
        "more": value["more_slots"],
    }


def _dates(values):
    """The campaign's dates and timezone from its values."""
    return {name: values[name] for name in ("start_date", "end_date", "timezone")}


def schedule_preview_model(context, version):
    """The command's projection of ``build_preview``'s review."""
    return SchedulePreview(
        campaign_id=context["campaign"].pk,
        version=version,
        window={
            "before": _dates(context["before_window"]),
            "after": _dates(context["after_window"]),
            "changed": sorted(context["window_changes"]),
        },
        changes=[
            {
                "id": change["id"],
                "operation": change["operation"],
                "kind": (change["after"] or change["before"])["kind"],
                "before": _side(change["before"]),
                "after": _side(change["after"]),
                "impact": {name: change["impact"].get(name, 0) for name in IMPACT},
            }
            for change in context["changes"]
        ],
        blocking=context["blocking"],
        series=context["series"],
        series_changes=context["series_changes"],
        preview=None if context["preview"] is None else {"token": context["preview"]},
    )


def _target(service, campaign_id):
    """The campaign a command names, or the current one (``not_available`` if none).

    Either way the configuration must be editable now: before setup, or
    during a restore under review, ``editable_configuration`` refuses with a
    ``ConfigError``, which the callers' ``_held`` reports as ``unavailable``.
    """
    from .accounts.admin_editing import editable_configuration

    current = editable_configuration(service).current_campaign_id
    target = campaign_id if campaign_id is not None else current
    if target is None:
        raise NotAvailable("There is no current campaign.")
    return target


def _admit_change(caller, service, *, read_only=False, capability=None):
    """The page's admission of a change, recording activity as its post does.

    A session that ended since the command was admitted is exit 5
    (``session_ended``), not the page's signed-out refusal. ``read_only``
    checks the same capability but renews nothing: a prompting command runs
    it before any lookup or prompt (#1339, #1341), so a session whose user
    lacks the capability is ``denied`` before anything is read, and the
    action admits again as its post does. ``capability`` is the one the
    command's page needs (``CONFIGURE``, an Administrator's, by default).
    """
    from .accounts.admin_editing import principal
    from .accounts.automation_sessions import SessionUnusable
    from .accounts.policy import Capability
    from .accounts.sessions import authenticated_admin

    try:
        return principal(
            caller,
            service,
            read_only=read_only,
            capability=capability or Capability.CONFIGURE,
        )
    except PermissionError:
        if authenticated_admin(caller, store=service.store, read_only=True) is None:
            raise SessionUnusable("session_ended") from None
        raise


def _ended_or_raise(caller, service, actor, error):
    """Re-raise a refusal, as exit 5 when the session ended meanwhile."""
    from .accounts.policy import Capability

    _recheck(caller, service.store, actor, Capability.CONFIGURE)
    raise error


def preview_schedule(caller, service, campaign_id, *, expected_version, changes):
    """``schedule preview``: the Dates and mail schedules page's review of a change.

    Admits as the page's form post does (``principal``, recording activity),
    then, in the page's work transaction, finds the campaign as the page does
    (an unknown campaign is ``not_available``; one not current, or held by
    background work, ``stale_version``), binds the change document to the
    page's forms and builds the page's review. A base other than
    ``expected_version`` is ``stale_version``. Rechecks access afterwards.
    Changes nothing.
    """
    from .accounts.content_views import _records
    from .accounts.schedule_changes import build_preview, preview_salt
    from .accounts.schedule_forms import Schedules, ScheduleWindow, schedule_action
    from .accounts.schedule_reads import (
        campaign_schedules,
        live_end_at,
        schedule_state,
    )
    from .campaigns.work_locks import work_transaction

    actor = _admit_change(caller, service)
    parsed = parse_changes(changes)

    def step():
        """Build the review in one work transaction, as the page's post does."""
        with work_transaction():
            target = _target(service, campaign_id)
            try:
                state, campaign, editable = schedule_state(service, target)
            except KeyError:
                raise
            except LookupError:
                raise NotAvailable("No such campaign.") from None
            previous = campaign.active_configuration.values
            saved = campaign_schedules(state[0], target)
            # Series edits are planned against the applied records (#469).
            changes = series_entries(parsed, state[0], saved, campaign)
            # A live campaign's end date alone may still change (#912).
            live_at = None if editable else live_end_at(state, campaign)
            data, identifiers = form_data(
                changes,
                saved,
                previous,
                editable=editable,
                base_digest=expected_version,
                end_only=live_at is not None,
            )
            window = ScheduleWindow(
                data,
                prefix="window",
                previous=previous,
                editable=editable,
                live_at=live_at,
            )
            schedule_action(data, window_fields=window.open_fields)
            schedules = Schedules(
                data,
                prefix="schedules",
                templates=_records(state[0], target),
                campaign_id=target,
                campaign=previous,
                previous=saved,
            )
            context = build_preview(
                service,
                actor,
                state,
                campaign,
                window,
                schedules,
                base_digest=expected_version,
                salt=preview_salt(target),
                series=changes["series"],
                series_changes=changes["series_changes"],
            )
            if context is None:
                raise InvalidChange(field_errors(window, schedules, identifiers))
            return schedule_preview_model(context, expected_version)

    try:
        model = _held(step)
    except (NotAvailable, PermissionError) as error:
        _ended_or_raise(caller, service, actor, error)
    from .accounts.policy import Capability

    _recheck(caller, service.store, actor, Capability.CONFIGURE)
    return model


@dataclass(frozen=True)
class ScheduleConfirm(ReadModel):
    """The configuration request a confirmation recorded, as its status reads.

    ``created`` is false when this token's request already existed (a
    repeated confirm), which then returns the original request unchanged.
    """

    created: bool
    request: dict


def confirm_schedule(caller, service, campaign_id, *, token, context):
    """``schedule confirm``: the page's **Confirm** for a reviewed change.

    Admits as the page does, then ``confirm_intent`` checks the token
    (expired or out of date is ``stale_version``, altered or for another
    campaign ``invalid``, another Administrator's ``denied``) and records the
    request, with ``admin_cmd_schedule_confirm`` in its own durable
    transaction. The configuration checks before intake run under ``_held``,
    so a restore under review or an activating change is ``unavailable``.

    ``context["committed"]`` is set once the request may have committed, so
    a later error is ``outcome_unknown`` (exit 6). A database error is
    treated so only once the request row was written (``attach`` ran): it
    may then have struck the commit. Before that, the transaction rolled
    back and nothing changed (exit 3). ``context["request_id"]`` is the
    request's id, fixed before intake (``policy_operation_id``), so the
    exit-6 document names it for ``config request show``; repeating with the
    same token, within its fifteen minutes, is also safe.
    """
    from django.core import signing

    from .accounts.admin_editing import confirm_intent
    from .accounts.configuration_requests import policy_operation_id
    from .accounts.schedule_changes import confirm_scope, preview_salt
    from .audit.schemas import Action, ActorKind, Outcome
    from .audit.services import record_action
    from .observability import _guard_refusal

    actor = _admit_change(caller, service)
    try:
        target = _held(lambda: _target(service, campaign_id))
    except NotAvailable as error:
        _ended_or_raise(caller, service, actor, error)
    try:
        # The request's id is derived from the signed key, before intake;
        # confirm_intent verifies the token properly (expiry included).
        key = UUID(signing.loads(token, salt=preview_salt(target))["key"])
    except (signing.BadSignature, KeyError, TypeError, ValueError):
        key = None
    if key is not None:
        context["request_id"] = str(policy_operation_id(actor.identity, key))
    created = []

    def attach(request, extra):
        """Record the command's event with the request it created."""
        record_action(
            Action.ADMIN_CMD_SCHEDULE_CONFIRM,
            actor_kind=ActorKind.PORTAL_USER,
            actor_id=actor.identity,
            subject_id=caller.automation_session_id,
            context={"outcome": Outcome.SUCCEEDED},
        )
        created.append(request.pk)

    try:
        receipt = confirm_intent(
            caller,
            service,
            actor,
            token=token,
            salt=preview_salt(target),
            link=None,
            current_scope=lambda service: confirm_scope(service, target),
            attach=attach,
        )
    except signing.BadSignature:
        # Altered, or signed for another campaign (an expired one is stale).
        raise ValueError("The preview token is not valid.") from None
    except PermissionError as error:
        _ended_or_raise(caller, service, actor, error)
    except DatabaseError as error:
        if created and not _guard_refusal(error):
            context["committed"] = True
        raise
    context["committed"] = True
    return ScheduleConfirm(
        created=bool(created), request=config_request(receipt).to_document()
    )


# The longest cancellation reason: the abort journal's column.
REASON_LIMIT = 1024
# ``config request cancel``'s refusal of a change still validating whose
# candidate was never prepared: no new machinery rescues it (#944).
UNPREPARED = {
    "field": "request",
    "code": "invalid",
    "message": (
        "This change's new settings were never prepared, so there is nothing "
        "to cancel yet. The configuration installer's log says what stops it; "
        "once that is fixed, the installer applies the change or refuses it."
    ),
}


@dataclass(frozen=True)
class ConfigCancel(ReadModel):
    """A live end-date change's cancellation and the request's status then.

    ``cancelled`` is false when this same cancellation was already recorded
    (a repeat), which changes nothing. The request is still unfinished until
    the configuration installer restores the previous settings and records
    it as failed; ``config request show`` follows it.
    """

    cancelled: bool
    request: dict


def cancel_end_change(caller, service, request_id, *, reason, context):
    """``config request cancel``: abort an unapplied live end-date change (#944).

    The operator's path out of a stuck change, which the data specification
    requires to go through the abort journal: in one durable transaction, as
    the installer's own lock order has it (the configuration lock, the
    runtime row, then the request), the current Administrator records the
    immutable ``CampaignConfigurationAbort`` with ``reason`` and the
    command's ``admin_cmd_config_request_cancel`` event. The journal's own
    trigger refuses a change that applied, has not started (``staged``) or
    whose base is no longer applied (``stale_version``). So does this
    command, more plainly, for a change already journaled with another
    reason (``stale_version``) and for one still ``validating`` whose
    candidate was never prepared (``invalid``, with ``UNPREPARED``'s
    message): that one has written nothing to undo, and only fixing what
    stops the installer moves it. The configuration
    installer then restores the previous YAML and records the request as
    failed, before any other request; nothing else is undone.

    A change that has not started is the change's own Administrator's to
    cancel, as Change status allows; another's is ``stale_version``, since
    the installer refuses or applies it within seconds. Only a request with
    a bound live end-date change is found here; any other is
    ``not_available``.
    """
    from django.db import IntegrityError, connection, transaction

    from .accounts.configuration_models import AppliedConfigurationVersion
    from .accounts.configuration_requests import _checkpoint, _status
    from .accounts.request_models import ConfigurationChangeRequest
    from .accounts.runtime_models import SystemConfiguration
    from .audit.schemas import Action, ActorKind, Outcome
    from .audit.services import record_action
    from .campaigns.configuration_intents import journal_abort
    from .campaigns.live_end_date import admit_end_edit, end_intent
    from .campaigns.models import Campaign, CampaignConfigurationAbort
    from .observability import _guard_refusal, current_correlation
    from .storage import StaleRecordError

    reason = (reason or "").strip()
    if not reason or len(reason) > REASON_LIMIT:
        raise ValueError(f"Give a reason of at most {REASON_LIMIT} characters.")
    actor = _admit_change(caller, service)
    context["request_id"] = str(request_id)
    correlation = current_correlation()

    def cancel():
        """Journal the abort, or cancel an unstarted change; return whether new."""
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(%s, %s)", [736220, 1])
        runtime = SystemConfiguration.objects.select_for_update().get()
        intent = end_intent(request_id)
        request = (
            ConfigurationChangeRequest.objects.select_for_update(of=("self",))
            .filter(pk=request_id, authority="admin")
            .first()
        )
        if intent is None or request is None:
            raise NotAvailable("No live end-date change has this id.")
        status = _status(request)
        abort = CampaignConfigurationAbort.objects.filter(intent=intent).first()
        if abort is not None and abort.reason == reason:
            return False
        if status.state == "staged" and request.actor_id == actor.identity:
            _checkpoint(
                request,
                sequence=status.sequence + 1,
                state="cancelled",
                actor_id=actor.identity,
                correlation_id=correlation,
            )
            return True
        if status.state in {"staged", "applied", "failed", "cancelled"}:
            raise StaleRecordError("This change can no longer be cancelled here.")
        if abort is not None:
            # Journaled already (by another cancellation or the installer's
            # own refusal) with another reason, and not yet restored: the
            # journal is immutable, so say so rather than let journal_abort's
            # invariant surface as an internal error.
            raise StaleRecordError(
                "This change is already being cancelled; config request show "
                "follows it."
            )
        if not AppliedConfigurationVersion.objects.filter(
            pk=request.candidate_version_id,
            digest=request.candidate_digest,
            predecessor_id=request.base_id,
        ).exists():
            # Stuck validating before its candidate was prepared: the journal
            # (and its restore) needs a prepared candidate, and nothing was
            # written yet that needs undoing. Refused as ``invalid`` because
            # only that code carries a message, and this one says what to do.
            raise InvalidChange([UNPREPARED])
        campaign = Campaign.objects.select_for_update().get(pk=intent.campaign_id)
        admit_end_edit("abort_configuration", campaign, runtime, intent)
        journal_abort(
            intent, reason=reason, actor_id=actor.identity, correlation_id=correlation
        )
        return True

    try:
        with transaction.atomic(durable=True):
            created = cancel()
            if created:
                record_action(
                    Action.ADMIN_CMD_CONFIG_REQUEST_CANCEL,
                    actor_kind=ActorKind.PORTAL_USER,
                    actor_id=actor.identity,
                    subject_id=caller.automation_session_id,
                    context={"outcome": Outcome.SUCCEEDED},
                )
            status = _status(ConfigurationChangeRequest.objects.get(pk=request_id))
    except IntegrityError as error:
        if not _guard_refusal(error):
            raise
        # The journal's trigger: applied, or its base no longer applied.
        _ended_or_raise(
            caller,
            service,
            actor,
            StaleRecordError("This change can no longer be cancelled."),
        )
    except (NotAvailable, StaleRecordError, PermissionError, InvalidChange) as error:
        _ended_or_raise(caller, service, actor, error)
    context["committed"] = True
    return ConfigCancel(cancelled=created, request=config_request(status).to_document())

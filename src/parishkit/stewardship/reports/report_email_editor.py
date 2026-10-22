"""New and Edit report email: the editor's fields and their values (#1786).

The report-emails spec ("New and Edit report email", delivery plan item 6).
``EditorForm`` reads the page's posted fields (and ``report-email
preview``'s ``create`` and ``edit`` entries, which ``values_document``
turns into the same fields) into one report's complete stored values
(``campaigns.report_values``), with each refusal at its field:

- **Report name** (the report's own name, not the From name), **Email
  subject line** and **Introduction** (HTML and plain text).
- **Recipients**: repeated ``recipient`` fields, each ``user:RECORD``,
  ``group:administrators``, ``group:staff`` or ``address:EMAIL``, in the
  order the page lists them.
- **Schedule**: Sends is ``once`` (a date and a time) or ``repeat``; a
  repeating rule is chosen in Basic (``preset`` ``daily`` or ``weekly``
  with ``preset_weekday``) or Advanced (``frequency``, ``weekdays``,
  ``from_kind``/``from_date``, ``until_kind``/``until_date``) mode, and the
  shared ``time``. Dates and times are typed in the browser's zone
  (``zone``) and stored as campaign-local wall-clock values through
  ``schedule_local``, which keeps an unchanged Edit exactly as saved. A
  repeating report's days are the campaign's days (spec, "Decisions made in
  this specification"); only its time is converted, at its next send.
- **As of**, **Blocks** (repeated ``block`` keys in order, with the Day by
  day table's ``days_shown`` and the Participation charts' ``charts``
  boxes, #1956) and **Send only when there is something to report**.

Nothing here reads a request or checks authority.
"""

import copy
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from django import forms
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from parishkit.stewardship.accounts import schedule_local
from parishkit.stewardship.accounts.policy_schema import normalized_email
from parishkit.stewardship.campaigns.intervals import resolve_local
from parishkit.stewardship.campaigns.report_values import (
    ADDRESS_LIMIT,
    FROM_BOUNDS,
    NAME_LIMIT,
    RECIPIENT_LIMIT,
    UNTIL_BOUNDS,
    name_key,
    refused_parts,
)
from parishkit.stewardship.wall_clock import WEEKDAYS
from parishkit.stewardship.web.admin_forms import AdminForm
from parishkit.stewardship.web.content import prepare_content
from parishkit.stewardship.web.dates import UnknownZone
from parishkit.stewardship.web.time_entry_fields import FlexibleTimeField

from .charts import CHARTS, CUMULATIVE_FAMILIES, FIRST_SUBMISSIONS, PLEDGES
from .report_blocks import REGISTRY
from .report_blocks.registry import AFTER_BUTTON

# The Participation charts block's Charts checkboxes, in the panels'
# order (spec, "Blocks field", #1956).
CHART_LABELS = {
    FIRST_SUBMISSIONS: _("First submissions of the day"),
    CUMULATIVE_FAMILIES: _("Cumulative participating Families"),
    PLEDGES: _("Effective annual pledges"),
}


def offered_charts(campaign):
    """The charts the editor offers: pledges only with financial stewardship.

    ``campaign`` is the applied campaign values.
    """
    financial = "financial" in campaign.get("modules", ())
    return [key for key in CHARTS if key != PLEDGES or financial]


def chart_choice(values):
    """A report's stored "which charts" list (None: the default, all)."""
    for entry in (values or {}).get("blocks", ()):
        if entry["block"] == "participation_charts":
            return entry["options"].get("charts")
    return None


def ticked_charts(values, campaign):
    """The charts a saved report's block draws, so the Charts boxes it ticks.

    Its choice as the campaign offers them, or every offered chart when
    none remains (``participation_charts.shown_charts``).
    """
    offered = offered_charts(campaign)
    choice = chart_choice(values) or CHARTS
    return [key for key in offered if key in choice] or offered


def stored_charts(choice, saved, campaign):
    """The stored form of a "which charts" choice, the same from every path.

    An unchanged saved choice stays as saved (even one naming pledges after
    financial stewardship was turned off). Otherwise every chart the
    campaign offers is stored as no choice (None: the default, all, which
    follows the campaign), and only a strict subset as its list (spec,
    "Participation charts block").
    """
    if saved is not None and choice == chart_choice(saved):
        return choice
    return None if choice == offered_charts(campaign) else choice


def with_charts(values, choice):
    """``values`` with the Participation charts block's choice set to ``choice``.

    None removes the option. A copy: the given values are not changed.
    """
    blocks = []
    for entry in values["blocks"]:
        if entry["block"] == "participation_charts":
            options = {k: v for k, v in entry["options"].items() if k != "charts"}
            if choice is not None:
                options["charts"] = choice
            entry = entry | {"options": options}
        blocks.append(entry)
    return values | {"blocks": blocks}


# The repeating presets of Basic mode (spec, "Repeating sends").
PRESETS = {
    "daily": {
        "frequency": "daily",
        "from": "day_after_campaign_start",
        "until": "day_after_campaign_end",
    },
    "weekly": {
        "frequency": "weekly",
        "from": "campaign_start",
        "until": "first_send_after_campaign_end",
    },
}
PRESET_LABELS = {"daily": _("Daily"), "weekly": _("Weekly")}
AS_OF_LABELS = {
    "previous_day_end": _("End of the previous day (campaign time zone)"),
    "send_time": _("When the email is made"),
}
FROM_CHOICES = (
    ("campaign_start", _("The campaign's first day")),
    ("day_after_campaign_start", _("The day after the campaign's first day")),
    ("date", _("A date")),
)
UNTIL_CHOICES = (
    ("campaign_end", _("The campaign's last day")),
    ("day_after_campaign_end", _("The day after the campaign's last day")),
    ("first_send_after_campaign_end", _("The first send after the campaign ends")),
    ("archive", _("Until the campaign is archived")),
    ("date", _("A date")),
)
GROUP_NAMES = {"administrators": _("All Administrators"), "staff": _("All Staff")}
ROLE_NAMES = {"administrator": _("Administrator"), "staff": _("Staff")}
DAY_CHOICES = tuple((str(index), str(name)) for index, name in enumerate(WEEKDAYS))
MAX_DAYS_SHOWN = 14


def portal_users(configuration):
    """Every Admin portal user, whatever their role, ordered by name.

    Each is ``{"id", "name", "email", "role"}``. The recipient check reads
    them all, so the address of a user a report may not name (a Ministry
    leader) is that user, refused, and never an outside address.
    """
    rows = [
        {
            "id": row["id"],
            "name": row["values"].get("name") or row["values"].get("email", ""),
            "email": row["values"].get("email", ""),
            "role": row["values"].get("role"),
        }
        for row in configuration.active_configuration.canonical_document[
            "sections"
        ].get("login_rules", [])
        if row["values"].get("kind") == "user"
    ]
    return sorted(rows, key=lambda row: (row["name"].casefold(), row["id"]))


def eligible_users(configuration):
    """The Admin portal users a report may name: Administrators and Staff.

    As ``portal_users``. Ministry leaders are never offered (decision 2).
    """
    return [user for user in portal_users(configuration) if user["role"] in ROLE_NAMES]


def group_counts(users):
    """How many people each group holds now (All Staff: Staff, not Administrators)."""
    return {
        "administrators": sum(user["role"] == "administrator" for user in users),
        "staff": sum(user["role"] == "staff" for user in users),
    }


def recipient_choices(users):
    """The picker's suggestions: the two groups, then each eligible user.

    Each is ``{"value", "label"}``; the label is what the chip says ("All
    Staff (group, 4 people now)", "Pat Doe — Staff").
    """
    counts = group_counts(users)
    choices = [
        {
            "value": f"group:{group}",
            "label": str(
                _("%(name)s (group, %(count)s now)")
                % {
                    "name": GROUP_NAMES[group],
                    "count": _people(counts[group]),
                }
            ),
        }
        for group in ("administrators", "staff")
    ]
    choices += [
        {
            "value": f"user:{user['id']}",
            "label": f"{user['name']} — {ROLE_NAMES[user['role']]}",
            "email": user["email"],
        }
        for user in users
    ]
    return choices


def _people(count):
    """ "1 person" or "N people"."""
    from django.utils.translation import ngettext

    return ngettext("%(count)s person", "%(count)s people", count) % {"count": count}


def entry_value(entry):
    """One stored recipient entry as the page's ``recipient`` field value."""
    ((kind, value),) = entry.items()
    return f"{'address' if kind == 'address' else kind}:{value}"


def chip_label(value, choices):
    """What a chosen recipient's chip says."""
    for choice in choices:
        if choice["value"] == value:
            return choice["label"]
    kind, _sep, rest = value.partition(":")
    if kind == "address":
        return str(_("%(address)s — outside address") % {"address": rest})
    return str(_("A removed user"))


def parse_recipients(posted, users):
    """The stored recipient entries of the posted ``recipient`` fields.

    Each field is ``KIND:VALUE``, read into ``{KIND: VALUE}`` and checked
    by ``recipient_entries``. Raises a coded ``ValidationError``.
    """
    entries = []
    for value in posted:
        kind, _sep, rest = value.partition(":")
        entries.append({kind: rest})
    return recipient_entries(entries, users)


UNKNOWN_RECIPIENT = _(
    "A chosen recipient is not a current Administrator or Staff user. Remove it."
)
LEADER_ADDRESS = _(
    "%(address)s is a Ministry leader; Ministry leaders don't receive report "
    "emails. Remove it."
)


def leader_addresses(users):
    """The addresses of the portal users a report may not name, lower case.

    The picker refuses them as they are typed (they are never offered), so
    a Ministry leader's address is never mistaken for an outside address.
    """
    return sorted(
        normalized_email(user["email"])
        for user in users
        if user["email"] and user["role"] not in ROLE_NAMES
    )


def recipient_entries(entries, users):
    """Check a report's recipient entries against the portal ``users`` now.

    The one recipient check of the page and ``report-email preview``
    (``value_problems``). A ``user`` must be an Administrator or Staff user
    now; a ``group`` is All Administrators or All Staff; an ``address`` is
    one valid address, stored in lower case. An address that is a current
    user's own (compared without regard to letter case) is that user's
    entry, so the user's role and removal still apply to it (a Ministry
    leader's address is refused, as their entry is). Returns the
    stored entries; raises a coded ``ValidationError`` naming the first
    problem. ``users`` are every portal user (``portal_users``).
    """
    eligible = {user["id"] for user in users if user["role"] in ROLE_NAMES}
    owners = {
        normalized_email(user["email"]): user["id"] for user in users if user["email"]
    }
    if type(entries) is not list:
        raise ValidationError(UNKNOWN_RECIPIENT, code="recipient_unknown")
    result, seen, addresses = [], set(), 0
    for given in entries:
        kind, rest = _entry_parts(given)
        if kind == "group" and rest in GROUP_NAMES:
            entry = {"group": rest}
        elif kind == "user" and rest in eligible:
            entry = {"user": rest}
        elif kind == "address":
            try:
                forms.EmailField().clean(rest)
            except ValidationError:
                raise ValidationError(
                    _("“%(address)s” is not a valid email address.")
                    % {"address": rest[:254]},
                    code="recipient_address",
                ) from None
            address = normalized_email(rest)
            if address in owners:
                # A portal user's address is that user, held to the same role
                # check as their own entry: a Ministry leader is refused
                # (decision 2), saying why.
                if owners[address] not in eligible:
                    raise ValidationError(
                        LEADER_ADDRESS % {"address": address}, code="recipient_leader"
                    )
                entry = {"user": owners[address]}
            else:
                entry = {"address": address}
                addresses += 1
        else:
            raise ValidationError(UNKNOWN_RECIPIENT, code="recipient_unknown")
        key = tuple(entry.items())
        if key in seen:
            raise ValidationError(
                _("A recipient is chosen twice. Remove one."), code="recipient_twice"
            )
        seen.add(key)
        result.append(entry)
    if not result:
        raise ValidationError(
            _("Choose at least one recipient."), code="recipient_missing"
        )
    if len(result) > RECIPIENT_LIMIT:
        raise ValidationError(
            _("Choose at most %(limit)s recipients.") % {"limit": RECIPIENT_LIMIT},
            code="recipient_limit",
        )
    if addresses > ADDRESS_LIMIT:
        raise ValidationError(
            _("Choose at most %(limit)s outside addresses.") % {"limit": ADDRESS_LIMIT},
            code="recipient_address_limit",
        )
    return result


def _entry_parts(entry):
    """A stored entry's kind and value; ``(None, None)`` unless ``{KIND: text}``."""
    if type(entry) is dict and len(entry) == 1:
        ((kind, value),) = entry.items()
        if type(value) is str:
            return kind, value
    return None, None


def parse_blocks(posted, days_shown, charts=None):
    """The stored block list of the posted ``block`` keys, in their order.

    Each block at most once, at its registered version; the Day by day
    table takes ``days_shown`` and Participation charts ``charts`` (None
    stores no choice: the default, all); a block placed after the button is
    kept last. Raises a coded ``ValidationError``.
    """
    if not posted:
        raise ValidationError(_("Add at least one block."), code="block_missing")
    if len(set(posted)) != len(posted):
        raise ValidationError(_("A block is in the report twice."), code="block_twice")
    entries = []
    for key in posted:
        block = REGISTRY.get(key)
        if block is None:
            raise ValidationError(
                _("A block is no longer available."), code="block_unknown"
            )
        options = {}
        if key == "day_by_day":
            options = {"days": days_shown}
        elif key == "participation_charts" and charts is not None:
            options = {"charts": charts}
        entries.append({"block": key, "version": block.version, "options": options})
    # An after-the-button block always follows the others (spec, "Blocks
    # field"); the page keeps it last too.
    entries.sort(key=lambda entry: REGISTRY[entry["block"]].placement == AFTER_BUTTON)
    return entries


class EditorForm(AdminForm):
    """The editor's fields, read into one report's complete values.

    ``campaign`` is the applied campaign values (dates and zone),
    ``saved`` the report's saved values (None on New), ``others`` the
    campaign's other reports' values (for the unique name), ``users`` every
    portal user (``portal_users``), ``now`` the database's clock and
    ``drawn`` the moment the page drew a saved schedule's time from.
    ``values()`` is the result once valid.
    """

    # The report's own name, not the From name (spec, "New and Edit report
    # email"); the subject is the email's subject line.
    name = forms.CharField(label=_("Report name"), max_length=NAME_LIMIT)
    subject = forms.CharField(
        label=_("Email subject line"), max_length=254, required=False
    )
    sends = forms.ChoiceField(
        label=_("Sends"),
        choices=(("once", _("Once")), ("repeat", _("Repeating"))),
    )
    once_date = forms.DateField(label=_("Date"), required=False)
    time = FlexibleTimeField(label=_("Time"), required=False)
    editor_mode = forms.ChoiceField(
        choices=(("basic", _("Basic")), ("advanced", _("Advanced"))),
        required=False,
    )
    preset = forms.ChoiceField(
        label=_("Frequency"),
        choices=tuple(PRESET_LABELS.items()),
        required=False,
    )
    preset_weekday = forms.ChoiceField(
        label=_("Day"), choices=DAY_CHOICES, required=False
    )
    frequency = forms.ChoiceField(
        label=_("Frequency"),
        choices=(("daily", _("Daily")), ("weekly", _("Weekly"))),
        required=False,
    )
    weekdays = forms.MultipleChoiceField(
        label=_("On these days"), choices=DAY_CHOICES, required=False
    )
    from_kind = forms.ChoiceField(label=_("From"), choices=FROM_CHOICES, required=False)
    from_date = forms.DateField(label=_("From date"), required=False)
    until_kind = forms.ChoiceField(
        label=_("Until"), choices=UNTIL_CHOICES, required=False
    )
    until_date = forms.DateField(label=_("Until date"), required=False)
    as_of = forms.ChoiceField(label=_("As of"), choices=tuple(AS_OF_LABELS.items()))
    days_shown = forms.IntegerField(
        label=_("Days shown"), min_value=1, max_value=MAX_DAYS_SHOWN, required=False
    )
    charts = forms.MultipleChoiceField(
        label=_("Charts"), choices=tuple(CHART_LABELS.items()), required=False
    )
    content_required = forms.BooleanField(
        label=_("Send only when there is something to report"), required=False
    )
    # The introduction's fields keep the content editors' names, so their
    # "Generate plain text from HTML" panel (ui-v1.js, data-plain-text)
    # serves this editor too.
    html = forms.CharField(
        label=_("Introduction (HTML)"),
        required=False,
        strip=False,
        widget=forms.Textarea(attrs={"rows": 6}),
    )
    generate_text = forms.BooleanField(
        label=_("Generate plain text from HTML"), required=False
    )
    text = forms.CharField(
        label=_("Introduction (plain text)"),
        required=False,
        strip=False,
        widget=forms.Textarea(attrs={"rows": 6}),
    )
    zone = forms.CharField(required=False)
    drawn = forms.CharField(required=False)
    # The repeated recipient and block fields are read in order by
    # ``clean`` (``getlist``); declared so their refusals sit at them.
    recipient = forms.Field(label=_("Recipients"), required=False)
    block = forms.Field(label=_("Blocks"), required=False)

    def __init__(
        self, data, *, campaign, campaign_id, saved, others, users, now, sent=False
    ):
        """Bind the posted fields and remember what the checks compare with.

        ``sent`` says the report has made a scheduled send, which fixes
        whether it sends once or repeats (spec, "Report list").
        """
        super().__init__(data, auto_id="report-%s")
        self.campaign, self.campaign_id = campaign, str(campaign_id)
        self.saved, self.others = saved, others
        self.users, self.now, self.sent = users, now, sent
        self._values = None
        if saved is not None:
            # A saved time with seconds is kept unchanged.
            self.fields["time"].keep(saved["schedule"]["time"])

    def _clock(self, name):
        """The typed time (a ``time``), or a coded error at the field."""
        clock = self.cleaned_data.get(name)
        if clock is None:
            self.add_error(
                name, ValidationError(_("Enter a time."), code="time_missing")
            )
        return clock

    def _schedule(self, data):
        """The stored schedule from the cleaned fields, or None with errors."""
        zone = data.get("zone") or ""
        campaign_zone = self.campaign["timezone"]
        saved = (self.saved or {}).get("schedule")
        if data["sends"] == "once":
            day, clock = data.get("once_date"), self._clock("time")
            if day is None:
                self.add_error(
                    "once_date",
                    ValidationError(_("Enter a date."), code="date_missing"),
                )
            if day is None or clock is None:
                return None
            kept = (
                {"date": saved["date"], "time": saved["time"], "weekday": None}
                if saved and saved["type"] == "once"
                else None
            )
            try:
                local_day, local_time, _weekday = schedule_local.to_campaign(
                    day,
                    clock,
                    None,
                    zone=zone,
                    campaign_zone=campaign_zone,
                    now=self.now,
                    saved=kept,
                )
            except UnknownZone:
                self.add_error(None, ValidationError(ZONE_MISSING, code="zone_missing"))
                return None
            except schedule_local.RepeatedHour:
                self.add_error(
                    "time", ValidationError(REPEATED_HOUR, code="time_repeated")
                )
                return None
            return {
                "type": "once",
                "date": local_day.isoformat(),
                "time": local_time.isoformat(timespec="seconds"),
            }
        rule = self._rule(data)
        clock = self._clock("time")
        if rule is None or clock is None:
            return None
        kept = (
            {"date": None, "time": saved["time"], "weekday": None}
            if saved and saved["type"] == "repeat"
            else None
        )
        try:
            _day, local_time, _weekday = schedule_local.to_campaign(
                None,
                clock,
                None,
                zone=zone,
                campaign_zone=campaign_zone,
                now=self.now,
                saved=kept,
                drawn=data.get("drawn") or None,
            )
        except UnknownZone:
            self.add_error(None, ValidationError(ZONE_MISSING, code="zone_missing"))
            return None
        return rule | {"time": local_time.isoformat(timespec="seconds")}

    def _rule(self, data):
        """A repeating rule from Basic's preset or Advanced's fields."""
        if data.get("editor_mode") == "basic":
            preset = data.get("preset")
            if preset not in PRESETS:
                self.add_error(
                    "preset",
                    ValidationError(_("Choose how it repeats."), code="preset_missing"),
                )
                return None
            rule = {"type": "repeat"} | PRESETS[preset]
            if preset == "weekly":
                if data.get("preset_weekday") in (None, ""):
                    self.add_error(
                        "preset_weekday",
                        ValidationError(_("Choose a day."), code="day_missing"),
                    )
                    return None
                rule["weekdays"] = [int(data["preset_weekday"])]
            return rule
        frequency = data.get("frequency")
        if frequency not in ("daily", "weekly"):
            self.add_error(
                "frequency",
                ValidationError(_("Choose how often."), code="frequency_missing"),
            )
            return None
        rule = {"type": "repeat", "frequency": frequency}
        if frequency == "weekly":
            days = sorted({int(day) for day in data.get("weekdays") or ()})
            if not days:
                self.add_error(
                    "weekdays",
                    ValidationError(
                        _("Choose at least one day."), code="weekdays_missing"
                    ),
                )
                return None
            rule["weekdays"] = days
        first = self._bound(data, "from", FROM_BOUNDS)
        last = self._bound(data, "until", UNTIL_BOUNDS)
        if first is None or last is None:
            return None
        if (
            first not in FROM_BOUNDS
            and last not in UNTIL_BOUNDS
            and date.fromisoformat(last) < date.fromisoformat(first)
        ):
            self.add_error(
                "until_date",
                ValidationError(
                    _("Until must not be before From."), code="until_before_from"
                ),
            )
            return None
        return rule | {"from": first, "until": last}

    def _bound(self, data, name, bounds):
        """One repeat bound: a campaign choice or a chosen date."""
        kind = data.get(f"{name}_kind")
        if kind in bounds:
            return kind
        if kind != "date":
            self.add_error(
                f"{name}_kind",
                ValidationError(_("Choose one."), code=f"{name}_missing"),
            )
            return None
        chosen = data.get(f"{name}_date")
        if chosen is None:
            self.add_error(
                f"{name}_date",
                ValidationError(_("Enter a date."), code=f"{name}_date_missing"),
            )
            return None
        return chosen.isoformat()

    def clean(self):
        """Build the complete values; each refusal is at its field."""
        data = super().clean()
        # Every list is checked even when a plain field is refused, so the
        # reader sees each problem at once.
        refused = bool(self.errors)
        name = " ".join((data.get("name") or "").split())
        try:
            recipients = parse_recipients(self.data.getlist("recipient"), self.users)
        except ValidationError as error:
            self.add_error("recipient", error)
            recipients = None
        charts = self._charts(data)
        try:
            blocks = parse_blocks(
                self.data.getlist("block"), data.get("days_shown") or 5, charts
            )
        except ValidationError as error:
            self.add_error("block", error)
            blocks = None
        # A name another report has is said in the same round as every other
        # refusal (#1964), not only once the rest is right.
        taken = name_taken(name, self.others) if name else None
        if taken is not None:
            self.add_error("name", taken)
        saved = self.saved
        if refused:
            return data
        schedule = self._schedule(data)
        if self.errors or recipients is None or blocks is None or schedule is None:
            return data
        values = {
            "campaign_id": self.campaign_id,
            "kind": "report",
            "name": name,
            "subject": data.get("subject") or "",
            **_introduction(
                data.get("html") or "",
                data.get("text") or "",
                generate=bool(data.get("generate_text")),
            ),
            "recipients": recipients,
            "schedule": schedule,
            "as_of": data["as_of"],
            "blocks": blocks,
            "content_required": bool(data.get("content_required")),
            # Skips, pauses and the slot basis belong to the report, not the
            # form: they are kept as saved (a new report keys by send date).
            "skip_dates": list((saved or {}).get("skip_dates", [])),
            "paused_ranges": list((saved or {}).get("paused_ranges", [])),
            "slot_basis": (saved or {}).get("slot_basis", "send_date"),
        }
        problems = value_problems(
            values,
            saved=saved,
            others=self.others,
            users=self.users,
            campaign=self.campaign,
            now=self.now,
            sent=self.sent,
        )
        for field, error in problems:
            if error.code != "name_taken":  # Added above already.
                self.add_error(field, error)
        if not problems:
            self._values = values
        return data

    def _charts(self, data):
        """The Participation charts block's stored choice from its boxes.

        None when the block is not in the report. Boxes left as the page
        ticked them for the saved report keep its saved choice, even one the
        campaign no longer offers (pledges after financial stewardship was
        turned off), as the command line does; changed boxes store what is
        ticked, in ``stored_charts``' form. A box the campaign does not
        offer is not read. No box ticked is refused at the Charts group.
        """
        if "participation_charts" not in self.data.getlist("block"):
            return None
        offered = offered_charts(self.campaign)
        ticked = [key for key in offered if key in (data.get("charts") or ())]
        if not ticked:
            self.add_error(
                "charts",
                ValidationError(_("Choose at least one chart."), code="charts_missing"),
            )
        if ticked == ticked_charts(self.saved, self.campaign):
            return chart_choice(self.saved)
        return stored_charts(ticked, self.saved, self.campaign)

    def values(self):
        """The report's complete values, once the form is valid."""
        return self._values

    def schedule_only(self):
        """The stored schedule the fields describe, or None while unreadable.

        For the live schedule preview, after ``is_valid`` has cleaned what
        it could: the other fields' errors do not matter there, and this
        one's are dropped, since the preview only says it cannot draw yet.
        """
        data = getattr(self, "cleaned_data", None) or {}
        if data.get("sends") not in ("once", "repeat"):
            return None
        # The form's own error dict (an ErrorDict, which the page draws)
        # is restored as it was: a refused page draws its table too.
        saved_errors = copy.deepcopy(self._errors)
        schedule = self._schedule(data)
        self._errors = saved_errors
        return schedule


def _introduction(html, text, *, generate):
    """The stored introduction: sanitized HTML and its plain text.

    With ``generate`` the plain text is generated from the HTML, as the
    content editors generate it; an empty HTML part means no introduction.
    """
    if not html.strip() and not text.strip():
        return {"intro_html": "", "intro_text": ""}
    prepared = prepare_content(html, text=None if generate else text)
    return {"intro_html": prepared.html, "intro_text": prepared.text}


CONTENT_REFUSED = _(
    "Check the subject and introduction: use only the listed placeholders, "
    "and no files or images."
)


def one_time_due(schedule, zone):
    """A one-time schedule's send instant in the campaign's ``zone``."""
    return resolve_local(
        datetime.combine(
            date.fromisoformat(schedule["date"]), time.fromisoformat(schedule["time"])
        ),
        zone,
    )


# Each part of a report's values that ``report_values`` may refuse
# (``report_values.PARTS``): the field it is shown at (None: the page's
# summary only), its problem in plain words and its code. On the page only
# the subject and introduction can fail here, every other value being built
# from checked fields; a ``report-email preview`` document can fail any.
PART_PROBLEMS = {
    "form": (
        None,
        _(
            "The report email's values can't be read. Give only name, subject, "
            "intro_html, intro_text, recipients, schedule, as_of, blocks and "
            "content_required."
        ),
        "values_invalid",
    ),
    "name": (
        "name",
        _("Enter a name of 1 to 80 characters, on one line."),
        "name_invalid",
    ),
    "content": ("html", CONTENT_REFUSED, "content_refused"),
    "recipients": (
        "recipient",
        _("Choose 1 to 50 recipients, each listed once."),
        "recipient_invalid",
    ),
    "schedule": (
        "sends",
        _(
            "The schedule can't be read. A one-time send has a date and a time; "
            "a repeating one has how often (and a weekly one its days), From, "
            "Until, not before From, and a time."
        ),
        "schedule_invalid",
    ),
    "as_of": (
        "as_of",
        _("Choose As of: the end of the previous day, or when the email is made."),
        "as_of_invalid",
    ),
    "blocks": (
        "block",
        _(
            "Check the blocks: 1 to 6, each at most once, at its current version, "
            "with only its own options (Days shown is a whole number from 1 to "
            "14; Charts lists at least one chart, each once, in their order); a "
            "block that follows the dashboard button is last."
        ),
        "block_invalid",
    ),
    "content_required": (
        "content_required",
        _("Send only when there is something to report is either true or false."),
        "content_required_invalid",
    ),
    "sends": (
        None,
        _("The report email's saved skips and pauses can't be read."),
        "sends_invalid",
    ),
}


def name_taken(name, others):
    """The refusal of a name another report of the campaign has, or None.

    Names are compared case-folded with their spaces collapsed.
    """
    if name_key(name) in {name_key(other["name"]) for other in others}:
        return ValidationError(
            _("Another report email of this campaign has this name."), code="name_taken"
        )
    return None


def value_problems(values, *, saved, others, users, campaign, now, sent):
    """What keeps one report's complete values from being saved.

    The checks the page and ``report-email preview`` share, each as
    ``(field, coded ValidationError)``: the recipients against the portal
    ``users`` now (``recipient_entries``, which also stores a current
    user's address as that user, so it updates ``values["recipients"]`` in
    place), each part of the stored form that is refused, named by its
    field (``PART_PROBLEMS``), a name another report of the campaign has, a
    one-time send at or before ``now`` (an unchanged saved one is kept),
    and, once the report has made a scheduled send (``sent``), a change
    between sending once and repeating (spec, "Report list"). Empty when
    the values may be saved.
    """
    problems = []
    try:
        values["recipients"] = recipient_entries(values.get("recipients"), users)
    except ValidationError as error:
        problems.append(("recipient", error))
    refused = refused_parts(values)
    for part in refused:
        if part == "recipients" and problems:
            continue  # Already named by the recipient check.
        field, message, code = PART_PROBLEMS[part]
        problems.append((field, ValidationError(message, code=code)))
    # A taken name is named with every other problem (#1964).
    if "form" not in refused and "name" not in refused:
        taken = name_taken(values["name"], others)
        if taken is not None:
            problems.append(("name", taken))
    if refused:
        # The checks below read values the stored form guarantees.
        return problems
    # Pledges need financial stewardship; a saved choice made before it was
    # turned off stays, as the block then draws the charts the campaign has.
    choice = chart_choice(values)
    if (
        PLEDGES in (choice or ())
        and PLEDGES not in offered_charts(campaign)
        and (saved is None or chart_choice(saved) != choice)
    ):
        problems.append(
            (
                "charts",
                ValidationError(
                    _(
                        "Effective annual pledges needs financial stewardship, "
                        "which this campaign does not have."
                    ),
                    code="charts_pledges",
                ),
            )
        )
    schedule = values["schedule"]
    if saved is not None and sent and schedule["type"] != saved["schedule"]["type"]:
        problems.append(
            (
                "sends",
                ValidationError(
                    _(
                        "This report has made a scheduled send, so whether it "
                        "sends once or repeats can no longer change."
                    ),
                    code="sends_fixed",
                ),
            )
        )
    unchanged = saved is not None and saved["schedule"] == schedule
    if schedule["type"] == "once" and not unchanged:
        due = one_time_due(schedule, campaign["timezone"])
        if due <= now:
            problems.append(
                (
                    "time",
                    ValidationError(
                        _("This send time has passed."), code="time_passed"
                    ),
                )
            )
    return problems


ZONE_MISSING = _(
    "Your browser didn't report a time zone, so the send time can't be read. "
    "Check your computer's time zone setting, then reload this page."
)
REPEATED_HOUR = _(
    "That time happens twice that day, when the clocks go back. Choose a time "
    "outside that hour."
)


def initial(values, campaign, now):
    """The fields' starting values for a saved report (None: New's blank form).

    Times and dates are the campaign-local ones; the page script rewrites
    them in the browser's zone from the moment ``moment`` returns.
    """
    if values is None:
        return {
            "sends": "repeat",
            "editor_mode": "basic",
            "frequency": "daily",
            "from_kind": "campaign_start",
            "until_kind": "day_after_campaign_end",
            "as_of": "previous_day_end",
            "days_shown": 5,
        }
    schedule = values["schedule"]
    data = {
        "name": values["name"],
        "subject": values["subject"],
        "sends": schedule["type"],
        "time": schedule["time"],
        "as_of": values["as_of"],
        "content_required": values["content_required"],
        "html": values["intro_html"],
        "text": values["intro_text"],
        "generate_text": not values["intro_html"]
        or prepare_content(values["intro_html"]).text == values["intro_text"],
        "days_shown": next(
            (
                entry["options"].get("days", 5)
                for entry in values["blocks"]
                if entry["block"] == "day_by_day"
            ),
            5,
        ),
    }
    if schedule["type"] == "once":
        return data | {"once_date": schedule["date"], "editor_mode": "basic"}
    first, last = schedule["from"], schedule["until"]
    data |= {
        "frequency": schedule["frequency"],
        "weekdays": [str(day) for day in schedule.get("weekdays", ())],
        "from_kind": first if first in FROM_BOUNDS else "date",
        "from_date": None if first in FROM_BOUNDS else first,
        "until_kind": last if last in UNTIL_BOUNDS else "date",
        "until_date": None if last in UNTIL_BOUNDS else last,
    }
    preset = basic_preset(schedule)
    data["editor_mode"] = "basic" if preset else "advanced"
    if preset:
        data["preset"] = preset
        if preset == "weekly":
            data["preset_weekday"] = str(schedule["weekdays"][0])
    return data


def basic_preset(schedule):
    """The Basic preset a repeating rule is, or None (it opens in Advanced)."""
    for key, preset in PRESETS.items():
        if all(schedule.get(name) == value for name, value in preset.items()) and (
            key != "weekly" or len(schedule.get("weekdays", ())) == 1
        ):
            return key
    return None


def moment(values, campaign, now):
    """The instant the page fills the time (and once, the date) fields from.

    A one-time report's send; a repeating report's next send from ``now``
    with its pauses and skips set aside, else its first. None on New.
    """
    if values is None:
        return None
    from .report_email_list import plan

    schedule = values["schedule"]
    zone = campaign["timezone"]
    if schedule["type"] == "once":
        return one_time_due(schedule, zone)
    free = plan(values | {"paused_ranges": [], "skip_dates": []}, campaign)
    found = free.next_slot(after=now) or next(
        iter(free.page(through=datetime.max.replace(tzinfo=UTC), limit=1).slots), None
    )
    if found is not None:
        return found.due_at
    today = now.astimezone(ZoneInfo(zone)).date()
    return resolve_local(
        datetime.combine(today, time.fromisoformat(schedule["time"])), zone
    )


def after_campaign(values, campaign, now):
    """The first send after the campaign's last day still ahead, or None.

    Decision 9 asks before a report sends after the campaign ends. Skips
    and pauses are set aside: the question is what the rule may send. Once
    every such send is past (``now``), there is nothing left to ask.
    """
    from .report_email_list import plan

    end = date.fromisoformat(campaign["end_date"])
    start = resolve_local(
        datetime.combine(end + timedelta(days=1), time()), campaign["timezone"]
    )
    free = plan(values | {"paused_ranges": [], "skip_dates": []}, campaign)
    return free.next_slot(
        after=max(start - timedelta(microseconds=1), now).astimezone(UTC)
    )


PREVIEW_SENDS = 5


def _upcoming(values, campaign, now):
    """The report's sends after ``now``, one more than a preview shows.

    Each is ``(Occurrence, slot key, state)``: its line for the shared
    schedule preview (the send, and as of the end of the previous day the
    day it reports on; a send after the campaign's last day says so), and
    ``state`` "send", "skipped" or "paused". Skipped and paused sends are
    kept, struck through with their reason, so a reader sees them.
    """
    from parishkit.stewardship.occurrence_preview import Occurrence
    from parishkit.stewardship.web import presentation

    from .report_email_list import plan

    free = plan(values | {"paused_ranges": [], "skip_dates": []}, campaign)
    pauses = [
        (
            datetime.fromisoformat(entry["start"]),
            entry["end"] and datetime.fromisoformat(entry["end"]),
        )
        for entry in values["paused_ranges"]
    ]
    end = date.fromisoformat(campaign["end_date"])
    zone = ZoneInfo(campaign["timezone"])
    found, after = [], now
    while len(found) <= PREVIEW_SENDS:
        slot = free.next_slot(after=after)
        if slot is None:
            break
        after = slot.due_at
        day = slot.due_at.astimezone(zone).date()
        what = values["name"]
        if values["as_of"] == "previous_day_end":
            what = str(
                _("%(name)s for %(day)s")
                % {
                    "name": values["name"],
                    "day": f"{WEEKDAYS[(day - timedelta(days=1)).weekday()]}, "
                    f"{presentation.parish_date(day - timedelta(days=1))}",
                }
            )
        if day > end:
            what = str(_("%(what)s, after the campaign ends") % {"what": what})
        state, reason = "send", None
        if slot.key in values["skip_dates"]:
            state, reason = "skipped", str(_("(skipped)"))
        elif any(
            start <= slot.due_at and (stop is None or slot.due_at < stop)
            for start, stop in pauses
        ):
            state, reason = "paused", str(_("(paused)"))
        occurrence = Occurrence(
            day=None,
            at=slot.due_at,
            what=what,
            struck=reason is not None,
            reason=reason,
        )
        found.append((occurrence, slot.key, state))
    return found


def _preview(found, campaign, now):
    """The shared schedule preview's context for ``_upcoming``'s sends."""
    from parishkit.stewardship.occurrence_preview import build

    return build(
        [occurrence for occurrence, _key, _state in found],
        schedule_zone=campaign["timezone"],
        now=now,
        limit=PREVIEW_SENDS,
        empty=str(_("No more sends.")),
    )


def sends_preview(values, campaign, now):
    """The schedule preview of a report's next five sends (spec, "Schedule fields").

    Drawn by the shared component (``occurrence_preview.build``), zone-less,
    so each line is a full browser-local instant, counted from now; the
    review dialog shows it. Skipped and paused sends stay listed, struck
    through, with their reason.
    """
    return _preview(_upcoming(values, campaign, now), campaign, now)


# The Next sends table's Status words (spec, "Next sends table").
STATUS_WORDS = {
    "send": _("Will send"),
    "skipped": _("Skipped"),
    "paused": _("Paused"),
    "worked": _("Being prepared"),
}


def sends_table(values, campaign, now, *, bulk=False, worked=None):
    """The editor's Next sends table: the next five sends with their status.

    The rows are the shared preview's lines (``sends_preview``) with each
    send's slot key and its Status in words: Will send, Skipped, Paused, or
    Being prepared (a send with Production work). ``bulk`` (Edit of a
    repeating report) gives the table its selection column and bulk bar;
    ``worked(slot key)`` is given too while the schedule is as saved: each
    send that will send or is skipped, still ahead and without Production
    work, then takes a box, whose kind (``skip`` or ``send``) says which of
    Skip selected sends and Send selected sends it fits. Without it no row
    has a box, and ``changed`` says the boxes wait for the schedule change
    to be applied. Returns the preview's context with ``rows``, ``bulk``
    and ``changed``.
    """
    found = _upcoming(values, campaign, now)
    preview = _preview(found, campaign, now)
    items = preview["days"][0]["items"] if preview["days"] else []
    rows = []
    for item, (_occurrence, key, state) in zip(items, found, strict=False):
        shown = state
        kind = None
        if worked is not None and state in ("send", "skipped"):
            if worked(key):
                shown = "worked"
            else:
                kind = "skip" if state == "send" else "send"
        rows.append(
            item
            | {
                "slot": key,
                "state": state,
                "status": STATUS_WORDS[shown],
                "kind": kind,
            }
        )
    return preview | {"rows": rows, "bulk": bulk, "changed": bulk and worked is None}


def describe(values, campaign, now, choices):
    """A report's values in plain words, for the review dialog.

    ``choices`` are the recipient picker's choices, which name each entry.
    None for a side that does not exist (a new report's before).
    """
    if values is None:
        return None
    from .report_email_list import schedule_words

    blocks = []
    for entry in values["blocks"]:
        block = REGISTRY.get(entry["block"])
        label = block.label if block else entry["block"]
        if entry["block"] == "day_by_day":
            label = str(
                _("%(label)s (%(days)s days)")
                % {"label": label, "days": entry["options"].get("days", 5)}
            )
        elif entry["block"] == "participation_charts":
            # The charts the block draws: the choice, as the campaign has them.
            names = ", ".join(
                str(CHART_LABELS[key]) for key in ticked_charts(values, campaign)
            )
            # A kept choice the campaign cannot draw is named too, so the
            # review shows when a change drops it.
            kept = [
                str(CHART_LABELS[key])
                for key in chart_choice(values) or ()
                if key not in offered_charts(campaign)
            ]
            if kept:
                names = str(
                    _("%(charts)s; kept, not drawn: %(kept)s")
                    % {"charts": names, "kept": ", ".join(kept)}
                )
            label = str(_("%(label)s (%(charts)s)") % {"label": label, "charts": names})
        blocks.append(label)
    return {
        "name": values["name"],
        "subject": values["subject"],
        "recipients": [
            chip_label(entry_value(entry), choices) for entry in values["recipients"]
        ],
        "schedule": schedule_words(values, campaign, now),
        "as_of": AS_OF_LABELS[values["as_of"]],
        "blocks": blocks,
        "content_required": values["content_required"],
        "intro": bool(values["intro_html"] or values["intro_text"]),
        "sends": sends_preview(values, campaign, now),
    }


def blank_values(campaign, campaign_id):
    """New's starting values, under the schedule its fields hold.

    Next sends draws New's sends from these with the form's own schedule
    (``drawn_values``); the daily 7:00 AM schedule here is never drawn,
    since New's blank form has no schedule yet (#1972).
    """
    return {
        "campaign_id": str(campaign_id),
        "kind": "report",
        "name": str(_("New report email")),
        "subject": "",
        "intro_html": "",
        "intro_text": "",
        "recipients": [{"group": "administrators"}],
        "schedule": {"type": "repeat", **PRESETS["daily"], "time": "07:00:00"},
        "as_of": "previous_day_end",
        "blocks": [{"block": "campaign_totals", "version": 1, "options": {}}],
        "content_required": False,
        "skip_dates": [],
        "paused_ranges": [],
        "slot_basis": "send_date",
    }


def drawn_values(form, saved, campaign, campaign_id):
    """The report values whose sends Next sends draws for ``form``, or None.

    An unbound form draws Edit's saved report as it is, and New's blank
    form nothing: its schedule and time are not chosen yet, so the table
    says to fill them in rather than drawing a schedule the reader has not
    chosen (#1972). A bound form (a refused Review, or the live redraw,
    after ``is_valid``) draws the schedule its fields hold, over the saved
    report or New's blank values, and nothing while they cannot be read.
    """
    if not form.is_bound:
        return saved
    schedule = form.schedule_only()
    if not schedule:
        return None
    base = saved or blank_values(campaign, campaign_id)
    return base | {
        "schedule": schedule,
        "as_of": form.cleaned_data.get("as_of") or base["as_of"],
        "name": form.cleaned_data.get("name") or base["name"],
    }

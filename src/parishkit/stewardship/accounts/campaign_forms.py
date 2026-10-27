"""Typed draft fields; source choices and saved identities are server-owned.

This form deliberately has no lifecycle, credential, content-version, or task
fields. Rendering an old field never confers permission to change a locked
campaign. The owning view binds the complete candidate to current runtime state.
"""

from copy import deepcopy
from datetime import date, timedelta

from django import forms
from django.utils.translation import gettext_lazy as _

from parishkit.config import ConfigError
from parishkit.stewardship.campaigns.configuration import (
    MINISTRY_LEADER_ROLES,
    campaign_values,
    leader_role_key,
)
from parishkit.stewardship.campaigns.intervals import financial_period_end
from parishkit.stewardship.campaigns.leader_roles import canonical_roles
from parishkit.stewardship.web.admin_forms import AdminForm
from parishkit.stewardship.web.presentation import parish_date

from . import field_tips
from .share_forms import default_share_options
from .timezone_field import TimezoneField

# How to use a plain multi-select list, shown with every one of them.
MULTI_SELECT_HELP = _(
    "To choose several, hold Ctrl (Cmd on a Mac) while clicking; to choose a "
    "range, click the first item and hold Shift while clicking the last."
)
# The one line each multi-select list keeps visible; its tip has the rest.
MULTI_SELECT_HINTS = dict.fromkeys(
    ("ministry_duids", "fund_duids", "comparison_fund_duids"),
    _("Hold Ctrl (Cmd on a Mac) to choose several, or Shift for a range."),
)
# What the Ministry leader roles decide (#922), in plain words.
LEADER_ROLES_HELP = _(
    "A Ministry leader sees the reports and follow-up of each Ministry where "
    "they hold one of these ParishSoft Ministry roles, matched by an email "
    "address their ParishSoft Member record lists. To sign in, a leader also "
    "needs a user; import leaders on the Users page. Choose at least one "
    "role; changes take effect on their next page."
)
# The financial fields Financial stewardship requires.
FINANCIAL_REQUIRED = (
    "financial_start",
    "financial_end",
    "comparison_start",
    "comparison_end",
    "fund_duids",
    "comparison_fund_duids",
)
# Each end date and the start it must follow (#563). The browser takes the
# start as the end's min (data-not-before, ui-v1.js) and the server puts an
# order or one-year refusal at the end field, not at the top of the page.
DATE_PAIRS = (
    ("start_date", "end_date"),
    ("financial_start", "financial_end"),
    ("comparison_start", "comparison_end"),
)


def _as_date(value):
    """A date from a form value (date or ISO text), or None when not a date."""
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)) if value else None
    except ValueError:
        return None


# The closed code of the refusal below, on Campaign settings, Dates and
# mail schedules and through pk-admin alike.
COMMITMENT_AFTER_END = "commitment_before_campaign_end"


def starts_after_campaign(campaign_end, period_start):
    """Whether a commitment period starting on ``period_start`` starts after
    a campaign ending on ``campaign_end`` (#2293).

    The commitment period is the one period pledges and Ministry requests
    apply to (data spec, "Commitment period"), so it may not begin before
    the campaign that asks for them has ended. This replaced the overlap
    confirmation. Incomplete or invalid dates are not judged here.
    """
    end, start = _as_date(campaign_end), _as_date(period_start)
    return end is None or start is None or start > end


def first_commitment_day(campaign_end):
    """The earliest commitment period start a campaign end allows, in words."""
    return parish_date(_as_date(campaign_end) + timedelta(days=1))


class SourceChoices(forms.MultipleChoiceField):
    """Accept only distinct catalog IDs, with deterministic integer serialization."""

    def clean(self, value):
        """Reject repeated IDs rather than silently changing the submitted intent."""
        values = super().clean(value)
        if len(set(values)) != len(values):
            raise forms.ValidationError(
                _("Select each item only once."), code="repeated"
            )
        return sorted(int(item) for item in values)


class LeaderRoleChoices(forms.MultipleChoiceField):
    """Ministry leader role names, ticked from the offered labels (#922)."""

    widget = forms.CheckboxSelectMultiple

    def __init__(self, **kwargs):
        """Every use has the same label and plain-language help."""
        super().__init__(
            label=_("Ministry leader roles"), help_text=LEADER_ROLES_HELP, **kwargs
        )

    def clean(self, value):
        """Refuse repeats, even in another case, and store the canonical order."""
        values = super().clean(value)
        if len({leader_role_key(name) for name in values}) != len(values):
            raise forms.ValidationError(
                _("Select each role only once."), code="repeated"
            )
        return canonical_roles(values)


class CampaignForm(AdminForm):
    """Complete structural fields, validated against the same canonical YAML rules."""

    name = forms.CharField(label=_("Campaign name"), max_length=254)
    year_label = forms.CharField(
        label=_("Stewardship year label"), max_length=64, required=False
    )
    timezone = TimezoneField(label=_("Campaign timezone"))
    start_date = forms.DateField(
        label=_("Campaign start date"), widget=forms.DateInput(attrs={"type": "date"})
    )
    end_date = forms.DateField(
        label=_("Campaign end date"), widget=forms.DateInput(attrs={"type": "date"})
    )
    census = forms.BooleanField(label=_("Census"), required=False)
    ministry = forms.BooleanField(label=_("Ministry stewardship"), required=False)
    financial_enabled = forms.BooleanField(
        label=_("Financial stewardship"), required=False
    )
    ministry_duids = SourceChoices(
        label=_("Included Ministries"), required=False, help_text=MULTI_SELECT_HELP
    )
    financial_start = forms.DateField(
        label=_("Upcoming financial period start"),
        required=False,
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    financial_end = forms.DateField(
        label=_("Upcoming financial period end"),
        required=False,
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    comparison_start = forms.DateField(
        label=_("Comparison financial period start"),
        required=False,
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    comparison_end = forms.DateField(
        label=_("Comparison financial period end"),
        required=False,
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    fund_duids = SourceChoices(
        label=_("Upcoming financial period funds"),
        required=False,
        help_text=MULTI_SELECT_HELP,
    )
    comparison_fund_duids = SourceChoices(
        label=_("Comparison financial period funds"),
        required=False,
        help_text=MULTI_SELECT_HELP,
    )
    additional_information = forms.BooleanField(
        label=_("Collect additional information"), required=False
    )
    base_digest = forms.RegexField(
        regex=r"^[0-9a-f]{64}$", max_length=64, widget=forms.HiddenInput
    )

    def __init__(
        self, *args, ministries=(), funds=(), previous=None, whole=True, **kwargs
    ):
        """Do not trust posted source labels or let fields replace saved content.

        ``whole`` False skips the checks between the dates and the financial
        periods (the commitment period's start and the whole campaign's
        rules): a
        live campaign's Campaign settings (#1119) changes only its end date
        and leader roles, and its end date is judged by its own review
        (``campaign_end_date.end_review``), in that review's own words.
        """
        super().__init__(*args, **kwargs)
        self.whole = whole
        self.previous = deepcopy(previous or {})
        self.share_options = deepcopy(self.previous.get("share_options", []))
        if "financial" not in self.previous.get("modules", []):
            self.share_options = default_share_options()
        self.fields["ministry_duids"].choices = ministries
        for name in ("fund_duids", "comparison_fund_duids"):
            self.fields[name].choices = funds
        # Entering a period start fills an empty end (ui-v1.js); the server
        # still checks that each period spans exactly one year.
        for start, end in (
            ("financial_start", "financial_end"),
            ("comparison_start", "comparison_end"),
        ):
            self.fields[start].widget.attrs["data-fills-end"] = self.add_prefix(end)
        # clean() requires these while Financial stewardship is ticked; the
        # page's complete gate (ui-v1.js) makes them required in the browser
        # then too, so Review waits for them instead of being refused (#563).
        financial_on = f"{self.add_prefix('financial_enabled')}=on"
        for name in FINANCIAL_REQUIRED:
            self.fields[name].widget.attrs["data-required-when"] = financial_on
        # One help line under each field (#2130): longer help, such as the
        # multi-select how-to and the leader roles', opens from its "i".
        # A view's setup_help.apply may replace the help and shorten again.
        field_tips.shorten(self, MULTI_SELECT_HINTS)
        if whole:
            self._mark_date_order()

    def _mark_date_order(self):
        """Let the browser hold an end date before its start (#563).

        Each end names its start by element id (data-not-before), so the page
        script makes the start its min and the complete gate holds Review with
        the end's hint until the order is right; a financial period's end
        (data-one-year) must be exactly one year after its start, so that day
        is its min and max (#1577). A financial end left empty keeps the
        financial fieldset's own hint (data-empty-hint). The server still
        checks the order and the one-year span (_date_errors). The commitment
        period's start names the campaign end as its own (data-not-before)
        and must come strictly after it (data-strictly-after), so its min is
        the day after the campaign end and the browser holds a start on or
        before that day, as the server does (starts_after_campaign). A live
        campaign's end date (``whole`` False) has its own live check instead.
        """
        hints = {
            "end_date": _("Choose a campaign end date after its start date."),
            "financial_end": _(
                "Choose an upcoming financial period end one year after its start."
            ),
            "comparison_end": _(
                "Choose a comparison financial period end one year after its start."
            ),
        }
        for start, end in DATE_PAIRS:
            attrs = self.fields[end].widget.attrs
            # The id the start is drawn with (BoundField.auto_id), worked out
            # without binding it: a BoundField made now would be cached
            # before field_tips swaps in its tip-aware class.
            attrs["data-not-before"] = self.auto_id % self.add_prefix(start)
            attrs["data-missing-hint"] = hints[end]
            if end != "end_date":
                attrs["data-empty-hint"] = _(
                    "Fill in both financial periods and their funds."
                )
                # Exactly the day before the start's first anniversary, as
                # _date_errors requires (#1577): the page script makes that
                # day the end's min and max.
                attrs["data-one-year"] = True
        attrs = self.fields["financial_start"].widget.attrs
        attrs["data-not-before"] = self.auto_id % self.add_prefix("end_date")
        attrs["data-strictly-after"] = True
        attrs["data-missing-hint"] = _(
            "Choose a commitment period start after the campaign end date."
        )
        attrs["data-empty-hint"] = _("Fill in both financial periods and their funds.")

    def _commitment_dates_changed(self, data):
        """Whether the campaign end or the commitment period start differs
        from the saved one (#2293).

        The rule that the period starts after the campaign is judged only
        then, so a campaign saved before #2293 with a confirmed overlap
        still saves its other settings (it keeps its stored answer, see
        values()). A new campaign has no saved dates, so it is always
        judged.
        """
        saved = (self.previous.get("financial") or {}).get("start")
        return (
            data["end_date"].isoformat() != self.previous.get("end_date")
            or data["financial_start"].isoformat() != saved
        )

    def _date_errors(self, data):
        """Put a date order or one-year refusal at the end field it is about.

        The rules are campaign_values' own: a campaign ends strictly after it
        starts, and each financial period ends the day before its start's
        first anniversary (financial_period_end). Returns whether any was
        found, so clean() can skip the whole-campaign check's generic
        message for a problem already shown at its field.
        """
        found = False
        if data["end_date"] <= data["start_date"]:
            self.add_error(
                "end_date",
                forms.ValidationError(
                    _("The campaign end date must be after its start date."),
                    code="end_before_start",
                ),
            )
            found = True
        if data["financial_enabled"]:
            for start, end in DATE_PAIRS[1:]:
                try:
                    expected = financial_period_end(data[start])
                except (ValueError, OverflowError):
                    self.add_error(
                        start,
                        forms.ValidationError(
                            _("Choose an earlier start date."), code="out_of_range"
                        ),
                    )
                    found = True
                    continue
                if data[end] != expected:
                    self.add_error(
                        end,
                        forms.ValidationError(
                            _(
                                "A financial period spans exactly one year: with this "
                                "start, it ends on %(date)s."
                            )
                            % {"date": parish_date(expected)},
                            code="financial_period_length",
                        ),
                    )
                    found = True
        return found

    def clean(self):
        """Reject module-dependent stray data and require complete financial periods."""
        data = super().clean()
        if self.errors:
            return data
        if not any(data[name] for name in ("census", "ministry", "financial_enabled")):
            raise forms.ValidationError(
                _("Enable at least one campaign module."), code="choose_one"
            )
        if not data["ministry"] and data["ministry_duids"]:
            self.add_error(
                "ministry_duids",
                forms.ValidationError(
                    _("Enable Ministry stewardship to select Ministries."),
                    code="module_disabled",
                ),
            )
        if not data["financial_enabled"]:
            if any(data[name] for name in FINANCIAL_REQUIRED):
                raise forms.ValidationError(
                    _("Financial settings require Financial stewardship."),
                    code="module_disabled",
                )
        else:
            for name in FINANCIAL_REQUIRED:
                if not data[name]:
                    self.add_error(
                        name,
                        forms.ValidationError(
                            _("Required for financial stewardship."), code="required"
                        ),
                    )
            if (
                not self.errors
                and self.whole
                and self._commitment_dates_changed(data)
                and not starts_after_campaign(data["end_date"], data["financial_start"])
            ):
                self.add_error(
                    "financial_start",
                    forms.ValidationError(
                        _(
                            "The commitment period must start after the campaign "
                            "ends: choose %(date)s or later."
                        )
                        % {"date": first_commitment_day(data["end_date"])},
                        code=COMMITMENT_AFTER_END,
                    ),
                )
        if not self.errors and self.whole and not self._date_errors(data):
            try:
                campaign_values(self.values())
            except (ConfigError, ValueError):
                raise forms.ValidationError(
                    _(
                        "Check the campaign dates and settings. The end date must "
                        "follow the start date, each period must span exactly one "
                        "year, and the commitment period must start after the "
                        "campaign ends."
                    ),
                    code="invalid_campaign",
                ) from None
        return data

    def values(self):
        """Build canonical values, retaining applicable saved optional slots."""
        data = self.cleaned_data
        modules = sorted(
            name
            for name, field in (
                ("census", "census"),
                ("ministry", "ministry"),
                ("financial", "financial_enabled"),
            )
            if data[field]
        )
        financial = None
        if data["financial_enabled"]:
            financial = {
                "start": data["financial_start"].isoformat(),
                "end": data["financial_end"].isoformat(),
                "comparison_start": data["comparison_start"].isoformat(),
                "comparison_end": data["comparison_end"].isoformat(),
                "fund_duids": data["fund_duids"],
                "comparison_fund_duids": data["comparison_fund_duids"],
                # The retired overlap confirmation (#2293): a saved answer
                # is kept, so saving changes nothing else, and a new period
                # stores false. The key stays, as configuration requires.
                "overlap_confirmed": bool(
                    (self.previous.get("financial") or {}).get("overlap_confirmed")
                ),
            }
        allowed_slots = {"welcome", "closing", "review", "thank_you", *modules}
        if data["additional_information"]:
            allowed_slots.add("additional")
        return {
            "name": data["name"],
            "year_label": data["year_label"] or None,
            "timezone": data["timezone"],
            "start_date": data["start_date"].isoformat(),
            "end_date": data["end_date"].isoformat(),
            "modules": modules,
            "ministry_duids": data["ministry_duids"],
            "financial": financial,
            "share_options": deepcopy(self.share_options) if financial else [],
            "content_versions": {
                key: value
                for key, value in self.previous.get("content_versions", {}).items()
                if key in allowed_slots
            },
            "additional_information": data["additional_information"],
        }

    @property
    def general_fields(self):
        """The campaign's name, time zone and dates, shown first."""
        return [
            self[name]
            for name in ("name", "year_label", "timezone", "start_date", "end_date")
        ]

    @property
    def module_fields(self):
        """The module boxes, kept outside the conditionally hidden groups.

        The template groups them so the page can require at least one, as
        clean() does.
        """
        return [self[name] for name in ("census", "ministry", "financial_enabled")]

    @property
    def financial_fields(self):
        """Expose a stable field order in the module's accessible fieldset."""
        return [
            self[name]
            for name in (
                "financial_start",
                "financial_end",
                "fund_duids",
                "comparison_start",
                "comparison_end",
                "comparison_fund_duids",
            )
        ]


def initial_fields(values, *, digest):
    """Map an applied record to form fields, without importing any runtime state."""
    result = {
        name: values[name]
        for name in (
            "name",
            "year_label",
            "timezone",
            "start_date",
            "end_date",
            "ministry_duids",
            "additional_information",
        )
    }
    result.update(
        base_digest=digest,
        census="census" in values["modules"],
        ministry="ministry" in values["modules"],
        financial_enabled="financial" in values["modules"],
    )
    if values["financial"]:
        financial = deepcopy(values["financial"])
        financial["financial_start"] = financial.pop("start")
        financial["financial_end"] = financial.pop("end")
        # Stored, but no longer a field (#2293).
        financial.pop("overlap_confirmed", None)
        result.update(financial)
    return result


class CampaignSettingsForm(CampaignForm):
    """Campaign settings' draft form: the shared fields plus leader roles (#922).

    The leader roles sit with the Ministry selections and matter only while
    Ministry stewardship is on. ``leader_roles`` is the campaign's effective
    list now (its saved value or the default), so leaving the default ticked
    never writes the key. Copy campaign keeps the shared form; Create the
    campaign uses this one, starting on the default list (#1404 PR 3).
    """

    ministry_leader_roles = LeaderRoleChoices(required=False)

    def __init__(self, *args, leader_choices=(), leader_roles=(), **kwargs):
        """Offer server-built role choices beside the effective list."""
        super().__init__(*args, **kwargs)
        self.fields["ministry_leader_roles"].choices = leader_choices
        self.leader_roles = canonical_roles(leader_roles)

    def clean(self):
        """Ministry stewardship needs at least one leader role."""
        data = super().clean()
        if (
            not self.has_error("ministry_leader_roles")
            and data.get("ministry")
            and not data.get("ministry_leader_roles")
        ):
            self.add_error(
                "ministry_leader_roles",
                forms.ValidationError(
                    _("Choose at least one role."), code="choose_one"
                ),
            )
        return data

    def values(self):
        """Add the leader roles only when they differ from what is in effect.

        A saved value is kept as it is while Ministry stewardship is off or
        nothing is ticked (which clean() refuses), so validation never sees an
        empty list.
        """
        values = super().values()
        roles = self.cleaned_data.get("ministry_leader_roles") or []
        if "ministry" in values["modules"] and roles and roles != self.leader_roles:
            values[MINISTRY_LEADER_ROLES] = roles
        elif MINISTRY_LEADER_ROLES in self.previous:
            values[MINISTRY_LEADER_ROLES] = deepcopy(
                self.previous[MINISTRY_LEADER_ROLES]
            )
        return values

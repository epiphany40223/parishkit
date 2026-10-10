"""Typed draft fields; source choices and saved identities are server-owned.

This form deliberately has no lifecycle, credential, content-version, or task
fields. Rendering an old field never confers permission to change a locked
campaign. The owning view binds the complete candidate to current runtime state.
"""

from copy import deepcopy
from datetime import date

from django import forms
from django.utils.html import format_html_join
from django.utils.translation import gettext_lazy as _

from parishkit.config import ConfigError
from parishkit.stewardship.campaigns.configuration import (
    MINISTRY_LEADER_ROLES,
    campaign_values,
    leader_role_key,
)
from parishkit.stewardship.campaigns.leader_roles import canonical_roles
from parishkit.stewardship.schema_primitives import timezone_names

from .share_forms import default_share_options

# How to use a plain multi-select list, shown with every one of them.
MULTI_SELECT_HELP = _(
    "To choose several, hold Ctrl (Cmd on a Mac) while clicking; to choose a "
    "range, click the first item and hold Shift while clicking the last."
)
# What the Ministry leader roles decide (#922), in plain words.
LEADER_ROLES_HELP = _(
    "A Ministry leader sees the reports and follow-up of each Ministry where "
    "they hold one of these ParishSoft Ministry roles, matched by an email "
    "address their ParishSoft Member record lists. Signing in still needs a "
    "sign-in rule. Choose at least one role; changes take effect on their "
    "next page."
)
# The campaign-overlap confirmation renders through this field template so it
# can be shown only while the entered dates overlap (see overlap_attributes).
OVERLAP_TEMPLATE = "stewardship/overlap-field.html"
# The financial fields Financial stewardship requires (the overlap
# confirmation is required only while the dates overlap).
FINANCIAL_REQUIRED = (
    "financial_start",
    "financial_end",
    "comparison_start",
    "comparison_end",
    "fund_duids",
    "comparison_fund_duids",
)


def _as_date(value):
    """A date from a form value (date or ISO text), or None when not a date."""
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)) if value else None
    except ValueError:
        return None


def overlaps(campaign_start, campaign_end, period_start, period_end):
    """Whether a financial period overlaps the campaign (inclusive days).

    This is the rule campaign_values() enforces: such an overlap needs an
    explicit confirmation. Incomplete or invalid dates never need one yet.
    """
    days = [
        _as_date(value)
        for value in (campaign_start, campaign_end, period_start, period_end)
    ]
    if None in days:
        return False
    return days[2] <= days[1] and days[3] >= days[0]


def overlap_attributes(sources):
    """Data attributes telling ui-v1.js where the four overlap dates come from.

    ``sources`` maps campaign-start, campaign-end, period-start and period-end
    to ("name", form field name) or ("value", ISO date) pairs.
    """
    return format_html_join(
        " ",
        'data-{}-{}="{}"',
        ((key, kind, value) for key, (kind, value) in sources.items()),
    )


class SourceChoices(forms.MultipleChoiceField):
    """Accept only distinct catalog IDs, with deterministic integer serialization."""

    def clean(self, value):
        """Reject repeated IDs rather than silently changing the submitted intent."""
        values = super().clean(value)
        if len(set(values)) != len(values):
            raise forms.ValidationError(_("Select each item only once."))
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
            raise forms.ValidationError(_("Select each role only once."))
        return canonical_roles(values)


class LeaderRolesForm(forms.Form):
    """A live campaign's one leader-role setting, against the applied digest."""

    ministry_leader_roles = LeaderRoleChoices(
        error_messages={"required": _("Choose at least one role.")}
    )
    base_digest = forms.RegexField(
        regex=r"^[0-9a-f]{64}$", max_length=64, widget=forms.HiddenInput
    )

    def __init__(self, *args, roles=(), **kwargs):
        """Offer only server-built choices (campaigns.leader_roles.role_choices).

        Its element ids differ from the read-only settings form's on the same
        page; the field names are the same, as the view expects.
        """
        kwargs.setdefault("auto_id", "leader_%s")
        super().__init__(*args, **kwargs)
        self.fields["ministry_leader_roles"].choices = roles


class CampaignForm(forms.Form):
    """Complete structural fields, validated against the same canonical YAML rules."""

    name = forms.CharField(label=_("Campaign name"), max_length=254)
    year_label = forms.CharField(
        label=_("Stewardship year label"), max_length=64, required=False
    )
    timezone = forms.ChoiceField(label=_("Campaign timezone"))
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
    overlap_confirmed = forms.BooleanField(
        label=_("I confirm that the upcoming financial period overlaps this campaign"),
        required=False,
    )
    additional_information = forms.BooleanField(
        label=_("Collect additional information"), required=False
    )
    base_digest = forms.RegexField(
        regex=r"^[0-9a-f]{64}$", max_length=64, widget=forms.HiddenInput
    )

    def __init__(self, *args, ministries=(), funds=(), previous=None, **kwargs):
        """Do not trust posted source labels or let fields replace saved content."""
        super().__init__(*args, **kwargs)
        self.previous = deepcopy(previous or {})
        self.share_options = deepcopy(self.previous.get("share_options", []))
        if "financial" not in self.previous.get("modules", []):
            self.share_options = default_share_options()
        self.fields["timezone"].choices = [
            (zone, zone) for zone in sorted(timezone_names())
        ]
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
        self.fields["overlap_confirmed"].template_name = OVERLAP_TEMPLATE

    @property
    def overlap_needed(self):
        """Whether the entered (or saved) dates need the overlap confirmation."""
        return overlaps(
            *(
                self[name].value()
                for name in (
                    "start_date",
                    "end_date",
                    "financial_start",
                    "financial_end",
                )
            )
        )

    @property
    def overlap_attributes(self):
        """Where the page script reads the dates that decide the confirmation."""
        return overlap_attributes(
            {
                key: ("name", self.add_prefix(name))
                for key, name in (
                    ("campaign-start", "start_date"),
                    ("campaign-end", "end_date"),
                    ("period-start", "financial_start"),
                    ("period-end", "financial_end"),
                )
            }
        )

    def clean(self):
        """Reject module-dependent stray data and require complete financial periods."""
        data = super().clean()
        if self.errors:
            return data
        if not any(data[name] for name in ("census", "ministry", "financial_enabled")):
            raise forms.ValidationError(_("Enable at least one campaign module."))
        if not data["ministry"] and data["ministry_duids"]:
            self.add_error(
                "ministry_duids", _("Enable Ministry stewardship to select Ministries.")
            )
        financial_fields = (*FINANCIAL_REQUIRED, "overlap_confirmed")
        if not data["financial_enabled"]:
            if any(data[name] for name in financial_fields):
                raise forms.ValidationError(
                    _("Financial settings require financial stewardship.")
                )
        else:
            for name in FINANCIAL_REQUIRED:
                if not data[name]:
                    self.add_error(name, _("Required for financial stewardship."))
            if (
                not self.errors
                and overlaps(
                    data["start_date"],
                    data["end_date"],
                    data["financial_start"],
                    data["financial_end"],
                )
                and not data["overlap_confirmed"]
            ):
                self.add_error(
                    "overlap_confirmed",
                    _(
                        "The upcoming financial period overlaps the campaign "
                        "dates. Check this box to confirm that is intended."
                    ),
                )
        if not self.errors:
            try:
                campaign_values(self.values())
            except (ConfigError, ValueError):
                raise forms.ValidationError(
                    _(
                        "Check the campaign dates and settings. The end date must "
                        "follow the start date; each financial period must span "
                        "exactly one year. Confirm any campaign overlap."
                    )
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
                "overlap_confirmed": data["overlap_confirmed"],
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
                "overlap_confirmed",
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
        result.update(financial)
    return result


class CampaignSettingsForm(CampaignForm):
    """Campaign settings' draft form: the shared fields plus leader roles (#922).

    The leader roles sit with the Ministry selections and matter only while
    Ministry stewardship is on. ``leader_roles`` is the campaign's effective
    list now (its saved value or the default), so leaving the default ticked
    never writes the key. The setup wizard and Copy campaign keep the shared
    form: a new campaign starts with the default.
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
            self.add_error("ministry_leader_roles", _("Choose at least one role."))
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

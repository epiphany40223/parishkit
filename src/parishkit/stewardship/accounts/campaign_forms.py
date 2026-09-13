"""Typed draft fields; source choices and saved identities are server-owned.

This form deliberately has no lifecycle, credential, content-version, or task
fields. Rendering an old field never confers permission to change a locked
campaign. The owning view binds the complete candidate to current runtime state.
"""

from copy import deepcopy

from django import forms
from django.utils.translation import gettext_lazy as _

from parishkit.config import ConfigError
from parishkit.stewardship.campaigns.configuration import campaign_values
from parishkit.stewardship.schema_primitives import timezone_names

from .share_forms import default_share_options


class SourceChoices(forms.MultipleChoiceField):
    """Accept only distinct catalog IDs, with deterministic integer serialization."""

    def clean(self, value):
        """Reject repeated IDs rather than silently changing the submitted intent."""
        values = super().clean(value)
        if len(set(values)) != len(values):
            raise forms.ValidationError(_("Select each item only once."))
        return sorted(int(item) for item in values)


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
    ministry_duids = SourceChoices(label=_("Included Ministries"), required=False)
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
        label=_("Upcoming financial period funds"), required=False
    )
    comparison_fund_duids = SourceChoices(
        label=_("Comparison financial period funds"), required=False
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
        financial_fields = (
            "financial_start",
            "financial_end",
            "comparison_start",
            "comparison_end",
            "fund_duids",
            "comparison_fund_duids",
            "overlap_confirmed",
        )
        if not data["financial_enabled"]:
            if any(data[name] for name in financial_fields):
                raise forms.ValidationError(
                    _("Financial settings require financial stewardship.")
                )
        else:
            for name in financial_fields[:-1]:
                if not data[name]:
                    self.add_error(name, _("Required for financial stewardship."))
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
        allowed_slots = {"welcome", "review", "thank_you", *modules}
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
        """Keep semantic module controls outside the conditionally hidden groups."""
        return [
            self[name]
            for name in (
                "name",
                "year_label",
                "timezone",
                "start_date",
                "end_date",
                "census",
                "ministry",
                "financial_enabled",
                "additional_information",
            )
        ]

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

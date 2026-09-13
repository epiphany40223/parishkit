"""Bounded logical mail schedules and combined draft-date reconciliation."""

from uuid import uuid4

from django import forms
from django.forms import BaseFormSet, formset_factory
from django.utils.translation import gettext_lazy as _

from parishkit.config import ConfigError
from parishkit.stewardship.campaigns.configuration import (
    campaign_values,
    schedule_values,
)
from parishkit.stewardship.schema_primitives import timezone_names

from .content_forms import EMAIL_LABELS

KINDS = ("initial", "reminder", "daily_digest", "weekly_digest")
WEEKDAYS = (
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
)


class ScheduleWindow(forms.Form):
    """Draft dates may change together with mail; locked structural inputs are inert."""

    timezone = forms.ChoiceField(label=_("Campaign timezone"))
    start_date = forms.DateField(
        label=_("Campaign start date"), widget=forms.DateInput(attrs={"type": "date"})
    )
    end_date = forms.DateField(
        label=_("Campaign end date"), widget=forms.DateInput(attrs={"type": "date"})
    )
    overlap_confirmed = forms.BooleanField(
        required=False,
        label=_("Acknowledge that the financial period overlaps the campaign"),
    )

    def __init__(self, *args, previous, editable, proposed=None, **kwargs):
        """Authoritative initial values, not POST values, own every disabled control."""
        self.previous = previous
        self.proposed = bool(proposed)
        initial = {
            name: previous[name] for name in ("timezone", "start_date", "end_date")
        }
        initial["overlap_confirmed"] = bool(
            previous["financial"] and previous["financial"]["overlap_confirmed"]
        )
        if proposed:
            if not editable or set(proposed) - {"timezone", "start_date", "end_date"}:
                raise ValueError("Invalid proposed campaign window.")
            initial.update(proposed)
        super().__init__(*args, initial=initial, **kwargs)
        self.fields["timezone"].choices = [
            (zone, zone) for zone in sorted(timezone_names())
        ]
        if previous["financial"] is None:
            del self.fields["overlap_confirmed"]
        for field in self.fields.values():
            field.disabled = not editable

    def clean(self):
        """Validate the whole resulting campaign, not dates in isolation."""
        values = super().clean()
        if self.errors:
            return values
        try:
            campaign_values(self.values())
        except ConfigError:
            raise forms.ValidationError(
                _("Check the campaign dates, timezone and financial overlap.")
            ) from None
        return values

    def values(self):
        """Return a full unchanged-module campaign with the explicitly edited window."""
        values = self.previous | {
            name: self.cleaned_data[name] for name in ("timezone",)
        }
        values.update(
            {
                name: self.cleaned_data[name].isoformat()
                for name in ("start_date", "end_date")
            }
        )
        if values["financial"] is not None:
            values["financial"] = values["financial"] | {
                "overlap_confirmed": self.cleaned_data["overlap_confirmed"],
            }
        return values


class ScheduleForm(forms.Form):
    """Stable schedule identity, local civil time, and an existing email revision."""

    id = forms.UUIDField(required=False, widget=forms.HiddenInput)
    kind = forms.ChoiceField(
        label=_("Mail type"),
        choices=[
            ("", _("Choose a mail type")),
            *((kind, EMAIL_LABELS[kind]) for kind in KINDS),
        ],
    )
    date = forms.DateField(
        label=_("Local date (initial/reminder only)"),
        required=False,
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    time = forms.TimeField(
        label=_("Local time"),
        widget=forms.TimeInput(format="%H:%M:%S", attrs={"type": "time", "step": "1"}),
    )
    weekday = forms.TypedChoiceField(
        label=_("Day of week (weekly digest only)"),
        required=False,
        coerce=int,
        empty_value=None,
        choices=[
            ("", _("Not weekly")),
            *((index, _(day)) for index, day in enumerate(WEEKDAYS)),
        ],
    )
    template_version = forms.ChoiceField(label=_("Email template and subject"))

    def clean_time(self):
        """Civil schedules have whole seconds, never an offset or truncated fraction."""
        value = self.cleaned_data["time"]
        if value.tzinfo is not None or value.microsecond:
            raise forms.ValidationError(_("Use a local time with whole seconds."))
        return value

    def __init__(self, *args, templates, **kwargs):
        """Offer email revisions and only this row's unresolved legacy value."""
        self.templates = templates
        super().__init__(*args, **kwargs)
        choices = [
            (
                row["id"],
                f"{EMAIL_LABELS[row['values']['slot']]} — {row['values']['subject']} "
                f"({row['id'][:8]})",
            )
            for row in templates
            if row["values"]["kind"] == "email" and row["values"]["slot"] in KINDS
        ]
        selected = self.initial.get("template_version")
        if selected and selected not in {key for key, _ in choices}:
            choices.append((selected, _("Existing unresolved template (not ready)")))
        self.fields["template_version"].choices = [
            ("", _("Choose a template")),
            *choices,
        ]


class ScheduleSet(BaseFormSet):
    """The server owns saved IDs; missing rows never imply schedule removal."""

    def __init__(self, *args, previous, templates, campaign_id, campaign, **kwargs):
        """Retain complete saved records for identity and legacy-template comparison."""
        self.previous, self.templates = list(previous), list(templates)
        self.campaign_id, self.campaign = str(campaign_id), campaign
        initial = [row["values"] | {"id": row["id"]} for row in self.previous]
        super().__init__(*args, initial=initial, **kwargs)

    def get_form_kwargs(self, index):
        """Template choices are server-provided for initial and new forms alike."""
        return super().get_form_kwargs(index) | {"templates": self.templates}

    def clean(self):
        """Reject forged identities/kinds and validate campaign-local scheduling."""
        if any(self.errors):
            return
        if self.management_form.cleaned_data.get("INITIAL_FORMS") != len(self.previous):
            raise forms.ValidationError(_("Reload schedules before saving."))
        old = {row["id"]: row["values"] for row in self.previous}
        seen = set()
        for form in self.forms:
            identifier = form.cleaned_data.get("id")
            identifier = str(identifier) if identifier else None
            if identifier:
                if identifier not in old or identifier in seen:
                    raise forms.ValidationError(
                        _("Schedule identities have changed. Reload before saving.")
                    )
                seen.add(identifier)
            if (
                form.cleaned_data.get("DELETE")
                or not form.has_changed()
                and not identifier
            ):
                continue
            values = self._values(form, old.get(identifier))
            if identifier and values["kind"] != old[identifier]["kind"]:
                raise forms.ValidationError(
                    _("A saved schedule's mail type cannot change.")
                )
            try:
                schedule_values(values, self.campaign)
            except ConfigError:
                raise forms.ValidationError(
                    _(
                        "Every schedule must fit the campaign dates. Only weekly "
                        "digests take a weekday; only initial/reminder mail "
                        "takes a date."
                    )
                ) from None
        if seen != old.keys():
            raise forms.ValidationError(
                _("Every saved schedule must be retained or explicitly deleted.")
            )

    def _values(self, form, previous):
        """Subjects come from immutable templates, never from browser fields."""
        data = form.cleaned_data
        template = next(
            (
                row["values"]
                for row in self.templates
                if row["id"] == data["template_version"]
            ),
            None,
        )
        if template:
            if (template["campaign_id"], template["kind"], template["slot"]) != (
                self.campaign_id,
                "email",
                data["kind"],
            ):
                raise forms.ValidationError(
                    _("Choose a template for this campaign and mail type.")
                )
            subject = template["subject"]
        elif previous and previous["template_version"] == data["template_version"]:
            subject = previous["subject"]
        else:
            raise forms.ValidationError(_("Choose a configured email template."))
        return {
            "campaign_id": self.campaign_id,
            "kind": data["kind"],
            "date": data["date"].isoformat() if data["date"] else None,
            "time": data["time"].isoformat(timespec="seconds"),
            "weekday": data["weekday"],
            "template_version": data["template_version"],
            "subject": subject,
        }

    def patch(self):
        """Produce explicit changed/add/remove operations with stable identities."""
        if not self.is_valid():
            raise ValueError("Valid schedules are required.")
        old = {row["id"]: row["values"] for row in self.previous}
        result = []
        for form in self.forms:
            identifier = form.cleaned_data.get("id")
            identifier = str(identifier) if identifier else None
            if form in self.deleted_forms:
                if identifier:
                    result.append(
                        {
                            "operation": "remove",
                            "section": "schedules",
                            "id": identifier,
                        }
                    )
                continue
            if not form.has_changed() and not identifier:
                continue
            value = self._values(form, old.get(identifier))
            if value != old.get(identifier):
                result.append(
                    {
                        "operation": "update" if identifier else "add",
                        "section": "schedules",
                        "id": identifier or str(uuid4()),
                        "values": value,
                    }
                )
        return result


Schedules = formset_factory(
    ScheduleForm,
    formset=ScheduleSet,
    extra=1,
    can_delete=True,
    max_num=100,
    validate_max=True,
    absolute_max=101,
)


def schedule_action(
    parameters, *, window_fields, extra_fields=frozenset(), multiple_fields=frozenset()
):
    """Closed scalar field parsing precedes formset allocation and kind validation."""
    from .admin_editing import form_action

    fields = {
        "base_digest",
        "schedules-TOTAL_FORMS",
        "schedules-INITIAL_FORMS",
        "schedules-MIN_NUM_FORMS",
        "schedules-MAX_NUM_FORMS",
        *(f"window-{name}" for name in window_fields),
        *extra_fields,
    }
    total = parameters.get("schedules-TOTAL_FORMS", "")
    preview = parameters.get("action") == "preview"
    if preview and (
        not total.isascii()
        or not total.isdecimal()
        or len(total) > 3
        or str(int(total)) != total
        or int(total) > 101
    ):
        raise ValueError("Invalid schedule count.")
    fields.update(
        f"schedules-{index}-{name}"
        for index in range(int(total) if preview else 0)
        for name in (*ScheduleForm.base_fields, "DELETE")
    )
    return form_action(
        parameters, preview_fields=fields, multiple_fields=multiple_fields
    )

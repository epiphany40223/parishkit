"""Bounded logical mail schedules and combined draft-date reconciliation."""

import json
from datetime import date
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
from parishkit.stewardship.web.presentation import parish_date

from . import field_tips
from .campaign_forms import OVERLAP_TEMPLATE, overlap_attributes, overlaps
from .content_forms import EMAIL_LABELS

KINDS = ("initial", "reminder", "daily_digest", "weekly_digest")
# The fields each mail type uses besides the type itself, as
# configuration.schedule_values requires them. The page script reads this (as
# ScheduleForm.field_rules) to show only these fields.
FIELDS = {
    "initial": ("date", "time", "template_version"),
    "reminder": ("date", "time", "template_version"),
    "daily_digest": ("time", "template_version"),
    "weekly_digest": ("weekday", "time", "template_version"),
}
WEEKDAYS = (
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
)


NO_TEMPLATES = _(
    "No invitation, reminder or digest email is saved yet, so there is nothing "
    "to choose. Save those emails with the page and email templates first."
)


def schedulable(templates):
    """Whether any saved template is an email a schedule can send."""
    return any(
        row["values"]["kind"] == "email" and row["values"]["slot"] in KINDS
        for row in templates
    )


class ScheduleWindow(forms.Form):
    """Draft dates may change together with mail; locked structural inputs are inert."""

    timezone = forms.ChoiceField(
        label=_("Campaign timezone"),
        help_text=_(
            "Every send date and time on this page is in this time zone, not "
            "your computer's."
        ),
    )
    start_date = forms.DateField(
        label=_("Campaign start date"),
        widget=forms.DateInput(attrs={"type": "date"}),
        help_text=_(
            "The first day Families can respond. Invitations and reminders must "
            "be scheduled between the start and end dates."
        ),
    )
    end_date = forms.DateField(
        label=_("Campaign end date"),
        widget=forms.DateInput(attrs={"type": "date"}),
        help_text=_("The last day Families can respond."),
    )
    overlap_confirmed = forms.BooleanField(
        required=False,
        label=_("Acknowledge that the financial period overlaps the campaign"),
        help_text=_(
            "Needed only when the campaign dates and the upcoming financial "
            "period overlap. Check it to confirm that is intended."
        ),
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
        else:
            self.fields["overlap_confirmed"].template_name = OVERLAP_TEMPLATE
        for field in self.fields.values():
            field.disabled = not editable

    @property
    def overlap_needed(self):
        """Whether these campaign dates overlap the (fixed) financial period."""
        financial = self.previous["financial"]
        return financial is not None and overlaps(
            self["start_date"].value(),
            self["end_date"].value(),
            financial["start"],
            financial["end"],
        )

    @property
    def overlap_attributes(self):
        """Editable campaign dates by input name; the period's dates as values."""
        financial = self.previous["financial"] or {}
        return overlap_attributes(
            {
                "campaign-start": ("name", self.add_prefix("start_date")),
                "campaign-end": ("name", self.add_prefix("end_date")),
                "period-start": ("value", financial.get("start", "")),
                "period-end": ("value", financial.get("end", "")),
            }
        )

    def clean(self):
        """Validate the whole resulting campaign, not dates in isolation."""
        values = super().clean()
        if self.errors:
            return values
        if (
            "overlap_confirmed" in self.fields
            and self.overlap_needed
            and not values["overlap_confirmed"]
        ):
            self.add_error(
                "overlap_confirmed",
                _(
                    "These campaign dates overlap the upcoming financial period. "
                    "Check this box to confirm that is intended."
                ),
            )
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


class TemplateSelect(forms.Select):
    """An email list whose options say their mail type, for the page to filter."""

    kinds = {}

    def create_option(self, name, value, *args, **kwargs):
        """Tag an email option with ``data-kind`` when its mail type is known."""
        option = super().create_option(name, value, *args, **kwargs)
        kind = self.kinds.get(str(value))
        if kind:
            option["attrs"]["data-kind"] = kind
        return option


class ScheduleForm(forms.Form):
    """Stable schedule identity, local civil time, and an existing email revision.

    Every field except the mail type is optional here: which of them a row needs
    depends on its mail type (FIELDS), so ScheduleSet.clean reports a missing or
    inapplicable value on that field in words that say what to do.
    """

    id = forms.UUIDField(required=False, widget=forms.HiddenInput)
    kind = forms.ChoiceField(
        label=_("Mail type"),
        choices=[
            ("", _("Choose a mail type")),
            *((kind, EMAIL_LABELS[kind]) for kind in KINDS),
        ],
        error_messages={"required": _("Choose a mail type.")},
        help_text=_(
            "Which email this schedule sends; see the list above for what each "
            "one is. A saved schedule's mail type cannot change."
        ),
    )
    date = forms.DateField(
        label=_("Send date"),
        required=False,
        widget=forms.DateInput(attrs={"type": "date"}),
        help_text=_(
            "Initial invitations and reminders only: the day it is sent. It must "
            "fall within the campaign dates."
        ),
    )
    time = forms.TimeField(
        label=_("Send time"),
        widget=forms.TimeInput(format="%H:%M:%S", attrs={"type": "time", "step": "1"}),
        error_messages={
            "required": _("Enter the time of day it is sent, for example 9:00 AM.")
        },
        help_text=_(
            "The time of day it is sent, in the campaign's time zone rather than "
            "your computer's, for example 9:00 AM."
        ),
    )
    weekday = forms.TypedChoiceField(
        label=_("Day of week"),
        required=False,
        coerce=int,
        empty_value=None,
        choices=[
            ("", _("Not weekly")),
            *((index, _(day)) for index, day in enumerate(WEEKDAYS)),
        ],
        help_text=_("Weekly Admin digests only: the day of the week it is sent."),
    )
    template_version = forms.ChoiceField(
        label=_("Email to send"),
        widget=TemplateSelect,
        error_messages={"required": _("Choose the email to send.")},
        help_text=_(
            "The saved email, with its subject, that this schedule sends. Only "
            "emails of the chosen mail type fit. Emails are written with the page "
            "and email templates."
        ),
    )

    def clean_time(self):
        """Civil schedules have whole seconds, never an offset or truncated fraction."""
        value = self.cleaned_data["time"]
        if value.tzinfo is not None or value.microsecond:
            raise forms.ValidationError(_("Use a local time with whole seconds."))
        return value

    def __init__(self, *args, templates, **kwargs):
        """Offer email revisions and only this row's unresolved legacy value.

        A saved schedule's mail type is fixed (the configuration refuses to
        retype one), so it is shown but not editable, and only emails of that
        type are offered. A disabled field keeps its saved value whatever the
        browser posts.
        """
        self.templates = templates
        super().__init__(*args, **kwargs)
        fixed = self.initial.get("kind") if self.initial.get("id") else None
        if fixed:
            self.fields["kind"].disabled = True
        emails = [
            row
            for row in templates
            if row["values"]["kind"] == "email"
            and row["values"]["slot"] in KINDS
            and fixed in {None, row["values"]["slot"]}
        ]
        choices = [
            (
                row["id"],
                f"{EMAIL_LABELS[row['values']['slot']]} — {row['values']['subject']} "
                f"({row['id'][:8]})",
            )
            for row in emails
        ]
        kinds = {row["id"]: row["values"]["slot"] for row in emails}
        field_tips.shorten(
            self, {"template_version": _("Only emails of the chosen mail type fit.")}
        )
        # After shortening, so this warning always stays visible.
        if not schedulable(templates):
            self.fields["template_version"].help_text = NO_TEMPLATES
        selected = self.initial.get("template_version")
        if selected and selected not in kinds:
            choices.append((selected, _("Existing unresolved template (not ready)")))
            kinds[selected] = self.initial.get("kind")
        self.fields["template_version"].choices = [
            ("", _("Choose a template")),
            *choices,
        ]
        self.fields["template_version"].widget.kinds = kinds

    @property
    def field_rules(self):
        """FIELDS as JSON, for the page script that shows only applicable fields."""
        return json.dumps(FIELDS)


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

    @property
    def has_templates(self):
        """Whether any email is saved that a schedule could send."""
        return schedulable(self.templates)

    @property
    def window(self):
        """The campaign's first and last local days.

        Views may replace ``campaign`` (with proposed dates) after construction,
        so this is read when needed, never cached.
        """
        return tuple(
            date.fromisoformat(self.campaign[name])
            for name in ("start_date", "end_date")
        )

    @property
    def window_text(self):
        """The campaign dates in words, for instructions and date errors."""
        start, end = self.window
        return f"{parish_date(start)} – {parish_date(end)}"

    @property
    def timezone(self):
        """The campaign time zone that every schedule time is in."""
        return self.campaign["timezone"]

    def clean(self):
        """Check each row's fields, then the rows together.

        A problem with one row's values is attached to the offending field, so
        the page says exactly what to change; only problems between rows are
        non-field errors. configuration.schedule_values and
        validate_campaign_sections remain the authority: they are applied here
        and again when the whole configuration is saved.
        """
        if any(self.errors):
            return
        if self.management_form.cleaned_data.get("INITIAL_FORMS") != len(self.previous):
            raise forms.ValidationError(_("Reload schedules before saving."))
        old = {row["id"]: row["values"] for row in self.previous}
        seen, rows, field_errors = set(), [], False
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
            if identifier and form.cleaned_data["kind"] != old[identifier]["kind"]:
                raise forms.ValidationError(
                    _("A saved schedule's mail type cannot change.")
                )
            if self._field_errors(form, old.get(identifier)):
                field_errors = True
                continue
            values = self._values(form, old.get(identifier))
            try:
                due = schedule_values(values, self.campaign)
            except ConfigError:
                # The field checks above mirror schedule_values, so this is
                # only a safety net should the two ever disagree.
                raise forms.ValidationError(
                    _("Check each schedule's date and time against the campaign.")
                ) from None
            rows.append((values["kind"], due))
        if seen != old.keys():
            raise forms.ValidationError(
                _("Every saved schedule must be retained or explicitly deleted.")
            )
        if not field_errors:
            self._check_collection(rows)

    def _field_errors(self, form, previous):
        """Attach each rule this row breaks to its field; return whether any did.

        The rules are those of configuration.schedule_values: initial and
        reminder mail needs a date within the campaign's (inclusive) dates and
        no weekday; a weekly digest needs a weekday and no date; a daily digest
        needs neither. Every schedule sends a saved email of its own mail type.
        """
        data = form.cleaned_data
        kind = data["kind"]
        start, end = self.window
        problems = []
        if "date" not in FIELDS[kind]:
            if data["date"] is not None:
                problems.append(
                    (
                        "date",
                        _(
                            "A date applies only to initial invitations and "
                            "reminders — leave it empty. Digests are sent "
                            "throughout the campaign."
                        ),
                    )
                )
        elif data["date"] is None:
            problems.append(
                (
                    "date",
                    _("Choose the date it is sent, within the campaign (%(window)s)."),
                )
            )
        elif not start <= data["date"] <= end:
            problems.append(
                ("date", _("Choose a date within the campaign (%(window)s)."))
            )
        if "weekday" not in FIELDS[kind]:
            if data["weekday"] is not None:
                problems.append(
                    (
                        "weekday",
                        _(
                            "A weekday applies only to weekly digests — leave it "
                            "at Not weekly."
                        ),
                    )
                )
        elif data["weekday"] is None:
            problems.append(
                ("weekday", _("Choose the day of the week the digest is sent."))
            )
        if self._subject(data, previous) is None:
            problems.append(
                (
                    "template_version",
                    _("Choose an email of this mail type (%(kind)s)."),
                )
            )
        params = {"window": self.window_text, "kind": EMAIL_LABELS[kind]}
        for field, message in problems:
            form.add_error(field, forms.ValidationError(message, params=params))
        return bool(problems)

    def _check_collection(self, rows):
        """Rules between schedules, as validate_campaign_sections applies them.

        ``rows`` holds (kind, due) for every retained schedule; ``due`` is the
        UTC send time of initial/reminder mail and None for digests.
        """
        mail = [(due, kind) for kind, due in rows if due is not None]
        initial = [due for due, kind in mail if kind == "initial"]
        digests = [kind for kind, due in rows if due is None]
        errors = []
        if mail and not initial:
            errors.append(
                _(
                    "Reminders need an initial invitation. Add one Initial "
                    "invitation schedule."
                )
            )
        elif len(initial) > 1:
            errors.append(
                _("Only one Initial invitation is allowed. Delete the extra one.")
            )
        elif any(due <= initial[0] for due, kind in mail if kind == "reminder"):
            errors.append(
                _(
                    "Every reminder must be sent after the initial invitation. "
                    "Choose a later date or time."
                )
            )
        if len({due for due, kind in mail}) != len(mail):
            errors.append(
                _(
                    "Two emails to Families cannot be sent at the same date and "
                    "time. Change one of them."
                )
            )
        if len(set(digests)) != len(digests):
            errors.append(
                _(
                    "Only one Daily Admin digest and one Weekly Admin digest are "
                    "allowed. Delete the extra one."
                )
            )
        if errors:
            raise forms.ValidationError(errors)

    def _subject(self, data, previous):
        """The chosen email's subject, or None when this row may not send it.

        Subjects come from immutable templates, never from browser fields. An
        unresolved legacy template stays acceptable only on the saved row that
        already uses it.
        """
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
                return None
            return template["subject"]
        if previous and previous["template_version"] == data["template_version"]:
            return previous["subject"]
        return None

    def _values(self, form, previous):
        """The configuration record for one valid row."""
        data = form.cleaned_data
        subject = self._subject(data, previous)
        if subject is None:
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
    # A blank new row has nothing to delete; only saved rows offer Delete.
    can_delete_extra=False,
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

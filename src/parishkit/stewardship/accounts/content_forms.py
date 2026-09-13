"""Named page/email revision editing with sample-only inert rendering."""

from datetime import date
from uuid import uuid4

from django import forms
from django.utils.translation import gettext_lazy as _

from parishkit.stewardship.web.content import (
    MAX_TEXT_BYTES,
    PLACEHOLDERS,
    prepare_content,
    render_template,
    validate_template,
)
from parishkit.stewardship.web.presentation import parish_date

PAGE_LABELS = {
    "welcome": _("Family welcome"),
    "login_help": _("Family login help"),
    "pre_start": _("Before the campaign"),
    "post_end": _("After the campaign"),
    "census": _("Family census introduction"),
    "member_census": _("Member census introduction"),
    "ministry": _("Ministry introduction"),
    "financial": _("Financial introduction"),
    "additional": _("Additional information prompt"),
    "review": _("Review and attestation introduction"),
    "thank_you": _("Thank You page"),
    "access_denied": _("Access-denied contact help"),
    "submission_confirmation": _("Submission confirmation"),
}
EMAIL_LABELS = {
    "initial": _("Initial invitation"),
    "reminder": _("Reminder"),
    "confirmation": _("Submission receipt"),
    "daily_digest": _("Daily Admin digest"),
    "weekly_digest": _("Weekly Admin digest"),
    "critical_alert": _("Critical alert"),
}
LEGACY_PAGE_REFERENCES = frozenset(
    {"welcome", "census", "ministry", "financial", "additional", "review", "thank_you"}
)


def page_slots(campaign):
    """Expose enabled module introductions; retained disabled text stays inert."""
    excluded = {
        slot
        for slot, module in (
            ("census", "census"),
            ("member_census", "census"),
            ("ministry", "ministry"),
            ("financial", "financial"),
        )
        if module not in campaign["modules"]
    }
    if not campaign["additional_information"]:
        excluded.add("additional")
    return {slot: label for slot, label in PAGE_LABELS.items() if slot not in excluded}


class ContentForm(forms.Form):
    """HTML is sanitized before preview; plaintext can be generated or edited."""

    base_digest = forms.CharField(widget=forms.HiddenInput(), max_length=64)
    subject = forms.CharField(label=_("Email subject"), max_length=254)
    html = forms.CharField(
        label=_("HTML source"),
        required=False,
        max_length=MAX_TEXT_BYTES,
        strip=False,
        widget=forms.Textarea(attrs={"rows": 12}),
    )
    generate_text = forms.BooleanField(
        label=_("Generate plain text from HTML"), required=False, initial=True
    )
    text = forms.CharField(
        label=_("Plain-text version"),
        required=False,
        max_length=MAX_TEXT_BYTES,
        strip=False,
        widget=forms.Textarea(attrs={"rows": 8}),
    )
    clear = forms.BooleanField(label=_("Remove this selected content"), required=False)

    def __init__(self, *args, kind, **kwargs):
        """Page content cannot carry a subject or masquerade as an email template."""
        self.kind = kind
        super().__init__(*args, **kwargs)
        if kind == "page":
            del self.fields["subject"]

    def clean(self):
        """Validate sanitized output and substitutions before creating any request."""
        values = super().clean()
        if self.errors or values.get("clear"):
            return values
        try:
            prepared = prepare_content(
                values["html"], text=None if values["generate_text"] else values["text"]
            )
            validate_template(prepared.html)
            validate_template(prepared.text)
            if self.kind == "email":
                validate_template(values["subject"], subject=True)
            values["prepared"] = prepared
        except ValueError:
            self.add_error(
                None,
                _(
                    "Use bounded content and only the documented placeholders. "
                    "Email subjects must be one line."
                ),
            )
        return values

    def values(self, *, campaign_id, slot):
        """Return one canonical revision payload, or explicit removal intent."""
        if self.cleaned_data["clear"]:
            return None
        prepared = self.cleaned_data["prepared"]
        return {
            "campaign_id": str(campaign_id),
            "kind": self.kind,
            "slot": slot,
            "subject": self.cleaned_data.get("subject"),
            "html": prepared.html,
            "text": prepared.text,
        }


def sample_render(value, *, parish, campaign):
    """Never look up a real Family or generate a live code/link for a sample preview."""
    if value is None:
        return None
    substitutions = {
        "parish_name": parish["name"],
        "parish_website": parish.get("website", "https://example.invalid/"),
        "parish_phone": parish.get("phone", "+12025550100"),
        "campaign_name": campaign["name"],
        "campaign_start": parish_date(date.fromisoformat(campaign["start_date"])),
        "campaign_end": parish_date(date.fromisoformat(campaign["end_date"])),
        "campaign_timezone": campaign["timezone"],
        "campaign_year": campaign.get("year_label") or campaign["start_date"][:4],
        "financial_start": parish_date(
            date.fromisoformat(campaign["financial"]["start"])
        )
        if campaign["financial"]
        else "",
        "financial_end": parish_date(date.fromisoformat(campaign["financial"]["end"]))
        if campaign["financial"]
        else "",
        "family_name": "Sample Family",
        "family_member_names": "Alex and Sam Sample",
        "family_code": "SAMPLE",
        "family_url": "https://example.invalid/sample-family",
        "generic_family_url": "https://example.invalid/",
        "pronoun": "We",
        "financial_period": (
            parish_date(date.fromisoformat(campaign["financial"]["start"]))
            + " – "
            + parish_date(date.fromisoformat(campaign["financial"]["end"]))
            if campaign["financial"]
            else "Sample financial period"
        ),
    }
    assert set(substitutions) == PLACEHOLDERS
    return {
        "html": render_template(value["html"], substitutions, html=True),
        "text": render_template(value["text"], substitutions),
        "subject": render_template(value["subject"], substitutions, subject=True)
        if value["subject"] is not None
        else None,
    }


def revision_patch(document, campaign, previous, values):
    """Replace one revision and reconcile every referencing page or mail schedule.

    Distinct reminder schedules may select different email revisions. Replacing
    a shared revision updates exactly its consumers, never every template of
    that kind. Removing a referenced email is refused until schedules change.
    """
    patch, affected = [], []
    if previous and previous["values"] == values:
        return patch, affected
    if previous is None and values is None:
        return patch, affected
    reference = str(uuid4()) if values is not None else None
    if previous:
        patch.append(
            {"operation": "remove", "section": "content", "id": previous["id"]}
        )
    if values is not None:
        patch.append(
            {
                "operation": "add",
                "section": "content",
                "id": reference,
                "values": values,
            }
        )
    selected = values or previous["values"]
    if selected["kind"] == "page" and selected["slot"] in LEGACY_PAGE_REFERENCES:
        mapping = dict(campaign.active_configuration.values["content_versions"])
        if reference:
            mapping[selected["slot"]] = reference
        else:
            mapping.pop(selected["slot"], None)
        patch.append(
            {
                "operation": "update",
                "section": "campaigns",
                "id": str(campaign.pk),
                "values": {"content_versions": mapping},
            }
        )
    if previous:
        for schedule in document["sections"].get("schedules", []):
            if schedule["values"]["template_version"] != previous["id"]:
                continue
            if values is None:
                raise ValueError(
                    "Select another template for its schedules before removal."
                )
            affected.append(schedule)
            patch.append(
                {
                    "operation": "update",
                    "section": "schedules",
                    "id": schedule["id"],
                    "values": {
                        "template_version": reference,
                        "subject": values["subject"],
                    },
                }
            )
    return patch, affected

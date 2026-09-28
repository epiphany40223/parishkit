"""Named page/email revision editing with sample-only inert rendering."""

from datetime import date
from uuid import uuid4

from django import forms
from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from parishkit.stewardship.web.content import (
    MAX_TEXT_BYTES,
    PLACEHOLDERS,
    family_email_problems,
    prepare_content,
    render_template,
    validate_admin_digest_content,
    validate_receipt_content,
    validate_template,
)
from parishkit.stewardship.web.presentation import campaign_year, parish_date
from parishkit.stewardship.web.presentation import phone as format_phone
from parishkit.stewardship.web.refusals import UserFacingError

from .content_defaults import default_data

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
    "submission_confirmation": _("Submission receipt message (email delivery)"),
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


PART_LABELS = {
    "subject": _("The email subject"),
    "html": _("The HTML version"),
    "text": _("The plain-text version"),
    "generated": _("The plain text generated from the HTML version"),
}


def family_access_message(part, problem, names, *, generated):
    """One specific, fixable sentence for a broken invitation/reminder part.

    Names which version has which problem; when the generated plain text is
    the one missing a placeholder, says how to fix that (placeholders only in
    a link's address are kept as "label: URL", so usually the HTML version
    lacks it too; otherwise uncheck generation and edit the plain text).
    """
    label = PART_LABELS["generated" if part == "text" and generated else part]
    listed = " and ".join("{{ " + name + " }}" for name in names)
    if problem == "credential":
        return _(
            "%(part)s contains %(names)s. Access codes and links belong only in "
            "the email body; remove them from the subject."
        ) % {"part": label, "names": listed}
    if problem == "reserved":
        return _(
            "%(part)s contains a reserved system marker. Remove it and use the "
            "documented placeholders instead."
        ) % {"part": label}
    fix = (
        _(
            " Add it to the HTML version as visible text or as a link, or uncheck "
            "“Generate plain text from HTML” and add it to the plain text."
        )
        if part == "text" and generated
        else _(" Add it where the Family should see it.")
    )
    return _("%(part)s is missing %(names)s.") % {"part": label, "names": listed} + fix


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

    def __init__(self, *args, kind, slot=None, **kwargs):
        """Page content cannot carry a subject or masquerade as an email template."""
        self.kind = kind
        self.slot = slot
        super().__init__(*args, **kwargs)
        if kind == "page":
            del self.fields["subject"]
        elif slot in {"initial", "reminder"}:
            self.fields["text"].help_text = _(
                "Both body versions require {{ family_code }} and {{ family_url }}. "
                "Generated plain text writes each link as “label: URL”. "
                "Keep credentials out of the subject."
            )

    def clean(self):
        """Validate sanitized output and substitutions before creating any request."""
        values = super().clean()
        if self.errors or values.get("clear"):
            return values
        try:
            prepared = prepare_content(
                values["html"], text=None if values["generate_text"] else values["text"]
            )
            if (
                values["generate_text"]
                and values["text"].strip()
                # Browsers submit a textarea's line breaks as CRLF.
                and values["text"].replace("\r\n", "\n") != prepared.text
            ):
                # Never silently drop typed plain text in favour of generated.
                self.add_error(
                    "text",
                    forms.ValidationError(
                        _(
                            "This plain text differs from the text generated from "
                            "the HTML version, and “Generate plain text from HTML” "
                            "is checked. Uncheck it to keep your plain text, or "
                            "clear the plain text to use the generated version."
                        ),
                        code="text_conflict",
                    ),
                )
                return values
            validate_template(prepared.html)
            validate_template(prepared.text)
            if self.kind == "email":
                validate_template(values["subject"], subject=True)
            if (self.kind, self.slot) in {
                ("email", "confirmation"),
                ("page", "submission_confirmation"),
            }:
                validate_receipt_content(
                    values.get("subject", ""), prepared.html, prepared.text
                )
            if self.kind == "email" and self.slot in {"daily_digest", "weekly_digest"}:
                validate_admin_digest_content(
                    values["subject"], prepared.html, prepared.text
                )
        except ValueError:
            self.add_error(
                None,
                _(
                    "Use bounded content and only the documented placeholders. "
                    "Email subjects must be one line."
                ),
            )
            return values
        if self.kind == "email" and self.slot in {"initial", "reminder"}:
            problems = family_email_problems(
                values["subject"], prepared.html, prepared.text
            )
            for part, problem, names in problems:
                self.add_error(
                    None,
                    forms.ValidationError(
                        family_access_message(
                            part, problem, names, generated=values["generate_text"]
                        ),
                        code="family_access",
                    ),
                )
            if problems:
                return values
        values["prepared"] = prepared
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


class _DefaultForm(ContentForm):
    """Validate built-in defaults without a configuration digest to compare."""

    base_digest = None


def applicable_slots(campaign):
    """Every (kind, slot) a campaign's enabled modules can show or send."""
    return [
        (kind, slot)
        for kind, labels in (("page", page_slots(campaign)), ("email", EMAIL_LABELS))
        for slot in labels
    ]


def default_values(kind, slot, *, campaign_id):
    """One slot's default as the canonical revision a manual editor save produces.

    The default passes through the same ``ContentForm`` cleaning and
    ``values()`` path as text an Admin types, so it is sanitized and validated
    identically and can never bypass a content rule.
    """
    form = _DefaultForm(default_data(kind, slot), kind=kind, slot=slot)
    if not form.is_valid():
        # Unit tests validate every default; this is defensive only.
        raise ValueError("Default content failed validation.")
    return form.values(campaign_id=campaign_id, slot=slot)


def matches_default(values):
    """Whether saved canonical content is exactly its slot's unmodified default.

    Stored content is already sanitized canonical output, so comparing it to
    the default's canonical revision (same campaign owner) is exact; any
    edit, however small, counts as customized.
    """
    return values == default_values(
        values["kind"], values["slot"], campaign_id=values["campaign_id"]
    )


def default_content(campaign_id, campaign):
    """Default content records for a brand-new campaign, plus its page references.

    Returns ``(records, content_versions)``: one new revision per applicable
    page and email slot, and the legacy page-reference mapping that selects
    the new page revisions, as a campaign's ``content_versions`` requires.
    """
    records = [
        {
            "id": str(uuid4()),
            "values": default_values(kind, slot, campaign_id=campaign_id),
        }
        for kind, slot in applicable_slots(campaign)
    ]
    versions = {
        row["values"]["slot"]: row["id"]
        for row in records
        if row["values"]["kind"] == "page"
        and row["values"]["slot"] in LEGACY_PAGE_REFERENCES
    }
    return records, versions


# Realistic but plainly fictional values for previews and setup email tests.
# Every link uses the reserved, never-resolving .invalid domain, so a sample
# can never send anyone to a real page. The code has a live code's format
# (eight letters without I, L or O) but is an obviously made-up sequence.
SAMPLE_ORIGIN = "https://stewardship.example.invalid"
SAMPLE_FAMILY = {
    "family_name": "Sample",
    "family_member_names": "Alex and Sam Sample",
    "family_code": "ABCDEFGH",
    "family_url": SAMPLE_ORIGIN + "/access/sample-household-link",
    "generic_family_url": SAMPLE_ORIGIN + "/",
}
SAMPLE_PARISH = {
    "website": "https://parish.example.invalid/",
    "phone": "(202) 555-0100",
    "email": "office@parish.example.invalid",
}


def sample_render(value, *, parish, campaign, confirmation=False, receipt_block=None):
    """Never look up a real Family or generate a live code/link for a sample preview.

    Parish and campaign values are the configured ones; parish contact fields
    that are not configured, and every Family value, are fictional samples.
    """
    confirmation = confirmation or (
        value is not None and value.get("slot") == "confirmation"
    )
    if value is None and not confirmation:
        return None
    substitutions = {
        "parish_name": parish["name"],
        "parish_website": parish.get("website") or SAMPLE_PARISH["website"],
        "parish_phone": format_phone(parish.get("phone") or SAMPLE_PARISH["phone"]),
        "parish_email": parish.get("email") or SAMPLE_PARISH["email"],
        "online_giving_url": parish.get("online_giving_url")
        or parish.get("website")
        or SAMPLE_PARISH["website"],
        "campaign_name": campaign["name"],
        "campaign_start": parish_date(date.fromisoformat(campaign["start_date"])),
        "campaign_end": parish_date(date.fromisoformat(campaign["end_date"])),
        "campaign_timezone": campaign["timezone"],
        "campaign_year": campaign_year(campaign),
        "financial_start": parish_date(
            date.fromisoformat(campaign["financial"]["start"])
        )
        if campaign["financial"]
        else "",
        "financial_end": parish_date(date.fromisoformat(campaign["financial"]["end"]))
        if campaign["financial"]
        else "",
        **SAMPLE_FAMILY,
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
    if confirmation:
        from parishkit.stewardship.jobs.receipt_preview import sample_receipt

        return sample_receipt(
            value, substitutions=substitutions, campaign=campaign, block=receipt_block
        )
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
                raise UserFacingError(
                    _("This email is used by a mail schedule, so it can't be removed."),
                    fix=_(
                        "Choose another email for that schedule, or remove the "
                        "schedule, under Mail schedules first. Editing the text "
                        "(for example, resetting it to the default) keeps the "
                        "schedule."
                    ),
                    link=reverse("admin:schedule_settings", args=[campaign.pk]),
                    link_label=_("Go to Mail schedules"),
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


def text_is_generated(values):
    """Whether saved content's plain text is exactly what its HTML generates.

    Editors open such content with "Generate plain text from HTML" checked;
    hand-written plain text opens with it unchecked, so a save never replaces
    it without the Admin choosing to.
    """
    return values is None or prepare_content(values["html"]).text == values["text"]

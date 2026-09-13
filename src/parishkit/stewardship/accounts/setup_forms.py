"""Closed, non-secret wizard forms; credentials have a separate sealed intake."""

from copy import deepcopy
from uuid import UUID

from django import forms
from django.utils.translation import gettext_lazy as _

from parishkit.config import ConfigError
from parishkit.stewardship.schema_primitives import typed

from .parish_views import ParishForm
from .policy_schema import normalized_domain, normalized_email

STEPS = {
    "parish": _("Parish profile"),
    "branding": _("Parish logo"),
    "access": _("Administrative access"),
    "mail": _("Outgoing email settings"),
    "slack": _("Optional Slack notifications"),
    "testing": _("Testing recipient"),
    "campaign": _("First campaign"),
    "schedules": _("First-campaign mail schedules"),
}


class SetupParishForm(ParishForm):
    """Reuse profile fields without accepting an active-configuration mutation."""

    base_digest = None

    def clean_website(self):
        """Reject credential-bearing or parameterized URLs before temporary storage."""
        value = self.cleaned_data["website"]
        try:
            typed(value, "url")
        except ConfigError:
            raise forms.ValidationError(
                _("Use a website URL without credentials, query or fragment.")
            ) from None
        return value


class IdentityLines(forms.CharField):
    """Normalize at most fifty distinct domains or addresses, one per line."""

    def __init__(self, *, domains=False, **kwargs):
        """Use bounded text controls and a compiled domain-or-address interpretation."""
        self.domains = domains
        super().__init__(
            required=False,
            max_length=12800,
            widget=forms.Textarea(attrs={"rows": 3, "spellcheck": "false"}),
            help_text=_("One per line. Leave empty when no additional rule is needed."),
            **kwargs,
        )

    def clean(self, value):
        """No wildcard, personal Gmail domain grant or duplicate alias expansion."""
        value = super().clean(value)
        lines = [line.strip() for line in value.splitlines() if line.strip()]
        if len(lines) > 50:
            raise forms.ValidationError(_("Enter at most fifty identities."))
        try:
            normalizer = normalized_domain if self.domains else normalized_email
            values = sorted({normalizer(line) for line in lines})
            if self.domains and "gmail.com" in values:
                raise ConfigError("A personal email domain cannot be allowlisted.")
        except ConfigError:
            raise forms.ValidationError(
                _("Check the domains or email addresses.")
            ) from None
        return values


class SetupAccessForm(forms.Form):
    """Additional rules cannot remove or replace the bootstrap Administrator."""

    staff_domains = IdentityLines(domains=True, label=_("Staff domains"))
    ministry_domains = IdentityLines(domains=True, label=_("Ministry-leader domains"))
    staff_addresses = IdentityLines(label=_("Staff email addresses"))
    ministry_addresses = IdentityLines(label=_("Ministry-leader email addresses"))
    admin_addresses = IdentityLines(label=_("Additional Administrator email addresses"))


class SetupMailForm(forms.Form):
    """Public mail scope does not imply credential installation or test delivery."""

    delegated_email = forms.EmailField(label=_("Delegated mailbox"), max_length=254)
    sender = forms.EmailField(label=_("From address"), max_length=254)
    reply_to = forms.EmailField(label=_("Reply-to address"), max_length=254)


class SetupSlackForm(forms.Form):
    """An optional notification target must be explicitly enabled or left empty."""

    enabled = forms.BooleanField(label=_("Enable Slack notifications"), required=False)
    channel_id = forms.RegexField(
        label=_("Slack channel ID"),
        regex=r"^[CG][A-Z0-9]{1,63}$",
        max_length=64,
        required=False,
    )

    def clean(self):
        """Reject hidden leftover channel data when the option is turned off."""
        values = super().clean()
        if not self.errors and bool(values["channel_id"]) != values["enabled"]:
            raise forms.ValidationError(
                _(
                    "Enable Slack and supply a channel, "
                    "or disable it and clear the channel."
                )
            )
        return values


class SetupTestingForm(forms.Form):
    """One destination applies to all test mail; no Production switch exists here."""

    testing_recipient = forms.EmailField(
        label=_("Testing email address"), max_length=254
    )


class SetupBrandingForm(forms.Form):
    """An opaque receipt reference still requires original-session ownership."""

    bundle_id = forms.UUIDField(widget=forms.HiddenInput)


FORMS = {
    "parish": SetupParishForm,
    "branding": SetupBrandingForm,
    "access": SetupAccessForm,
    "mail": SetupMailForm,
    "slack": SetupSlackForm,
    "testing": SetupTestingForm,
}


def form_values(form):
    """Copy only declared, validated fields in canonical public-storage types."""
    if not form.is_valid():
        raise ValueError("Valid setup fields are required.")
    return {
        name: normalized_email(value)
        if isinstance(form.fields[name], forms.EmailField)
        else str(value)
        if isinstance(form.fields[name], forms.UUIDField)
        else deepcopy(value)
        for name, value in form.cleaned_data.items()
    }


def initial_values(step, values):
    """Convert stored access arrays back to bounded text controls for this owner."""
    if step not in FORMS:
        raise ValueError("Unknown setup step.")
    return {
        name: "\n".join(value) if step == "access" else deepcopy(value)
        for name, value in values.items()
    }


def validate_values(step, values):
    """Service callers cannot inject extra fields, coercions or private payloads."""
    from .setup_content_values import CONTENT_STEPS, validate_content_step

    if step in CONTENT_STEPS:
        return validate_content_step(step, values)
    if step == "schedules":
        from .setup_schedule_values import validate_schedule_step

        return validate_schedule_step(values)
    if step == "campaign":
        from parishkit.stewardship.campaigns.configuration import campaign_values

        if (
            type(values) is not dict
            or set(values) != {"campaign", "source_result"}
            or type(values["source_result"]) is not str
            or str(UUID(values["source_result"])) != values["source_result"]
        ):
            raise ValueError("Invalid first-campaign setup fields.")
        campaign_values(values["campaign"])
        return deepcopy(values)
    if step not in FORMS or type(values) is not dict:
        raise ValueError("Invalid public setup values.")
    form_type = FORMS[step]
    if set(values) != set(form_type.base_fields):
        raise ValueError("Invalid public setup fields.")
    if step != "access" and any(
        type(value) is not (bool if step == "slack" and name == "enabled" else str)
        for name, value in values.items()
    ):
        raise ValueError("Invalid public setup types.")
    if step == "access" and any(
        type(value) is not list or any(type(item) is not str for item in value)
        for value in values.values()
    ):
        raise ValueError("Invalid setup access list.")
    form = form_type(initial_values(step, values))
    if not form.is_valid() or form_values(form) != values:
        raise ValueError("Invalid or noncanonical public setup values.")
    return deepcopy(values)

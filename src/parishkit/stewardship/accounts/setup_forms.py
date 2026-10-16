"""Closed, non-secret wizard forms; credentials have a separate sealed intake."""

from copy import deepcopy

from django import forms
from django.utils.translation import gettext_lazy as _

from parishkit.config import ConfigError
from parishkit.stewardship.schema_primitives import canonical_timezone, typed
from parishkit.stewardship.web.admin_forms import AdminForm
from parishkit.stewardship.web.refusals import UserFacingError
from parishkit.stewardship.web.sender_name_field import SenderNameField

from . import setup_help
from .parish_views import ParishForm
from .policy_schema import normalized_email
from .timezone_field import browser_default


class SetupParishForm(ParishForm):
    """Reuse profile fields without accepting an active-configuration mutation."""

    base_digest = None
    # Set later in Parish settings; the wizard keeps its fixed field list.
    date_format = None

    def __init__(self, *args, **kwargs):
        """Explain each profile field; a new zone starts on the browser's."""
        super().__init__(*args, **kwargs)
        # No zone saved yet: start on the browser's zone, or on a blank
        # required choice, never the list's first zone (Africa/Abidjan was
        # stored on pk-local that way, #1330), and say so in the help. A saved
        # zone is kept, with help that does not mention the browser.
        fresh = not self.initial.get("timezone")
        texts = setup_help.PARISH
        if fresh:
            texts = texts | {"timezone": setup_help.PARISH_NEW_TIMEZONE}
        setup_help.apply(self, texts, replace=True)
        if fresh:
            browser_default(self, choose=True)

    def clean_timezone(self):
        """Store a new setup's zone by its canonical name (#1286).

        Once the source load has fixed the zone the field is disabled and
        keeps the draft's own value, which the save compares unchanged.
        """
        value = self.cleaned_data["timezone"]
        return value if self.fields["timezone"].disabled else canonical_timezone(value)

    def clean_website(self):
        """Reject credential-bearing or parameterized URLs before temporary storage."""
        value = self.cleaned_data["website"]
        try:
            typed(value, "url")
        except ConfigError:
            raise forms.ValidationError(
                _("Use a website URL without credentials, query or fragment."),
                code="invalid_url",
            ) from None
        return value


class IdentityLines(forms.CharField):
    """Normalize at most fifty distinct email addresses, one per line."""

    def __init__(self, **kwargs):
        """Use bounded text controls and a compiled address interpretation."""
        super().__init__(
            required=False,
            max_length=12800,
            widget=forms.Textarea(attrs={"rows": 3, "spellcheck": "false"}),
            help_text=_("One per line. Leave empty when no one else needs this role."),
            **kwargs,
        )

    def clean(self, value):
        """No malformed or duplicate address."""
        value = super().clean(value)
        lines = [line.strip() for line in value.splitlines() if line.strip()]
        if len(lines) > 50:
            raise forms.ValidationError(
                _("Enter at most fifty addresses."), code="too_many"
            )
        try:
            values = sorted({normalized_email(line) for line in lines})
        except ConfigError:
            raise forms.ValidationError(
                _("Check the email addresses."), code="invalid_email"
            ) from None
        return values


class SetupAccessForm(AdminForm):
    """Additional users cannot remove or replace the bootstrap Administrator.

    Sign-in is by email address only (#952), so each address listed becomes
    one user with the highest role it is listed under; there are no
    hosted-domain rules.
    """

    staff_addresses = IdentityLines(label=_("Staff email addresses"))
    ministry_addresses = IdentityLines(label=_("Ministry-leader email addresses"))
    admin_addresses = IdentityLines(label=_("Additional Administrator email addresses"))

    def __init__(self, *args, **kwargs):
        """Replace the generic one-per-line hint with what each role grants."""
        super().__init__(*args, **kwargs)
        setup_help.apply(self, setup_help.ACCESS, replace=True)


class SetupMailForm(AdminForm):
    """Public mail scope does not imply credential installation or test delivery."""

    delegated_email = forms.EmailField(label=_("Delegated mailbox"), max_length=254)
    sender = forms.EmailField(label=_("From address"), max_length=254)
    sender_name = SenderNameField()
    reply_to = forms.EmailField(label=_("Reply-to address"), max_length=254)

    def __init__(self, *args, **kwargs):
        """Explain the mailbox, From and Reply-to roles."""
        super().__init__(*args, **kwargs)
        setup_help.apply(self, setup_help.MAIL)


class SetupSlackForm(AdminForm):
    """An optional notification target must be explicitly enabled or left empty."""

    enabled = forms.BooleanField(label=_("Enable Slack notifications"), required=False)
    channel_id = forms.RegexField(
        label=_("Slack channel ID"),
        regex=r"^[CG][A-Z0-9]{1,63}$",
        max_length=64,
        required=False,
    )

    def __init__(self, *args, **kwargs):
        """Explain Slack, and show the channel only while Slack is enabled.

        clean() wants a channel exactly when Slack is enabled. The page
        (ui-v1.js) shows the channel field, and requires it, only while
        "Enable Slack notifications" is ticked; hidden, it is disabled and not
        sent, so turning Slack off clears a saved channel (#563).
        """
        super().__init__(*args, **kwargs)
        setup_help.apply(self, setup_help.SLACK)
        self.fields["channel_id"].widget.attrs.update(
            {
                "data-show-when": f"{self.add_prefix('enabled')}=on",
                "data-required-when-shown": "",
                "data-missing-hint": _(
                    "Enter the Slack channel ID, or untick Enable Slack notifications."
                ),
            }
        )

    def clean(self):
        """Reject hidden leftover channel data when the option is turned off."""
        values = super().clean()
        if not self.errors and bool(values["channel_id"]) != values["enabled"]:
            raise forms.ValidationError(
                _(
                    "Enable Slack and supply a channel, "
                    "or disable it and clear the channel."
                ),
                code="channel_mismatch",
            )
        return values


class SetupTestingForm(AdminForm):
    """One destination applies to all test mail; no Production switch exists here."""

    testing_recipient = forms.EmailField(
        label=_("Testing email address"), max_length=254
    )

    def __init__(self, *args, **kwargs):
        """Warn that this address receives all mail, including real Family data."""
        super().__init__(*args, **kwargs)
        setup_help.apply(self, setup_help.TESTING)


class SetupBrandingForm(AdminForm):
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


# The hosted-domain lists an access step saved before sign-in became
# address-only (#952, #993). Such a step is still read when every list is
# empty; one that granted a domain must be saved again.
LEGACY_ACCESS_FIELDS = frozenset({"staff_domains", "ministry_domains"})


def _without_domains(values):
    """An access step saved with the retired domain lists, without them.

    Domains no longer grant sign-in, so a saved domain is refused rather than
    silently dropped: the Admin saves the step again and lists addresses. The
    refusal reaches Review and Finish as a user-facing message that names the
    access step and links to it (the step's own form no longer has a domain
    field to mark, and the step reads its saved addresses back unchanged).
    """
    if any(values.get(name) != [] for name in LEGACY_ACCESS_FIELDS):
        # Imported here: the wizard's page list pulls in the campaign forms,
        # which the values-only callers of this module do not otherwise need.
        from .setup_wizard import BY_KEY

        raise UserFacingError(
            _(
                "The “Administrative access” step was saved by an earlier "
                "version that let whole email domains sign in. Domains no "
                "longer grant access."
            ),
            fix=_(
                "Open “Administrative access”, list each person's own email "
                "address under Administrator, Staff or Ministry-leader email "
                "addresses, and save the step again."
            ),
            link=BY_KEY["access"].url,
            link_label=_("Go to “Administrative access”"),
        )
    return {
        name: value
        for name, value in values.items()
        if name not in LEGACY_ACCESS_FIELDS
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
    """Service callers cannot inject extra fields, coercions or private payloads.

    Only the wizard's steps (``FORMS``) are accepted. The first-campaign
    steps the wizard saved before #142 (campaign, schedules, content) are
    refused like any other unknown step.
    """
    if step not in FORMS or type(values) is not dict:
        raise ValueError("Invalid public setup values.")
    if step == "parish" and "online_giving_url" not in values:
        # A parish step saved before the optional giving URL existed means
        # "not configured"; it is rewritten with the key on its next save.
        values = values | {"online_giving_url": ""}
    if step == "mail" and "sender_name" not in values:
        # Likewise a mail step saved before the optional From name existed.
        values = values | {"sender_name": ""}
    if step == "access" and LEGACY_ACCESS_FIELDS & values.keys():
        values = _without_domains(values)
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

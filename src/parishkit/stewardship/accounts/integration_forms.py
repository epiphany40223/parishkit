"""Closed public-settings and write-only private-candidate forms."""

from django import forms
from django.utils.translation import gettext_lazy as _

from .key_files import MAX_FILE_BYTES
from .policy_schema import normalized_email

LABELS = {
    "parishsoft": _("ParishSoft"),
    "google_workspace": _("Google Workspace mail"),
    "email": _("Outgoing email addresses"),
    "slack": _("Slack notifications"),
}


class IntegrationForm(forms.Form):
    """Non-secret settings never accept fingerprints, keys or arbitrary URLs."""

    base_digest = forms.RegexField(
        regex=r"^[0-9a-f]{64}$", max_length=64, widget=forms.HiddenInput
    )

    def __init__(self, target, *args, **kwargs):
        """Use a compiled form per integration, not caller-provided schema fields."""
        super().__init__(*args, **kwargs)
        self.target = target
        if target == "parishsoft":
            self.fields["organization_id"] = forms.IntegerField(
                label=_("Expected ParishSoft organization ID"),
                min_value=1,
                max_value=2**31 - 1,
            )
            self.fields["nightly_time"] = forms.RegexField(
                label=_("Nightly full refresh time"),
                regex=r"^(?:[01][0-9]|2[0-3]):[0-5][0-9]$",
                initial="02:00",
                required=False,
                max_length=5,
                widget=forms.TimeInput(format="%H:%M", attrs={"type": "time"}),
                help_text=_(
                    "Parish-local time; default 2:00 a.m. "
                    "The next scheduler tick uses the applied setting."
                ),
            )
        elif target == "google_workspace":
            self.fields["delegated_email"] = forms.EmailField(
                label=_("Delegated mailbox"), max_length=254
            )
        elif target == "email":
            self.fields["sender"] = forms.EmailField(
                label=_("From address"), max_length=254
            )
            self.fields["reply_to"] = forms.EmailField(
                label=_("Reply-to address"), max_length=254
            )
        elif target == "slack":
            self.fields["channel_id"] = forms.RegexField(
                label=_("Slack channel ID"),
                regex=r"^[CG][A-Z0-9]{1,63}$",
                max_length=64,
                help_text=_("Use the channel ID, not its name or a webhook URL."),
            )
        else:
            raise ValueError("Unsupported integration form.")

    def public_settings(self):
        """Normalize exact YAML types after validation; never include the base field."""
        if not self.is_valid():
            raise ValueError("Valid integration settings are required.")
        return {
            name: str(value)
            if name == "organization_id"
            else (
                normalized_email(value)
                if isinstance(self.fields[name], forms.EmailField)
                else value
            )
            for name, value in self.cleaned_data.items()
            if name != "base_digest"
        }

    def clean_nightly_time(self):
        """An omitted time retains the documented default, never browser-local time."""
        return self.cleaned_data["nightly_time"] or "02:00"


class WriteOnlyTextarea(forms.Textarea):
    """A validation error must not redisplay a posted key or service-account JSON."""

    def format_value(self, value):
        return None


class CredentialForm(forms.Form):
    """Paste secret material in memory; no upload/temp-file handling is involved."""

    candidate = forms.CharField(
        label=_("Replacement credential"),
        max_length=MAX_FILE_BYTES,
        strip=False,
        widget=WriteOnlyTextarea(
            attrs={"autocomplete": "off", "spellcheck": "false", "rows": 5}
        ),
        help_text=_(
            "Paste the API token or Google service-account JSON. "
            "It will never be displayed again."
        ),
    )
    intent = forms.CharField(max_length=4096, widget=forms.HiddenInput)

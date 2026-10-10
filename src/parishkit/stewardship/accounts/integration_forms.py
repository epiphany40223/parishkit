"""Closed public-settings and write-only private-candidate forms."""

from django import forms
from django.utils.translation import gettext_lazy as _

from parishkit.stewardship.web.sender_name_field import SenderNameField

from . import field_tips, setup_help
from .key_files import MAX_FILE_BYTES
from .policy_schema import normalized_email
from .refresh_schedule_forms import ScheduleEditor

LABELS = {
    "parishsoft": _("ParishSoft"),
    "google_workspace": _("Google Workspace mail"),
    "email": _("Outgoing email addresses"),
    "slack": _("Slack notifications"),
    "backup": _("Off-site backups (Google Drive)"),
    "backup_key": _("Backup encryption key"),
}


class IntegrationForm(forms.Form):
    """Non-secret settings never accept fingerprints, keys or arbitrary URLs."""

    base_digest = forms.RegexField(
        regex=r"^[0-9a-f]{64}$", max_length=64, widget=forms.HiddenInput
    )

    def __init__(self, target, *args, loaded_organization=None, stored=None, **kwargs):
        """Use a compiled form per integration, not caller-provided schema fields.

        ``loaded_organization`` is the ParishSoft organization ID whose data
        is already loaded, or None before the first load. Once data is
        loaded, the organization ID is shown read-only and a different value
        is refused: every later refresh must read that same organization, so
        a changed ID would stop them all (see ``source.requests``).
        ``stored`` are the integration's stored settings: ParishSoft's
        refresh schedule editor (``schedule``, #632) shows and compares
        against the schedule they hold.
        """
        super().__init__(*args, **kwargs)
        self.target = target
        self.loaded_organization = loaded_organization
        self.schedule = None
        if target == "parishsoft":
            self.fields["organization_id"] = forms.IntegerField(
                label=_("Expected ParishSoft organization ID"),
                min_value=1,
                max_value=2**31 - 1,
            )
            if loaded_organization is not None:
                self.fields["organization_id"].widget.attrs["readonly"] = True
                self.fields["organization_id"].help_text = _(
                    "ParishSoft data for this organization is already loaded, "
                    "so its ID can no longer change."
                )
            else:
                # Before the first load, explain the value as setup does.
                self.fields["organization_id"].help_text = setup_help.CREDENTIALS[
                    "parishsoft"
                ]["organization_id"]
            # The refresh schedule is edited as rows of rules (#632), read
            # from the same submitted data as the other settings.
            self.schedule = ScheduleEditor(
                self.data if self.is_bound else None, stored=stored or {}
            )
        elif target == "google_workspace":
            self.fields["delegated_email"] = forms.EmailField(
                label=_("Delegated mailbox"), max_length=254
            )
        elif target == "email":
            self.fields["sender"] = forms.EmailField(
                label=_("From address"), max_length=254
            )
            self.fields["sender_name"] = SenderNameField(
                help_text=_(
                    "Shown next to the From address. Leave empty to use the "
                    "Parish name."
                )
            )
            self.fields["reply_to"] = forms.EmailField(
                label=_("Reply-to address"), max_length=254
            )
        elif target == "slack":
            self.fields["channel_id"] = forms.RegexField(
                label=_("Slack channel ID"),
                regex=r"^[CG][A-Z0-9]{1,63}$",
                max_length=64,
                help_text=_(
                    "The ID (not the name) of the Slack channel for alerts, for "
                    "example C0123456789. In Slack, open the channel, click its "
                    "name, and copy the Channel ID shown at the bottom of the "
                    "About tab. Invite your Slack app's bot to that channel. A "
                    "webhook URL does not work here."
                ),
            )
        elif target == "backup":
            self.fields["target"] = forms.CharField(
                label=_("Google Drive folder link"),
                max_length=500,
                help_text=_(
                    "Open the folder in Google Drive and copy the link from the "
                    "address bar (it contains /folders/). Each backup is copied "
                    "there as its own folder of encrypted files; the newest 30 "
                    "are kept."
                ),
            )
        else:
            raise ValueError("Unsupported integration form.")
        # The mail fields reuse the setup wizard's plain-language help, so
        # both pages explain them the same way; fields with their own help
        # (the From name) keep it.
        for name, text in setup_help.MAIL.items():
            if name in self.fields and not self.fields[name].help_text:
                self.fields[name].help_text = text
        field_tips.shorten(
            self,
            {
                "target": _("The folder link from the address bar; it has /folders/."),
                "delegated_email": setup_help.HINTS["delegated_email"],
                "sender": setup_help.HINTS["sender"],
                "channel_id": setup_help.HINTS["channel_id"],
                "organization_id": setup_help.HINTS["organization_id"],
            },
        )

    def clean_target(self):
        """Store one canonical folder link, whichever form the Admin pasted."""
        from parishkit.stewardship.backup_drive import folder_id_from_url

        try:
            folder = folder_id_from_url(self.cleaned_data["target"])
        except ValueError as error:
            raise forms.ValidationError(str(error)) from None
        return f"https://drive.google.com/drive/folders/{folder}"

    def is_valid(self):
        """The settings are valid, and so is the schedule, if this page edits one."""
        return super().is_valid() and (
            self.schedule is None or self.schedule.is_valid()
        )

    def public_settings(self):
        """Normalize exact YAML types after validation; never include the base field.

        ParishSoft's schedule keys are the editor's: the stored ones for an
        unchanged schedule, else those its rules give.
        """
        if not self.is_valid():
            raise ValueError("Valid integration settings are required.")
        # An empty From name is omitted: the applied settings then use the
        # Parish name, and the setting stays absent as in older documents.
        settings = {
            name: str(value)
            if name == "organization_id"
            else (
                normalized_email(value)
                if isinstance(self.fields[name], forms.EmailField)
                else value
            )
            for name, value in self.cleaned_data.items()
            if name != "base_digest" and not (name == "sender_name" and not value)
        }
        if self.schedule is not None:
            settings |= self.schedule.settings()
        return settings

    def clean_organization_id(self):
        """Refuse a different organization once its ParishSoft data is loaded."""
        value = self.cleaned_data["organization_id"]
        if self.loaded_organization is not None and value != self.loaded_organization:
            raise forms.ValidationError(
                _(
                    "The organization ID can't change after ParishSoft data has "
                    "been loaded: the loaded Families and Members belong to "
                    "organization %(loaded)s, and every refresh must read that "
                    "same organization. Keep %(loaded)s here."
                ),
                params={"loaded": self.loaded_organization},
            )
        return value


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


# Label and help for each integration's inline "replace the key" field.
KEY_FIELDS = {
    "parishsoft": (
        _("New ParishSoft API key"),
        _(
            "Paste a new key only to replace the current one, for example after "
            "creating a new key in ParishSoft. It is checked against the "
            "organization ID above. Leave this blank to keep the current key, "
            "which is never shown."
        ),
    ),
    "google_workspace": (
        _("New service-account key (JSON)"),
        _(
            "Paste the whole JSON key file you downloaded from Google Cloud for "
            "the service account, only to replace the current key. It is checked "
            "by sending as the delegated mailbox above. Leave this blank to keep "
            "the current key, which is never shown."
        ),
    ),
    "slack": (
        _("New Slack bot token"),
        _(
            "Paste the app's bot token (it starts with xoxb-) only to replace the "
            "current one. Leave this blank to keep the current token, which is "
            "never shown."
        ),
    ),
}


class WriteOnlyPasswordInput(forms.PasswordInput):
    """A single-line secret field that never redisplays a submitted value."""

    def __init__(self):
        super().__init__(
            render_value=False,
            attrs={"autocomplete": "off", "spellcheck": "false"},
        )


class InlineCredentialForm(forms.Form):
    """The optional replacement key shown on an integration's settings page."""

    intent = forms.CharField(max_length=4096, widget=forms.HiddenInput)

    def __init__(self, target, *args, **kwargs):
        """Use the target's own label, help text and one- or multi-line field."""
        super().__init__(*args, **kwargs)
        label, help_text = KEY_FIELDS[target]
        self.fields["candidate"] = forms.CharField(
            label=label,
            help_text=help_text,
            required=False,
            max_length=MAX_FILE_BYTES,
            strip=target != "google_workspace",
            widget=(
                WriteOnlyTextarea(
                    attrs={"autocomplete": "off", "spellcheck": "false", "rows": 5}
                )
                if target == "google_workspace"
                else WriteOnlyPasswordInput()
            ),
        )
        self.order_fields(["candidate", "intent"])
        field_tips.shorten(
            self, {"candidate": _("Leave blank to keep the current key.")}
        )

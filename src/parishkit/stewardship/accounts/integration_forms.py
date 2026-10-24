"""Closed public-settings and write-only private-candidate forms."""

from django import forms
from django.utils.translation import gettext_lazy as _

from parishkit.stewardship.web.admin_forms import AdminForm
from parishkit.stewardship.web.field_rules import (
    EMAIL,
    SPACE,
    EmailRuleField,
    FieldRule,
    RuleField,
    apply,
    char_class,
)
from parishkit.stewardship.web.sender_name_field import SenderNameField

from . import field_tips, setup_help
from .key_files import MAX_FILE_BYTES
from .policy_schema import normalized_email
from .refresh_schedule_forms import ScheduleEditor
from .secret_input import SecretInput

# A field group with a reserved error line (file-size-field.html).
ERROR_LINE_TEMPLATE = "stewardship/file-size-field.html"

LABELS = {
    "parishsoft": _("ParishSoft"),
    "google_workspace": _("Google Workspace mail"),
    "email": _("Outgoing email addresses"),
    "slack": _("Slack notifications"),
    "backup": _("Off-site backups (Google Drive)"),
    "backup_key": _("Backup encryption key"),
}

# What Save waits for while a required setting is empty or malformed (#563).
MISSING_HINTS = {
    "delegated_email": _("Enter a valid delegated mailbox address."),
    "sender": _("Enter a valid From address."),
    "reply_to": _("Enter a valid reply-to address."),
    "channel_id": _("Enter a Slack channel ID, such as C0123456789."),
    "target": _("Paste the Google Drive folder link."),
}

# A Slack channel ID, checked by the browser and the server alike (#1577);
# spaces around it are admitted, as the server strips them.
SLACK_CHANNEL = FieldRule(
    name="slack_channel_id",
    pattern=rf"[{SPACE}]*[CG][A-Z0-9]{{1,63}}[{SPACE}]*",
    message=MISSING_HINTS["channel_id"],
    code="invalid",
)

# A pasted API key or bot token as its installer reads one
# (source.credentials, integration_candidates.slack_candidate): 1 to 4096
# printable ASCII characters, so no spaces inside. The key fields strip the
# spaces around a pasted one first, so only spaces is no key (blank keeps
# the saved one, or the required rule asks for one). Checked by the browser
# and the server alike (#1577), in the setup step's words for a key it
# can't use.
_KEY_TEXT = rf"[{SPACE}]*({char_class(((0x21, 0x7E),))}{{1,4096}}[{SPACE}]*)?"
KEY_RULES = {
    "parishsoft": FieldRule(
        name="parishsoft_key",
        pattern=_KEY_TEXT,
        message=_(
            "This does not look like a ParishSoft API key. Paste the key exactly "
            "as ParishSoft provided it, on one line with no spaces."
        ),
        code="invalid_credential",
    ),
    "slack": FieldRule(
        name="slack_token",
        pattern=_KEY_TEXT,
        message=_(
            "This does not look like a Slack bot token. Paste the Bot User OAuth "
            "Token, which starts with xoxb-, on one line with no spaces."
        ),
        code="invalid_credential",
    ),
}


def rule_key(field, target, empty=None):
    """Give a single-line key field its target's rule, if it has one.

    ``empty`` is what the complete gate says while a required key is empty;
    a target without a rule (the Google service-account JSON) keeps it as
    the field's hint. Returns the rule, or None.
    """
    rule = KEY_RULES.get(target)
    if rule is None:
        if empty:
            field.widget.attrs["data-missing-hint"] = empty
        return None
    apply(field, rule.worded(rule.message, empty=empty))
    return rule


# What the browser checks of an off-site folder link (#1330), leniently: a
# bare folder ID or any https link on drive.google.com, as folder_id_from_url
# reads them (case-blind scheme and host, spaces around it). The server
# decides whether the link names a folder. Written for the "v" pattern flag.
FOLDER_LINK_PATTERN = (
    r"\s*([A-Za-z0-9_\-]{10,200}|[Hh][Tt][Tt][Pp][Ss]:\/\/([^\/?#]*@)?"
    r"[Dd][Rr][Ii][Vv][Ee]\.[Gg][Oo][Oo][Gg][Ll][Ee]\.[Cc][Oo][Mm]([:\/?#].*)?)\s*"
)


class IntegrationForm(AdminForm):
    """Non-secret settings never accept fingerprints, keys or arbitrary URLs."""

    base_digest = forms.RegexField(
        regex=r"^[0-9a-f]{64}$", max_length=64, widget=forms.HiddenInput
    )

    def __init__(self, target, *args, stored=None, **kwargs):
        """Use a compiled form per integration, not caller-provided schema fields.

        ``stored`` are the integration's stored settings: ParishSoft's
        refresh schedule editor (``schedule``, #632) shows and compares
        against the schedule they hold, and ParishSoft's organization ID is
        carried over from them. It is chosen once, in the setup wizard
        (#1554), so the form has no field for it: the page shows it as text
        and a posted organization is refused as unexpected input.
        """
        super().__init__(*args, **kwargs)
        self.target = target
        self.stored = stored or {}
        self.schedule = None
        if target == "parishsoft":
            # The refresh schedule is edited as rows of rules (#632), read
            # from the same submitted data as the other settings.
            self.schedule = ScheduleEditor(
                self.data if self.is_bound else None, stored=stored or {}
            )
        elif target == "google_workspace":
            self.fields["delegated_email"] = EmailRuleField(
                EMAIL.worded(MISSING_HINTS["delegated_email"]),
                label=_("Delegated mailbox"),
                max_length=254,
            )
        elif target == "email":
            self.fields["sender"] = EmailRuleField(
                EMAIL.worded(MISSING_HINTS["sender"]),
                label=_("From address"),
                max_length=254,
            )
            self.fields["sender_name"] = SenderNameField(
                help_text=_(
                    "Shown next to the From address. Leave empty to use the "
                    "Parish name."
                )
            )
            self.fields["reply_to"] = EmailRuleField(
                EMAIL.worded(MISSING_HINTS["reply_to"]),
                label=_("Reply-to address"),
                max_length=254,
            )
        elif target == "slack":
            self.fields["channel_id"] = RuleField(
                SLACK_CHANNEL,
                label=_("Slack channel ID"),
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
        # The page's complete gate (#563) holds Save while a required field
        # is empty or malformed; each says what it waits for. The channel ID
        # is checked in the browser by the same rule as the server's
        # (SLACK_CHANNEL).
        for name, hint in MISSING_HINTS.items():
            if name in self.fields:
                self.fields[name].widget.attrs["data-missing-hint"] = hint
        if "target" in self.fields:
            self.fields["target"].widget.attrs["pattern"] = FOLDER_LINK_PATTERN
        field_tips.shorten(
            self,
            {
                "target": _("The folder link from the address bar; it has /folders/."),
                "delegated_email": setup_help.HINTS["delegated_email"],
                "sender": setup_help.HINTS["sender"],
                "channel_id": setup_help.HINTS["channel_id"],
            },
        )
        # The folder link keeps a line for its error, shown or not, so Test
        # access's in-place refusal there never moves the buttons below
        # (#1334 review; ui-conventions "No layout shift"). Set after the tip,
        # which only a field with no template of its own takes.
        if "target" in self.fields:
            self.fields["target"].template_name = ERROR_LINE_TEMPLATE

    def clean_target(self):
        """Store one canonical folder link, whichever form the Admin pasted."""
        from parishkit.stewardship.backup_drive import folder_id_from_url

        try:
            folder = folder_id_from_url(self.cleaned_data["target"])
        except ValueError as error:
            raise forms.ValidationError(
                str(error), code="invalid_folder_link"
            ) from None
        return f"https://drive.google.com/drive/folders/{folder}"

    def is_valid(self):
        """The settings are valid, and so is the schedule, if this page edits one."""
        return super().is_valid() and (
            self.schedule is None or self.schedule.is_valid()
        )

    def public_settings(self):
        """Normalize exact YAML types after validation; never include the base field.

        ParishSoft's schedule keys are the editor's: the stored ones for an
        unchanged schedule, else those its rules give; its organization ID
        is the stored one, which no form changes (#1554).
        """
        if not self.is_valid():
            raise ValueError("Valid integration settings are required.")
        # An empty From name is omitted: the applied settings then use the
        # Parish name, and the setting stays absent as in older documents.
        settings = {
            name: normalized_email(value)
            if isinstance(self.fields[name], forms.EmailField)
            else value
            for name, value in self.cleaned_data.items()
            if name != "base_digest" and not (name == "sender_name" and not value)
        }
        if self.target == "parishsoft" and "organization_id" in self.stored:
            settings = {"organization_id": self.stored["organization_id"]} | settings
        if self.schedule is not None:
            settings |= self.schedule.settings()
        return settings


class WriteOnlyTextarea(forms.Textarea):
    """A validation error must not redisplay a posted key or service-account JSON."""

    def format_value(self, value):
        return None


class CredentialForm(AdminForm):
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
# ParishSoft's help names its organization (``%(organization)s``, "Name
# (ID)", #1554), which InlineCredentialForm fills in.
KEY_FIELDS = {
    "parishsoft": (
        _("New ParishSoft API key"),
        _(
            "Paste a new key only to replace the current one, for example after "
            "creating a new key in ParishSoft. It must be a key that reaches "
            "%(organization)s; any other key is refused. Leave this blank to "
            "keep the current key, which is never shown. A new key can't be "
            "saved together with a refresh schedule change: "
            "save the schedule change first."
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


# Label, help and missing-key message for an optional integration that is not
# set up yet, whose key is then required (#563): without one, Save could only
# be refused.
SETUP_KEY_FIELDS = {
    "slack": (
        _("Slack bot token"),
        _(
            "Paste the app's bot token (it starts with xoxb-). It is checked with "
            "Slack before it is used, and is never shown again."
        ),
        _("Paste the Slack bot token to set up Slack."),
    ),
}


class InlineCredentialForm(AdminForm):
    """The replacement key shown on an integration's settings page.

    The key is optional once the integration is set up (blank keeps the
    current key). For an optional integration that is not set up yet
    (``required``), the key is what sets it up, so it is required, with
    set-up wording and the message the page's complete gate shows while it
    is empty.
    """

    intent = forms.CharField(max_length=4096, widget=forms.HiddenInput)

    def __init__(self, target, *args, required=False, organization=None, **kwargs):
        """Use the target's own label, help text and one- or multi-line field.

        ``organization`` is ParishSoft's organization, named "Name (ID)"
        (``organization_names``), which its key's help names; a form that is
        only validated, never drawn, may leave it out.
        """
        super().__init__(*args, **kwargs)
        label, help_text = KEY_FIELDS[target]
        if target == "parishsoft":
            help_text = str(help_text) % {
                "organization": organization or _("the organization chosen in setup")
            }
        missing = None
        if required:
            label, help_text, missing = SETUP_KEY_FIELDS[target]
        self.fields["candidate"] = forms.CharField(
            label=label,
            help_text=help_text,
            required=required,
            error_messages={"required": missing} if missing else None,
            max_length=MAX_FILE_BYTES,
            strip=target != "google_workspace",
            widget=(
                WriteOnlyTextarea(
                    attrs={"autocomplete": "off", "spellcheck": "false", "rows": 5}
                )
                if target == "google_workspace"
                else SecretInput(target)
            ),
        )
        # The complete gate's hint names what Save is waiting for: the
        # missing key, or the key rule's words for one it can't use (#1577).
        self.key_rule = rule_key(self.fields["candidate"], target, missing)
        self.order_fields(["candidate", "intent"])
        field_tips.shorten(
            self,
            {}
            if required
            else {"candidate": _("Leave blank to keep the current key.")},
        )

    def clean_candidate(self):
        """A pasted key follows its rule (#1577); blank keeps the current key."""
        value = self.cleaned_data["candidate"]
        if value and self.key_rule is not None:
            self.key_rule.check(value)
        return value

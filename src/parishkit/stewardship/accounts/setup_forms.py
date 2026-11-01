"""Closed, non-secret wizard forms; credentials have a separate sealed intake."""

from copy import deepcopy

from django import forms
from django.utils.html import escape
from django.utils.safestring import mark_safe
from django.utils.translation import gettext_lazy as _

from parishkit.config import ConfigError
from parishkit.stewardship.schema_primitives import canonical_timezone
from parishkit.stewardship.web.admin_forms import AdminForm
from parishkit.stewardship.web.field_rules import (
    EMAIL,
    EmailRuleField,
    FieldRule,
    RuleField,
    apply,
    email_lines_pattern,
)
from parishkit.stewardship.web.refusals import UserFacingError
from parishkit.stewardship.web.sender_name_field import SenderNameField

from . import field_tips, setup_help
from .integration_forms import MISSING_HINTS, SLACK_CHANNEL
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


# At most fifty addresses, one per line, each as sign-in reads it (at most
# 254 characters, normalized_email), checked by the browser and the server
# alike (#1577). One refusal covers a malformed line and too many lines.
IDENTITY_LINES = FieldRule(
    name="email_lines",
    pattern=email_lines_pattern(50, 254),
    message=_("Enter one email address on each line, at most fifty."),
    code="invalid_email",
)


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
        apply(self, IDENTITY_LINES)

    def clean(self, value):
        """No malformed or duplicate address.

        The shared rule refuses a malformed line or more than fifty first,
        in its words; the checks after it are what the rule mirrors.
        """
        value = super().clean(value)
        if value:
            IDENTITY_LINES.check(value)
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

    # Each address is checked by the browser and the server alike (#1577),
    # in the words Integration settings uses for it.
    delegated_email = EmailRuleField(
        EMAIL.worded(MISSING_HINTS["delegated_email"]),
        label=_("Delegated mailbox"),
        max_length=254,
    )
    sender = EmailRuleField(
        EMAIL.worded(MISSING_HINTS["sender"]), label=_("From address"), max_length=254
    )
    sender_name = SenderNameField()
    reply_to = EmailRuleField(
        EMAIL.worded(MISSING_HINTS["reply_to"]),
        label=_("Reply-to address"),
        max_length=254,
    )

    def __init__(self, *args, **kwargs):
        """Explain the mailbox, From and Reply-to roles."""
        super().__init__(*args, **kwargs)
        setup_help.apply(self, setup_help.MAIL)


class SetupSlackForm(AdminForm):
    """An optional notification target must be explicitly enabled or left empty."""

    enabled = forms.BooleanField(label=_("Enable Slack notifications"), required=False)
    # Checked in the browser by the same rule as the server's (#1577).
    channel_id = RuleField(
        SLACK_CHANNEL,
        label=_("Slack channel ID"),
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
                # An empty channel says what to do; a malformed one, the
                # rule's own words (data-missing-hint).
                "data-empty-hint": _(
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


# What the Off-site backups step's complete gate says while copies are on and
# the folder link or the private-key checkbox is missing (#2200).
BACKUP_MISSING = _(
    "Add the folder link and confirm that the backup private key is kept safe, "
    "or untick Copy each backup to Google Drive to go on without it."
)
# What a refused save says at each field while copies are on (#2200): each
# says what that field needs (ui-conventions, field errors).
BACKUP_FIELD_MISSING = {
    "folder_link": _(
        "Paste the Google Drive folder link, or untick Copy each backup to "
        "Google Drive."
    ),
    "acknowledged": _(
        "Tick this box to confirm that the parish keeps the backup private key "
        "safe, or untick Copy each backup to Google Drive."
    ),
}
# Why the step cannot turn copies on in the local environment while it does
# not reach Google Drive: the words the Off-site backups page gives for Test
# access there (integration-settings.html).
BACKUP_LOCAL_REASON = _(
    "Off-site copies are not available in this local environment: it reaches "
    "Google Drive only with a mail redirect (deploy --mail-redirect) and the "
    "normal clock, so its backups stay inside the VM."
)


class SetupBackupForm(AdminForm):
    """The optional Off-site backups step: on or off, folder and key word (#2200).

    Only public values: whether copies are on, the canonical folder link (the
    Off-site backups page's field and form) and whether the Administrator
    confirmed that the backup private key is kept off the server. The folder
    and the checkbox are shown, and required, only while copies are ticked
    (``data-show-when``); hidden, they are disabled and not sent, so turning
    copies off clears them. ``clean`` is the authority either way.
    """

    enabled = forms.BooleanField(
        label=_("Copy each backup to Google Drive"), required=False
    )
    folder_link = forms.CharField(
        label=_("Google Drive folder link"), max_length=500, required=False
    )
    acknowledged = forms.BooleanField(
        label=_(
            "I know where the parish keeps the backup private key, away from "
            "this server. Without it no backup can be restored."
        ),
        required=False,
    )

    def __init__(self, *args, mailbox=None, drive_closed=False, **kwargs):
        """Explain the fields; follow the switch; close copies where Drive is.

        ``mailbox`` is the delegated mailbox user saved on Outgoing email
        settings, which the folder must let add files. ``drive_closed`` is
        the local environment without its Drive terms
        (``integration_views._drive_in_local``): the switch is drawn
        unticked and unavailable, its help saying why, and ``clean``
        refuses a tick anyway.
        """
        from .integration_forms import ERROR_LINE_TEMPLATE, FOLDER_LINK_PATTERN

        super().__init__(*args, **kwargs)
        self.drive_closed = drive_closed
        # Help text may be drawn unescaped (help_text|safe), so the saved
        # address is escaped here and the whole text marked safe.
        folder_help = mark_safe(
            escape(
                _(
                    "Open the folder in Google Drive and copy the link from the "
                    "address bar (it contains /folders/). Each backup is copied "
                    "there as its own folder of encrypted files; the newest 30 "
                    "are kept. The delegated mailbox user saved on Outgoing email "
                    "settings (%(mailbox)s) must be able to add files to the "
                    "folder, for example as a Content manager of the folder's "
                    "shared drive."
                )
            )
            % {"mailbox": escape(mailbox or _("not saved yet"))}
        )
        texts = setup_help.BACKUP | {"folder_link": folder_help}
        if drive_closed:
            # The reason stays visible under the switch (a checkbox with no
            # one-line hint keeps its help, field_tips.eligible) and describes it.
            texts |= {"enabled": BACKUP_LOCAL_REASON}
        setup_help.apply(self, texts, replace=True)
        if not drive_closed:
            # The switch's long help opens from its "i", leaving one line
            # under its words (one help line under a field, #2283).
            field_tips.shorten(self, {"enabled": setup_help.BACKUP_ENABLED_HINT})
        shown = f"{self.add_prefix('enabled')}=on"
        for name in ("folder_link", "acknowledged"):
            self.fields[name].widget.attrs.update(
                {
                    "data-show-when": shown,
                    "data-required-when-shown": "",
                    "data-missing-hint": BACKUP_MISSING,
                }
            )
        self.fields["folder_link"].widget.attrs |= {
            "pattern": FOLDER_LINK_PATTERN,
            "spellcheck": "false",
        }
        # A reserved error line, shown or not, as on the Off-site backups
        # page, so a refused link never moves the controls below (no layout
        # shift). Set after the tip, which only a field with no template of
        # its own takes.
        self.fields["folder_link"].template_name = ERROR_LINE_TEMPLATE
        if drive_closed:
            # Drawn unticked as well as unavailable: a disabled box is not
            # sent, so a draft saved on elsewhere would otherwise draw its
            # folder fields with a switch whose tick the save never sees.
            self.initial["enabled"] = False
            self.fields["enabled"].widget.attrs["disabled"] = True

    def clean_folder_link(self):
        """Store one canonical folder link, whichever form was pasted."""
        from parishkit.stewardship.backup_drive import folder_id_from_url

        from .backup_destination import folder_link

        value = self.cleaned_data["folder_link"]
        if not value.strip():
            return ""
        try:
            return folder_link(folder_id_from_url(value))
        except ValueError as error:
            raise forms.ValidationError(
                str(error), code="invalid_folder_link"
            ) from None

    def clean(self):
        """Copies on need a folder and the key word; off carries neither."""
        values = super().clean()
        if self.errors:
            return values
        if values["enabled"] and self.drive_closed:
            raise forms.ValidationError(BACKUP_LOCAL_REASON, code="drive_not_in_local")
        if values["enabled"]:
            for name in ("folder_link", "acknowledged"):
                if not values[name]:
                    self.add_error(
                        name,
                        forms.ValidationError(
                            BACKUP_FIELD_MISSING[name], code="required"
                        ),
                    )
        elif values["folder_link"] or values["acknowledged"]:
            raise forms.ValidationError(
                _(
                    "Tick Copy each backup to Google Drive to save a folder, or "
                    "clear the folder link and the private-key checkbox."
                ),
                code="backup_mismatch",
            )
        return values


class SetupTestingForm(AdminForm):
    """One destination applies to all test mail; no Production switch exists here."""

    testing_recipient = EmailRuleField(
        EMAIL.worded(_("Enter a valid Testing recipient address.")),
        label=_("Testing recipient"),
        max_length=254,
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
# Steps a setup may finish without ever saving (#2200): the draft may hold
# them, and they are compiled when saved, but no step needs them.
OPTIONAL_FORMS = {"backup": SetupBackupForm}
# Every step a draft can save.
STEP_FORMS = FORMS | OPTIONAL_FORMS
# The boolean values of each step; every other value is a string, except the
# access step's lists.
BOOLEAN_VALUES = {
    "slack": frozenset({"enabled"}),
    "backup": frozenset({"enabled", "acknowledged"}),
}


# The hosted-domain lists an access step saved before sign-in became
# address-only (#952, #993). Such a step is still read when every list is
# empty; one that granted a domain must be saved again.
LEGACY_ACCESS_FIELDS = frozenset({"staff_domains", "ministry_domains"})


def _without_domains(values):
    """An access step saved with the retired domain lists, without them.

    Domains no longer grant sign-in, so a saved domain is refused rather than
    silently dropped: the Admin saves the step again and lists addresses. The
    refusal reaches Finish setup as a user-facing message that names the
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
    if step not in STEP_FORMS:
        raise ValueError("Unknown setup step.")
    return {
        name: "\n".join(value) if step == "access" else deepcopy(value)
        for name, value in values.items()
    }


def validate_values(step, values):
    """Service callers cannot inject extra fields, coercions or private payloads.

    Only the wizard's steps (``STEP_FORMS``) are accepted. The first-campaign
    steps the wizard saved before #142 (campaign, schedules, content) are
    refused like any other unknown step.
    """
    if step not in STEP_FORMS or type(values) is not dict:
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
    form_type = STEP_FORMS[step]
    if set(values) != set(form_type.base_fields):
        raise ValueError("Invalid public setup fields.")
    if step != "access" and any(
        type(value) is not (bool if name in BOOLEAN_VALUES.get(step, ()) else str)
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

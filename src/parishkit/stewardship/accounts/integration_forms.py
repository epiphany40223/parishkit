"""Closed public-settings and write-only private-candidate forms."""

import re

from django import forms
from django.utils.translation import gettext_lazy as _

from parishkit.stewardship.jobs.operational_sources import configured_policy
from parishkit.stewardship.source.cadence import (
    DEFAULT_DELTA_REFRESH,
    DEFAULT_TIME,
    MAX_FULL_REFRESH_TIMES,
    canonical_time,
    longest_gap,
)
from parishkit.stewardship.web.sender_name_field import SenderNameField

from . import field_tips, setup_help
from .key_files import MAX_FILE_BYTES
from .policy_schema import normalized_email

LABELS = {
    "parishsoft": _("ParishSoft"),
    "google_workspace": _("Google Workspace mail"),
    "email": _("Outgoing email addresses"),
    "slack": _("Slack notifications"),
    "backup": _("Off-site backups (Google Drive)"),
    "backup_key": _("Backup encryption key"),
}


# Stored values match source.cadence.FREQUENCIES.
REFRESH_CHOICES = (
    ("daily", _("At set times each day")),
    ("hourly", _("Once an hour")),
    ("quarter_hour", _("Every 15 minutes")),
)
# Stored values match source.cadence.DELTA_REFRESHES (#465).
DELTA_CHOICES = (
    ("quarter_hour", _("Every 15 minutes (recommended)")),
    ("hourly", _("Once an hour")),
    ("off", _("Off")),
)


class RefreshTimesField(forms.CharField):
    """One to eight parish-local HH:MM times, typed as a comma-separated list.

    The stored value is a sorted list of unique times; the field shows it
    as "02:00, 12:00" and reads the same shape back, also accepting spaces
    or semicolons between times. Omitted, it keeps the documented default.
    """

    def prepare_value(self, value):
        """Show a stored list as text; a posted string is shown as typed."""
        if isinstance(value, (list, tuple)):
            return ", ".join(value)
        return value

    def to_python(self, value):
        """Split the typed text into a sorted list of unique canonical times."""
        text = super().to_python(value)
        if not text:
            return [DEFAULT_TIME]
        times = [part for part in re.split(r"[\s,;]+", text.strip()) if part]
        bad = next((part for part in times if not canonical_time(part)), None)
        if not times or bad is not None:
            raise forms.ValidationError(
                _(
                    "%(value)s is not a 24-hour HH:MM time. Separate times with "
                    "commas, for example 02:00, 12:00 (leave blank for 02:00)."
                ),
                code="invalid",
                params={"value": bad},
            )
        times = sorted(set(times))
        if len(times) > MAX_FULL_REFRESH_TIMES:
            raise forms.ValidationError(
                _("Enter at most %(count)s times."),
                code="max_times",
                params={"count": MAX_FULL_REFRESH_TIMES},
            )
        return times


class IntegrationForm(forms.Form):
    """Non-secret settings never accept fingerprints, keys or arbitrary URLs."""

    base_digest = forms.RegexField(
        regex=r"^[0-9a-f]{64}$", max_length=64, widget=forms.HiddenInput
    )

    def __init__(self, target, *args, loaded_organization=None, **kwargs):
        """Use a compiled form per integration, not caller-provided schema fields.

        ``loaded_organization`` is the ParishSoft organization ID whose data
        is already loaded, or None before the first load. Once data is
        loaded, the organization ID is shown read-only and a different value
        is refused: every later refresh must read that same organization, so
        a changed ID would stop them all (see ``source.requests``).
        """
        super().__init__(*args, **kwargs)
        self.target = target
        self.loaded_organization = loaded_organization
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
            self.fields["full_refresh"] = forms.ChoiceField(
                label=_("Full ParishSoft refresh"),
                choices=REFRESH_CHOICES,
                initial="daily",
                required=False,
                help_text=_(
                    "How often to reload all Families, Members and ministries from "
                    "ParishSoft. A full load takes a few minutes and is the only "
                    "way new Families, status changes, Members, ministries and "
                    "giving arrive. Refreshes never overlap: a refresh that comes "
                    "due while another is running waits for it."
                ),
            )
            self.fields["full_refresh_times"] = RefreshTimesField(
                label=_("At these times"),
                initial=[DEFAULT_TIME],
                required=False,
                max_length=128,
                # Shown only for the set-times frequency (ui-v1.js); the view
                # keeps the stored times otherwise.
                widget=forms.TextInput(
                    attrs={
                        "data-show-when": "full_refresh=daily",
                        "placeholder": "02:00, 12:00",
                        "autocomplete": "off",
                    }
                ),
                help_text=_(
                    "Parish-local times, 24-hour, up to eight, separated by "
                    "commas; blank means 02:00 alone. The earliest is the nightly "
                    "refresh, which runs even while Family emails are being sent; "
                    "the others wait for a send to finish and then catch up. Each "
                    "full refresh takes a few minutes of ParishSoft's time."
                ),
            )
            self.fields["delta_refresh"] = forms.ChoiceField(
                label=_("Quick updates between full refreshes"),
                choices=DELTA_CHOICES,
                initial=DEFAULT_DELTA_REFRESH,
                required=False,
                help_text=_(
                    "Quick updates read only the Family address, phone, email and "
                    "registration changes ParishSoft reports; everything else "
                    "waits for a full refresh. They also keep the data counted as "
                    "fresh, so a longer gap than this server's freshness window "
                    "(%(minutes)s minutes) is refused."
                )
                % {"minutes": configured_policy().source_stale_seconds // 60},
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
                "full_refresh": _("A full refresh takes a few minutes."),
                "full_refresh_times": _("HH:MM, comma-separated, up to eight."),
                "delta_refresh": _("Family contact changes only; keep 15 minutes."),
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

    def public_settings(self):
        """Normalize exact YAML types after validation; never include the base field."""
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
        if "full_refresh_times" in settings:
            # The earliest listed time is the nightly refresh, the one that
            # runs during a Family send; the schedule stores it by name too.
            settings["nightly_time"] = settings["full_refresh_times"][0]
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

    def clean_full_refresh(self):
        """An omitted frequency keeps the documented once-a-day default."""
        return self.cleaned_data["full_refresh"] or "daily"

    def clean_delta_refresh(self):
        """An omitted cadence keeps the documented quarter-hour default."""
        return self.cleaned_data["delta_refresh"] or DEFAULT_DELTA_REFRESH

    def clean(self):
        """Refuse a schedule whose gaps would trip this server's staleness alarm.

        The source-staleness alarm (``source_stale_seconds``, 30 minutes by
        default) sounds whenever no refresh has run within its window. Quick
        updates every 15 minutes keep it quiet; hourly or no quick updates
        leave gaps the alarm would report around the clock, unless the
        deployment's window is long enough. The schedule itself stays
        valid configuration: only this form knows the server's policy.
        """
        cleaned = super().clean()
        if self.target != "parishsoft" or self.errors:
            return cleaned
        stale = configured_policy().source_stale_seconds
        gap = longest_gap(
            frequency=cleaned["full_refresh"],
            full_refresh_times=cleaned["full_refresh_times"],
            delta_refresh=cleaned["delta_refresh"],
        )
        if gap.total_seconds() > stale:
            # More full-refresh times can close the gap only when deltas are
            # off and eight times a day would fit the window.
            more_times = (
                cleaned["delta_refresh"] == "off"
                and cleaned["full_refresh"] == "daily"
                and stale * MAX_FULL_REFRESH_TIMES >= 24 * 3600
            )
            self.add_error(
                "delta_refresh",
                forms.ValidationError(
                    _(
                        "With this schedule ParishSoft data would go %(gap)s "
                        "minutes between refreshes, longer than this server's "
                        "%(stale)s-minute freshness window, so the server would "
                        "keep reporting the data as out of date. Choose "
                        "\u201cEvery 15 minutes (recommended)\u201d%(more)s, or "
                        "ask whoever manages the server to lengthen "
                        "source_stale_seconds."
                    ),
                    code="stale_gap",
                    params={
                        "gap": int(gap.total_seconds()) // 60,
                        "stale": stale // 60,
                        "more": _(", add full refresh times") if more_times else "",
                    },
                ),
            )
        return cleaned


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

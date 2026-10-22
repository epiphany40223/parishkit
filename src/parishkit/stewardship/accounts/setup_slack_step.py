"""The one Slack notifications setup step: switch, channel and bot token (#1578).

Slack had two setup steps, the public settings and a separate Slack
connection page, only because the credential page is shared by the three
providers. The step now takes all three together: Enable Slack
notifications and, shown only while it is ticked, the channel ID and the
bot token. One Save and continue saves the step and stages the token in one
work transaction under the exact draft version, so a refused token saves
nothing. The token field is the credential page's (``SecretInput``, its
labels and keep rules): never redisplayed, kept by leaving it empty, and
needed again when the channel changes, since it is sealed with its channel.
"""

from django import forms

from parishkit.config import ConfigError
from parishkit.stewardship.campaigns.work_locks import work_transaction

from . import setup_help
from .key_files import MAX_FILE_BYTES
from .secret_input import SecretInput
from .setup_credential_views import FIELD_LABELS, INVALID, KEEP_ERRORS, MISSING
from .setup_credentials import stage_credential
from .setup_drafts import save_section
from .setup_forms import SetupSlackForm, form_values
from .setup_secret_models import SetupSealedCredential


def saved_channel(draft):
    """The channel this setup's staged Slack token is sealed with, or None.

    None when no unscrubbed token is staged (never entered, or discarded by
    turning Slack off, #1499). Only the public receipt column is read.
    """
    settings = (
        SetupSealedCredential.objects.filter(
            attempt_id=draft.status.attempt_id, target="slack", scrubbed_at=None
        )
        .values_list("settings", flat=True)
        .first()
    )
    return None if settings is None else settings["channel_id"]


class SetupSlackStepForm(SetupSlackForm):
    """The Slack step's page form: the saved section plus the bot token.

    Only ``enabled`` and ``channel_id`` are the draft section (``values``);
    the token is staged, never stored in the draft. Shown only while Slack
    is ticked and disabled (not sent) otherwise. It is required while shown
    when no token is saved for the channel on the page: always without a
    saved token, and once the channel differs from the saved token's
    (``data-required-when``, as the ParishSoft key follows its organization).
    The server's checks in ``save`` are the authority either way.
    """

    candidate = forms.CharField(
        required=False,
        max_length=MAX_FILE_BYTES,
        strip=False,
        widget=SecretInput("slack"),
    )

    def __init__(self, *args, saved=None, **kwargs):
        """``saved`` is ``saved_channel``: the staged token's channel, or None."""
        super().__init__(*args, **kwargs)
        field = self.fields["candidate"]
        field.label = FIELD_LABELS["slack"]
        # The widget is built after the field, so restore maxlength.
        field.widget.attrs.update(field.widget_attrs(field.widget))
        field.widget.attrs |= {
            "data-show-when": f"{self.add_prefix('enabled')}=on",
            "data-missing-hint": MISSING["slack"],
        }
        if saved is None:
            field.widget.attrs["data-required-when-shown"] = ""
        else:
            field.widget.attrs |= {
                "data-required-when": f"{self.add_prefix('channel_id')}!={saved}",
                "data-missing-hint": KEEP_ERRORS["channel"],
            }
        setup_help.apply(self, setup_help.CREDENTIALS["slack"], replace=True)

    def values(self):
        """The validated draft section, without the token."""
        token = self.cleaned_data.pop("candidate")
        try:
            return form_values(self)
        finally:
            self.cleaned_data["candidate"] = token


def save(request, service, draft, form, version):
    """Save the valid Slack step; return True, or False with a field error.

    Off: only the section is saved, which discards a staged token and a
    queued test (#1499). On with no token entered: the saved token is kept
    when it is sealed with this channel, and otherwise the field says it is
    needed. On with a token: the section and the token in one transaction,
    the token staged at the version the section save produced, so a token
    the staging owner refuses (malformed) leaves nothing saved.
    """
    values = form.values()
    token = form.cleaned_data["candidate"]
    if values["enabled"] and not token:
        saved = saved_channel(draft)
        if saved != values["channel_id"]:
            message = MISSING["slack"] if saved is None else KEEP_ERRORS["channel"]
            form.add_error(
                "candidate", forms.ValidationError(message, code="token_required")
            )
            return False
    arguments = dict(step="slack", values=values, expected_version=version)
    if not (values["enabled"] and token):
        save_section(request, service, draft.status.attempt_id, **arguments)
        return True
    candidate = token.encode("utf-8")
    try:
        with work_transaction():
            status = save_section(
                request, service, draft.status.attempt_id, **arguments
            )
            try:
                stage_credential(
                    request,
                    service,
                    draft.status.attempt_id,
                    target="slack",
                    candidate=candidate,
                    expected_version=status.version,
                )
            except ValueError as error:
                if isinstance(error, ConfigError):
                    raise
                # Leave the transaction by an exception, so the section
                # save rolls back with the refused token.
                raise _Refused from None
    except _Refused:
        form.add_error(
            "candidate",
            forms.ValidationError(INVALID["slack"], code="invalid_credential"),
        )
        return False
    finally:
        del candidate
    return True


class _Refused(Exception):
    """The staging owner refused the token's format (``save``'s rollback)."""

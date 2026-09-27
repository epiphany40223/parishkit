"""Write-only credential forms for one original, still-collecting setup attempt."""

from django import forms
from django.http import HttpResponseRedirect
from django.shortcuts import render
from django.utils.translation import gettext_lazy as _
from django.views.decorators.debug import sensitive_post_parameters
from django.views.decorators.http import require_http_methods

from parishkit.config import ConfigError
from parishkit.stewardship.storage import StaleRecordError
from parishkit.stewardship.web.contracts import expected_version

from . import setup_help
from .authentication import runtime
from .integration_forms import LABELS, CredentialForm
from .sessions import require_fresh
from .setup_credentials import TARGETS, credential_status, stage_credential
from .setup_drafts import view_draft
from .setup_policy import SetupState
from .setup_views import ERRORS, _checked, _closed, _context, page_error
from .setup_wizard import BY_KEY, continue_after, wizard_for

# The wizard page key of each credential target's page.
STEP = {
    "parishsoft": "parishsoft",
    "google_workspace": "google_workspace",
    "slack": "slack_credential",
}


# Closed messages for a credential the staging owner rejects as malformed; the
# owner's own exception text is never shown.
INVALID = {
    "parishsoft": _(
        "This does not look like a ParishSoft API key. Paste the key exactly as "
        "ParishSoft provided it, on one line with no spaces."
    ),
    "google_workspace": _(
        "This is not a usable Google service-account key. Paste the entire, "
        "unmodified JSON key file downloaded from Google Cloud."
    ),
    "slack": _(
        "This does not look like a Slack bot token. Paste the Bot User OAuth "
        "Token, which starts with xoxb-, on one line with no spaces."
    ),
}


def prerequisite(wizard, key):
    """Return (reason, fixing page URL) when this credential cannot be staged yet.

    The staging owner enforces the same rule; checking first lets the page show
    the reason next to the form instead of failing the whole request.
    """
    step = wizard.step(key) if wizard else None
    if key == "slack_credential" and step is None:
        return (
            _("Turn on Slack notifications and save the Slack channel first."),
            BY_KEY["slack"].url,
        )
    if step is not None and step.state == "blocked":
        return step.status, step.fix_url
    return None


# What exactly to paste for each service.
FIELD_LABELS = {
    "parishsoft": _("ParishSoft API key"),
    "google_workspace": _("Service-account JSON key file contents"),
    "slack": _("Slack bot token"),
}


class SetupCredentialForm(CredentialForm):
    """Reuse the write-only widget; this attempt/version replaces a live intent."""

    intent = None

    def __init__(self, target, *args, **kwargs):
        """Only ParishSoft accepts an explicit organization; other scope is staged.

        API keys and bot tokens are single-line secrets, so they use a password
        input that never redisplays its value; the Google service-account key
        is multi-line JSON and keeps the write-only text area.
        """
        super().__init__(*args, **kwargs)
        if target == "parishsoft":
            self.fields["organization_id"] = forms.IntegerField(
                label=_("Expected ParishSoft organization ID"),
                min_value=1,
                max_value=2**31 - 1,
            )
        candidate = self.fields["candidate"]
        if target in {"parishsoft", "slack"}:
            candidate.widget = forms.PasswordInput(
                render_value=False,
                attrs={
                    "autocomplete": "off",
                    "spellcheck": "false",
                    "autocapitalize": "off",
                },
            )
            # PasswordInput is built after the field, so restore maxlength.
            candidate.widget.attrs.update(candidate.widget_attrs(candidate.widget))
        candidate.label = FIELD_LABELS[target]
        setup_help.apply(self, setup_help.CREDENTIALS[target], replace=True)


@sensitive_post_parameters("candidate")
@require_http_methods(["GET", "HEAD", "POST"])
def setup_credential(request, target):
    """Do not write files, contact providers or enqueue a live replacement here."""
    try:
        if target not in TARGETS:
            raise LookupError("Unknown setup credential target.")
        service = runtime()
        draft = view_draft(request, service)
        if draft is None or draft.status.state != SetupState.COLLECTING:
            return _checked(request, service, HttpResponseRedirect("/admin/setup"))
        require_fresh(request)
        _closed(request, {*SetupCredentialForm(target).fields, "version"})
        receipts = credential_status(request, service, draft.status.attempt_id)
        blocked = prerequisite(wizard_for(draft), STEP[target])
        if request.method == "POST":
            version = expected_version(request.POST.get("version"))
            if version != draft.status.version:
                raise StaleRecordError("Reload this setup form.")
            form = SetupCredentialForm(target, request.POST)
            if form.is_valid() and blocked:
                form.add_error(None, blocked[0])
            elif form.is_valid():
                candidate = form.cleaned_data.pop("candidate").encode("utf-8")
                try:
                    stage_credential(
                        request,
                        service,
                        draft.status.attempt_id,
                        target=target,
                        candidate=candidate,
                        expected_version=version,
                        organization_id=form.cleaned_data.get("organization_id"),
                    )
                except ValueError as error:
                    if isinstance(error, ConfigError):
                        raise
                    # A malformed candidate is a field error on this page. The
                    # staging transaction has already rolled back.
                    form.add_error("candidate", INVALID[target])
                else:
                    return _checked(
                        request,
                        service,
                        continue_after(request, service, STEP[target]),
                    )
                finally:
                    del candidate
            status = 400
        else:
            form, status = SetupCredentialForm(target), 200
        response = render(
            request,
            "stewardship/setup-credential.html",
            _context(draft, STEP[target])
            | {
                "form": form,
                "target": target,
                "label": LABELS[target],
                "saved": any(receipt.target == target for receipt in receipts),
                "prerequisite": blocked,
            },
            status=status,
        )
        return _checked(request, service, response, draft)
    except ERRORS as error:
        return page_error(request, error, STEP.get(target))

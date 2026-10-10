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
from .setup_wizard import BY_KEY, continue_after

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


# Messages for keeping (not re-entering) an already saved credential.
KEEP_ERRORS = {
    "stale": _(
        "The saved credential was entered for settings you have since changed. "
        "Enter it again so it matches the current settings."
    ),
    "organization": _(
        "To change the organization ID, enter the ParishSoft API key again as well."
    ),
}


# What the complete gate's hint says while the secret field is empty (#563).
MISSING = {
    "parishsoft": _("Paste the ParishSoft API key to continue."),
    "google_workspace": _("Paste the service-account JSON key to continue."),
    "slack": _("Paste the Slack bot token to continue."),
}


class SetupCredentialForm(CredentialForm):
    """Reuse the write-only widget; this attempt/version replaces a live intent.

    API keys and bot tokens are single-line secrets, so they use a password
    input that never redisplays its value; the Google service-account key is
    multi-line JSON and keeps the write-only text area. When a credential is
    already saved and still current, the secret field may be left empty to
    keep it. A saved one that is ``stale`` (entered for settings changed
    since) must be entered again, so the field is required with the same
    message the keep check gives. While a current ParishSoft key is kept, the
    page script requires the key only once the organization ID differs from
    ``organization``, the saved one (#563); the server's keep check is the
    authority either way.
    """

    intent = None

    def __init__(
        self, target, *args, saved=False, stale=False, organization=None, **kwargs
    ):
        """Only ParishSoft accepts an explicit organization; other scope is staged."""
        super().__init__(*args, **kwargs)
        if target == "parishsoft":
            self.fields["organization_id"] = forms.IntegerField(
                label=_("Expected ParishSoft organization ID"),
                min_value=1,
                max_value=2**31 - 1,
                widget=forms.NumberInput(
                    attrs={
                        "data-missing-hint": _(
                            "Enter the ParishSoft organization ID, a whole number."
                        )
                    }
                ),
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
        candidate.required = not saved or stale
        candidate.widget.attrs["data-missing-hint"] = MISSING[target]
        if saved and stale:
            candidate.error_messages["required"] = KEEP_ERRORS["stale"]
            candidate.widget.attrs["data-missing-hint"] = KEEP_ERRORS["stale"]
        elif saved and organization is not None:
            candidate.widget.attrs |= {
                "data-required-when": f"organization_id!={organization}",
                "data-missing-hint": KEEP_ERRORS["organization"],
            }
        setup_help.apply(self, setup_help.CREDENTIALS[target], replace=True)


def _keep_refusal(form, current, organization):
    """Why an empty key cannot keep the saved credential, or None if it can.

    A key saved for mail or Slack settings changed since is stale, and a
    ParishSoft organization cannot change without re-entering its key, since
    the organization is sealed together with that key.
    """
    if not current:
        return KEEP_ERRORS["stale"]
    if form.cleaned_data.get("organization_id", organization) != organization:
        return KEEP_ERRORS["organization"]
    return None


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
        context = _context(draft, STEP[target])
        wizard = context["wizard"]
        blocked = prerequisite(wizard, STEP[target])
        saved = next((item for item in receipts if item.target == target), None)
        # Never the secret: only whether one is saved and its public scope. A
        # saved key whose step is not done is stale, unless the step is blocked
        # (``blocked``): the page then shows that reason instead.
        step = wizard.step(STEP[target])
        current = saved is not None and step is not None and step.state == "done"
        organization = saved.settings.get("organization_id") if saved else None
        initial = {"organization_id": organization} if organization else {}
        # A stale saved key must be entered again. A blocked step keeps the
        # field as it was: its Save is unavailable and the server answers
        # with the prerequisite, not with a missing key.
        options = {
            "saved": saved is not None,
            "stale": saved is not None and not current and not blocked,
            "organization": organization,
            "initial": initial,
        }
        if request.method == "POST":
            version = expected_version(request.POST.get("version"))
            if version != draft.status.version:
                raise StaleRecordError("Reload this setup form.")
            form = SetupCredentialForm(target, request.POST, **options)
            if form.is_valid() and blocked:
                form.add_error(None, blocked[0])
            elif form.is_valid() and not form.cleaned_data["candidate"]:
                # Keep the saved credential: nothing is staged or re-sealed.
                refusal = _keep_refusal(form, current, organization)
                if refusal is None:
                    return _checked(
                        request,
                        service,
                        continue_after(request, service, STEP[target]),
                    )
                form.add_error("candidate", refusal)
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
            form = SetupCredentialForm(target, **options)
            status = 200
        response = render(
            request,
            "stewardship/setup-credential.html",
            context
            | {
                "form": form,
                "target": target,
                "label": LABELS[target],
                # The heading uses the step's stepper label (one name per page).
                "step_label": BY_KEY[STEP[target]].label,
                "saved": saved is not None,
                "current": current,
                "organization": organization,
                "prerequisite": blocked,
            },
            status=status,
        )
        return _checked(request, service, response, draft)
    except ERRORS as error:
        return page_error(request, error, STEP.get(target))

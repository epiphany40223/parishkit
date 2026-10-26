"""Write-only credential forms for one original, still-collecting setup attempt."""

from django import forms
from django.http import HttpResponseRedirect
from django.shortcuts import render
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.views.decorators.debug import sensitive_post_parameters
from django.views.decorators.http import require_http_methods

from parishkit.config import ConfigError
from parishkit.stewardship.source.organization_names import organization_label
from parishkit.stewardship.storage import StaleRecordError
from parishkit.stewardship.web.admin_forms import AdminForm
from parishkit.stewardship.web.contracts import expected_version

from . import setup_help
from .authentication import runtime
from .integration_forms import KEY_RULES, LABELS, CredentialForm, rule_key
from .secret_input import SecretInput
from .setup_credentials import credential_status, stage_credential
from .setup_drafts import view_draft
from .setup_lookups import (
    choose_organization,
    chosen_name,
    current_lookup,
    discard_key,
    retry_lookup,
)
from .setup_policy import SetupState
from .setup_views import ERRORS, _checked, _closed, _context, page_error
from .setup_wizard import BY_KEY, continue_after

# The wizard page key of each credential target's page. The Slack bot token
# has no page of its own: the Slack notifications step takes it with the
# channel (#1578), through the shared pieces below, so this page refuses the
# Slack target (404) and its old address is not kept (no legacy URLs).
STEP = {
    "parishsoft": "parishsoft",
    "google_workspace": "google_workspace",
}


# Closed messages for a credential the staging owner rejects as malformed; the
# owner's own exception text is never shown.
# A ParishSoft key or Slack token says its shared key rule's words, which
# the page checks as it is typed (#1577).
INVALID = {
    "parishsoft": KEY_RULES["parishsoft"].message,
    "google_workspace": _(
        "This is not a usable Google service-account key. Paste the entire, "
        "unmodified JSON key file downloaded from Google Cloud."
    ),
    "slack": KEY_RULES["slack"].message,
}


# What the page says at the key field when the organization lookup fails,
# by its stable error code (#1554, admin-portal spec "ParishSoft
# organization choice").
LOOKUP_ERRORS = {
    "parishsoft_key_refused": _(
        "ParishSoft did not accept this key. Check that you copied the whole "
        "key, then try again."
    ),
    "parishsoft_no_organizations": _(
        "ParishSoft accepted this key, but it does not reach any parish "
        "organization. Ask ParishSoft support for your parish's API key."
    ),
    "parishsoft_no_family_organizations": _(
        "ParishSoft accepted this key, but none of the organizations it reaches "
        "has any Families. Ask ParishSoft support for your parish's API key."
    ),
    "parishsoft_organizations_unreadable": _(
        "ParishSoft's answer could not be read. Try again in a few minutes; if "
        "it keeps happening, tell your IT contact."
    ),
    "parishsoft_unavailable": _(
        "ParishSoft could not be reached. Try again in a few minutes."
    ),
}
# A list longer than this adds a type-to-filter box above it (#1554).
FILTER_ABOVE = 20


def prerequisite(wizard, key):
    """Return (reason, fixing page URL) when this credential cannot be staged yet.

    The staging owner enforces the same rule; checking first lets the page show
    the reason next to the form instead of failing the whole request.
    """
    step = wizard.step(key) if wizard else None
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
    "channel": _(
        "To change the Slack channel ID, enter the Slack bot token again as well."
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

    API keys and bot tokens are single-line secrets, so they use the shared
    ``SecretInput`` (masked, with the Show control, #1475), which never
    redisplays its value; the Google service-account key is
    multi-line JSON and keeps the write-only text area. When a credential is
    already saved and still current, the secret field may be left empty to
    keep it. A saved one that is ``stale`` (entered for settings changed
    since) must be entered again, so the field is required with the same
    message the keep check gives. The ParishSoft page has no organization
    field: the organization is chosen from the key's own lookup (#1554).
    """

    intent = None

    def __init__(self, target, *args, saved=False, stale=False, **kwargs):
        """Only the secret field; its requirement follows the saved credential."""
        super().__init__(*args, **kwargs)
        candidate = self.fields["candidate"]
        if target in {"parishsoft", "slack"}:
            candidate.widget = SecretInput(target)
            # The widget is built after the field, so restore maxlength.
            candidate.widget.attrs.update(candidate.widget_attrs(candidate.widget))
        candidate.label = FIELD_LABELS[target]
        candidate.required = not saved or stale
        # What the complete gate says while the key is empty.
        empty = MISSING[target]
        if saved and stale:
            candidate.error_messages["required"] = KEEP_ERRORS["stale"]
            empty = KEEP_ERRORS["stale"]
        if target in KEY_RULES:
            # Spaces around a pasted key are stripped, as Integration
            # settings strips them; the key itself may hold none (#1577).
            candidate.strip = True
        self.key_rule = rule_key(candidate, target, empty)
        setup_help.apply(self, setup_help.CREDENTIALS[target], replace=True)

    def clean_candidate(self):
        """A pasted key follows its rule (#1577); blank may keep the saved one."""
        value = self.cleaned_data["candidate"]
        if value and self.key_rule is not None:
            self.key_rule.check(value)
        return value


class LookupForm(AdminForm):
    """One answer to the organization lookup: Yes or Use this, No or Cancel,
    and Check again or Try again (#1554).

    ``action`` names the button; ``lookup`` binds it to the exact lookup the
    page showed. ``organization`` is the chosen ID, one of the selectable
    listed organizations (anything else is ``invalid_choice``); only the
    choice needs it.
    """

    ACTIONS = ("choose", "discard", "retry")
    action = forms.ChoiceField(choices=[(name, name) for name in ACTIONS])
    lookup = forms.UUIDField()
    organization = forms.TypedChoiceField(coerce=int, required=False)

    def __init__(self, *args, lookup, **kwargs):
        """Offer exactly the lookup's selectable organizations."""
        super().__init__(*args, **kwargs)
        self.fields["organization"].choices = [
            (str(item.identifier), str(item.identifier))
            for item in lookup.organizations
            if item.selectable
        ]
        self.fields["organization"].error_messages["invalid_choice"] = _(
            "Choose one of the listed organizations that has Families."
        )

    def clean(self):
        """The choice names an organization; the other buttons need none."""
        cleaned = super().clean()
        if cleaned.get("action") == "choose" and cleaned.get("organization") in (
            None,
            "",
        ):
            self.add_error(
                "organization",
                forms.ValidationError(
                    _("Choose your parish's organization."), code="required"
                ),
            )
        return cleaned


def _keep_refusal(current):
    """Why an empty key cannot keep the saved credential, or None if it can.

    A key saved for mail or Slack settings changed since is stale.
    """
    return None if current else KEEP_ERRORS["stale"]


def _self(target):
    """This page's own address, where a ParishSoft key's lookup is answered."""
    return HttpResponseRedirect(reverse("admin:setup_credential", args=[target]))


def _lookup_context(attempt_id, lookup):
    """What the page shows of an open organization lookup (#1554)."""
    if lookup is None:
        return {}
    shown = [
        {
            "identifier": item.identifier,
            "label": organization_label(item.identifier, item.name),
            "families": item.families,
            "selectable": item.selectable,
        }
        for item in lookup.organizations
    ]
    return {
        "lookup": lookup,
        "organizations": shown,
        "filterable": len(shown) > FILTER_ABOVE,
        "lookup_error": LOOKUP_ERRORS.get(lookup.error_code),
    }


def lookup_failure(form, lookup):
    """``form`` with a failed lookup's message at its key field, if any.

    A failed lookup is answered at the key field, with its stable code,
    ready for a different key (#1554). The form is unbound, so it has no
    cleaned data for ``add_error`` to trim.
    """
    if lookup is not None and lookup.error_code in LOOKUP_ERRORS:
        form.cleaned_data = {}
        form.add_error(
            "candidate",
            forms.ValidationError(
                LOOKUP_ERRORS[lookup.error_code], code=lookup.error_code
            ),
        )
    return form


def _answer_lookup(request, service, draft, target, lookup):
    """Handle a lookup button; return a redirect, or the bound form refused."""
    form = LookupForm(request.POST, lookup=lookup)
    if not form.is_valid():
        return None, form
    if form.cleaned_data["lookup"] != lookup.identifier:
        raise StaleRecordError("Reload this setup page.")
    values = dict(
        lookup_id=lookup.identifier,
        expected_version=expected_version(request.POST.get("version")),
    )
    attempt_id = draft.status.attempt_id
    action = form.cleaned_data["action"]
    if action == "choose":
        try:
            choose_organization(
                request,
                service,
                attempt_id,
                organization_id=form.cleaned_data["organization"],
                **values,
            )
        except ValueError as error:
            if isinstance(error, ConfigError):
                raise
            form.add_error(
                "organization",
                forms.ValidationError(
                    _("Choose one of the listed organizations that has Families."),
                    code="invalid_choice",
                ),
            )
            return None, form
        return continue_after(request, service, STEP[target]), form
    if action == "discard":
        discard_key(request, service, attempt_id, **values)
    else:
        retry_lookup(request, service, attempt_id, **values)
    return _self(target), form


@sensitive_post_parameters("candidate")
@require_http_methods(["GET", "HEAD", "POST"])
def setup_credential(request, target):
    """Do not write files, contact providers or enqueue a live replacement here.

    The ParishSoft page stages the key and then answers its organization
    lookup (#1554): while a lookup is open the page shows it (asking
    ParishSoft, the one organization to confirm, the list to choose from,
    or why it failed) instead of the saved-key notice, and its buttons post
    a ``LookupForm`` here.
    """
    try:
        if target not in STEP:
            raise LookupError("Unknown setup credential target.")
        service = runtime()
        draft = view_draft(request, service)
        if draft is None or draft.status.state != SetupState.COLLECTING:
            return _checked(request, service, HttpResponseRedirect("/admin/setup"))
        attempt_id = draft.status.attempt_id
        lookup = current_lookup(attempt_id) if target == "parishsoft" else None
        answering = request.method == "POST" and "action" in request.POST
        if answering and (target != "parishsoft" or lookup is None):
            # A lookup button from a page whose lookup is over or replaced.
            raise StaleRecordError("Reload this setup page.")
        if answering:
            _closed(request, {"action", "lookup", "organization", "version"})
        else:
            _closed(request, {*SetupCredentialForm(target).fields, "version"})
        receipts = credential_status(request, service, attempt_id)
        context = _context(draft, STEP[target])
        wizard = context["wizard"]
        blocked = prerequisite(wizard, STEP[target])
        # A scrubbed receipt (a Slack token discarded by turning Slack off,
        # #1499) is no saved credential, and neither is a ParishSoft key
        # whose organization is not chosen yet (#1554).
        saved = next(
            (
                item
                for item in receipts
                if item.target == target
                and not item.scrubbed
                and (target != "parishsoft" or "organization_id" in item.settings)
            ),
            None,
        )
        # Never the secret: only whether one is saved and its public scope. A
        # saved key whose step is not done is stale, unless the step is blocked
        # (``blocked``): the page then shows that reason instead.
        step = wizard.step(STEP[target])
        current = saved is not None and step is not None and step.state == "done"
        organization = saved.settings.get("organization_id") if saved else None
        # A stale saved key must be entered again. A blocked step keeps the
        # field as it was: its Save is unavailable and the server answers
        # with the prerequisite, not with a missing key.
        options = {
            "saved": saved is not None,
            "stale": saved is not None and not current and not blocked,
        }
        lookup_form = None
        if answering:
            redirect, lookup_form = _answer_lookup(
                request, service, draft, target, lookup
            )
            if redirect is not None:
                return _checked(request, service, redirect)
            form = SetupCredentialForm(target, **options)
            status = 400
        elif request.method == "POST":
            version = expected_version(request.POST.get("version"))
            if version != draft.status.version:
                raise StaleRecordError("Reload this setup form.")
            form = SetupCredentialForm(target, request.POST, **options)
            if form.is_valid() and blocked:
                form.add_error(
                    None, forms.ValidationError(blocked[0], code="prerequisite")
                )
            elif form.is_valid() and not form.cleaned_data["candidate"]:
                # Keep the saved credential: nothing is staged or re-sealed.
                refusal = _keep_refusal(current)
                if refusal is None:
                    if lookup is not None:
                        # The saved key pasted again, then kept (#1554): its
                        # lookup (a failed one, at the key field) goes, and
                        # only it, so Check parish data is not held back by a
                        # lookup nobody will answer. The key keeps its
                        # organization and any load bound to it.
                        discard_key(
                            request,
                            service,
                            attempt_id,
                            lookup_id=lookup.identifier,
                            expected_version=version,
                        )
                    return _checked(
                        request,
                        service,
                        continue_after(request, service, STEP[target]),
                    )
                form.add_error(
                    "candidate",
                    forms.ValidationError(refusal, code="kept_credential_refused"),
                )
            elif form.is_valid():
                candidate = form.cleaned_data.pop("candidate").encode("utf-8")
                try:
                    stage_credential(
                        request,
                        service,
                        attempt_id,
                        target=target,
                        candidate=candidate,
                        expected_version=version,
                    )
                except ValueError as error:
                    if isinstance(error, ConfigError):
                        raise
                    # A malformed candidate is a field error on this page. The
                    # staging transaction has already rolled back.
                    form.add_error(
                        "candidate",
                        forms.ValidationError(
                            INVALID[target], code="invalid_credential"
                        ),
                    )
                else:
                    # A ParishSoft key comes back here for its organization
                    # lookup; any other credential continues.
                    return _checked(
                        request,
                        service,
                        _self(target)
                        if target == "parishsoft"
                        else continue_after(request, service, STEP[target]),
                    )
                finally:
                    del candidate
            status = 400
        else:
            form = lookup_failure(SetupCredentialForm(target, **options), lookup)
            status = 200
        name = chosen_name(attempt_id, organization) if organization else None
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
                "organization_label": (
                    organization_label(organization, name) if organization else None
                ),
                "prerequisite": blocked,
                "lookup_form": lookup_form,
            }
            | _lookup_context(attempt_id, lookup),
            status=status,
        )
        return _checked(request, service, response, draft)
    except ERRORS as error:
        return page_error(request, error, STEP.get(target))

"""Versioned parish profile editor; existing campaigns retain their own timezone."""

from uuid import uuid4

from django import forms
from django.core import signing
from django.db import DatabaseError
from django.shortcuts import render
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_http_methods

from parishkit.config import ConfigError
from parishkit.stewardship.schema_primitives import timezone_names
from parishkit.stewardship.storage import StaleRecordError
from parishkit.stewardship.web.contracts import filters

from .admin_editing import (
    confirm,
    editable_configuration,
    error_response,
    form_action,
    principal,
    sign_preview,
)
from .authentication import runtime
from .limiting import LimiterUnavailable
from .policy import Capability, allows
from .request_patch import build_candidate
from .sessions import authenticated_admin

SALT = "stewardship-parish-profile-preview-v1"
PROFILE_FIELDS = ("name", "website", "timezone", "phone")


class ParishForm(forms.Form):
    """The profile form cannot modify branding, runtime mode or campaign snapshots."""

    name = forms.CharField(label=_("Parish name"), max_length=254)
    website = forms.URLField(
        label=_("Parish website"), max_length=2048, assume_scheme="https"
    )
    timezone = forms.ChoiceField(label=_("Parish timezone"))
    phone = forms.RegexField(
        label=_("Parish telephone"),
        regex=r"^\+1[2-9][0-9]{2}[2-9][0-9]{6}$",
        max_length=12,
        help_text=_(
            "Use +1 followed by the ten-digit US number, such as +12125551234."
        ),
        widget=forms.TextInput(attrs={"type": "tel", "autocomplete": "tel"}),
    )
    base_digest = forms.RegexField(
        regex=r"^[0-9a-f]{64}$", max_length=64, widget=forms.HiddenInput
    )

    def __init__(self, *args, **kwargs):
        """Use the same frozen timezone vocabulary as authoritative YAML validation."""
        super().__init__(*args, **kwargs)
        self.fields["timezone"].choices = [
            (name, name) for name in sorted(timezone_names())
        ]


def _scope(service):
    """Parish-only changes pin YAML; they have no dependency on a source refresh."""
    return editable_configuration(service), None


def _profile(configuration):
    """Read the applied canonical record so branding is preserved byte-for-byte."""
    return configuration.active_configuration.canonical_document["sections"]["parish"][
        0
    ]


def _form_page(request, configuration, form, *, status=200):
    """Render accessible field errors and the prospective timezone warning."""
    response = render(
        request,
        "stewardship/parish-settings.html",
        {
            "form": form,
            "parish_name": _profile(configuration)["values"]["name"],
            "configuration": configuration,
        },
        status=status,
    )
    if status == 400:
        response.stewardship_safe_error = True
    return response


def _preview(request, service, actor):
    """Validate the full resulting document and show only changed profile values."""
    configuration = editable_configuration(service)
    form = ParishForm(request.POST)
    if not form.is_valid():
        return _form_page(request, configuration, form, status=400)
    if form.cleaned_data["base_digest"] != configuration.active_configuration.digest:
        raise StaleRecordError("Reload the profile before changing it.")
    record = _profile(configuration)
    values = {
        name: form.cleaned_data[name]
        for name in PROFILE_FIELDS
        if form.cleaned_data[name] != record["values"][name]
    }
    if not values:
        form.add_error(None, _("No settings have changed."))
        return _form_page(request, configuration, form, status=400)
    patch = [
        {
            "operation": "update",
            "section": "parish",
            "id": record["id"],
            "values": values,
        }
    ]
    base = service.store.active()
    if base is None or base.digest != configuration.active_configuration.digest:
        raise StaleRecordError("The applied configuration changed.")
    try:
        build_candidate(base, patch, candidate_id=uuid4())
    except ConfigError:
        form.add_error(
            None,
            _(
                "Check the settings. Website URLs cannot include credentials, "
                "query parameters or fragments."
            ),
        )
        return _form_page(request, configuration, form, status=400)
    return render(
        request,
        "stewardship/parish-preview.html",
        {
            "configuration": configuration,
            "changes": [
                {
                    "label": form.fields[name].label,
                    "before": record["values"][name],
                    "after": value,
                }
                for name, value in values.items()
            ],
            "preview": sign_preview(
                actor=actor, configuration=configuration, patch=patch, salt=SALT
            ),
            "timezone_changed": "timezone" in values,
        },
    )


@require_http_methods(["GET", "HEAD", "POST"])
def parish_settings(request):
    """Preview and enqueue parish edits without granting web configuration writes."""
    try:
        service = runtime()
        actor = principal(request, service)
        if request.method == "POST":
            action = form_action(
                request.POST, preview_fields={*PROFILE_FIELDS, "base_digest"}
            )
            response = (
                _preview(request, service, actor)
                if action == "preview"
                else confirm(request, service, actor, salt=SALT, current_scope=_scope)
            )
        else:
            filters(request.GET, allowed=set())
            configuration = editable_configuration(service)
            initial = {
                name: _profile(configuration)["values"][name] for name in PROFILE_FIELDS
            }
            initial["base_digest"] = configuration.active_configuration.digest
            response = _form_page(request, configuration, ParishForm(initial=initial))
        if not allows(
            authenticated_admin(request, store=service.store, read_only=True),
            Capability.CONFIGURE,
        ):
            raise PermissionError("Configuration access was revoked.")
        response["Cache-Control"] = "no-store"
        return response
    except (
        ConfigError,
        DatabaseError,
        LimiterUnavailable,
        PermissionError,
        ValueError,
        StaleRecordError,
        signing.BadSignature,
    ) as error:
        return error_response(error)

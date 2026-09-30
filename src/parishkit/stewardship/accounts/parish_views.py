"""Versioned parish profile editor; existing campaigns retain their own timezone."""

from uuid import uuid4

from django import forms
from django.core import signing
from django.db import DatabaseError
from django.shortcuts import render
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_http_methods

from parishkit.config import ConfigError
from parishkit.stewardship.schema_primitives import timezone_names, typed
from parishkit.stewardship.storage import StaleRecordError
from parishkit.stewardship.web import dates
from parishkit.stewardship.web.contracts import filters
from parishkit.stewardship.web.presentation import parse_us_phone, phone

from . import admin_navigation
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
PROFILE_FIELDS = (
    "name",
    "website",
    "timezone",
    "phone",
    "online_giving_url",
    "date_format",
)
# Values an older canonical document omits; unset means the default.
DEFAULTS = {"date_format": dates.DEFAULT}


class PhoneField(forms.CharField):
    """A US telephone typed any common way, stored as canonical E.164.

    The configuration keeps "+12125551234"; people read and type
    "+1 (212) 555-1234", "(212) 555-1234" or "212-555-1234".
    """

    def __init__(self, **kwargs):
        """Leave room for punctuation while bounding the typed text."""
        kwargs.setdefault(
            "widget", forms.TextInput(attrs={"type": "tel", "autocomplete": "tel"})
        )
        super().__init__(max_length=32, **kwargs)

    def prepare_value(self, value):
        """Show stored and re-displayed values in the familiar grouping."""
        return phone(value) if value else value

    def clean(self, value):
        """Normalize to E.164, or explain the accepted forms."""
        value = super().clean(value)
        if not value:
            return value
        try:
            return parse_us_phone(value)
        except ValueError:
            raise forms.ValidationError(
                _("Enter a ten-digit US telephone number, such as (212) 555-1234.")
            ) from None


class ParishForm(forms.Form):
    """The profile form cannot modify branding, runtime mode or campaign snapshots."""

    name = forms.CharField(label=_("Parish name"), max_length=254)
    website = forms.URLField(
        label=_("Parish website"), max_length=2048, assume_scheme="https"
    )
    timezone = forms.ChoiceField(label=_("Parish timezone"))
    phone = PhoneField(
        label=_("Parish telephone"),
        help_text=_("The ten-digit US number, such as (212) 555-1234."),
    )
    online_giving_url = forms.URLField(
        label=_("Online giving URL"),
        max_length=2048,
        required=False,
        assume_scheme="https",
        help_text=_(
            "Optional. The page where Families can give online. Emails and pages "
            "can link to it with the online_giving_url placeholder."
        ),
    )
    date_format = forms.ChoiceField(
        label=_("Date format"),
        choices=dates.CHOICES,
        required=False,
        help_text=_(
            "How dates appear on Admin and Family pages, in emails and in PDF "
            "reports. US styles use a 12-hour clock; European and ISO styles a "
            "24-hour clock. Spreadsheets use each viewer's own Excel date "
            "format, and CSV files always use ISO 8601 (2027-01-31)."
        ),
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

    def clean_date_format(self):
        """A blank choice (an older form post) keeps the default style."""
        return self.cleaned_data["date_format"] or dates.DEFAULT

    def clean_online_giving_url(self):
        """Accept only the HTTPS, credential-free links the configuration stores."""
        value = self.cleaned_data["online_giving_url"]
        if not value:
            return ""
        try:
            typed(value, "link")
            if not value.lower().startswith("https://"):
                raise ConfigError("Online giving requires HTTPS.")
        except ConfigError:
            raise forms.ValidationError(
                _(
                    "Use an https:// address without a user name or password, "
                    "for example https://giving.example.org/parish?tab=home."
                )
            ) from None
        return value


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
    # The first step of edit, review, apply (#196).
    admin_navigation.place(request, flow="change", step="edit")
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


def _shown(name, value):
    """Preview telephone and date-format changes the way people will read them."""
    if name == "date_format":
        return dict(dates.CHOICES).get(value, value)
    return phone(value) if name == "phone" else value


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
        if form.cleaned_data[name] != record["values"].get(name, DEFAULTS.get(name, ""))
    }
    if "online_giving_url" in values:
        # None removes the optional key; see request_patch's parish update.
        values["online_giving_url"] = values["online_giving_url"] or None
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
    admin_navigation.place(request, flow="change", step="review")
    return render(
        request,
        "stewardship/parish-preview.html",
        {
            "configuration": configuration,
            "changes": [
                {
                    "label": form.fields[name].label,
                    "before": _shown(
                        name, record["values"].get(name, DEFAULTS.get(name, ""))
                    ),
                    "after": _shown(name, value or ""),
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
                name: _profile(configuration)["values"].get(
                    name, DEFAULTS.get(name, "")
                )
                for name in PROFILE_FIELDS
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

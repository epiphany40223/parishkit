"""Versioned parish profile editor; existing campaigns retain their own timezone."""

from uuid import uuid4

from django import forms
from django.core import signing
from django.db import DatabaseError
from django.shortcuts import render
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_http_methods

from parishkit.config import ConfigError
from parishkit.stewardship.schema_primitives import canonical_timezone, typed
from parishkit.stewardship.storage import StaleRecordError
from parishkit.stewardship.web import dates
from parishkit.stewardship.web.admin_forms import AdminForm
from parishkit.stewardship.web.contracts import filters
from parishkit.stewardship.web.field_rules import (
    HOST,
    SPACE,
    FieldRule,
    RuleField,
    apply,
    trimmed,
)
from parishkit.stewardship.web.presentation import parse_us_phone, phone
from parishkit.stewardship.web.refusals import (
    UserFacingError,
    stale_page,
    with_default_code,
)

from . import admin_navigation
from .admin_editing import (
    confirm,
    editable_configuration,
    error_response,
    form_action,
    principal,
    sign_preview,
    summary_errors,
)
from .authentication import runtime
from .limiting import LimiterUnavailable
from .policy import Capability, allows
from .request_patch import build_candidate
from .sessions import authenticated_admin
from .timezone_field import TimezoneField

SALT = "stewardship-parish-profile-preview-v1"
# The refusal of a Review with nothing changed.
UNCHANGED = _("No settings have changed.")
# The review's note when the change includes the timezone.
TIMEZONE_NOTE = _(
    "This timezone change is prospective. Existing campaign timezones, "
    "boundaries, schedules and report buckets remain unchanged."
)
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


# A US telephone typed any common way: an optional +1 or 1, a ten-digit
# number whose area code and exchange do not start with 0 or 1 (the stored
# constraint), the area code optionally in parentheses, and spaces, dots or
# hyphens between the groups. The browser checks the same rule (#1577);
# ``parse_us_phone`` then stores it as E.164, and reads every value it
# admits (the shared case table pins both). Any whitespace may surround it
# (the field strips it), but between the groups only the separators
# phonenumbers reads: a space, a no-break space (pasted from a page), a dot
# or a hyphen.
_BETWEEN = r" \u00a0.\-"
PHONE = FieldRule(
    name="parish_phone",
    pattern=(
        rf"[{SPACE}]*(\+?1[{_BETWEEN}]*)?(\([2-9][0-9]{{2}}\)|[2-9][0-9]{{2}})"
        rf"[{_BETWEEN}]*[2-9][0-9]{{2}}[{_BETWEEN}]*[0-9]{{4}}[{SPACE}]*"
    ),
    message=_("Enter a ten-digit US telephone number, such as (212) 555-1234."),
    code="invalid_phone",
    empty=_("Enter the parish telephone, such as (212) 555-1234."),
)


class PhoneField(RuleField):
    """A US telephone typed any common way, stored as canonical E.164.

    The configuration keeps "+12125551234"; people read and type
    "+1 (212) 555-1234", "(212) 555-1234" or "212-555-1234". The browser
    checks the same rule (``PHONE``) as the field is left.
    """

    def __init__(self, **kwargs):
        """Leave room for punctuation while bounding the typed text."""
        kwargs.setdefault(
            "widget", forms.TextInput(attrs={"type": "tel", "autocomplete": "tel"})
        )
        super().__init__(PHONE, max_length=32, **kwargs)

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
            # Not expected: parse_us_phone reads every value PHONE admits.
            raise forms.ValidationError(PHONE.message, code=PHONE.code) from None


# The Parish website's rule, checked by the browser and the server alike:
# http:// or https://, a host name as URLValidator admits one (``HOST``, so
# no user name or password), then any path without spaces; a lone "?" or "#"
# (an empty query or fragment) is still admitted, as the configuration
# admits it. Spaces around the value are admitted (``trimmed``): the field
# strips them. The server then also checks it as a stored URL
# (``clean_website``).
WEBSITE = FieldRule(
    name="parish_website",
    pattern=trimmed(rf"[Hh][Tt][Tt][Pp][Ss]?:\/\/{HOST}(\/[^?#{SPACE}]*)?\??#?"),
    message=_(
        "Use a website URL such as https://example.org, without credentials, "
        "query or fragment."
    ),
    # URLField's own check says "invalid"; one code per field.
    code="invalid",
    empty=_("Enter the parish website, such as https://example.org."),
)
# An online giving link: https://, a host name as URLValidator admits one
# (``HOST``, so no user name or password), then any path, query or fragment
# without spaces (giving providers use "?tab=home"). The server then also
# checks it as a stored link.
GIVING = FieldRule(
    name="online_giving_url",
    pattern=trimmed(rf"[Hh][Tt][Tt][Pp][Ss]:\/\/{HOST}([\/?#][^{SPACE}]*)?"),
    message=_(
        "Use an https:// address without a user name or password, "
        "for example https://giving.example.org/parish?tab=home."
    ),
    # URLField's own check says "invalid"; one code per field.
    code="invalid",
)


def url_input():
    """A text box that asks phones for a URL keyboard.

    Not ``type="url"``: the browser's own URL check refuses a value padded
    with a Unicode space, which the server strips and admits, so the rule's
    pattern (``trimmed``) is the one check both sides share.
    """
    return forms.TextInput(attrs={"inputmode": "url"})


class ParishForm(AdminForm):
    """The profile form cannot modify branding, runtime mode or campaign snapshots."""

    name = forms.CharField(label=_("Parish name"), max_length=254)
    # The browser refuses a user name, query or fragment as the field is
    # left (#1330, #1577); clean_website checks the same rule.
    website = apply(
        forms.URLField(
            label=_("Parish website"),
            max_length=2048,
            assume_scheme="https",
            widget=url_input(),
        ),
        WEBSITE,
    )
    timezone = TimezoneField(label=_("Parish timezone"))
    phone = PhoneField(
        label=_("Parish telephone"),
        help_text=_("The ten-digit US number, such as (212) 555-1234."),
    )
    # The browser refuses an http:// address or a user name as the field is
    # left (#563, #1577); clean_online_giving_url checks the same rule.
    online_giving_url = apply(
        forms.URLField(
            label=_("Online giving URL"),
            max_length=2048,
            required=False,
            assume_scheme="https",
            widget=url_input(),
            help_text=_(
                "Optional. The page where Families can give online. Emails and "
                "pages can link to it with the online_giving_url placeholder."
            ),
        ),
        GIVING,
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

    def clean_website(self):
        """Refuse credentials, a query or a fragment at the field (#1330).

        The configuration refuses them too (``typed(value, "url")``); checking
        here shows the refusal beside the field, on Parish settings and in
        setup, rather than as a notice above the form.
        """
        value = self.cleaned_data["website"]
        WEBSITE.check(value)
        try:
            typed(value, "url")
        except ConfigError:
            raise forms.ValidationError(WEBSITE.message, code=WEBSITE.code) from None
        return value

    def clean_date_format(self):
        """A blank choice (an older form post) keeps the default style."""
        return self.cleaned_data["date_format"] or dates.DEFAULT

    def clean_online_giving_url(self):
        """Accept only the HTTPS, credential-free links the configuration stores."""
        value = self.cleaned_data["online_giving_url"]
        if not value:
            return ""
        GIVING.check(value)
        try:
            typed(value, "link")
            if not value.lower().startswith("https://"):
                raise ConfigError("Online giving requires HTTPS.")
        except ConfigError:
            raise forms.ValidationError(GIVING.message, code=GIVING.code) from None
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
    """Render the editor; a Review refused for its values (400) has its errors.

    ``form`` is the unbound editor at the applied version, or a refused
    Review's bound form, whose errors the page script marks on the form the
    reader is using and whose summary it places above the heading.
    """
    admin_navigation.place(request, flow="change", step="edit")
    page_url = reverse("admin:parish_settings")
    response = render(
        request,
        "stewardship/parish-settings.html",
        {
            "form": form,
            "parish_name": _profile(configuration)["values"]["name"],
            "configuration": configuration,
            # Review and the review dialog's Apply post here.
            "page_url": page_url,
            # Once a change is applied, the dialog redraws the form's region
            # from this page's own address.
            "refresh_url": page_url,
            "errors": summary_errors(form) if form.is_bound else [],
        },
        status=status,
    )
    if status != 200:
        response.stewardship_safe_error = True
        # The refusal log's code for this page's own refusal (#1444).
        with_default_code(response)
    return response


def _initial_form(configuration):
    """The editor as the applied profile fills it, at its current version."""
    record = _profile(configuration)["values"]
    initial = {
        name: record.get(name, DEFAULTS.get(name, "")) for name in PROFILE_FIELDS
    }
    initial["base_digest"] = configuration.active_configuration.digest
    return ParishForm(initial=initial)


def _shown(name, value):
    """A setting's value in words for the review's "Field: old → new" line.

    Telephone and date-format values are shown the way people will read
    them. The online giving address is the only setting that can be empty
    (the website is required); unset, it reads "Not set", as in Campaign
    settings' compact review, so the line never has an empty side.
    """
    if name == "date_format":
        return dict(dates.CHOICES).get(value, value)
    return (phone(value) if name == "phone" else value) or _("Not set")


def review_change(service, actor, configuration, form):
    """The page's review of a valid ``form``, or None with its refusal on the form.

    Request-free, so the page's Review and ``pk-admin parish preview`` share
    it. A form whose version is not the applied one raises ``stale_page()``.
    Only changed profile values are reviewed; no change, or a resulting
    document the configuration refuses, adds the page's refusal to the form
    and returns None. The review's ``changes`` are drawn for people
    (``_shown``), one compact-review line each (#1356); ``values`` maps
    each changed field to its stored ``(before, after)``, None for an unset
    value, which the command line reports.
    """
    if form.cleaned_data["base_digest"] != configuration.active_configuration.digest:
        # Refused in the review dialog with the standard explanation.
        raise stale_page()
    record = _profile(configuration)
    posted = dict(form.cleaned_data)
    if posted["timezone"] != record["values"]["timezone"]:
        # A new zone is stored by its canonical name (#1286); an unchanged
        # stored alias is left as it is, so saving the page changes nothing.
        posted["timezone"] = canonical_timezone(posted["timezone"])
    values = {
        name: posted[name]
        for name in PROFILE_FIELDS
        if posted[name] != record["values"].get(name, DEFAULTS.get(name, ""))
    }
    if "online_giving_url" in values:
        # None removes the optional key; see request_patch's parish update.
        values["online_giving_url"] = values["online_giving_url"] or None
    if not values:
        form.add_error(None, forms.ValidationError(UNCHANGED, code="unchanged"))
        return None
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
        raise stale_page()
    try:
        build_candidate(base, patch, candidate_id=uuid4())
    except ConfigError:
        form.add_error(
            None,
            forms.ValidationError(
                _(
                    "Check the settings. Website URLs cannot include credentials, "
                    "query parameters or fragments."
                ),
                code="candidate_refused",
            ),
        )
        return None
    return {
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
        "values": {
            name: (record["values"].get(name, DEFAULTS.get(name)), value)
            for name, value in values.items()
        },
        "notes": [TIMEZONE_NOTE] if "timezone" in values else [],
        "preview": sign_preview(
            actor=actor, configuration=configuration, patch=patch, salt=SALT
        ),
    }


def _preview(request, service, actor):
    """The review dialog's fragment, the page refused (400), or a refusal raised.

    A field's refusal, or the resulting document's, is the page again with
    status 400, so it stays on the form. A stale page is raised by
    ``review_change`` for the dialog; so is no change at all, which the
    page's change gate keeps out of reach.
    """
    configuration = editable_configuration(service)
    form = ParishForm(request.POST)
    if not form.is_valid():
        return _form_page(request, configuration, form, status=400)
    review = review_change(service, actor, configuration, form)
    if review is None:
        if str(UNCHANGED) in form.non_field_errors():
            raise UserFacingError(UNCHANGED, fix=_("Nothing was changed."))
        return _form_page(request, configuration, form, status=400)
    return render(
        request, "stewardship/parish-settings-review.html", {"review": review}
    )


@require_http_methods(["GET", "HEAD", "POST"])
def parish_settings(request):
    """Preview and enqueue parish edits without granting web configuration writes.

    GET draws the editor (it takes no query: its address is all there is).
    Review is answered with the review dialog's fragment (UX conventions,
    "Review dialog", #750), and the dialog's Apply with the change's status,
    which the dialog follows. A Review refused for its values is the page
    again (400); every other refusal (a stale page, an out-of-date preview)
    is raised and shown in the dialog. A refusal never redraws the form, so
    it keeps the version the reader's values came from, and the next Review
    is refused until the page is reloaded rather than quietly undoing a
    change made elsewhere.
    """
    try:
        service = runtime()
        actor = principal(request, service)
        filters(request.GET, allowed=set())
        if request.method == "POST":
            action = form_action(
                request.POST, preview_fields={*PROFILE_FIELDS, "base_digest"}
            )
            if action == "preview":
                response = _preview(request, service, actor)
            else:
                response = confirm(
                    request, service, actor, salt=SALT, current_scope=_scope
                )
        else:
            configuration = editable_configuration(service)
            response = _form_page(request, configuration, _initial_form(configuration))
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
        LookupError,
        StaleRecordError,
        signing.BadSignature,
    ) as error:
        return error_response(error)

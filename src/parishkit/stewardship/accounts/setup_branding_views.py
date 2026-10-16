"""Private setup logo upload/preview using the same normalized durable file owner."""

from io import BytesIO
from uuid import UUID

from django import forms
from django.http import FileResponse
from django.shortcuts import render
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_http_methods, require_safe

from parishkit.stewardship.web.content import MAX_IMAGE_BYTES, prepare_graphics
from parishkit.stewardship.web.contracts import expected_version, filters
from parishkit.stewardship.web.refusals import stale_page, unexpected_fields

from . import setup_help
from .authentication import runtime
from .branding_files import read_variant
from .branding_models import BrandingAsset
from .branding_slots import file_size
from .branding_staging import file_receipt, stage_branding, staged_bundle
from .branding_views import (
    ERRORS,
    LOGO_TOO_LARGE,
    SIZE_FIELD_TEMPLATE,
    LogoForm,
    _error,
    media_root,
)
from .setup_drafts import save_section, view_draft
from .setup_policy import SetupState
from .setup_views import _checked, _context, page_error
from .setup_wizard import continue_after


class SetupLogoForm(LogoForm):
    """The original attempt pins the base; no active-YAML selector is accepted."""

    base_digest = None

    def __init__(self, *args, saved=False, **kwargs):
        """Explain the logo; once one is saved, choosing no file keeps it.

        As on Parish logos (#1062), the page refuses a file over the limit
        as soon as it is chosen, in the server's words, in the field's
        reserved error line (file-size-field.html), and Save and continue
        waits for a file that fits (#1332 item 29).
        """
        super().__init__(*args, **kwargs)
        logo = self.fields["logo"]
        logo.required = not saved
        logo.template_name = SIZE_FIELD_TEMPLATE
        logo.widget.attrs.update(
            {
                "data-max-bytes": MAX_IMAGE_BYTES,
                "data-max-bytes-message": LOGO_TOO_LARGE,
            }
        )
        setup_help.apply(self, setup_help.LOGO, replace=True)

    def clean_logo(self):
        """An empty optional upload keeps the saved logo; others are checked.
        A file over the limit is refused in the words the page uses for it."""
        value = self.cleaned_data.get("logo")
        if value is None:
            return None
        if value.size > MAX_IMAGE_BYTES:
            raise forms.ValidationError(
                str(LOGO_TOO_LARGE).replace("{size}", file_size(value.size)),
                code="too_large",
            )
        return super().clean_logo()


def _draft(request, service):
    """Only a currently collecting original-login attempt can handle logo data."""
    draft = view_draft(request, service)
    if draft is None or draft.status.state != SetupState.COLLECTING:
        raise PermissionError("Setup logo is unavailable.")
    return draft


@require_http_methods(["GET", "HEAD", "POST"])
def setup_branding(request):
    """Normalize outside SQL, then version-check selection after durable file writes."""
    try:
        filters(request.GET, allowed=set())
        service = runtime()
        draft = _draft(request, service)
        if request.method == "POST":
            # A browser sends the logo input left empty (Save and continue
            # with no file chosen) as a part with an empty filename, which
            # Django keeps as an empty POST value rather than a file (#1396):
            # nothing was chosen, so it is allowed; any other value is not.
            allowed = {"version", "csrfmiddlewaretoken"}
            if request.POST.getlist("logo") == [""]:
                allowed.add("logo")
            if (
                set(request.POST) - allowed
                or set(request.FILES) - {"logo"}
                or any(len(values) != 1 for _, values in request.POST.lists())
                or any(len(values) != 1 for _, values in request.FILES.lists())
            ):
                raise unexpected_fields()
            version = expected_version(request.POST.get("version"))
            if version != draft.status.version:
                raise stale_page()
            form = SetupLogoForm(
                request.POST, request.FILES, saved="branding" in draft.sections
            )
            if form.is_valid() and form.cleaned_data["logo"] is None:
                # Keep the saved logo: nothing is uploaded or re-staged.
                return _checked(
                    request, service, continue_after(request, service, "branding")
                )
            if form.is_valid():
                try:
                    graphics = prepare_graphics(form.cleaned_data["logo"])
                except ValueError:
                    form.add_error(
                        "logo",
                        forms.ValidationError(
                            _("Choose a supported, bounded static image."),
                            code="unsupported_image",
                        ),
                    )
                else:
                    identifier = stage_branding(
                        request,
                        service,
                        media_root(),
                        graphics,
                        base_digest=service.store.active().digest,
                        setup_attempt_id=draft.status.attempt_id,
                        # The setup step takes one image (#1062).
                        mode="basic",
                    )
                    save_section(
                        request,
                        service,
                        draft.status.attempt_id,
                        step="branding",
                        values={"bundle_id": str(identifier)},
                        expected_version=version,
                    )
                    return _checked(
                        request, service, continue_after(request, service, "branding")
                    )
            status = 400
        else:
            form, status = SetupLogoForm(saved="branding" in draft.sections), 200
        assets = []
        if "branding" in draft.sections:
            bundle, assets = staged_bundle(
                request,
                service,
                UUID(draft.sections["branding"]["bundle_id"]),
                setup_attempt_id=draft.status.attempt_id,
            )
        response = render(
            request,
            "stewardship/setup-branding.html",
            _context(draft, "branding") | {"form": form, "assets": assets},
            status=status,
        )
        return _checked(request, service, response, draft)
    except ERRORS as error:
        return page_error(request, error, "branding", fallback=_error)


@require_safe
def setup_branding_asset(request, asset_id):
    """Setup PNGs never use the public retained-branding route or browser caching."""
    try:
        filters(request.GET, allowed=set())
        service = runtime()
        draft = _draft(request, service)
        asset = BrandingAsset.objects.filter(pk=asset_id).first()
        if asset is None:
            raise LookupError("Setup logo is unavailable.")
        staged_bundle(
            request,
            service,
            asset.bundle_id,
            setup_attempt_id=draft.status.attempt_id,
        )
        data = read_variant(media_root(), asset.bundle_id, file_receipt(asset))
        response = FileResponse(BytesIO(data), content_type="image/png")
        response["Content-Security-Policy"] = "default-src 'none'; sandbox"
        response["X-Content-Type-Options"] = "nosniff"
        return _checked(request, service, response, draft)
    except ERRORS as error:
        return _error(error)

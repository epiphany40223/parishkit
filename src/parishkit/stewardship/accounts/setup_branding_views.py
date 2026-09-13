"""Private setup logo upload/preview using the same normalized durable file owner."""

from io import BytesIO
from uuid import UUID

from django.http import FileResponse, HttpResponseRedirect
from django.shortcuts import render
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_http_methods, require_safe

from parishkit.stewardship.storage import StaleRecordError
from parishkit.stewardship.web.content import prepare_graphics
from parishkit.stewardship.web.contracts import expected_version, filters

from .authentication import runtime
from .branding_files import read_variant
from .branding_models import BrandingAsset
from .branding_staging import file_receipt, stage_branding, staged_bundle
from .branding_views import ERRORS, LogoForm, _error, media_root
from .setup_drafts import save_section, view_draft
from .setup_policy import SetupState
from .setup_views import _checked, _context


class SetupLogoForm(LogoForm):
    """The original attempt pins the base; no active-YAML selector is accepted."""

    base_digest = None


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
            if (
                set(request.POST) - {"version", "csrfmiddlewaretoken"}
                or set(request.FILES) - {"logo"}
                or any(len(values) != 1 for _, values in request.POST.lists())
                or any(len(values) != 1 for _, values in request.FILES.lists())
            ):
                raise ValueError("Invalid setup logo fields.")
            version = expected_version(request.POST.get("version"))
            if version != draft.status.version:
                raise StaleRecordError("Reload setup before uploading a logo.")
            form = SetupLogoForm(request.POST, request.FILES)
            if form.is_valid():
                try:
                    graphics = prepare_graphics(form.cleaned_data["logo"])
                except ValueError:
                    form.add_error(
                        "logo", _("Choose a supported, bounded static image.")
                    )
                else:
                    identifier = stage_branding(
                        request,
                        service,
                        media_root(),
                        graphics,
                        base_digest=service.store.active().digest,
                        setup_attempt_id=draft.status.attempt_id,
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
                        request, service, HttpResponseRedirect("/admin/setup/branding")
                    )
            status = 400
        else:
            form, status = SetupLogoForm(), 200
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
            _context(draft) | {"form": form, "assets": assets},
            status=status,
        )
        return _checked(request, service, response, draft)
    except ERRORS as error:
        return _error(error)


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

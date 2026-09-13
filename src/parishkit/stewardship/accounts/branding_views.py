"""Normalized logo upload, versioned preview and narrowly served immutable PNGs."""

from io import BytesIO
from pathlib import Path
from uuid import uuid4

from django import forms
from django.conf import settings
from django.core import signing
from django.db import DatabaseError
from django.http import FileResponse, HttpResponseRedirect
from django.shortcuts import render
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_http_methods

from parishkit.config import ConfigError
from parishkit.stewardship.storage import StaleRecordError
from parishkit.stewardship.web.content import MAX_IMAGE_BYTES, prepare_graphics
from parishkit.stewardship.web.contracts import (
    ErrorCode,
    FieldError,
    filters,
    validation_response,
)

from .admin_editing import (
    confirm,
    editable_configuration,
    error_response,
    form_action,
    principal,
    sign_preview,
)
from .authentication import runtime
from .branding_files import read_variant
from .branding_models import BrandingAsset
from .branding_staging import file_receipt, stage_branding, staged_bundle
from .branding_validation import validate_installation
from .configuration_installation import coherent_configuration
from .configuration_models import Parish
from .integration_views import _checked
from .limiting import LimiterUnavailable
from .request_patch import build_candidate
from .runtime_models import ConfigurationActivation

SALT = "stewardship-branding-preview-v1-"
ERRORS = (
    OSError,
    ConfigError,
    DatabaseError,
    LimiterUnavailable,
    PermissionError,
    ValueError,
    StaleRecordError,
    signing.BadSignature,
    LookupError,
)


def _error(error):
    """Report invalid uploads on the form and storage failures as retryable."""
    # PermissionError is also an OSError: preserve explicit authorization denial.
    if isinstance(error, ConfigError) or (
        isinstance(error, OSError) and not isinstance(error, PermissionError)
    ):
        return validation_response([FieldError(ErrorCode.UNAVAILABLE)], status=503)
    return error_response(error)


class LogoForm(forms.Form):
    """Only the bounded original image is submitted; output names are server-owned."""

    logo = forms.FileField(
        label=_("Parish logo"),
        max_length=254,
        widget=forms.FileInput(attrs={"accept": "image/png,image/jpeg,image/webp"}),
        help_text=_(
            "PNG, JPEG or WebP, up to 5 MB. All four display sizes are generated."
        ),
    )
    base_digest = forms.RegexField(
        regex=r"^[0-9a-f]{64}$", max_length=64, widget=forms.HiddenInput
    )

    def clean_logo(self):
        """Size is an early bound; signature, pixels and animation are checked next."""
        value = self.cleaned_data["logo"]
        if not 0 < value.size <= MAX_IMAGE_BYTES:
            raise forms.ValidationError(_("Choose an image no larger than 5 MB."))
        return value


def media_root():
    """Use only admitted deployment storage, never a posted path or checkout default."""
    value = getattr(settings, "STEWARDSHIP_MEDIA_ROOT", None)
    if not isinstance(value, Path) or not value.is_absolute():
        raise ConfigError("Branding media storage is unavailable.")
    return value


def _retained(asset):
    """Only actual activation history publishes logos; prepared YAML is not enough."""
    field = {
        "large": "large_logo_id",
        "menu": "menu_logo_id",
        "icon": "icon_logo_id",
        "favicon": "favicon_id",
    }[asset.label]
    return Parish.objects.filter(
        configuration_id__in=ConfigurationActivation.objects.values("configuration_id"),
        **{field: asset.pk},
    ).exists()


@require_http_methods(["GET", "HEAD", "POST"])
def branding_settings(request):
    """Upload normalized staging, without claiming that the parish logo has changed."""
    try:
        service = runtime()
        principal(request, service)
        configuration = editable_configuration(service)
        if request.method == "POST":
            if (
                set(request.POST) - {"base_digest", "csrfmiddlewaretoken"}
                or set(request.FILES) - {"logo"}
                or any(len(values) != 1 for _, values in request.POST.lists())
                or any(len(values) != 1 for _, values in request.FILES.lists())
            ):
                raise ValueError("Invalid logo upload fields.")
            form = LogoForm(request.POST, request.FILES)
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
                        base_digest=form.cleaned_data["base_digest"],
                    )
                    return _checked(
                        request,
                        service,
                        HttpResponseRedirect(
                            f"/admin/configuration/branding/{identifier}"
                        ),
                    )
            status = 400
        else:
            filters(request.GET, allowed=set())
            form = LogoForm(
                initial={"base_digest": configuration.active_configuration.digest}
            )
            status = 200
        current = configuration.active_configuration.parish
        assets = BrandingAsset.objects.filter(
            pk__in=[
                current.large_logo_id,
                current.menu_logo_id,
                current.icon_logo_id,
                current.favicon_id,
            ]
        ).order_by("label")
        response = render(
            request,
            "stewardship/branding-settings.html",
            {"form": form, "assets": assets},
            status=status,
        )
        if status == 400:
            response.stewardship_safe_error = True
        return _checked(request, service, response)
    except ERRORS as error:
        return _error(error)


@require_http_methods(["GET", "HEAD", "POST"])
def branding_preview(request, bundle_id):
    """Preview all normalized variants and confirm their exact YAML references."""
    try:
        service = runtime()
        actor = principal(request, service)
        if request.method == "POST":
            if form_action(request.POST, preview_fields=set()) != "confirm":
                raise ValueError("Confirm the prepared branding preview.")

            def scope(service):
                """Permit exact receipt retries after selection, not stale new edits."""
                asset = BrandingAsset.objects.filter(
                    bundle_id=bundle_id, label="large"
                ).first()
                if asset is None or not _retained(asset):
                    staged_bundle(request, service, bundle_id)
                return editable_configuration(service), None

            response = confirm(
                request, service, actor, salt=SALT + str(bundle_id), current_scope=scope
            )
        else:
            filters(request.GET, allowed=set())
            row, assets = staged_bundle(request, service, bundle_id)
            for asset in assets:
                read_variant(media_root(), row.pk, file_receipt(asset))
            configuration = editable_configuration(service)
            record = configuration.active_configuration.canonical_document["sections"][
                "parish"
            ][0]
            patch = [
                {
                    "operation": "update",
                    "section": "parish",
                    "id": record["id"],
                    "values": {
                        "branding": {asset.label: str(asset.pk) for asset in assets}
                    },
                }
            ]
            base = service.store.active()
            if base is None or base.digest != configuration.active_configuration.digest:
                raise StaleRecordError("The branding preview base changed.")
            candidate = build_candidate(base, patch, candidate_id=uuid4())
            validate_installation(
                candidate.candidate.document(), actor_id=actor.identity
            )
            response = render(
                request,
                "stewardship/branding-preview.html",
                {
                    "assets": assets,
                    "bundle": row,
                    "preview": sign_preview(
                        actor=actor,
                        configuration=configuration,
                        patch=patch,
                        salt=SALT + str(bundle_id),
                    ),
                },
            )
        return _checked(request, service, response)
    except ERRORS as error:
        return _error(error)


@require_http_methods(["GET", "HEAD"])
def branding_asset(request, asset_id, *, private=False):
    """Opaque paths serve only recorded PNG bytes, never a media directory listing."""
    try:
        filters(request.GET, allowed=set())
        service = runtime()
        asset = (
            BrandingAsset.objects.select_related("bundle").filter(pk=asset_id).first()
        )
        if asset is None:
            raise LookupError("Branding is unavailable.")
        if private:
            principal(request, service, passive=True)
            staged_bundle(request, service, asset.bundle_id)
        else:
            configuration = coherent_configuration(service.store)
            if (
                not service.configured()
                or configuration.restore_review_required
                or not _retained(asset)
            ):
                raise LookupError("Branding is unavailable.")
        data = read_variant(media_root(), asset.bundle_id, file_receipt(asset))
        response = FileResponse(
            BytesIO(data), content_type="image/png", filename=str(asset.pk) + ".png"
        )
        response["Content-Security-Policy"] = "default-src 'none'; sandbox"
        response["X-Content-Type-Options"] = "nosniff"
        response["Cache-Control"] = (
            "no-store" if private else "public, max-age=31536000, immutable"
        )
        if private:
            return _checked(request, service, response)
        return response
    except ERRORS as error:
        return _error(error)

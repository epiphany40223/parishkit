"""Normalized logo upload, versioned preview and narrowly served immutable PNGs.

Review, apply and the change's status happen on Parish logos itself (#750),
in its review region under the upload form: a successful upload stages the
four variants and answers with the page naming them (``?review=<bundle>``),
and Apply posts the signed preview to that same address. The answer is the
page again naming the request (``?request=<id>``), whose status the region
follows until the change settles and the page refreshes itself quietly with
the new current logos.
"""

from io import BytesIO
from pathlib import Path
from urllib.parse import urlencode
from uuid import UUID, uuid4

from django import forms
from django.conf import settings
from django.core import signing
from django.db import DatabaseError, transaction
from django.http import FileResponse, HttpResponseRedirect
from django.shortcuts import render
from django.urls import reverse
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
from parishkit.stewardship.web.refusals import UserFacingStale, stale_page

from . import admin_navigation, field_tips
from .admin_editing import (
    REVIEW_REGION,
    confirm,
    editable_configuration,
    error_response,
    form_action,
    principal,
    requested_status,
    review_region,
    sign_preview,
)
from .authentication import runtime
from .branding_files import read_variant
from .branding_models import BrandingAsset
from .branding_slots import (
    MAX_TOTAL_BYTES,
    SLOTS,
    MissingSlot,
    changed_sizes,
    check_slot,
    complete_bundle,
    current_assets,
    file_size,
    missing_message,
)
from .branding_staging import file_receipt, stage_branding, staged_bundle
from .branding_validation import campaign_artwork_retained, validate_installation
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


ONE, EACH = "one", "each"
# A file field with the page's size check and a reserved error line.
SIZE_FIELD_TEMPLATE = "stewardship/file-size-field.html"
# One image over its limit, in the server's and the page's words (ui-v1.js
# fills in "{size}" as file_size writes it).
LOGO_TOO_LARGE = _("Parish logo: the file is {size}; the limit is 5 MB.")


class LogoUploadForm(LogoForm):
    """Parish logos' upload: one image for every size, or each size apart.

    ``mode`` chooses (#1062): "one" is ``LogoForm``'s single upload, exactly
    as before; "each" takes up to four files, one per size, each checked
    against its own rules (``branding_slots``). The page shows only the
    chosen mode's fields (data-show-when); the hidden ones are disabled, not
    sent, and in any case ignored here. A post without ``mode`` is one
    image, as every upload was before. The setup wizard keeps ``LogoForm``.
    """

    mode = forms.ChoiceField(
        label=_("Upload"),
        choices=[
            (ONE, _("One image for all sizes")),
            (EACH, _("Each size separately")),
        ],
        widget=forms.RadioSelect,
        required=False,
        initial=ONE,
    )
    field_order = ["mode", "logo", *SLOTS]
    # The browser's check of the files chosen together (ui-v1.js), in the
    # words of clean()'s own refusal; "{size}" is their total.
    max_total_bytes = MAX_TOTAL_BYTES
    max_total_message = _(
        "Together the chosen files are {size}; one upload can carry at most "
        "5 MB. Upload the larger sizes on their own."
    )

    def __init__(self, *args, **kwargs):
        """Add one optional file field per size, shown only in "each" mode,
        with its one-line rule visible and the rest behind its "i" tip.
        A post without a mode is redrawn with one image chosen, so a refused
        one-image upload still shows its field and error."""
        super().__init__(*args, **kwargs)
        if self.is_bound and not self.data.get("mode"):
            self.data = self.data.copy()
            self.data["mode"] = ONE
        logo = self.fields["logo"]
        logo.required = False
        logo.template_name = SIZE_FIELD_TEMPLATE
        logo.widget.attrs.update(
            {
                "data-show-when": f"mode={ONE}",
                "data-required-when-shown": "",
                "data-max-bytes": MAX_IMAGE_BYTES,
                "data-max-bytes-message": LOGO_TOO_LARGE,
            }
        )
        for name, slot in SLOTS.items():
            accept = ",".join(f"image/{item.lower()}" for item in slot.formats)
            field = forms.FileField(
                label=slot.title,
                max_length=254,
                required=False,
                widget=forms.FileInput(
                    attrs={
                        "accept": accept,
                        "data-show-when": f"mode={EACH}",
                        # The browser refuses a file over its size's limit
                        # before sending it (ui-v1.js), in the server's words.
                        "data-max-bytes": slot.max_bytes,
                        "data-max-bytes-message": _(
                            "%(slot)s: the file is {size}; the limit is %(limit)s."
                        )
                        % {"slot": slot.title, "limit": file_size(slot.max_bytes)},
                    }
                ),
            )
            field_tips.add(field, slot.tip, slot.hint)
            field.template_name = SIZE_FIELD_TEMPLATE
            self.fields[name] = field
        self.order_fields(self.field_order)

    def clean_logo(self):
        """The single image is checked only in one-image mode. A file over
        the limit is refused in the words the page uses for it."""
        if self.data["mode"] != ONE:
            return None
        value = self.cleaned_data.get("logo")
        if value is None:
            raise forms.ValidationError(self.fields["logo"].error_messages["required"])
        if value.size > MAX_IMAGE_BYTES:
            raise forms.ValidationError(
                str(LOGO_TOO_LARGE).replace("{size}", file_size(value.size))
            )
        return super().clean_logo()

    def clean_mode(self):
        """No mode is one image, the only mode before #1062."""
        return self.cleaned_data.get("mode") or ONE

    def clean(self):
        """In "each" mode check every chosen size against its rules, keeping
        each normalized size in ``cleaned_data["sizes"]``; any refusal marks
        its field, so nothing is staged unless every chosen size passes."""
        values = super().clean()
        if values.get("mode") != EACH:
            return values
        chosen = {name: values.get(name) for name in SLOTS if values.get(name)}
        if not chosen:
            self.add_error(
                "large", _("Choose an image for at least one size to upload.")
            )
            return values
        total = sum(item.size for item in chosen.values())
        if total > MAX_TOTAL_BYTES:
            self.add_error(
                next(iter(chosen)),
                str(self.max_total_message).replace("{size}", file_size(total)),
            )
            return values
        sizes = {}
        for name, upload in chosen.items():
            try:
                sizes[name] = check_slot(name, upload.read(MAX_IMAGE_BYTES + 1))
            except forms.ValidationError as error:
                self.add_error(name, error)
        values["sizes"] = sizes
        return values


def media_root():
    """Use only admitted deployment storage, never a posted path or checkout default."""
    value = getattr(settings, "STEWARDSHIP_MEDIA_ROOT", None)
    if not isinstance(value, Path) or not value.is_absolute():
        raise ConfigError("Branding media storage is unavailable.")
    return value


def _retained(asset):
    """Only actual activation history publishes logos; prepared YAML is not enough."""
    if asset.label in {"banner", "section"}:
        # Campaign artwork (#248) is published by an activated campaign
        # configuration that names it.
        return campaign_artwork_retained(asset.pk)
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


def _upload(request, service):
    """Stage one uploaded logo, or return its refused form.

    Returns ``(redirect, None)`` once the four variants are staged: the
    page naming them, whose review region shows them with Apply. Otherwise
    ``(None, form)``, the bound form with its errors.
    """
    if (
        set(request.POST) - {"base_digest", "csrfmiddlewaretoken", "mode"}
        or set(request.FILES) - {"logo", *SLOTS}
        or any(len(values) != 1 for _, values in request.POST.lists())
        or any(len(values) != 1 for _, values in request.FILES.lists())
    ):
        raise ValueError("Invalid logo upload fields.")
    form = LogoUploadForm(request.POST, request.FILES)
    if form.is_valid():
        graphics = None
        if form.cleaned_data["mode"] == EACH:
            # Sizes left empty keep the current logo's image (#1062). The
            # form's version is checked before the current logos are read,
            # so a page whose settings changed elsewhere is refused as any
            # other is (stage_branding checks it again under its lock).
            active = editable_configuration(service).active_configuration
            if active.digest != form.cleaned_data["base_digest"]:
                raise StaleRecordError("The branding form's configuration changed.")
            try:
                graphics = complete_bundle(
                    form.cleaned_data["sizes"], active.parish, media_root()
                )
            except MissingSlot as missing:
                form.add_error(missing.slot, missing_message(missing.slot))
        else:
            try:
                graphics = prepare_graphics(form.cleaned_data["logo"])
            except ValueError:
                form.add_error("logo", _("Choose a supported, bounded static image."))
        if graphics is not None:
            identifier = stage_branding(
                request,
                service,
                media_root(),
                graphics,
                base_digest=form.cleaned_data["base_digest"],
            )
            query = urlencode({"review": identifier})
            return HttpResponseRedirect(
                f"{reverse('admin:branding_settings')}?{query}#{REVIEW_REGION}"
            ), None
    return None, form


def _review(service, actor, configuration, staged):
    """Review every staged variant; nothing is requested yet.

    ``staged`` is ``staged_bundle``'s row and assets, read before the
    configuration so the preview is signed against the version checked
    last. Returns ``review_region``'s ``review``: the generated sizes and
    the signed preview Apply posts.
    """
    row, assets = staged
    for asset in assets:
        read_variant(media_root(), row.pk, file_receipt(asset))
    record = configuration.active_configuration.canonical_document["sections"][
        "parish"
    ][0]
    patch = [
        {
            "operation": "update",
            "section": "parish",
            "id": record["id"],
            "values": {"branding": {asset.label: str(asset.pk) for asset in assets}},
        }
    ]
    base = service.store.active()
    if base is None or base.digest != configuration.active_configuration.digest:
        raise StaleRecordError("The branding preview base changed.")
    candidate = build_candidate(base, patch, candidate_id=uuid4())
    validate_installation(candidate.candidate.document(), actor_id=actor.identity)
    # Each size is named, and says whether this upload changes it (#1062):
    # one uploaded separately changes only its own size.
    changed = changed_sizes(assets, configuration.active_configuration.parish)
    for asset in assets:
        asset.title = SLOTS[asset.label].title
        asset.changed = asset.label in changed
    return {
        # Drawn inside settings-review.html, above its Apply.
        "template": "stewardship/branding-review.html",
        "notes": [],
        "assets": assets,
        "preview": sign_preview(
            actor=actor,
            configuration=configuration,
            patch=patch,
            # The salt the separate review page used, so tokens keep meaning.
            salt=SALT + str(row.pk),
        ),
    }


def _scope(request, bundle_id):
    """Confirmation's current scope: the staged logos must still be this
    session's, or already published (an exact retry after selection)."""

    def scope(service):
        """Permit exact receipt retries after selection, not stale new edits."""
        asset = BrandingAsset.objects.filter(bundle_id=bundle_id, label="large").first()
        if asset is None or not _retained(asset):
            staged_bundle(request, service, bundle_id)
        return editable_configuration(service), None

    return scope


def _page(request, service, configuration, form, *, status=200, redraw=False, **region):
    """Render the upload form, its review region and the current logos.

    ``region`` is ``review_region``'s ``review``, ``receipt``, ``refusal`` or
    ``message``; the step indicator follows it through edit, review and
    apply (#196), all on this page. ``redraw`` marks the form and the
    current logos to be redrawn even by Apply's answer.
    """
    assets = sorted(
        current_assets(configuration.active_configuration.parish).values(),
        key=lambda asset: asset.label,
    )
    step = "review" if region.get("review") else "edit"
    if region.get("receipt"):
        step = "apply"
    admin_navigation.place(request, flow="change", step=step)
    response = render(
        request,
        "stewardship/branding-settings.html",
        {
            "form": form,
            "assets": assets,
            # A refused Apply's answer redraws the form and the current
            # logos at the current version (data-in-place-always), though
            # Apply's answer otherwise takes only the review region.
            "redraw": redraw,
            # A refused upload's errors are summarized in the review region
            # too, linked to its field, where focus then goes.
            **review_region("branding_settings", form, **region),
        },
        status=status,
    )
    if status != 200:
        response.stewardship_safe_error = True
    return _checked(request, service, response)


# Why staged logos cannot be used: they expired after a day, belong to
# another sign-in, were already applied, or the settings changed since.
EXPIRED = _(
    "These logos expired, or the settings changed after they were uploaded, "
    "so they were not used. Upload the logo again."
)
UNREVIEWABLE = _(
    "These uploaded logos can no longer be reviewed: they were already "
    "applied, expired after a day, or the settings changed since they were "
    "uploaded. Upload the logo again."
)


def _unreviewable(error):
    """The review region's values and status for staged logos that cannot
    be reviewed (a reload of an old review address): the page itself, with
    the explanation where the review would be. An unknown or another
    sign-in's upload is 404; an expired, applied or stale one 409."""
    return {"message": UNREVIEWABLE}, 404 if isinstance(error, LookupError) else 409


@require_http_methods(["GET", "HEAD", "POST"])
def branding_settings(request):
    """Upload, review and apply new logos, all on this page (#750).

    The query is nothing, ``request`` (Apply's answer) or ``review`` (the
    staged logos a successful upload names); Apply posts to the ``review``
    address. A refused Apply (an out-of-date preview, an upload that
    expired) is the page again (status 409) explained in its review region.
    """
    try:
        service = runtime()
        # Reading a change's status (the page's own quiet refresh once it is
        # applied) is passive, as Change status is: it never renews idle time.
        actor = principal(
            request,
            service,
            passive=request.method != "POST" and "request" in request.GET,
        )
        selected = filters(request.GET, allowed={"review", "request"})
        if len(selected) > 1:
            raise ValueError("Invalid Parish logos query.")
        bundle_id = UUID(selected["review"]) if "review" in selected else None
        form, region, status = None, {}, 200
        if request.method == "POST" and "action" in request.POST:
            if (
                bundle_id is None
                or form_action(request.POST, preview_fields=set()) != "confirm"
            ):
                raise ValueError("Confirm reviewed Parish logos.")
            try:
                return _checked(
                    request,
                    service,
                    confirm(
                        request,
                        service,
                        actor,
                        salt=SALT + str(bundle_id),
                        current_scope=_scope(request, bundle_id),
                        in_place=True,
                    ),
                )
            except UserFacingStale as error:
                region, status = {"refusal": error.refusal, "redraw": True}, 409
            except StaleRecordError:
                # Staged logos are tied to the settings they were uploaded
                # against and to this sign-in, and expire after a day.
                region, status = {"message": EXPIRED, "redraw": True}, 409
        elif request.method == "POST":
            try:
                redirect, form = _upload(request, service)
            except StaleRecordError:
                # The form's version is not the current one: say so in place,
                # as any other settings page does, and stage nothing.
                region, status = {"refusal": stale_page().refusal}, 409
            else:
                if redirect is not None:
                    return _checked(request, service, redirect)
                status = 400
        if request.method != "POST" and "request" in selected:
            # The change's status is read first, as on Parish settings.
            with transaction.atomic():
                region["receipt"] = requested_status(
                    request, service, actor, request.GET
                )
        staged = None
        if request.method != "POST" and bundle_id is not None:
            # The staged upload is read before the configuration it is
            # reviewed against (as the separate review page did).
            try:
                staged = staged_bundle(request, service, bundle_id)
            except (StaleRecordError, LookupError, ConfigError) as error:
                region, status = _unreviewable(error)
        configuration = editable_configuration(service)
        if staged is not None:
            try:
                region["review"] = _review(service, actor, configuration, staged)
            except StaleRecordError as error:
                region, status = _unreviewable(error)
        form = form or LogoUploadForm(
            initial={"base_digest": configuration.active_configuration.digest}
        )
        return _page(request, service, configuration, form, status=status, **region)
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
        # Keep this public, immutable caching past the security middleware's
        # no-store default (#355); its own sandbox CSP is already the policy
        # the middleware gives marked responses.
        response.stewardship_own_policy = True
        return response
    except ERRORS as error:
        return _error(error)

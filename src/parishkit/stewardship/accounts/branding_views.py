"""Normalized logo upload, versioned preview and narrowly served immutable PNGs.

New logos are reviewed and applied in the shared review dialog (UX
conventions, "Review dialog", #750). The upload form posts its image (or,
in Advanced, its sizes) without a page load: staged logos are answered with
the dialog's fragment (``branding-review.html``), which carries the signed
preview and the staged bundle it was signed for; the dialog's Apply posts
both back to Parish logos. Apply is answered with the change's status, which
the dialog follows; once the change is applied it redraws the form and the
current logos from the page's address. A refused upload (a field error) is
the page again with status 400, so the error stays on its field and the
dialog stays shut; any other refusal (a stale page, expired or out-of-date
logos) is raised and shown in the dialog.
"""

from io import BytesIO
from pathlib import Path
from uuid import UUID, uuid4

from django import forms
from django.conf import settings
from django.core import signing
from django.db import DatabaseError
from django.http import FileResponse
from django.shortcuts import render
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_http_methods

from parishkit.config import ConfigError
from parishkit.stewardship.storage import StaleRecordError
from parishkit.stewardship.web.admin_forms import AdminForm
from parishkit.stewardship.web.content import MAX_IMAGE_BYTES, prepare_graphics
from parishkit.stewardship.web.contracts import (
    ErrorCode,
    FieldError,
    filters,
    validation_response,
)
from parishkit.stewardship.web.field_rules import refuse_empty_file
from parishkit.stewardship.web.refusals import (
    UserFacing,
    UserFacingStale,
    stale_page,
    unexpected_fields,
    with_default_code,
    with_reason,
)

from . import admin_navigation, field_tips
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
from .branding_files import read_variant
from .branding_models import BrandingAsset, BrandingBundle
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
from .branding_staging import (
    applied_upload,
    file_receipt,
    stage_branding,
    staged_bundle,
)
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


def image_reason(response, form, field):
    """Give a refused image upload's page the reason its image was refused.

    An image over the size limit is ``image_too_large``, with the limit and
    the file's size in bytes; a file that is not a supported, bounded still
    image is ``image_unsupported``. Any other refusal of the form keeps its
    fields and codes only (#1403). Returns ``response``.
    """
    if form.has_error(field, "too_large"):
        upload = form.files.get(form.add_prefix(field))
        return with_reason(
            response,
            "image_too_large",
            expected=MAX_IMAGE_BYTES,
            actual=getattr(upload, "size", None),
        )
    if form.has_error(field, "unsupported_image"):
        return with_reason(response, "image_unsupported")
    return response


def _error(error):
    """Report invalid uploads on the form and storage failures as retryable."""
    # PermissionError is also an OSError: preserve explicit authorization denial.
    if isinstance(error, ConfigError) or (
        isinstance(error, OSError) and not isinstance(error, PermissionError)
    ):
        return validation_response([FieldError(ErrorCode.UNAVAILABLE)], status=503)
    return error_response(error)


class LogoForm(AdminForm):
    """Only the bounded original image is submitted; output names are server-owned."""

    # A 0-byte file is refused as soon as it is chosen (#1577).
    logo = refuse_empty_file(
        forms.FileField(
            label=_("Parish logo"),
            max_length=254,
            widget=forms.FileInput(attrs={"accept": "image/png,image/jpeg,image/webp"}),
            help_text=_(
                "PNG, JPEG or WebP, up to 5 MB. All four display sizes are generated."
            ),
        )
    )
    base_digest = forms.RegexField(
        regex=r"^[0-9a-f]{64}$", max_length=64, widget=forms.HiddenInput
    )

    def clean_logo(self):
        """Size is an early bound; signature, pixels and animation are checked next."""
        value = self.cleaned_data["logo"]
        if not 0 < value.size <= MAX_IMAGE_BYTES:
            raise forms.ValidationError(
                _("Choose an image no larger than 5 MB."), code="too_large"
            )
        return value


# The page's two modes (#1062; ui-conventions spec, "Basic and Advanced
# modes"): Basic, one image resized for every size, and Advanced, each size
# uploaded separately. The shared toggle (components/editor-mode.html) sends
# the mode as editor_mode.
BASIC, ADVANCED = "basic", "advanced"
# A file field with the page's size check and a reserved error line.
SIZE_FIELD_TEMPLATE = "stewardship/file-size-field.html"
# One image over its limit, in the server's and the page's words (ui-v1.js
# fills in "{size}" as file_size writes it).
LOGO_TOO_LARGE = _("Parish logo: the file is {size}; the limit is 5 MB.")


class LogoUploadForm(LogoForm):
    """Parish logos' upload: one image for every size, or each size apart.

    ``editor_mode`` chooses (#1062): Basic is ``LogoForm``'s single upload,
    exactly as before, which the server turns into the four sizes; Advanced
    takes up to four files, one per size, each checked against its own
    rules (``branding_slots``). The page shows only the chosen mode's panel.
    Basic's panel is disabled while hidden, so its image is not sent; the
    hidden Advanced panel is still sent (the convention's recorded
    exception), but a Basic upload ignores it, and the page empties it as a
    Basic image is chosen. A post without a mode is Basic, as every upload
    was before. The setup wizard keeps ``LogoForm``.

    ``stored_mode`` is the mode that saved the current logos
    (``opening_mode``): an unbound form opens in it, and Advanced → Basic
    asks first unless it is Basic. A bound form opens in the mode it was
    sent from, so a refused upload keeps its fields and errors.
    """

    editor_mode = forms.ChoiceField(
        choices=[(BASIC, _("Basic")), (ADVANCED, _("Advanced"))],
        required=False,
        widget=forms.HiddenInput,
    )
    field_order = ["logo", *SLOTS]
    # The browser's check of the files chosen together (ui-v1.js), in the
    # words of clean()'s own refusal; "{size}" is their total.
    max_total_bytes = MAX_TOTAL_BYTES
    max_total_message = _(
        "Together the chosen files are {size}; one upload can carry at most "
        "5 MB. Upload the larger sizes on their own."
    )

    def __init__(self, *args, stored_mode=BASIC, **kwargs):
        """Add one optional file field per size, drawn in the Advanced panel,
        with its one-line rule visible and the rest behind its "i" tip.
        A post without a mode is drawn as Basic, so a refused one-image
        upload still shows its field and error."""
        super().__init__(*args, **kwargs)
        self.stored_mode = stored_mode
        logo = self.fields["logo"]
        logo.required = False
        logo.template_name = SIZE_FIELD_TEMPLATE
        logo.widget.attrs.update(
            {
                # Required in the browser only while Basic is shown: the
                # Basic panel is disabled otherwise (complete-before-submit).
                "required": True,
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
            refuse_empty_file(field)
            field_tips.add(field, slot.tip, slot.hint)
            field.template_name = SIZE_FIELD_TEMPLATE
            self.fields[name] = field
        self.order_fields(self.field_order)
        # Each file field shows the chosen file at once in the preview box
        # drawn above it, whose id is the field's own plus "_preview"
        # (branding-settings.html; ui-v1.js, wireFilePreview; #2279).
        for name in ("logo", *SLOTS):
            self.fields[name].widget.attrs["data-preview-target"] = (
                f"{self[name].auto_id}_preview"
            )

    @property
    def opened_mode(self):
        """The mode the page is drawn in: the one sent, else the stored one."""
        if self.is_bound:
            return ADVANCED if self.data.get("editor_mode") == ADVANCED else BASIC
        return self.stored_mode

    @property
    def stored_basic(self):
        """Whether Basic can show the current logos (made from one image)."""
        return self.stored_mode == BASIC

    @property
    def slot_fields(self):
        """The Advanced panel's fields, one per size, in display order."""
        return [self[name] for name in SLOTS]

    def clean_logo(self):
        """The single image is checked only in Basic. A file over the limit
        is refused in the words the page uses for it."""
        # editor_mode is cleaned after logo, so read the raw value; none
        # is Basic, as in clean_editor_mode.
        if (self.data.get("editor_mode") or BASIC) != BASIC:
            return None
        value = self.cleaned_data.get("logo")
        if value is None:
            raise forms.ValidationError(
                self.fields["logo"].error_messages["required"], code="required"
            )
        if value.size > MAX_IMAGE_BYTES:
            raise forms.ValidationError(
                str(LOGO_TOO_LARGE).replace("{size}", file_size(value.size)),
                code="too_large",
            )
        return super().clean_logo()

    def clean_editor_mode(self):
        """No mode is Basic, the only mode before #1062."""
        return self.cleaned_data.get("editor_mode") or BASIC

    def clean(self):
        """In Advanced check every chosen size against its rules, keeping
        each normalized size in ``cleaned_data["sizes"]``; any refusal marks
        its field, so nothing is staged unless every chosen size passes."""
        values = super().clean()
        if values.get("editor_mode") != ADVANCED:
            return values
        chosen = {name: values.get(name) for name in SLOTS if values.get(name)}
        if not chosen:
            self.add_error(
                "large",
                forms.ValidationError(
                    _("Choose an image for at least one size to upload."),
                    code="choose_one",
                ),
            )
            return values
        total = sum(item.size for item in chosen.values())
        if total > MAX_TOTAL_BYTES:
            self.add_error(
                next(iter(chosen)),
                forms.ValidationError(
                    str(self.max_total_message).replace("{size}", file_size(total)),
                    code="total_too_large",
                ),
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

    Returns ``(bundle_id, None)`` once the four variants are staged, which
    the review dialog then shows with Apply. Otherwise ``(None, form)``, the
    bound form with its errors.
    """
    # A file input left empty (in Basic, the hidden Advanced panel's sizes,
    # which are still sent; in Advanced, each size not chosen) arrives as a
    # part with an empty filename, which Django keeps as an empty POST value
    # rather than a file (#1396). It means nothing was chosen there, so it
    # is allowed; any other value under a file field's name is refused.
    allowed = {"base_digest", "csrfmiddlewaretoken", "editor_mode"}
    allowed |= {name for name in ("logo", *SLOTS) if request.POST.getlist(name) == [""]}
    if (
        set(request.POST) - allowed
        or request.POST.get("editor_mode", BASIC) not in {BASIC, ADVANCED}
        or set(request.FILES) - {"logo", *SLOTS}
        or any(len(values) != 1 for _, values in request.POST.lists())
        or any(len(values) != 1 for _, values in request.FILES.lists())
    ):
        raise unexpected_fields(
            request.POST, request.FILES, allowed=allowed | {"logo", *SLOTS}
        )
    return stage_upload(request, service, LogoUploadForm(request.POST, request.FILES))


def stage_upload(caller, service, form, *, record=None):
    """Stage a bound upload form's logos, or return the refused form.

    The page's upload and ``pk-admin branding preview`` (#1865) share it:
    the form's own checks, then (Advanced) the version check and the
    current logos for the sizes left empty, or (Basic) the four sizes made
    from one image, then ``stage_branding`` under ``caller``'s sign-in.
    ``record`` is ``stage_branding``'s. Returns ``(bundle_id, None)`` once
    the four variants are staged, else ``(None, form)`` with its errors.
    """
    if form.is_valid():
        graphics = None
        mode = form.cleaned_data["editor_mode"]
        if mode == ADVANCED:
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
                form.add_error(
                    missing.slot,
                    forms.ValidationError(
                        missing_message(missing.slot), code="missing_size"
                    ),
                )
        else:
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
        if graphics is not None:
            identifier = stage_branding(
                caller,
                service,
                media_root(),
                graphics,
                base_digest=form.cleaned_data["base_digest"],
                # Parish logos opens in this mode once these logos are
                # applied (#1062).
                mode=mode,
                record=record,
            )
            return UUID(str(identifier)), None
    return None, form


def _review(service, actor, configuration, staged):
    """Review every staged variant; nothing is requested yet.

    ``staged`` is ``staged_bundle``'s row and assets, read before the
    configuration so the preview is signed against the version checked
    last. Returns the dialog fragment's ``review``: the generated sizes,
    the signed preview Apply posts and the staged bundle it names.
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
        "bundle": row.pk,
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
            applied_upload(request, service, bundle_id, refusal=GONE_UPLOAD)
        return editable_configuration(service), None

    return scope


def opening_mode(parish):
    """The mode Parish logos opens in for ``parish``'s current logos (#1062).

    Basic when no logo is stored, or the stored bundle was saved from Basic
    or before the mode was recorded (every such bundle was made from one
    image: the setup step and the one-image upload); Advanced when it was
    saved from Advanced, as its sizes need not share one image.
    """
    if parish is None or parish.large_logo_id is None:
        return BASIC
    mode = (
        BrandingBundle.objects.filter(assets__pk=parish.large_logo_id)
        .values_list("mode", flat=True)
        .first()
    )
    return ADVANCED if mode == ADVANCED else BASIC


def logo_frames(assets):
    """One frame per logo size, in size order, for the current logos.

    Each frame holds the size's title and its saved asset, or ``None`` when
    that size has none, so the page draws all four frames whatever is saved
    and the current logos keep one height (#2279, no layout shift).
    """
    by_size = {asset.label: asset for asset in assets}
    return [
        {"title": slot.title, "asset": by_size.get(name)}
        for name, slot in SLOTS.items()
    ]


def _page(request, service, configuration, form, *, status=200):
    """Render the upload form and the current logos.

    ``form`` is the unbound upload form, or a refused upload's bound one
    (status 400), whose errors are marked on its fields and summarized
    above the heading.
    """
    current = configuration.active_configuration.parish
    # The toggle opens in (and Advanced → Basic asks unless) the mode that
    # saved the current logos.
    form.stored_mode = opening_mode(current)
    admin_navigation.place(request, flow="change", step="edit")
    page_url = reverse("admin:branding_settings")
    response = render(
        request,
        "stewardship/branding-settings.html",
        {
            "form": form,
            "logos": logo_frames(current_assets(current).values()),
            # The upload's Review and the dialog's Apply post here.
            "page_url": page_url,
            # Once a change is applied, the dialog redraws the form and the
            # current logos from this page's own address.
            "refresh_url": page_url,
            "errors": summary_errors(form) if form.is_bound else [],
        },
        status=status,
    )
    # Each file field's preview shows the chosen file from a blob: object
    # URL (#2279, as on setup's Parish logos, #2270).
    response.stewardship_blob_images = True
    if status != 200:
        response.stewardship_safe_error = True
        # The refusal log's code for this page's own refusal (#1444).
        with_default_code(response)
    return _checked(request, service, response)


# Why staged logos cannot be used: they expired after a day, belong to
# another sign-in, were already applied, or the settings changed since. A
# refusal in the review dialog never redraws the form, which keeps the
# version the page was loaded at (as on every dialog editor), so each
# message says to reload first; otherwise the next upload would be refused
# again as a stale page.
EXPIRED = _(
    "These logos expired, or the settings changed after they were uploaded, "
    "so they were not used. Reload the page, then upload the logo again."
)
# Why staged logos unknown to this sign-in cannot be applied: they were
# uploaded before the reader signed in again (another tab), or are gone.
GONE_UPLOAD = _(
    "These logos were uploaded in another sign-in, or are no longer "
    "available, so they were not used. Reload the page, then upload the logo "
    "again."
)
UNREVIEWABLE = _(
    "These uploaded logos can no longer be reviewed: the settings changed "
    "after they were uploaded. Reload the page, then upload the logo again."
)


def _reviewed(request, service, actor, bundle_id):
    """The review dialog's fragment for the staged logos ``bundle_id``.

    The staged upload is read before the configuration it is reviewed
    against, so the preview is signed against the version checked last.
    Settings that changed in between are refused in the dialog.
    """
    staged = staged_bundle(request, service, bundle_id)
    configuration = editable_configuration(service)
    try:
        review = _review(service, actor, configuration, staged)
    except StaleRecordError as error:
        if isinstance(error, UserFacing):
            raise
        raise UserFacingStale(UNREVIEWABLE) from None
    return _checked(
        request,
        service,
        render(request, "stewardship/branding-review.html", {"review": review}),
    )


@require_http_methods(["GET", "HEAD", "POST"])
def branding_settings(request):
    """Show the upload form and current logos; review an upload; apply it.

    GET takes no query. A POST without ``action`` is the upload, answered
    with the review dialog's fragment (or, refused for its fields, the
    page again with status 400). A POST with ``action=confirm`` is the
    dialog's Apply (the preview and the staged bundle it was signed for),
    answered with the change's status. Other refusals are raised for the
    dialog (see the module docstring).
    """
    try:
        service = runtime()
        actor = principal(request, service)
        filters(request.GET, allowed=set())
        if request.method == "POST" and "action" in request.POST:
            if (
                form_action(
                    request.POST, preview_fields=set(), confirm_fields={"bundle"}
                )
                != "confirm"
            ):
                raise ValueError("Review Parish logos by uploading them.")
            bundle_id = UUID(request.POST.get("bundle", ""))
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
                    ),
                )
            except StaleRecordError as error:
                if isinstance(error, UserFacing):
                    raise
                # Staged logos are tied to the settings they were uploaded
                # against and to this sign-in, and expire after a day.
                raise UserFacingStale(EXPIRED) from None
        form = None
        if request.method == "POST":
            try:
                bundle_id, form = _upload(request, service)
            except StaleRecordError as error:
                if isinstance(error, UserFacing):
                    raise
                # The form's version is not the current one: refused in the
                # dialog, as any other settings page does; nothing staged.
                raise stale_page() from None
            if bundle_id is not None:
                return _reviewed(request, service, actor, bundle_id)
        configuration = editable_configuration(service)
        if form is not None:
            return image_reason(
                _page(request, service, configuration, form, status=400), form, "logo"
            )
        form = LogoUploadForm(
            initial={"base_digest": configuration.active_configuration.digest}
        )
        return _page(request, service, configuration, form)
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

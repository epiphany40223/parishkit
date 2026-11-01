"""Campaign artwork (#248): upload, review and select a banner and page icons.

The images are campaign theme artwork, so they belong to the campaign: its
configuration's optional ``artwork`` value names them. Each slot holds one
normalized PNG in its own branding bundle. Uploads reuse the logo pipeline's
private staging, and a selection becomes live only through a confirmed
configuration request. The banner heads the Family emails only (never a
Family page); each icon sits above the heading of its Family page. Artwork
stays editable while the campaign is live because it is presentation, not
structure.

Each change is reviewed and applied in the shared review dialog (UX
conventions, "Review dialog", #750). A slot's upload form posts its image
without a page load: a staged image is answered with the dialog's fragment
(``artwork-review.html``), and Remove this image (a small form drawn only
when the slot has an image) posts to Campaign images for the removal's.
The fragment carries the signed preview and the slot (and staged bundle)
it was signed for, which the dialog's Apply posts back to Campaign images.
Apply is answered with the change's status, which the dialog follows; once
the change is applied it redraws the slots from the page's address with the
new current image. A refused upload (a field error, an unavailable slot) is
the page again with status 400, so the error stays on the slot's field and
the dialog stays shut; any other refusal (a stale page, an expired upload,
an out-of-date preview) is raised and shown in the dialog.

An icon whose Family page this campaign does not show (the Financial page
while Financial stewardship is off; the Member pages while both Census and
Ministries are) cannot be uploaded: its slot shows Upload unavailable, named
by the reason, and the server refuses such an upload at the field (#563).
The Financial page can still appear for a Family whose earlier financial
answers await a decision after the module was turned off (family-v1.js's
unresolved ``financial.removed`` conflict); that page is transitional, so
its icon is not offered for it.

Inside the campaign wizard (#1404 PR 4c) the slots draw no Upload and
review: the action row's Save and continue first stages each chosen image
at its slot's upload address (the same answer, of which the page reads only
the staged bundle), then posts ``save`` here with the staged bundle of each
chosen slot and the page's request key. ``_save`` checks them and records
one change setting every chosen slot through ``campaign_wizard.save_step``,
with no review dialog; Remove this image keeps its confirmation.
"""

from uuid import UUID, uuid4

from django import forms
from django.shortcuts import render
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_http_methods

from parishkit.config import ConfigError
from parishkit.stewardship.campaigns.configuration import ARTWORK_SLOTS
from parishkit.stewardship.campaigns.models import Campaign
from parishkit.stewardship.storage import StaleRecordError
from parishkit.stewardship.web.admin_forms import AdminForm
from parishkit.stewardship.web.content import MAX_IMAGE_BYTES, prepare_artwork
from parishkit.stewardship.web.contracts import filters
from parishkit.stewardship.web.field_rules import refuse_empty_file
from parishkit.stewardship.web.refusals import (
    UserFacing,
    UserFacingError,
    UserFacingStale,
    stale_page,
    unexpected_fields,
    with_default_code,
)

from . import admin_navigation, campaign_wizard
from .admin_editing import (
    confirm,
    direct_key,
    editable_configuration,
    form_action,
    principal,
    sign_preview,
    summary_errors,
)
from .authentication import runtime
from .branding_files import read_variant
from .branding_models import BrandingAsset
from .branding_staging import (
    applied_upload,
    file_receipt,
    stage_branding,
    staged_bundle,
)
from .branding_validation import validate_installation
from .branding_views import (
    ERRORS,
    _checked,
    _error,
    _retained,
    image_reason,
    media_root,
)
from .request_patch import build_candidate

SALT = "stewardship-artwork-preview-v1-"
# Why an Apply was refused because its change went stale, by change kind.
# The slots' forms keep the version the page was loaded at (a refusal in
# the review dialog never redraws them, as on every dialog editor), so each
# message says to reload first; otherwise the next upload or removal would
# be refused again as a stale page.
STALE = {
    "image": _(
        "This upload expired, or the settings changed after it was uploaded, "
        "so it was not used. Reload the page, then upload the image again."
    ),
    "remove": _(
        "The settings changed after this removal was reviewed, so the image "
        "was not removed. Reload the page, then choose Remove this image "
        "again if the slot still shows an image."
    ),
}
# Why a staged image unknown to this sign-in cannot be applied: it was
# uploaded before the reader signed in again (another tab), or is gone.
GONE_UPLOAD = _(
    "This upload was made in another sign-in, or is no longer available, so "
    "it was not used. Reload the page, then upload the image again."
)
# Why a review address can no longer be reviewed, by change kind.
UNREVIEWABLE = {
    "image": _(
        "This uploaded image can no longer be reviewed: it was already "
        "applied, expired after a day, or the settings changed since it was "
        "uploaded. Reload the page, then upload the image again."
    ),
    "remove": _(
        "This removal can no longer be reviewed: the slot has no image to "
        "remove now, or the settings changed since. Reload the page, then "
        "choose Remove this image again if the slot still shows an image."
    ),
}
# Why a direct save's staged image can no longer be saved (#1404 PR 4c): it
# is gone, expired, of another sign-in, or staged against older settings.
# The images are chosen again after a reload, which stages them anew.
UNSAVABLE = _(
    "An uploaded image can no longer be saved: it expired after a day, was "
    "made in another sign-in, or the settings changed since it was uploaded. "
    "Reload the page, then choose the image again."
)
# The direct save's own fields; each chosen slot adds ``bundle_<slot>``.
SAVE_FIELDS = frozenset({"action", "csrfmiddlewaretoken", "base_digest", "request_key"})
# Why an upload that is not a usable image is refused, at its field, by the
# kind of image the slot takes. Each names only what its slot needs, so it
# fits the slot's reserved status line (#1410); the browser tests check the
# fit at phone and desktop width.
NOT_STATIC_IMAGE = {
    "banner": _(
        "Choose a static PNG, JPEG or WebP image at least twice as wide as it is tall."
    ),
    "section": _("Choose a static PNG, JPEG or WebP image that is roughly square."),
}
SLOT_LABELS = {
    "banner": _("Banner (top of the Family emails only)"),
    "welcome": _("Welcome page icon"),
    "member": _("Member pages icon"),
    "financial": _("Financial stewardship page icon"),
    "closing": _("Closing page icon"),
}

# Why a slot's icon would not be seen, by slot, for the slots that belong to
# a Family page only some campaign modules show (#563).
UNAVAILABLE = {
    "financial": _(
        "Families see no Financial stewardship page while Financial stewardship is off "
        "for this campaign, so this icon cannot be uploaded."
    ),
    "member": _(
        "Families see no Member pages while Census and Ministries are both "
        "off for this campaign, so this icon cannot be uploaded."
    ),
}
# The campaign modules that show each such slot's Family page (family-v1.js:
# the Member pages need Census or Ministries; the Financial page, Financial,
# apart from a Family's unresolved financial.removed conflict, which can still
# show it while the module is off).
SLOT_MODULES = {"financial": {"financial"}, "member": {"census", "ministry"}}


def unavailable(campaign, slot):
    """Why ``slot`` cannot take an image for ``campaign``, or None when it can.

    A slot whose Family page the campaign's modules do not show would hold an
    image Families almost never see (only the Financial page of an unresolved
    financial.removed conflict), so its upload is shown unavailable with this reason
    and refused by the server (#563). Its current image, if any, can still
    be removed.
    """
    modules = set(campaign.active_configuration.values.get("modules", ()))
    if slot in SLOT_MODULES and not modules & SLOT_MODULES[slot]:
        return UNAVAILABLE[slot]
    return None


def name_unavailable(form, slot):
    """Name an unavailable slot's greyed field by its reason line too (#563).

    The field keeps its help and is also described by the reason line under
    Upload (``artwork-hint-<slot>`` in artwork-settings.html), as the button
    is, so a reader reaching the field hears why it is unavailable.
    """
    form.fields["image"].widget.attrs["aria-describedby"] = (
        f"id_{slot}_image_helptext artwork-hint-{slot}"
    )
    return form


def current_images(campaign):
    """The campaign's current image rows, keyed by slot.

    Campaign images draws these and ``pk-admin artwork show`` prints them.
    The configuration names assets by plain id, not foreign key, so a slot
    whose asset row is missing is simply absent.
    """
    current = campaign.active_configuration.values.get("artwork", {}).get("images", {})
    assets = {
        str(asset.pk): asset
        for asset in BrandingAsset.objects.filter(pk__in=list(current.values()))
    }
    return {slot: assets[ref] for slot, ref in current.items() if ref in assets}


def _kind(slot):
    """The one normalized image kind a slot accepts."""
    return "banner" if slot == "banner" else "section"


def _slot(value):
    """Admit only a known slot name from the URL."""
    if value not in ARTWORK_SLOTS:
        raise LookupError("Unknown campaign image slot.")
    return value


def _campaign(configuration, campaign_id):
    """Only the current campaign's artwork is editable; history stays fixed."""
    if campaign_id != configuration.current_campaign_id:
        raise LookupError("Only the current campaign's images can be changed.")
    campaign = (
        Campaign.objects.select_related("active_configuration")
        .filter(pk=campaign_id)
        .first()
    )
    if campaign is None:
        raise LookupError("Campaign is unavailable.")
    return campaign


class ArtworkForm(AdminForm):
    """One bounded original image; the stored name and size are server-owned."""

    # data-max-bytes (ui-v1.js, #563) refuses a file over the server's
    # bound at the field as soon as it is chosen, and a 0-byte one (#1577);
    # the server still checks.
    image = refuse_empty_file(
        forms.FileField(
            label=_("Image"),
            max_length=254,
            widget=forms.FileInput(
                attrs={
                    "accept": "image/png,image/jpeg,image/webp",
                    "data-max-bytes": MAX_IMAGE_BYTES,
                    "data-max-bytes-message": _("Choose an image no larger than 5 MB."),
                }
            ),
            help_text=_("PNG, JPEG or WebP, up to 5 MB."),
        )
    )
    base_digest = forms.RegexField(
        regex=r"^[0-9a-f]{64}$", max_length=64, widget=forms.HiddenInput
    )

    def clean_image(self):
        """Size is an early bound; signature, pixels and animation are checked next."""
        value = self.cleaned_data["image"]
        if not 0 < value.size <= MAX_IMAGE_BYTES:
            raise forms.ValidationError(
                _("Choose an image no larger than 5 MB."), code="too_large"
            )
        return value


def artwork_patch(campaign, artwork):
    """A campaign update setting its ``artwork`` value, or clearing it when empty.

    ``None`` removes the optional key, so a campaign without artwork has one
    canonical form. Parts of the mapping that are empty are dropped too.
    """
    artwork = {key: value for key, value in artwork.items() if value}
    return [
        {
            "operation": "update",
            "section": "campaigns",
            "id": str(campaign.pk),
            "values": {"artwork": artwork or None},
        }
    ]


def _image_patch(campaign, slot, reference):
    """Set one slot's image, or remove it when ``reference`` is None."""
    artwork = dict(campaign.active_configuration.values.get("artwork", {}))
    images = dict(artwork.get("images", {}))
    if reference is None:
        images.pop(slot, None)
    else:
        images[slot] = reference
    artwork["images"] = images
    return artwork_patch(campaign, artwork)


def _check_candidate(service, configuration, patch, actor):
    """Build and validate the exact candidate the Admin is about to confirm."""
    base = service.store.active()
    if base is None or base.digest != configuration.active_configuration.digest:
        raise StaleRecordError("The campaign image preview base changed.")
    candidate = build_candidate(base, patch, candidate_id=uuid4())
    validate_installation(candidate.candidate.document(), actor_id=actor.identity)


def _page(request, service, configuration, campaign, forms_by_slot, *, status=200):
    """Render every slot with its current image and upload form.

    ``forms_by_slot`` holds a refused upload's bound form (status 400),
    whose errors are marked on its field and summarized above the heading.
    """
    images = current_images(campaign)
    slots = []
    for slot in ARTWORK_SLOTS:
        form = forms_by_slot.get(slot) or ArtworkForm(
            initial={"base_digest": configuration.active_configuration.digest},
            auto_id=f"id_{slot}_%s",
        )
        reason = unavailable(campaign, slot)
        if reason is not None:
            name_unavailable(form, slot)
        slots.append(
            {
                "slot": slot,
                "label": SLOT_LABELS[slot],
                "asset": images.get(slot),
                "unavailable": reason,
                "form": form,
            }
        )
    admin_navigation.place(request, flow="change", step="edit")
    refused = next(iter(forms_by_slot.values()), None)
    page_url = reverse("admin:artwork_settings")
    response = render(
        request,
        "stewardship/artwork-settings.html",
        {
            "slots": slots,
            "campaign": campaign,
            "base_digest": configuration.active_configuration.digest,
            # Remove this image's Review and the dialog's Apply post here.
            "page_url": page_url,
            # Once a change is applied, the dialog redraws the slots from
            # this page's own address.
            "refresh_url": page_url,
            "errors": summary_errors(refused) if refused is not None else [],
        },
        status=status,
    )
    if status != 200:
        response.stewardship_safe_error = True
        # The refusal log's code for this page's own refusal (#1444).
        with_default_code(response)
    return _checked(request, service, response)


def _posted_change(parameters):
    """The change a removal's Review or an Apply names: ``("image", slot,
    bundle)`` when a staged bundle is posted, else ``("remove", slot, None)``.
    """
    slot = _slot(parameters.get("slot"))
    if "bundle" in parameters:
        return "image", slot, UUID(parameters["bundle"])
    return "remove", slot, None


def _salt(campaign_id, change):
    """The signed preview's salt, bound to the campaign, slot and change."""
    kind, slot, bundle_id = change
    # The salts the separate review pages used, so the tokens keep their meaning.
    return (
        SALT
        + str(campaign_id)
        + slot
        + ("remove" if kind == "remove" else str(bundle_id))
    )


def _staged_asset(request, service, slot, bundle_id):
    """The one normalized image staged in ``bundle_id`` for ``slot``.

    Only this sign-in's own ready upload of the slot's kind (a banner or an
    icon) is admitted; its file must still be readable.
    """
    row, assets = staged_bundle(request, service, bundle_id)
    if len(assets) != 1 or assets[0].label != _kind(slot):
        raise LookupError("This upload is not an image for that slot.")
    read_variant(media_root(), row.pk, file_receipt(assets[0]))
    return assets[0]


def _review(request, service, actor, configuration, campaign, change):
    """Review one staged image or one removal; nothing is requested yet.

    Returns the dialog fragment's ``review``: the image (or the removal),
    the signed preview Apply posts, and the slot and bundle it names.
    """
    kind, slot, bundle_id = change
    if kind == "remove":
        asset = None
        preview = review_removal(service, actor, configuration, campaign, slot)
    else:
        asset = _staged_asset(request, service, slot, bundle_id)
        patch = _image_patch(campaign, slot, str(asset.pk))
        _check_candidate(service, configuration, patch, actor)
        preview = sign_preview(
            actor=actor,
            configuration=configuration,
            patch=patch,
            salt=_salt(campaign.pk, change),
        )
    return {
        "removing": kind == "remove",
        "slot": slot,
        "bundle": bundle_id,
        "asset": asset,
        "label": SLOT_LABELS[slot],
        "preview": preview,
    }


def review_removal(service, actor, configuration, campaign, slot):
    """The signed preview of removing ``slot``'s image, as Apply posts it.

    Request-free, so the page's review and ``pk-admin artwork
    remove-preview`` share it. A slot with no image to remove raises
    ``LookupError``; a base that is no longer the applied one raises
    ``StaleRecordError`` and a resulting document the configuration refuses
    ``ConfigError`` (``_check_candidate``). A slot whose upload is
    unavailable can still be removed (#563).
    """
    images = campaign.active_configuration.values.get("artwork", {}).get("images", {})
    if slot not in images:
        raise LookupError("That slot has no image to remove.")
    patch = _image_patch(campaign, slot, None)
    _check_candidate(service, configuration, patch, actor)
    return sign_preview(
        actor=actor,
        configuration=configuration,
        patch=patch,
        salt=_salt(campaign.pk, ("remove", slot, None)),
    )


def _scope(request, change):
    """Confirmation's current scope: a staged image must still be this
    session's (or already published, for an exact retry); a removal needs
    only the editable configuration."""
    kind, slot, bundle_id = change
    return _staged_scope(request, {slot: bundle_id} if kind == "image" else {})


def _staged_scope(request, staged, gone=GONE_UPLOAD):
    """The current scope of a change that uses the ``staged`` {slot: bundle}.

    Under the work lock each staged image must still be this sign-in's own
    upload (``applied_upload``), unless it is already published, for an
    exact retry of the same change; an upload this sign-in no longer has
    is refused with ``gone``, the page's own words.
    """

    def scope(service):
        """Permit exact receipt retries after selection, not stale new edits."""
        for slot, bundle_id in staged.items():
            asset = BrandingAsset.objects.filter(
                bundle_id=bundle_id, label=_kind(slot)
            ).first()
            if asset is None or not _retained(asset):
                applied_upload(request, service, bundle_id, refusal=gone)
        return editable_configuration(service), None

    return scope


def _reviewed(request, service, actor, configuration, campaign, change):
    """The review dialog's fragment for ``change``, or its refusal raised.

    An upload that can no longer be reviewed, or a slot with nothing left
    to remove, is refused in the dialog in the page's words.
    """
    try:
        review = _review(request, service, actor, configuration, campaign, change)
    except (KeyError, IndexError):
        # A defect, not an unreviewable upload: a 500 (#2044).
        raise
    except (StaleRecordError, LookupError, ConfigError) as error:
        if isinstance(error, UserFacing):
            raise
        raise UserFacingStale(UNREVIEWABLE[change[0]]) from None
    return _checked(
        request,
        service,
        render(request, "stewardship/artwork-review.html", {"review": review}),
    )


def _staged(parameters):
    """The {slot: staged bundle} a direct save names (#1404 PR 4c).

    The save posts only its own fields (``SAVE_FIELDS``) and one
    ``bundle_<slot>`` per chosen slot, each once; it must name at least one.
    """
    allowed = SAVE_FIELDS | {f"bundle_{slot}" for slot in ARTWORK_SLOTS}
    if set(parameters) - allowed or any(
        len(values) != 1 for _, values in parameters.lists()
    ):
        raise unexpected_fields(parameters, allowed=allowed)
    staged = {
        slot: UUID(parameters[f"bundle_{slot}"])
        for slot in ARTWORK_SLOTS
        if f"bundle_{slot}" in parameters
    }
    if not staged:
        raise ValueError("Choose an image to save.")
    return staged


def _save(request, service, actor, campaign_id):
    """Campaign images' Save and continue in the campaign wizard (#1404 PR 4c).

    The page has already staged each chosen image at its slot's upload
    address; this records one change setting every slot it names to its
    staged image. The change's request key comes from the page's key and the
    staged bundle ids (``direct_key``), never the image bytes, so a repeat
    of the same save answers with the first one's status
    (``repeated_save``). Otherwise the page's version must be current, each
    slot one the campaign shows, and each image this sign-in's own staged
    upload of the slot's kind (else ``UNSAVABLE``); the candidate is
    validated, signed with that key and confirmed at once by
    ``campaign_wizard.save_step``, which refuses unless the wizard still
    applies. Returns the change's status redirect.
    """
    staged = _staged(request.POST)
    key = direct_key(request.POST)
    if repeat := campaign_wizard.repeated_save(request, actor, key):
        return repeat
    configuration = editable_configuration(service)
    campaign = _campaign(configuration, campaign_id)
    if request.POST.get("base_digest") != configuration.active_configuration.digest:
        raise stale_page()
    for slot in staged:
        if reason := unavailable(campaign, slot):
            # The page greys such a slot's field and its staging refuses it,
            # and modules changed meanwhile move the version (refused above),
            # so only a request that skips the page lands here.
            raise UserFacingError(reason, fix=_("Nothing was saved."))
    artwork = dict(campaign.active_configuration.values.get("artwork", {}))
    images = dict(artwork.get("images", {}))
    try:
        for slot, bundle_id in staged.items():
            images[slot] = str(_staged_asset(request, service, slot, bundle_id).pk)
        patch = artwork_patch(campaign, {**artwork, "images": images})
        _check_candidate(service, configuration, patch, actor)
    except (KeyError, IndexError):
        # A defect, not an unsavable upload: a 500 (#2044).
        raise
    except (StaleRecordError, LookupError, ConfigError) as error:
        if isinstance(error, UserFacing):
            raise
        raise UserFacingStale(UNSAVABLE) from None
    salt = f"{SALT}{campaign.pk}save"
    token = sign_preview(
        actor=actor, configuration=configuration, patch=patch, salt=salt, key=key
    )
    try:
        return campaign_wizard.save_step(
            request,
            service,
            actor,
            token=token,
            salt=salt,
            current_scope=_staged_scope(request, staged, UNSAVABLE),
            campaign_id=campaign_id,
        )
    except StaleRecordError as error:
        if isinstance(error, UserFacing):
            raise
        # An upload that expired, or went stale, between the checks above
        # and the work lock: worded as those checks word it.
        raise UserFacingStale(UNSAVABLE) from None


@require_http_methods(["GET", "HEAD", "POST"])
def artwork_settings(request, campaign_id):
    """List the slots; review a removal; apply a reviewed change.

    GET takes no query. A POST is a removal's Review (``action=preview``
    with the slot and the page's version), answered with the dialog's
    fragment, or the dialog's Apply (``action=confirm`` with the preview and
    the slot and bundle it was signed for), answered with the change's
    status, as is the campaign wizard's direct save (``action=save``,
    ``_save``). Refusals are raised for the dialog, or the wizard's status
    line (see the module docstring).
    """
    try:
        service = runtime()
        actor = principal(request, service)
        filters(request.GET, allowed=set())
        if request.method == "POST" and request.POST.get("action") == "save":
            return _checked(
                request, service, _save(request, service, actor, campaign_id)
            )
        if request.method == "POST":
            action = form_action(
                request.POST,
                preview_fields={"slot", "base_digest"},
                confirm_fields={"slot", "bundle"},
            )
            change = _posted_change(request.POST)
            if action == "confirm":
                try:
                    return _checked(
                        request,
                        service,
                        confirm(
                            request,
                            service,
                            actor,
                            salt=_salt(campaign_id, change),
                            current_scope=_scope(request, change),
                        ),
                    )
                except StaleRecordError as error:
                    if isinstance(error, UserFacing):
                        raise
                    # A staged upload is tied to the settings it was
                    # uploaded against and to this sign-in, and expires
                    # after a day; a removal goes stale only when the
                    # settings change.
                    raise UserFacingStale(STALE[change[0]]) from None
            # An image is reviewed by its upload's answer; only a removal
            # is reviewed here.
            if change[0] != "remove":
                raise ValueError("Review a campaign image by uploading it.")
        configuration = editable_configuration(service)
        campaign = _campaign(configuration, campaign_id)
        if request.method == "POST":
            if request.POST.get("base_digest") != (
                configuration.active_configuration.digest
            ):
                raise stale_page()
            return _reviewed(request, service, actor, configuration, campaign, change)
        return _page(request, service, configuration, campaign, {})
    except ERRORS as error:
        return _error(error)


@require_http_methods(["POST"])
def artwork_upload(request, campaign_id, slot):
    """Normalize and stage one image; nothing changes until it is confirmed.

    A staged image is answered with the review dialog's fragment; a refused
    upload is the page again (status 400) with the slot's field error, and
    a stale page is refused in the dialog.
    """
    try:
        slot = _slot(slot)
        service = runtime()
        actor = principal(request, service)
        configuration = editable_configuration(service)
        campaign = _campaign(configuration, campaign_id)
        if (
            set(request.POST) - {"base_digest", "csrfmiddlewaretoken"}
            or set(request.FILES) - {"image"}
            or any(len(values) != 1 for _, values in request.POST.lists())
            or any(len(values) != 1 for _, values in request.FILES.lists())
        ):
            raise unexpected_fields(
                request.POST,
                request.FILES,
                allowed={"base_digest", "csrfmiddlewaretoken", "image"},
            )
        form = ArtworkForm(request.POST, request.FILES, auto_id=f"id_{slot}_%s")
        reason = unavailable(campaign, slot)
        if reason is not None:
            # The page shows this upload unavailable; a request that skips it
            # is refused at the field like any other upload error (#563).
            form.is_valid()
            form.add_error("image", forms.ValidationError(reason, code="not_available"))
        elif form.is_valid():
            try:
                graphics = prepare_artwork(form.cleaned_data["image"], _kind(slot))
            except ValueError:
                form.add_error(
                    "image",
                    forms.ValidationError(
                        NOT_STATIC_IMAGE[_kind(slot)], code="unsupported_image"
                    ),
                )
            else:
                try:
                    identifier = stage_branding(
                        request,
                        service,
                        media_root(),
                        graphics,
                        base_digest=form.cleaned_data["base_digest"],
                    )
                except StaleRecordError:
                    # The page's version is not the current one: refused in
                    # the dialog, as any settings page does; nothing staged.
                    raise stale_page() from None
                change = ("image", slot, UUID(str(identifier)))
                return _reviewed(
                    request, service, actor, configuration, campaign, change
                )
        return image_reason(
            _page(request, service, configuration, campaign, {slot: form}, status=400),
            form,
            "image",
        )
    except ERRORS as error:
        return _error(error)

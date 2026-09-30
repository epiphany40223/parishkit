"""Campaign artwork (#248): upload, review and select a banner and page icons.

The images are campaign theme artwork, so they belong to the campaign: its
configuration's optional ``artwork`` value names them. Each slot holds one
normalized PNG in its own branding bundle. Uploads reuse the logo pipeline's
private staging, and a selection becomes live only through a confirmed
configuration request. The banner heads the Family emails and welcome page;
each icon sits above the heading of its Family page. Artwork stays editable
while the campaign is live because it is presentation, not structure.
"""

from uuid import uuid4

from django import forms
from django.http import HttpResponseRedirect
from django.shortcuts import render
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_http_methods

from parishkit.stewardship.campaigns.configuration import ARTWORK_SLOTS
from parishkit.stewardship.campaigns.models import Campaign
from parishkit.stewardship.storage import StaleRecordError
from parishkit.stewardship.web.content import MAX_IMAGE_BYTES, prepare_artwork
from parishkit.stewardship.web.contracts import filters

from . import admin_navigation
from .admin_editing import (
    confirm,
    editable_configuration,
    form_action,
    principal,
    sign_preview,
)
from .authentication import runtime
from .branding_files import read_variant
from .branding_models import BrandingAsset
from .branding_staging import file_receipt, stage_branding, staged_bundle
from .branding_validation import validate_installation
from .branding_views import ERRORS, _checked, _error, _retained, media_root
from .request_patch import build_candidate

SALT = "stewardship-artwork-preview-v1-"
SLOT_LABELS = {
    "banner": _("Banner (top of the Family emails and the welcome page)"),
    "welcome": _("Welcome page icon"),
    "member": _("Member pages icon"),
    "financial": _("Financial page icon"),
    "closing": _("Closing page icon"),
}


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


class ArtworkForm(forms.Form):
    """One bounded original image; the stored name and size are server-owned."""

    image = forms.FileField(
        label=_("Image"),
        max_length=254,
        widget=forms.FileInput(attrs={"accept": "image/png,image/jpeg,image/webp"}),
        help_text=_("PNG, JPEG or WebP, up to 5 MB."),
    )
    base_digest = forms.RegexField(
        regex=r"^[0-9a-f]{64}$", max_length=64, widget=forms.HiddenInput
    )

    def clean_image(self):
        """Size is an early bound; signature, pixels and animation are checked next."""
        value = self.cleaned_data["image"]
        if not 0 < value.size <= MAX_IMAGE_BYTES:
            raise forms.ValidationError(_("Choose an image no larger than 5 MB."))
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
    """Render every slot with its current image and its own upload form."""
    current = campaign.active_configuration.values.get("artwork", {}).get("images", {})
    assets = {
        str(asset.pk): asset
        for asset in BrandingAsset.objects.filter(pk__in=list(current.values()))
    }
    slots = [
        {
            "slot": slot,
            "label": SLOT_LABELS[slot],
            "asset": assets.get(current.get(slot)),
            "form": forms_by_slot.get(slot)
            or ArtworkForm(
                initial={"base_digest": configuration.active_configuration.digest},
                auto_id=f"id_{slot}_%s",
            ),
        }
        for slot in ARTWORK_SLOTS
    ]
    # Choosing an image is the first step of edit, review, apply (#196).
    admin_navigation.place(request, flow="change", step="edit")
    response = render(
        request,
        "stewardship/artwork-settings.html",
        {"slots": slots, "campaign": campaign},
        status=status,
    )
    if status == 400:
        response.stewardship_safe_error = True
    return _checked(request, service, response)


@require_http_methods(["GET", "HEAD"])
def artwork_settings(request, campaign_id):
    """List the campaign image slots and what each currently shows."""
    try:
        service = runtime()
        principal(request, service)
        configuration = editable_configuration(service)
        filters(request.GET, allowed=set())
        campaign = _campaign(configuration, campaign_id)
        return _page(request, service, configuration, campaign, {})
    except ERRORS as error:
        return _error(error)


@require_http_methods(["POST"])
def artwork_upload(request, campaign_id, slot):
    """Normalize and stage one image; nothing changes until it is confirmed."""
    try:
        slot = _slot(slot)
        service = runtime()
        principal(request, service)
        configuration = editable_configuration(service)
        campaign = _campaign(configuration, campaign_id)
        if (
            set(request.POST) - {"base_digest", "csrfmiddlewaretoken"}
            or set(request.FILES) - {"image"}
            or any(len(values) != 1 for _, values in request.POST.lists())
            or any(len(values) != 1 for _, values in request.FILES.lists())
        ):
            raise ValueError("Invalid campaign image upload fields.")
        form = ArtworkForm(request.POST, request.FILES, auto_id=f"id_{slot}_%s")
        if form.is_valid():
            try:
                graphics = prepare_artwork(form.cleaned_data["image"], _kind(slot))
            except ValueError:
                form.add_error(
                    "image",
                    _(
                        "Choose a supported, bounded static image: a wide banner "
                        "at least twice as wide as it is tall, or a roughly "
                        "square icon."
                    ),
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
                        f"/admin/campaign/{campaign.pk}/images/{slot}/{identifier}"
                    ),
                )
        return _page(
            request, service, configuration, campaign, {slot: form}, status=400
        )
    except ERRORS as error:
        return _error(error)


@require_http_methods(["GET", "HEAD", "POST"])
def artwork_preview(request, campaign_id, slot, bundle_id):
    """Show one staged image and confirm it as that slot's selection."""
    try:
        slot = _slot(slot)
        service = runtime()
        actor = principal(request, service)
        salt = SALT + str(campaign_id) + slot + str(bundle_id)
        if request.method == "POST":
            if form_action(request.POST, preview_fields=set()) != "confirm":
                raise ValueError("Confirm the prepared campaign image preview.")

            def scope(service):
                """Permit exact receipt retries after selection, not stale new edits."""
                asset = BrandingAsset.objects.filter(
                    bundle_id=bundle_id, label=_kind(slot)
                ).first()
                if asset is None or not _retained(asset):
                    staged_bundle(request, service, bundle_id)
                return editable_configuration(service), None

            response = confirm(request, service, actor, salt=salt, current_scope=scope)
        else:
            filters(request.GET, allowed=set())
            configuration = editable_configuration(service)
            campaign = _campaign(configuration, campaign_id)
            row, assets = staged_bundle(request, service, bundle_id)
            if len(assets) != 1 or assets[0].label != _kind(slot):
                raise LookupError("This upload is not an image for that slot.")
            read_variant(media_root(), row.pk, file_receipt(assets[0]))
            patch = _image_patch(campaign, slot, str(assets[0].pk))
            _check_candidate(service, configuration, patch, actor)
            admin_navigation.place(request, flow="change", step="review")
            response = render(
                request,
                "stewardship/artwork-preview.html",
                {
                    "asset": assets[0],
                    "label": SLOT_LABELS[slot],
                    "campaign": campaign,
                    "preview": sign_preview(
                        actor=actor,
                        configuration=configuration,
                        patch=patch,
                        salt=salt,
                    ),
                },
            )
        return _checked(request, service, response)
    except ERRORS as error:
        return _error(error)


@require_http_methods(["GET", "HEAD", "POST"])
def artwork_remove(request, campaign_id, slot):
    """Review and confirm removing one slot's image; the page then shows none."""
    try:
        slot = _slot(slot)
        service = runtime()
        actor = principal(request, service)
        salt = SALT + str(campaign_id) + slot + "remove"
        if request.method == "POST":
            if form_action(request.POST, preview_fields=set()) != "confirm":
                raise ValueError("Confirm removing the campaign image.")
            response = confirm(
                request,
                service,
                actor,
                salt=salt,
                current_scope=lambda service: (editable_configuration(service), None),
            )
        else:
            filters(request.GET, allowed=set())
            configuration = editable_configuration(service)
            campaign = _campaign(configuration, campaign_id)
            images = campaign.active_configuration.values.get("artwork", {}).get(
                "images", {}
            )
            if slot not in images:
                raise LookupError("That slot has no image to remove.")
            patch = _image_patch(campaign, slot, None)
            _check_candidate(service, configuration, patch, actor)
            admin_navigation.place(request, flow="change", step="review")
            response = render(
                request,
                "stewardship/artwork-remove.html",
                {
                    "label": SLOT_LABELS[slot],
                    "campaign": campaign,
                    "preview": sign_preview(
                        actor=actor,
                        configuration=configuration,
                        patch=patch,
                        salt=salt,
                    ),
                },
            )
        return _checked(request, service, response)
    except ERRORS as error:
        return _error(error)

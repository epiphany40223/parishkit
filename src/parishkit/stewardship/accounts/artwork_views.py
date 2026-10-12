"""Campaign artwork (#248): upload, review and select a banner and page icons.

The images are campaign theme artwork, so they belong to the campaign: its
configuration's optional ``artwork`` value names them. Each slot holds one
normalized PNG in its own branding bundle. Uploads reuse the logo pipeline's
private staging, and a selection becomes live only through a confirmed
configuration request. The banner heads the Family emails only (never a
Family page); each icon sits above the heading of its Family page. Artwork
stays editable while the campaign is live because it is presentation, not
structure.

Review, apply and the change's status happen on Campaign images itself
(#750), in its review region under the slots: a successful upload stages
the image and answers with the page naming it (``?review=<slot>&bundle=<id>``),
Remove this image names the slot (``?remove=<slot>``), and Apply posts the
signed preview to that same address, whose query says which change it is.
The answer is the page again naming the request (``?request=<id>``), whose
status the region follows until the change settles and the page refreshes
itself quietly with the new current image.

An icon whose Family page this campaign does not show (the Financial page
while Financial stewardship is off; the Member pages while both Census and
Ministries are) cannot be uploaded: its slot shows Upload unavailable, named
by the reason, and the server refuses such an upload at the field (#563).
The Financial page can still appear for a Family whose earlier financial
answers await a decision after the module was turned off (family-v1.js's
unresolved ``financial.removed`` conflict); that page is transitional, so
its icon is not offered for it.
"""

from urllib.parse import urlencode
from uuid import UUID, uuid4

from django import forms
from django.db import transaction
from django.http import HttpResponseRedirect
from django.shortcuts import render
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_http_methods

from parishkit.config import ConfigError
from parishkit.stewardship.campaigns.configuration import ARTWORK_SLOTS
from parishkit.stewardship.campaigns.models import Campaign
from parishkit.stewardship.storage import StaleRecordError
from parishkit.stewardship.web.content import MAX_IMAGE_BYTES, prepare_artwork
from parishkit.stewardship.web.contracts import filters
from parishkit.stewardship.web.refusals import UserFacingStale, stale_page

from . import admin_navigation
from .admin_editing import (
    REVIEW_REGION,
    confirm,
    editable_configuration,
    form_action,
    principal,
    requested_status,
    review_region,
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
# Why an Apply was refused because its change went stale, by change kind.
STALE = {
    "image": _(
        "This upload expired, or the settings changed after it was uploaded, "
        "so it was not used. Upload the image again."
    ),
    "remove": _(
        "The settings changed after this removal was reviewed, so the image "
        "was not removed. Choose Remove this image again."
    ),
}
# Why a review address can no longer be reviewed, by change kind.
UNREVIEWABLE = {
    "image": _(
        "This uploaded image can no longer be reviewed: it was already "
        "applied, expired after a day, or the settings changed since it was "
        "uploaded. Upload the image again."
    ),
    "remove": _(
        "This removal can no longer be reviewed: the slot has no image to "
        "remove now, or the settings changed since. Choose Remove this image "
        "again if the slot still shows one."
    ),
}
SLOT_LABELS = {
    "banner": _("Banner (top of the Family emails only)"),
    "welcome": _("Welcome page icon"),
    "member": _("Member pages icon"),
    "financial": _("Financial page icon"),
    "closing": _("Closing page icon"),
}

# Why a slot's icon would not be seen, by slot, for the slots that belong to
# a Family page only some campaign modules show (#563).
UNAVAILABLE = {
    "financial": _(
        "Families see no Financial page while Financial stewardship is off "
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
        # data-max-bytes (ui-v1.js, #563) refuses a file over the server's
        # bound at the field as soon as it is chosen; the server still checks.
        widget=forms.FileInput(
            attrs={
                "accept": "image/png,image/jpeg,image/webp",
                "data-max-bytes": MAX_IMAGE_BYTES,
                "data-max-bytes-message": _("Choose an image no larger than 5 MB."),
            }
        ),
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


def _page(
    request,
    service,
    configuration,
    campaign,
    forms_by_slot,
    *,
    status=200,
    redraw=False,
    **region,
):
    """Render every slot with its current image and upload form, and the review.

    ``region`` is ``review_region``'s ``review``, ``receipt`` or ``refusal``
    for the review region under the slots; the step indicator follows it
    through edit, review and apply (#196), all on this page. ``redraw``
    marks the slots to be redrawn even by Apply's answer (a refused Apply:
    their forms then carry the current version).
    """
    current = campaign.active_configuration.values.get("artwork", {}).get("images", {})
    assets = {
        str(asset.pk): asset
        for asset in BrandingAsset.objects.filter(pk__in=list(current.values()))
    }
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
                "asset": assets.get(current.get(slot)),
                "unavailable": reason,
                "form": form,
            }
        )
    step = "review" if region.get("review") else "edit"
    if region.get("receipt"):
        step = "apply"
    admin_navigation.place(request, flow="change", step=step)
    response = render(
        request,
        "stewardship/artwork-settings.html",
        {
            "slots": slots,
            "campaign": campaign,
            "redraw": redraw,
            # A refused upload's errors are summarized in the review region
            # too, linked to the slot's field, where focus then goes.
            **review_region(
                "artwork_settings",
                next(iter(forms_by_slot.values()), None),
                **region,
            ),
        },
        status=status,
    )
    if status != 200:
        response.stewardship_safe_error = True
    return _checked(request, service, response)


def _change(parameters):
    """The change the page's query names: ``("image", slot, bundle)``,
    ``("remove", slot, None)`` or None, with the request it names, if any.

    The query is nothing, ``request`` alone (Apply's answer), ``review`` and
    ``bundle`` together (a staged upload) or ``remove`` alone; anything else
    is refused.
    """
    selected = filters(parameters, allowed={"review", "bundle", "remove", "request"})
    names = set(selected)
    if names == {"review", "bundle"}:
        return ("image", _slot(selected["review"]), UUID(selected["bundle"])), None
    if names == {"remove"}:
        return ("remove", _slot(selected["remove"]), None), None
    if names <= {"request"}:
        return None, selected.get("request")
    raise ValueError("Invalid campaign image query.")


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


def _review(request, service, actor, configuration, campaign, change):
    """Review one staged image or one removal; nothing is requested yet.

    Returns ``review_region``'s ``review``: the image (or the removal) and
    the signed preview Apply posts.
    """
    kind, slot, bundle_id = change
    if kind == "remove":
        images = campaign.active_configuration.values.get("artwork", {}).get(
            "images", {}
        )
        if slot not in images:
            raise LookupError("That slot has no image to remove.")
        patch, asset = _image_patch(campaign, slot, None), None
    else:
        row, assets = staged_bundle(request, service, bundle_id)
        if len(assets) != 1 or assets[0].label != _kind(slot):
            raise LookupError("This upload is not an image for that slot.")
        asset = assets[0]
        read_variant(media_root(), row.pk, file_receipt(asset))
        patch = _image_patch(campaign, slot, str(asset.pk))
    _check_candidate(service, configuration, patch, actor)
    return {
        # Drawn inside settings-review.html, above its Apply.
        "template": "stewardship/artwork-review.html",
        "notes": [],
        "removing": kind == "remove",
        "asset": asset,
        "label": SLOT_LABELS[slot],
        "preview": sign_preview(
            actor=actor,
            configuration=configuration,
            patch=patch,
            salt=_salt(campaign.pk, change),
        ),
    }


def _scope(request, change):
    """Confirmation's current scope: a staged image must still be this
    session's (or already published, for an exact retry); a removal needs
    only the editable configuration."""
    kind, slot, bundle_id = change

    def scope(service):
        """Permit exact receipt retries after selection, not stale new edits."""
        if kind == "image":
            asset = BrandingAsset.objects.filter(
                bundle_id=bundle_id, label=_kind(slot)
            ).first()
            if asset is None or not _retained(asset):
                staged_bundle(request, service, bundle_id)
        return editable_configuration(service), None

    return scope


@require_http_methods(["GET", "HEAD", "POST"])
def artwork_settings(request, campaign_id):
    """List the slots, review a staged image or a removal, and apply it.

    Every step happens on this page (#750). A refused Apply (an out-of-date
    preview, an upload that expired) is the page again (status 409) with
    the explanation in its review region.
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
        change, requested = _change(request.GET)
        region, status = {}, 200
        if request.method == "POST":
            if change is None or form_action(request.POST, preview_fields=set()) != (
                "confirm"
            ):
                raise ValueError("Confirm a reviewed campaign image change.")
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
                        in_place=True,
                    ),
                )
            except UserFacingStale as error:
                region, status = {"refusal": error.refusal, "redraw": True}, 409
            except StaleRecordError:
                # A staged upload is tied to the settings it was uploaded
                # against and to this sign-in, and expires after a day; a
                # removal goes stale only when the settings change.
                region, status = {"message": STALE[change[0]], "redraw": True}, 409
        if request.method != "POST" and requested is not None:
            # The change's status is read first, as on Parish settings.
            with transaction.atomic():
                region["receipt"] = requested_status(
                    request, service, actor, request.GET
                )
        configuration = editable_configuration(service)
        campaign = _campaign(configuration, campaign_id)
        if request.method != "POST" and change is not None:
            try:
                region["review"] = _review(
                    request, service, actor, configuration, campaign, change
                )
            except (StaleRecordError, LookupError, ConfigError) as error:
                # A reload of an old review address: the page itself, with
                # the explanation where the review would be. An unknown or
                # another sign-in's upload (or a slot with nothing left to
                # remove) is 404; an expired, applied or stale one 409.
                region = {"message": UNREVIEWABLE[change[0]]}
                status = 404 if isinstance(error, LookupError) else 409
        return _page(
            request, service, configuration, campaign, {}, status=status, **region
        )
    except ERRORS as error:
        return _error(error)


@require_http_methods(["POST"])
def artwork_upload(request, campaign_id, slot):
    """Normalize and stage one image; nothing changes until it is confirmed.

    A staged image answers with Campaign images naming it, whose review
    region shows it with Apply (#750); a refused upload is the page again
    with the slot's field error.
    """
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
        reason = unavailable(campaign, slot)
        if reason is not None:
            # The page shows this upload unavailable; a request that skips it
            # is refused at the field like any other upload error (#563).
            form.is_valid()
            form.add_error("image", reason)
        elif form.is_valid():
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
                try:
                    identifier = stage_branding(
                        request,
                        service,
                        media_root(),
                        graphics,
                        base_digest=form.cleaned_data["base_digest"],
                    )
                except StaleRecordError:
                    # The page's version is not the current one: say so in
                    # place, as any settings page does; nothing is staged.
                    return _page(
                        request,
                        service,
                        configuration,
                        campaign,
                        {},
                        status=409,
                        refusal=stale_page().refusal,
                    )
                query = urlencode({"review": slot, "bundle": identifier})
                return _checked(
                    request,
                    service,
                    HttpResponseRedirect(
                        f"{reverse('admin:artwork_settings')}?{query}#{REVIEW_REGION}"
                    ),
                )
        return _page(
            request, service, configuration, campaign, {slot: form}, status=400
        )
    except ERRORS as error:
        return _error(error)

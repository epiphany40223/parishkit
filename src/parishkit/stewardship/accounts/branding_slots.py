"""Each Parish logo size uploaded separately, with its own rules (#1062).

Parish logos can take one image that is resized into all four sizes (the
original behaviour, ``prepare_graphics``) or each size on its own: a
hand-tuned favicon that stays legible at 32 pixels, a wide wordmark for the
menu. Every separately uploaded size has its own formats, byte limit and
shape rules, checked before anything is staged; a refusal names the size,
the rule and what the file actually was, so the Administrator can fix it
without guessing. An accepted upload is normalized exactly like a generated
size (fresh pixel buffer, PNG, fitted within the size's output bound and
never enlarged).

A size left empty keeps its current image, copied byte for byte into the
new bundle (a bundle is always complete: four files, one directory). When
no current image of that size is stored, it is generated from the large
logo uploaded with it, as one-image mode would make it.
"""

import math
from dataclasses import dataclass
from uuid import uuid4

from django import forms
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy

from parishkit.config import ConfigError
from parishkit.stewardship.web.content import (
    MAX_IMAGE_BYTES,
    MAX_IMAGE_PIXELS,
    Graphic,
    image_facts,
    prepare_slot_graphic,
)

from .branding_files import VARIANTS, read_variant
from .branding_models import BrandingAsset
from .branding_staging import file_receipt

KB = 1024
MB = 1024 * KB
# Every file chosen in one per-size upload together. The ingress admits
# 6 MB per request on this route (runtime_ingress.py), so the files must
# leave room for the form's other fields and the multipart framing; a
# larger request would be refused before the page could explain why.
MAX_TOTAL_BYTES = MAX_IMAGE_BYTES
# A shape counts as square while its longer side is at most this many times
# its shorter side (within 10%).
SQUARE = 1.1


@dataclass(frozen=True)
class Slot:
    """One logo size's upload rules.

    ``title`` names the size at the start of an error ("Favicon: ..."), and
    ``noun`` inside one ("a favicon must be square"). ``formats`` are the
    decoder names accepted and ``max_bytes`` the file's byte limit.
    """

    title: str
    noun: str
    formats: tuple
    max_bytes: int
    hint: str
    tip: str


# The four sizes, smallest first by their LOGO_SIZES bounds (#2288): favicon,
# icon, menu logo, large logo. Every list of them follows this order: Parish
# logos' current logos, its Advanced fields and review dialog, and the
# branding commands.
SLOTS = {
    "favicon": Slot(
        gettext_lazy("Favicon"),
        gettext_lazy("a favicon"),
        ("PNG",),
        256 * KB,
        gettext_lazy("PNG, square, 32 to 64 pixels, up to 256 KB."),
        gettext_lazy(
            "The small icon in the browser tab on both portals, kept within 32 "
            "pixels. A mark designed for that size stays legible where a "
            "shrunken logo often does not. Refused when it is not a PNG, over "
            "256 KB, not square (within 10%), under 16 pixels or over 512 "
            "pixels on a side."
        ),
    ),
    "icon": Slot(
        gettext_lazy("Icon"),
        gettext_lazy("an icon"),
        ("PNG",),
        1 * MB,
        gettext_lazy("PNG, square, 180 to 512 pixels, up to 1 MB."),
        gettext_lazy(
            "A square mark for places such as a phone's home screen, kept "
            "within 128 pixels. Refused when it is not a PNG, over 1 MB, not "
            "square (within 10%), or under 128 pixels on a side."
        ),
    ),
    "menu": Slot(
        gettext_lazy("Menu logo"),
        gettext_lazy("a menu logo"),
        ("PNG", "JPEG", "WEBP"),
        1 * MB,
        gettext_lazy(
            "PNG, WebP or JPEG, at least 64 pixels tall; stored within 512 × 128, "
            "up to 1 MB."
        ),
        gettext_lazy(
            "Shown beside the parish name at the top of every Admin and Family "
            "page, and kept within 512 pixels wide and 128 tall. It can be "
            "square or a wide wordmark (up to 4 times as wide as it is tall), "
            "but not taller than it is wide. "
            "Refused when over 1 MB, over 2,048 pixels on a side, wider than "
            "4:1, taller than it is wide, or under 64 pixels tall."
        ),
    ),
    "large": Slot(
        gettext_lazy("Large logo"),
        gettext_lazy("a large logo"),
        ("PNG", "JPEG", "WEBP"),
        5 * MB,
        gettext_lazy("PNG, JPEG or WebP, at least 256 pixels, up to 5 MB."),
        gettext_lazy(
            "The full-size logo, kept up to 1,024 pixels. It is the source the "
            "other sizes are made from when they have no image of their own. "
            "Use at least 512 pixels on the longer side; a PNG with a "
            "transparent background works best. Refused when over 5 MB, over "
            "16 megapixels, animated, or under 256 pixels on the longer side."
        ),
    ),
}
_FORMAT_NAMES = {"PNG": "PNG", "JPEG": "JPEG", "WEBP": "WebP"}


def file_size(value):
    """A byte count as people read it ("3.2 MB", "300 KB"), rounded up, so a
    file just over a limit never reads as the limit itself."""
    if value >= MB:
        return f"{math.ceil(value * 10 / MB) / 10:g} MB"
    return f"{math.ceil(value / KB):,} KB"


def _shape_problem(name, width, height):
    """The slot's dimension rule this displayed size breaks, or None."""
    longer, shorter = max(width, height), min(width, height)
    square = longer <= shorter * SQUARE
    noun = SLOTS[name].noun
    if name == "large" and longer < 256:
        return _("the longer side must be at least 256 pixels")
    if name == "menu":
        if longer > 2048:
            return _("neither side of %(noun)s may be over 2,048 pixels") % {
                "noun": noun
            }
        if width > 4 * height:
            return _("%(noun)s can be at most 4 times as wide as it is tall") % {
                "noun": noun
            }
        if height > width:
            return _("%(noun)s can't be taller than it is wide") % {"noun": noun}
        if height < 64:
            return _("%(noun)s must be at least 64 pixels tall") % {"noun": noun}
    if name in {"icon", "favicon"} and not square:
        return _("%(noun)s must be square (within 10%%)") % {"noun": noun}
    if name == "icon" and shorter < 128:
        return _("%(noun)s must be at least 128 pixels on each side") % {"noun": noun}
    if name == "favicon" and shorter < 16:
        return _("%(noun)s must be at least 16 pixels on each side") % {"noun": noun}
    if name == "favicon" and longer > 512:
        return _("%(noun)s can be at most 512 pixels on a side") % {"noun": noun}
    return None


def check_slot(name, data):
    """Apply one slot's rules to an upload's bytes, then normalize it.

    Returns the slot's normalized ``Graphic``. Raises ``ValidationError``
    whose message names the slot, the rule broken and what the file was, in
    the order a reader would fix them: the file's size, whether it is a
    readable image of an accepted format, animation and pixel count, then
    its shape.
    """
    slot = SLOTS[name]
    title = {"slot": slot.title}
    if not 0 < len(data) <= slot.max_bytes:
        raise forms.ValidationError(
            _("%(slot)s: the file is %(size)s; the limit is %(limit)s.")
            % {
                **title,
                "size": file_size(len(data)),
                "limit": file_size(slot.max_bytes),
            },
            code="too_large",
        )
    formats = _("PNG, JPEG or WebP") if len(slot.formats) > 1 else "PNG"
    try:
        facts = image_facts(data)
    except ValueError:
        raise forms.ValidationError(
            _("%(slot)s: the file is not a %(formats)s image that can be read.")
            % {**title, "formats": formats},
            code="unreadable_image",
        ) from None
    if facts.format not in slot.formats:
        raise forms.ValidationError(
            _(
                "%(slot)s: the file is a %(found)s image; "
                "%(noun)s must be a %(formats)s."
            )
            % {
                **title,
                "found": _FORMAT_NAMES.get(facts.format, facts.format),
                "noun": slot.noun,
                "formats": formats,
            },
            code="wrong_format",
        )
    if facts.frames != 1:
        raise forms.ValidationError(
            _("%(slot)s: the image is animated; choose a single still image.") % title,
            code="animated",
        )
    measured = {**title, "width": f"{facts.width:,}", "height": f"{facts.height:,}"}
    if facts.width * facts.height > MAX_IMAGE_PIXELS:
        raise forms.ValidationError(
            _(
                "%(slot)s: the image is %(width)s × %(height)s pixels; the limit is "
                "16 megapixels."
            )
            % measured,
            code="too_many_pixels",
        )
    problem = _shape_problem(name, facts.width, facts.height)
    if problem:
        raise forms.ValidationError(
            _("%(slot)s: the image is %(width)s × %(height)s pixels; %(rule)s.")
            % {**measured, "rule": problem},
            code="wrong_shape",
        )
    try:
        return prepare_slot_graphic(data, name, VARIANTS[name])
    except ValueError:
        raise forms.ValidationError(
            _("%(slot)s: the file is not a %(formats)s image that can be read.")
            % {**title, "formats": formats},
            code="unreadable_image",
        ) from None


class MissingSlot(Exception):
    """A size left empty that has no current image to keep and no large
    logo uploaded with it to generate one from; ``slot`` names it."""

    def __init__(self, slot):
        super().__init__(slot)
        self.slot = slot


def missing_message(slot):
    """The refusal shown at a size that can be neither kept nor generated."""
    return _(
        "%(slot)s: there is no current image to keep. Choose an image for it, "
        "or upload a large logo to generate it from."
    ) % {"slot": SLOTS[slot].title}


def _chosen(parish):
    """The current Parish's logo asset id for each size, in ``SLOTS`` order."""
    return {
        "favicon": parish.favicon_id,
        "icon": parish.icon_logo_id,
        "menu": parish.menu_logo_id,
        "large": parish.large_logo_id,
    }


def current_assets(parish):
    """The current Parish's logo asset rows, keyed by asset id.

    Parish logos draws these (in slot order, ``branding_views.logo_frames``)
    and ``pk-admin branding show`` prints them (in slot order). The record
    holds plain ids, not foreign keys, so a size whose row is missing is
    simply absent.
    """
    return BrandingAsset.objects.in_bulk(list(_chosen(parish).values()))


def _current(parish, media_root, labels):
    """The current logo's normalized bytes for each size in ``labels`` that
    has one stored and readable, keyed by size.

    A configuration made before logos were stored as files, a bundle that is
    not ready, or a file that no longer matches its receipt has none for
    that size, which is then treated as missing (generated from the large
    logo, or refused) rather than failing the whole upload.
    """
    chosen = _chosen(parish)
    result = {}
    for asset in BrandingAsset.objects.filter(
        pk__in=[chosen[label] for label in labels], bundle__state="ready"
    ):
        if asset.label not in labels or chosen[asset.label] != asset.pk:
            continue
        try:
            data = read_variant(media_root, asset.bundle_id, file_receipt(asset))
        except (ConfigError, ValueError):
            continue
        result[asset.label] = Graphic(
            f"{uuid4().hex}.png", "image/png", data, asset.width, asset.height
        )
    return result


def complete_bundle(uploaded, parish, media_root):
    """All four sizes for a new bundle from the sizes uploaded separately.

    ``uploaded`` maps each chosen size to its normalized ``Graphic``. Every
    other size keeps the current image (only those sizes are read), or is
    generated from the uploaded large logo when none is stored and readable;
    raises ``MissingSlot`` for the first size that can be neither. The
    caller must have checked the form's base version against ``parish``'s
    configuration first.
    """
    current = _current(parish, media_root, set(VARIANTS) - set(uploaded))
    result = {}
    for name, size in VARIANTS.items():
        if name in uploaded:
            result[name] = uploaded[name]
        elif name in current:
            result[name] = current[name]
        elif "large" in uploaded:
            result[name] = prepare_slot_graphic(uploaded["large"].data, name, size)
        else:
            raise MissingSlot(name)
    return result


def changed_sizes(assets, parish):
    """The staged sizes whose image differs from the current one, by label.

    A size kept from the current logo is copied byte for byte, so its digest
    matches; one with no stored current image always counts as changed.
    """
    current = dict(
        BrandingAsset.objects.filter(pk__in=_chosen(parish).values()).values_list(
            "label", "sha256"
        )
    )
    return {asset.label for asset in assets if current.get(asset.label) != asset.sha256}

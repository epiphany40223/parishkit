"""``artwork show``: the Campaign images page's read (ADM-11 PR 10).

Campaign images (``admin:artwork_settings``, #248, #750) holds the current
campaign's banner and Family page icons. The command reads it through the
page's own request-free pieces, so the page and the command line cannot
drift: the applied configuration (``admin_editing.editable_configuration``),
the page's current-campaign check (``artwork_views._campaign``), its
current-image query (``artwork_views.current_images``) and its upload
availability check (``artwork_views.unavailable``).

It admits passively with the page's ``CONFIGURE`` capability (so
Administrators only, and a read-only session reads), reads in one read
transaction, rechecks the session before reporting, and records nothing, as
the page's GET records nothing. The images are the campaign's own theme
artwork, never a Family's. ``artwork remove`` (a change) is still owed by
PR 10; uploads stay in the browser.
"""

from dataclasses import dataclass
from uuid import UUID

from .admin_reads import NotAvailable, ReadModel
from .admin_settings import _read


@dataclass(frozen=True)
class ArtworkShow(ReadModel):
    """The current campaign's images, as Campaign images draws them.

    ``images`` has one entry per slot in the page's order (``banner``,
    ``welcome``, ``member``, ``financial``, ``closing``): ``slot``, ``name``
    (the page's label), ``unavailable`` (None, or the page's reason why the
    slot cannot take an upload) and ``image`` (None, or the asset's ``id``,
    ``width``, ``height``, ``size`` in bytes and ``sha256``).
    """

    version: str
    campaign_id: UUID
    images: list


def artwork_model(version, campaign, images):
    """``artwork show``'s document from the page's pieces.

    ``images`` is ``artwork_views.current_images(campaign)``, keyed by slot;
    a slot without a current image (or whose asset row is missing, which the
    page also draws as empty) prints ``image`` None.
    """
    from .accounts.artwork_views import SLOT_LABELS, unavailable
    from .campaigns.configuration import ARTWORK_SLOTS

    rows = []
    for slot in ARTWORK_SLOTS:
        asset = images.get(slot)
        reason = unavailable(campaign, slot)
        rows.append(
            {
                "slot": slot,
                "name": str(SLOT_LABELS[slot]),
                "unavailable": None if reason is None else str(reason),
                "image": None
                if asset is None
                else {
                    "id": asset.pk,
                    "width": asset.width,
                    "height": asset.height,
                    "size": asset.size,
                    "sha256": asset.sha256,
                },
            }
        )
    return ArtworkShow(version=version, campaign_id=campaign.pk, images=rows)


def read_artwork(caller, service):
    """``artwork show``: the current campaign's Campaign images.

    No current campaign is the page's ``LookupError`` (404), so
    ``not_available``.
    """
    from .accounts.admin_editing import editable_configuration
    from .accounts.artwork_views import _campaign, current_images

    def read():
        """The page's campaign check and current images, without its refusal."""
        configuration = editable_configuration(service)
        try:
            campaign = _campaign(configuration, configuration.current_campaign_id)
        except LookupError:
            raise NotAvailable("There is no current campaign.") from None
        return artwork_model(
            configuration.active_configuration.digest,
            campaign,
            current_images(campaign),
        )

    return _read(caller, service, read)

"""Campaign images commands (ADM-11 PR 10): ``artwork show`` and removal.

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
artwork, never a Family's. Uploads stay in the browser.

``artwork remove-preview`` and ``artwork remove`` are the page's **Remove
this image** and its in-place **Apply** (#750), as ``parish preview`` and
``parish confirm`` are Parish settings' Review and Apply:

- ``artwork remove-preview`` admits as the page does (recording activity,
  so it needs a full-scope session) and builds the page's own removal
  review (``artwork_views.review_removal``) in one read snapshot, printing
  the image being removed and ``preview.token``, the same signed binding
  the page's Apply posts. It changes nothing and records no event, as the
  page's review records none.
- ``artwork remove`` takes that token (or one the page signed) and records
  the configuration request exactly as the page's Apply does, through
  ``admin_editing.confirm_intent`` with the page's salt (bound to the
  current campaign and the slot) and scope, with one
  ``admin_cmd_artwork_remove`` event in the request's own durable
  transaction. A repeat returns the original request and records nothing.

Neither is fresh-gated or prompts, as the page asks for no recent sign-in
and shows no confirmation dialog: the reviewed token is the confirmation,
and a removal deletes nothing (the image stays in the activated
configurations' history; a new upload fills the slot again).
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
        reason = unavailable(campaign, slot)
        rows.append(
            {
                "slot": slot,
                "name": str(SLOT_LABELS[slot]),
                "unavailable": None if reason is None else str(reason),
                "image": image_fields(images.get(slot)),
            }
        )
    return ArtworkShow(version=version, campaign_id=campaign.pk, images=rows)


def image_fields(asset):
    """An image's stored facts, as ``artwork show`` prints them, or None."""
    if asset is None:
        return None
    return {
        "id": asset.pk,
        "width": asset.width,
        "height": asset.height,
        "size": asset.size,
        "sha256": asset.sha256,
    }


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


@dataclass(frozen=True)
class ArtworkRemovePreview(ReadModel):
    """The page's review of removing one slot's image, and its token.

    ``version`` is the applied configuration's digest the removal was
    reviewed against; ``image`` the image being removed (as ``artwork
    show`` prints it, or None when its asset row is missing).
    ``preview.token`` is the signed binding ``artwork remove`` takes, valid
    for fifteen minutes.
    """

    version: str
    campaign_id: UUID
    slot: str
    name: str
    image: dict | None
    preview: dict


@dataclass(frozen=True)
class ArtworkRemove(ReadModel):
    """The configuration request a removal recorded, as its status reads.

    ``created`` is false when this token's request already existed (a
    repeated removal), which then returns the original request unchanged.
    """

    created: bool
    request: dict


def _current_campaign(configuration):
    """The page's current-campaign check; none is ``not_available``."""
    from .accounts.artwork_views import _campaign

    try:
        return _campaign(configuration, configuration.current_campaign_id)
    except LookupError:
        raise NotAvailable("There is no current campaign.") from None


def preview_removal(caller, service, slot, *, expected_version):
    """``artwork remove-preview``: the page's review of removing ``slot``'s image.

    Admits as the page does (``CONFIGURE``, recording activity), then, in
    one read snapshot, checks the version (another one is
    ``stale_version``) and builds the page's own removal review. A slot
    with no image is ``not_available`` (the page's 404); a resulting
    document the configuration refuses is ``invalid``. Rechecks access
    afterwards. Changes nothing.
    """
    from parishkit.config import ConfigError

    from .accounts.admin_editing import editable_configuration
    from .accounts.artwork_views import SLOT_LABELS, current_images, review_removal
    from .accounts.policy import Capability
    from .admin_changes import _admit_change, _ended_or_raise
    from .admin_reads import _held, _recheck
    from .campaigns.work_locks import read_transaction
    from .storage import StaleRecordError

    actor = _admit_change(caller, service)

    def step():
        """Check the version and review the removal in one snapshot."""
        with read_transaction():
            configuration = editable_configuration(service)
            campaign = _current_campaign(configuration)
            if configuration.active_configuration.digest != expected_version:
                raise StaleRecordError("The configuration changed since then.")
            try:
                token = review_removal(service, actor, configuration, campaign, slot)
            except LookupError:
                raise NotAvailable("That slot has no image to remove.") from None
            except ConfigError:
                # Raised past _held, which would read it as unavailable: the
                # configuration refuses the resulting document, as the page's
                # review would.
                raise ValueError("The configuration refuses this removal.") from None
            asset = current_images(campaign).get(slot)
            return ArtworkRemovePreview(
                version=expected_version,
                campaign_id=campaign.pk,
                slot=slot,
                name=str(SLOT_LABELS[slot]),
                image=image_fields(asset),
                preview={"token": token},
            )

    try:
        model = _held(step)
    except PermissionError as error:
        _ended_or_raise(caller, service, actor, error)
    _recheck(caller, service.store, actor, Capability.CONFIGURE)
    return model


def remove_image(caller, service, slot, *, token, context):
    """``artwork remove``: the page's **Apply** for a reviewed removal.

    Admits as the page does, then ``confirm_intent`` checks the token with
    the page's salt for the current campaign and ``slot`` (expired or out of
    date is ``stale_version``; altered, another slot's or another page's
    ``invalid``; another Administrator's ``denied``) and records the request
    with ``admin_cmd_artwork_remove`` in its own durable transaction. The
    configuration must be editable first (``_held``), so a restore under
    review or an activating change is ``unavailable``.
    ``context["committed"]`` and ``context["request_id"]`` follow ``parish
    confirm``'s rules, so a later error is ``outcome_unknown`` naming the
    request.
    """
    from django.core import signing
    from django.db import DatabaseError

    from .accounts.admin_editing import confirm_intent, editable_configuration
    from .accounts.artwork_views import _salt, _scope
    from .accounts.configuration_requests import policy_operation_id
    from .admin_changes import _admit_change, _ended_or_raise
    from .admin_reads import _held, config_request
    from .audit.schemas import Action, ActorKind, Outcome
    from .audit.services import record_action
    from .observability import _guard_refusal

    actor = _admit_change(caller, service)
    change = ("remove", slot, None)
    configuration = _held(lambda: editable_configuration(service))
    if configuration.current_campaign_id is None:
        raise NotAvailable("There is no current campaign.")
    salt = _salt(configuration.current_campaign_id, change)
    try:
        # The request's id is derived from the signed key, before intake;
        # confirm_intent verifies the token properly (expiry included).
        key = UUID(signing.loads(token, salt=salt)["key"])
    except (signing.BadSignature, KeyError, TypeError, ValueError):
        key = None
    if key is not None:
        context["request_id"] = str(policy_operation_id(actor.identity, key))
    created = []

    def attach(request, extra):
        """Record the command's event with the request it created."""
        record_action(
            Action.ADMIN_CMD_ARTWORK_REMOVE,
            actor_kind=ActorKind.PORTAL_USER,
            actor_id=actor.identity,
            subject_id=caller.automation_session_id,
            context={"outcome": Outcome.SUCCEEDED},
        )
        created.append(request.pk)

    try:
        receipt = confirm_intent(
            caller,
            service,
            actor,
            token=token,
            salt=salt,
            link=None,
            # A removal's scope needs no request: only the editable
            # configuration (a staged upload's needs the browser's sign-in).
            current_scope=_scope(None, change),
            attach=attach,
        )
    except signing.BadSignature:
        # Altered, another slot's or another page's (an expired one is stale).
        raise ValueError("The preview token is not valid.") from None
    except PermissionError as error:
        _ended_or_raise(caller, service, actor, error)
    except DatabaseError as error:
        if created and not _guard_refusal(error):
            context["committed"] = True
        raise
    context["committed"] = True
    return ArtworkRemove(
        created=bool(created), request=config_request(receipt).to_document()
    )

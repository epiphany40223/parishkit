"""``parish show``, ``ministries list``, ``branding show``, ``campaign shares``.

The read-only halves of four configuration pages (ADM-11 PR 10), each
through the page's own request-free pieces, so the page and the command
line cannot drift:

- ``parish show`` is Parish settings (``admin:parish_settings``): the
  profile its editor fills (``parish_views._profile`` and its defaults);
- ``ministries list`` is Ministries (``admin:ministries``): the page's
  catalog (``ministry_views._state``), every row rather than one page of it;
- ``branding show`` is Parish logos (``admin:branding_settings``): the
  current logos the page draws, from the applied parish record;
- ``campaign shares`` is Share options (``admin:share_settings``): the
  campaign's options and the page's own editability check
  (``campaign_views._state`` and ``_target``). Unlike the page, which is only
  an editor and refuses to open for a locked campaign, it reads a locked or
  live campaign too, with ``editable`` false.

Each admits passively with the pages' ``CONFIGURE`` capability (so
Administrators only, and a read-only session reads), reads in one read
transaction, rechecks the session before reporting, and records nothing, as
the pages' GETs record nothing. Each prints ``version``, the applied
configuration's digest, the base a later change is previewed against. The
values are the parish's and the campaign's own, never a Family's.
"""

from dataclasses import dataclass
from uuid import UUID

from .admin_reads import NotAvailable, ReadModel, _admit, _held, _recheck


@dataclass(frozen=True)
class ParishShow(ReadModel):
    """The parish profile as Parish settings fills its editor.

    ``parish`` holds ``name``, ``website``, ``timezone``, ``phone`` (stored
    E.164), ``online_giving_url`` (None when unset) and ``date_format``.
    """

    version: str
    parish: dict


@dataclass(frozen=True)
class MinistryList(ReadModel):
    """The Ministries page's catalog, every row, in the page's order.

    ``campaign_id`` is the current campaign (None without one);
    ``snapshot_id`` the promoted ParishSoft snapshot the catalog comes from;
    ``ministries`` each Ministry's ``duid`` (a Ministry's, never a
    Family's), ``name`` and ``included`` (in the current campaign). There
    is no ``active``: the local Ministry activity is gone (#1267).
    """

    version: str
    campaign_id: UUID | None
    snapshot_id: UUID
    ministries: list


@dataclass(frozen=True)
class BrandingShow(ReadModel):
    """The current parish logos, as Parish logos draws them.

    ``logos`` has one entry per slot, in the order ``large``, ``menu``,
    ``icon``, ``favicon``: ``slot``, ``id``, ``width``, ``height``, ``size``
    (bytes) and ``sha256``.
    """

    version: str
    logos: list


@dataclass(frozen=True)
class CampaignShares(ReadModel):
    """One campaign's Share options and whether the page would edit them.

    ``editable`` is the page's own check (an unlocked current Testing draft
    that background work does not hold, with the financial module);
    ``financial`` whether the campaign has the financial module; ``options``
    each option's ``id``, ``name`` (the page's label), ``short_name`` (its
    short label, empty when unset, #1569) and ``free_text``, in order.
    """

    version: str
    campaign_id: UUID
    editable: bool
    financial: bool
    options: list


def parish_model(configuration):
    """``parish show``'s document from the applied configuration."""
    from .accounts.parish_views import DEFAULTS, PROFILE_FIELDS, _profile

    record = _profile(configuration)["values"]
    return ParishShow(
        version=configuration.active_configuration.digest,
        parish={name: record.get(name, DEFAULTS.get(name)) for name in PROFILE_FIELDS},
    )


def ministry_model(state):
    """``ministries list``'s document from ``ministry_views._state``."""
    configuration, current, catalog = state
    return MinistryList(
        version=configuration.active_configuration.digest,
        campaign_id=configuration.current_campaign_id,
        snapshot_id=current.snapshot_id,
        ministries=[
            {key: row[key] for key in ("duid", "name", "included")} for row in catalog
        ],
    )


def shares_model(configuration, campaign, editable):
    """``campaign shares``'s document from the page's ``_target``.

    The options are the page's own list (``share_views.EDITOR.current``).
    """
    from .accounts.share_views import EDITOR

    financial = EDITOR.module in campaign.active_configuration.values["modules"]
    return CampaignShares(
        version=configuration.active_configuration.digest,
        campaign_id=campaign.pk,
        editable=editable and financial,
        financial=financial,
        options=[
            {
                "id": option["id"],
                "name": option["label"],
                "short_name": option.get("short_label", ""),
                "free_text": option["free_text"],
            }
            for option in EDITOR.current(campaign)
        ],
    )


def _read(caller, service, read):
    """Admit, run ``read()`` in one read snapshot, then recheck.

    A ``NotAvailable`` from the read is reported only after the session
    recheck, so an ended session is exit 5 rather than a refusal.
    """
    from .accounts.policy import Capability
    from .campaigns.work_locks import read_transaction

    def step():
        """One attempt, repeated by ``_held`` while a change activates."""
        actor = _admit(caller, service.store, Capability.CONFIGURE)
        try:
            with read_transaction():
                model = read()
        except NotAvailable:
            _recheck(caller, service.store, actor, Capability.CONFIGURE)
            raise
        _recheck(caller, service.store, actor, Capability.CONFIGURE)
        return model

    return _held(step)


def read_parish(caller, service):
    """``parish show``: the Parish settings page's profile."""
    from .accounts.admin_editing import editable_configuration

    return _read(caller, service, lambda: parish_model(editable_configuration(service)))


def read_ministries(caller, service):
    """``ministries list``: the Ministries page's catalog.

    No promoted catalog for the configured organization is the page's
    ``ConfigError`` (503), so ``unavailable``.
    """
    from .accounts.ministry_views import _state

    return _read(caller, service, lambda: ministry_model(_state(service)))


def branding_model(version, parish, assets):
    """``branding show``'s document: the parish record's logos, in slot order.

    ``assets`` is ``branding_slots.current_assets(parish)``, the rows the
    page draws, keyed by id. The record holds plain ids, not foreign keys,
    so as on the page a slot whose asset row is missing is left out.
    """
    from .accounts.branding_slots import _chosen

    logos = []
    for slot, asset_id in _chosen(parish).items():
        asset = assets.get(asset_id)
        if asset is not None:
            logos.append(
                {
                    "slot": slot,
                    "id": asset.pk,
                    "width": asset.width,
                    "height": asset.height,
                    "size": asset.size,
                    "sha256": asset.sha256,
                }
            )
    return BrandingShow(version=version, logos=logos)


def read_branding(caller, service):
    """``branding show``: the current logos Parish logos draws."""
    from .accounts.admin_editing import editable_configuration
    from .accounts.branding_slots import current_assets

    def read():
        """The applied parish record's logos, through the page's own read."""
        configuration = editable_configuration(service)
        parish = configuration.active_configuration.parish
        return branding_model(
            configuration.active_configuration.digest, parish, current_assets(parish)
        )

    return _read(caller, service, read)


def read_shares(caller, service, campaign_id):
    """``campaign shares``: one campaign's Share options, the current by default.

    An unknown campaign, or no current campaign, is ``not_available``.
    """
    from .accounts.campaign_views import _state, _target

    def read():
        """The page's campaign state and target check, without its refusal."""
        configuration, campaigns, _, held, _ = _state(service)
        target = campaign_id or configuration.current_campaign_id
        if target is None:
            raise NotAvailable("There is no current campaign.")
        try:
            campaign, editable = _target(configuration, campaigns, held, target)
        except KeyError:
            # A KeyError is a bug, never "no such campaign": internal.
            raise
        except LookupError:
            raise NotAvailable("No such campaign.") from None
        return shares_model(configuration, campaign, editable)

    return _read(caller, service, read)

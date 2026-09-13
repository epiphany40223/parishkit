"""Reconcile campaign identity from the exact, newly promoted source generation.

The caller owns source promotion and suppression admission in one transaction.
This service sends no mail and does not replace the later invitation evaluator.
Provider suppression is an explicit input, never inferred from publishability
or from a Family's proposed census email preference.
"""

from django.db import connection

from parishkit.config import ConfigError
from parishkit.stewardship.accounts.policy_schema import normalized_email
from parishkit.stewardship.campaigns.family_identity import (
    FamilyStatus,
    reconcile_families,
)
from parishkit.stewardship.jobs.admission import require_source_refresh
from parishkit.stewardship.storage import StorageInvariantError

from .canonical import InvalidSourcePayload
from .leases import verify_source
from .requests import _organization
from .snapshot_models import SourceCurrent
from .snapshots import read_snapshot
from .version_models import ENTITY_MODELS


def family_statuses(corpus, *, suppressed_addresses):
    """Use only valid active-head addresses; one unsuppressed address suffices.

    Suppression owners supply a transactionally current, canonical set. Requiring
    it explicitly prevents a future caller from accidentally ignoring bounces.
    Malformed/legacy payloads fail rather than silently removing Family access.
    """
    if not isinstance(suppressed_addresses, frozenset):
        raise TypeError("Family reconciliation requires an explicit suppression set.")
    try:
        if any(normalized_email(value) != value for value in suppressed_addresses):
            raise ConfigError("Noncanonical suppression address.")
    except (ConfigError, TypeError):
        raise ValueError("Suppression addresses must be canonical emails.") from None
    try:
        return tuple(
            _status(key, family, corpus, suppressed_addresses)
            for key, family in sorted(corpus["family"].items())
        )
    except (KeyError, TypeError, ValueError, ConfigError):
        raise InvalidSourcePayload(
            "Source Family eligibility is inconsistent."
        ) from None


def _status(key, family, corpus, suppressed):
    """Cross-check normalized eligibility against the same snapshot's head contacts."""
    if str(int(key)) != key or not 1 <= int(key) < 2**31:
        raise ValueError("Invalid Family identity.")
    if (
        type(family["schema_version"]) is not int
        or family["schema_version"] != 1
        or any(
            type(family[field]) is not bool
            for field in ("active", "parishioner", "portal_eligible", "email_eligible")
        )
    ):
        raise ValueError("Invalid Family schema.")
    heads = family["active_head_duids"]
    if (
        type(heads) is not list
        or any(type(head) is not int or not 1 <= head < 2**31 for head in heads)
        or len(set(heads)) != len(heads)
    ):
        raise ValueError("Invalid Family heads.")
    addresses = set()
    for head in heads:
        member = corpus["member"][str(head)]
        if (
            type(member["schema_version"]) is not int
            or member["schema_version"] != 1
            or member["family_key"] != key
            or member["active"] is not True
        ):
            raise ValueError("Invalid Family head relationship.")
        contact = corpus["contact"].get(f"member:{head}")
        if contact is not None:
            if (
                type(contact["schema_version"]) is not int
                or contact["schema_version"] != 1
                or contact["owner_kind"] != "member"
                or contact["owner_key"] != str(head)
            ):
                raise ValueError("Invalid Family head contact.")
            for email in contact["emails"]:
                if type(email["valid"]) is not bool:
                    raise ValueError("Invalid email eligibility.")
                if email["valid"]:
                    address = normalized_email(email["value"])
                    if address != email["value"]:
                        raise ValueError("Noncanonical head address.")
                    addresses.add(address)
    eligible = family["active"] and family["parishioner"]
    email_eligible = eligible and bool(addresses)
    if (
        family["portal_eligible"] != eligible
        or family["email_eligible"] != email_eligible
    ):
        raise ValueError("Contradictory source eligibility.")
    deliverable = email_eligible and bool(addresses - suppressed)
    return FamilyStatus(
        int(key),
        family["active"],
        eligible,
        email_eligible,
        deliverable,
        "inactive"
        if not family["active"]
        else "eligible"
        if eligible
        else "non_parishioner",
        "ineligible"
        if not eligible
        else "no_eligible_email"
        if not email_eligible
        else "deliverable"
        if deliverable
        else "provider_suppressed",
    )


def reconcile_source_families(
    snapshot_id,
    claim,
    *,
    campaign_id,
    general,
    mac,
    public,
    suppressed_addresses,
    admit,
):
    """Apply Family effects inside promotion, retaining its fence and exact input.

    Admission receives the locked Campaign and must check the current immutable
    refresh request. The promotion owner subsequently applies other domain
    effects before returning True to promote_snapshot; any failure rolls back
    this population as well as the source pointer. Key rotation uses a try-lock
    and therefore cannot wait behind a writer while this holds source locks.
    """
    if not connection.in_atomic_block or connection.vendor != "postgresql":
        raise StorageInvariantError("Source Family effects require outer promotion.")
    scope = require_source_refresh(campaign_id=campaign_id)
    if scope.campaign is None or scope.campaign.state == "archived":
        raise PermissionError("Historical campaigns cannot receive new Family effects.")
    organization_id = _organization(scope)
    verify_source(claim)
    current = SourceCurrent.objects.select_for_update().get(singleton=True)
    if current.snapshot_id != snapshot_id or claim.phase not in {"full", "delta"}:
        raise StorageInvariantError("Family effects require the current refresh.")
    with read_snapshot(snapshot_id) as snapshot:
        if snapshot.organization_id != organization_id:
            raise PermissionError("Family effects require the configured organization.")
        if (snapshot.task_id, snapshot.source_fence, snapshot.generation) != (
            claim.task_id,
            claim.fence,
            current.generation,
        ):
            raise StorageInvariantError(
                "Family effects belong to another source owner."
            )
        corpus = {
            kind: {
                row.source_key: row.payload.payload
                for row in ENTITY_MODELS[kind][1]
                .objects.filter(snapshot=snapshot)
                .select_related("payload")
                .iterator(chunk_size=500)
            }
            for kind in ("family", "member", "contact")
        }
        result = reconcile_families(
            campaign_id=campaign_id,
            source_snapshot_id=snapshot.pk,
            source_generation=snapshot.generation,
            statuses=family_statuses(corpus, suppressed_addresses=suppressed_addresses),
            general=general,
            mac=mac,
            public=public,
            admit=admit,
            actor_id=claim.worker_id,
        )
        verify_source(claim)
        return result

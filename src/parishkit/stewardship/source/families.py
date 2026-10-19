"""Reconcile campaign identity from the exact, newly promoted source generation.

The caller owns source promotion and suppression admission in one transaction.
This service sends no mail and does not replace the later invitation evaluator.
Provider suppression is an explicit input, never inferred from publishability
or from a Family's proposed census email preference.
"""

import json
from dataclasses import dataclass, field

from django.db import connection, transaction

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
from .snapshot_models import SourceCurrent, SourceSnapshot
from .snapshots import read_snapshot
from .version_models import ENTITY_MODELS


@dataclass(frozen=True)
class FamilySuppressions:
    """Family-scoped canonical refusals; a shared email never crosses households."""

    entries: frozenset[tuple[int, str]] = field(repr=False)

    def __post_init__(self):
        """Reject mutable, coerced or noncanonical refusal identities."""
        if type(self.entries) is not frozenset:
            raise TypeError("Family suppressions require immutable entries.")
        try:
            for entry in self.entries:
                if (
                    type(entry) is not tuple
                    or len(entry) != 2
                    or type(entry[0]) is not int
                    or not 1 <= entry[0] < 2**31
                    or normalized_email(entry[1]) != entry[1]
                ):
                    raise ValueError
        except (ConfigError, TypeError, ValueError):
            raise ValueError(
                "Family suppressions require canonical identities."
            ) from None


@dataclass(frozen=True)
class FamilyRecipients:
    """One source Family's status and deterministic, private head-address sets.

    These are source facts, not permission to send. The delivery owner must
    recheck campaign, response, suppression and credential scope before dispatch.
    Keep addresses out of repr so ordinary diagnostic context cannot expose PII.
    """

    status: FamilyStatus
    eligible: tuple[str, ...] = field(repr=False)
    deliverable: tuple[str, ...] = field(repr=False)


def family_statuses(corpus, *, suppressed_addresses):
    """Derive identity and mail status from the same recipient projection."""
    return tuple(
        row.status
        for row in family_recipients(corpus, suppressed_addresses=suppressed_addresses)
    )


def family_recipients(corpus, *, suppressed_addresses):
    """Use only valid active-head addresses; one unsuppressed address suffices.

    Suppression owners supply a transactionally current, canonical set. Requiring
    it explicitly prevents a future caller from accidentally ignoring bounces.
    Malformed/legacy payloads fail rather than silently removing Family access.
    """
    return _suppressed(family_eligibility(corpus), suppressed_addresses)


@dataclass(frozen=True)
class FamilyEligibility:
    """One Family's corpus-only facts: everything but provider suppression.

    ``addresses`` are the canonical valid addresses of its active heads. Only
    ``_suppressed`` turns these into a status, so the same suppression rule
    applies whether they were derived inside promotion or ahead of it.
    """

    key: str
    active: bool
    eligible: bool
    addresses: frozenset[str] = field(repr=False)


def family_eligibility(corpus):
    """Each Family's corpus-only eligibility, in Family key order.

    Malformed or contradictory payloads raise InvalidSourcePayload.
    """
    try:
        return tuple(
            _eligibility(key, family, corpus)
            for key, family in sorted(corpus["family"].items())
        )
    except (KeyError, TypeError, ValueError, ConfigError):
        raise InvalidSourcePayload(
            "Source Family eligibility is inconsistent."
        ) from None


def _suppressed(eligibility, suppressed_addresses):
    """Apply one canonical suppression set to precomputed Family eligibility."""
    if not isinstance(suppressed_addresses, (frozenset, FamilySuppressions)):
        raise TypeError("Family reconciliation requires an explicit suppression set.")
    by_family = None
    if isinstance(suppressed_addresses, FamilySuppressions):
        by_family = {}
        for duid, address in suppressed_addresses.entries:
            by_family.setdefault(str(duid), set()).add(address)
        suppressed_addresses = frozenset()
    try:
        if any(normalized_email(value) != value for value in suppressed_addresses):
            raise ConfigError("Noncanonical suppression address.")
    except (ConfigError, TypeError):
        raise ValueError("Suppression addresses must be canonical emails.") from None
    try:
        return tuple(
            _recipients(
                item,
                by_family.get(item.key, frozenset())
                if by_family is not None
                else suppressed_addresses,
            )
            for item in eligibility
        )
    except (TypeError, ValueError):
        raise InvalidSourcePayload(
            "Source Family eligibility is inconsistent."
        ) from None


def _eligibility(key, family, corpus):
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
    return FamilyEligibility(key, family["active"], eligible, frozenset(addresses))


def _recipients(item, suppressed):
    """One Family's status and head addresses once ``suppressed`` is applied."""
    email_eligible = item.eligible and bool(item.addresses)
    deliverable = email_eligible and bool(item.addresses - suppressed)
    status = FamilyStatus(
        int(item.key),
        item.active,
        item.eligible,
        email_eligible,
        deliverable,
        "inactive"
        if not item.active
        else "eligible"
        if item.eligible
        else "non_parishioner",
        "ineligible"
        if not item.eligible
        else "no_eligible_email"
        if not email_eligible
        else "deliverable"
        if deliverable
        else "provider_suppressed",
    )
    return FamilyRecipients(
        status,
        tuple(sorted(item.addresses)) if email_eligible else (),
        tuple(sorted(item.addresses - suppressed)) if email_eligible else (),
    )


# The collections Family effects read: statuses, proposals and Ministry requests.
FAMILY_KINDS = ("family", "member", "contact", "ministry", "roster")


def _decode(snapshot):
    """Decode ``snapshot``'s Family collections, under the caller's row lock.

    Each payload's canonical text is decoded directly (what SourceVersion's
    payload property does) instead of building two ORM models per row: for
    about 25,000 rows, model construction cost more than the query (#147).
    """
    return {
        kind: {
            key: json.loads(canonical)
            for key, canonical in ENTITY_MODELS[kind][1]
            .objects.filter(snapshot=snapshot)
            .values_list("source_key", "payload__canonical")
            .iterator(chunk_size=500)
        }
        for kind in FAMILY_KINDS
    }


@dataclass(frozen=True)
class PreparedPromotion:
    """A snapshot's decoded Family corpus and corpus-only eligibility (#895).

    Both are pure functions of the snapshot's membership, which cannot change
    once it is ready, so they may be derived before promotion takes the work
    order. Provider suppression is not part of it: ``statuses`` applies the
    set promotion reads under the lock.
    """

    snapshot_id: object
    corpus: dict = field(repr=False)
    eligibility: tuple[FamilyEligibility, ...] = field(repr=False)

    @classmethod
    def of(cls, snapshot):
        """Decode and check ``snapshot``, whose row the caller holds locked."""
        corpus = _decode(snapshot)
        return cls(snapshot.pk, corpus, family_eligibility(corpus))

    def statuses(self, suppressed_addresses):
        """Each Family's status once ``suppressed_addresses`` is applied."""
        return tuple(
            row.status for row in _suppressed(self.eligibility, suppressed_addresses)
        )


def prepare_promotion(snapshot_id, claim):
    """Derive promotion's read-only Family inputs before the work order (#895).

    Runs in the caller's source step, outside the global work-order lock. It
    reads only this claim's own ready snapshot, its row held ``FOR SHARE``.
    A ready snapshot's membership is immutable in SQL (inserts only while
    staging, no updates), its payload versions are content-addressed, and
    deletes happen only to compacted or rejected snapshots under the source
    lease this attempt holds. So these are exactly the rows promotion would
    decode. Returns None, and promotion decodes under the lock as before,
    when the snapshot is not this claim's ready snapshot or its corpus is
    inconsistent (promotion then raises the same error as it always did).
    """
    with transaction.atomic():
        verify_source(claim)
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT id FROM stewardship_source_snapshot WHERE id=%s "
                "AND state='ready' AND task_id=%s AND source_fence=%s FOR SHARE",
                (snapshot_id, claim.task_id, claim.fence),
            )
            if cursor.fetchone() is None:
                return None
        snapshot = SourceSnapshot.objects.only("id").get(pk=snapshot_id)
        try:
            return PreparedPromotion.of(snapshot)
        except InvalidSourcePayload:
            return None


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
    prepared=None,
):
    """Apply Family effects inside promotion, retaining its fence and exact input.

    Admission receives the locked Campaign and must check the current immutable
    refresh request. The promotion owner subsequently applies other domain
    effects before returning True to promote_snapshot; any failure rolls back
    this population as well as the source pointer. Key rotation uses a try-lock
    and therefore cannot wait behind a writer while this holds source locks.

    ``prepared`` is ``prepare_promotion``'s result, computed before the work
    order. It is used only when it is for this very snapshot; otherwise the
    corpus is decoded and checked here, as it always was. Either way the
    suppressions passed in, read under the lock, decide deliverability.
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
        if prepared is None or prepared.snapshot_id != snapshot.pk:
            prepared = PreparedPromotion.of(snapshot)
        corpus = prepared.corpus
        result = reconcile_families(
            campaign_id=campaign_id,
            source_snapshot_id=snapshot.pk,
            source_generation=snapshot.generation,
            statuses=prepared.statuses(suppressed_addresses),
            general=general,
            mac=mac,
            public=public,
            admit=admit,
            actor_id=claim.worker_id,
        )
        from parishkit.stewardship.responses.added_members import (
            link_added_members,
        )
        from parishkit.stewardship.responses.ministry_reconciliation import (
            reconcile_ministry_requests,
        )
        from parishkit.stewardship.responses.reconciliation import reconcile_proposals

        reconcile_proposals(snapshot, corpus, campaign_id=campaign_id)
        reconcile_ministry_requests(snapshot, corpus, campaign_id=campaign_id)
        # A Member the Family added, now in ParishSoft (#1670): the snapshot
        # is current, so its evidence is the one the link's guard checks.
        link_added_members(snapshot, campaign_id=campaign_id)
        verify_source(claim)
        return result

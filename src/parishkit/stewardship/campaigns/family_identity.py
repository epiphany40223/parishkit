"""Atomic READ COMMITTED Family reconciliation and set-based cross-key issuance."""

import hashlib
import re
from dataclasses import dataclass
from functools import partial
from uuid import UUID, uuid4

from django.db import IntegrityError, connection, transaction
from django.db.models import Exists, OuterRef, Q

from parishkit.stewardship.accounts.cryptography import (
    CryptographicError,
    TokenPublicKeyring,
    new_code,
)
from parishkit.stewardship.observability import current_correlation
from parishkit.stewardship.storage import StorageInvariantError

from .credential_keys import key_set_lock
from .credential_models import (
    CampaignCredentialState,
    FamilyAccessToken,
    FamilyCampaign,
    FamilyCodeFingerprint,
)
from .models import Campaign
from .runtime import _now


@dataclass(frozen=True)
class FamilyStatus:
    """Source-promotion input, not browser-supplied Family or eligibility data."""

    duid: int
    active: bool
    portal_eligible: bool
    email_eligible: bool
    email_deliverable: bool
    status_reason: str = "eligible"
    deliverability_reason: str = "deliverable"

    def __post_init__(self):
        if type(self.duid) is not int or not 1 <= self.duid <= 2**63 - 1:
            raise ValueError("A valid Family DUID is required.")
        if any(
            type(getattr(self, key)) is not bool
            for key in (
                "active",
                "portal_eligible",
                "email_eligible",
                "email_deliverable",
            )
        ):
            raise ValueError("Family status values must be booleans.")
        if (self.portal_eligible and not self.active) or (
            self.email_deliverable and not self.email_eligible
        ):
            raise ValueError("Family status is inconsistent.")
        if any(
            type(value) is not str
            or not re.fullmatch(r"[a-z][a-z_]{0,47}", value)
            or value == "population_pending"
            for value in (self.status_reason, self.deliverability_reason)
        ):
            raise ValueError("Family status reasons must be bounded identifiers.")


def code_context(identifier):
    """General ciphertext is bound to one stable FamilyCampaign UUID."""
    if not isinstance(identifier, UUID):
        raise TypeError("Family identity must be a UUID.")
    return b"family-display-code-v1:" + identifier.bytes


def collision_query(digests):
    """A single set-based lookup covers all accepted key versions for the batch."""
    query = Q(pk__in=[])
    for key_id, values in digests.items():
        query |= Q(key_id=key_id, digest__in=values)
    return query


def _write_families(rows, fields):
    """Write ``fields`` of locked FamilyCampaign rows with set-based UPDATEs.

    This is Django's ``bulk_update`` without its per-row ``CASE WHEN`` per
    field: building those expressions in Python took most of a source
    promotion at parish scale (about 2,700 Families), and promotion holds the
    global work-order lock, stalling every Family and Admin request (#147).
    Each batch is one ``UPDATE ... FROM unnest(...)`` of the same columns and
    values, so every row and statement trigger still fires. Values pass through
    each field's own database preparation, keeping its write guards (for
    example, UTCDateTimeField's refusal of naive instants).
    """
    columns = [FamilyCampaign._meta.get_field(name) for name in fields]
    names = ",".join(field.column for field in columns)
    arrays = ",".join(f"%s::{field.cast_db_type(connection)}[]" for field in columns)
    assignments = ",".join(f"{field.column}=v.{field.column}" for field in columns)
    statement = (
        f"UPDATE {FamilyCampaign._meta.db_table} f SET {assignments} "
        f"FROM unnest(%s::uuid[],{arrays}) AS v(id,{names}) WHERE f.id=v.id"
    )
    for start in range(0, len(rows), 1000):
        batch = rows[start : start + 1000]
        values = [[row.pk for row in batch]] + [
            [
                field.get_db_prep_save(getattr(row, field.attname), connection)
                for row in batch
            ]
            for field in columns
        ]
        with connection.cursor() as cursor:
            cursor.execute(statement, values)
            if cursor.rowcount != len(batch):
                raise StorageInvariantError(
                    "Family identity rows changed unexpectedly."
                )


def _allocate(campaign_id, rows, general, mac):
    """At most eight batch-level retries; never a savepoint per Family."""
    pending = list(rows)
    for _ in range(8):
        if not pending:
            return
        proposals, seen = [], set()
        for row in pending:
            code = new_code()
            if code not in seen:
                seen.add(code)
                proposals.append((row, code, mac.lookups(campaign_id, code)))
        digests = {
            key: [item[2][key] for item in proposals]
            for key in mac.lookups(campaign_id, "AAAAAAAA")
        }
        occupied = set(
            FamilyCodeFingerprint.objects.filter(campaign_id=campaign_id)
            .filter(collision_query(digests))
            .values_list("key_id", "digest")
        )
        accepted = [
            item
            for item in proposals
            if not any(pair in occupied for pair in item[2].items())
        ]
        try:
            with transaction.atomic():
                for row, code, _ in accepted:
                    row.code_ciphertext = general.encrypt(
                        code.encode("ascii"), context=code_context(row.pk)
                    )
                # Identities exist first, still inside the caller's atomic source
                # promotion. They become eligible only after code assignment.
                _write_families(
                    [item[0] for item in accepted],
                    ["code_ciphertext", "version", "actor_id", "correlation_id"],
                )
                FamilyCodeFingerprint.objects.bulk_create(
                    [
                        FamilyCodeFingerprint(
                            family=row,
                            campaign_id=campaign_id,
                            key_id=key_id,
                            digest=digest,
                        )
                        for row, _, fingerprints in accepted
                        for key_id, digest in fingerprints.items()
                    ]
                )
        except IntegrityError:
            # A surprising cross-process constraint race retries the whole batch.
            continue
        assigned = {item[0].pk for item in accepted}
        pending = [row for row in pending if row.pk not in assigned]
    raise CryptographicError("Family-code allocation exhausted its bounded retries.")


# The status columns reconciliation copies from each FamilyStatus.
STATUS_FIELDS = (
    "active",
    "portal_eligible",
    "email_eligible",
    "email_deliverable",
    "status_reason",
    "deliverability_reason",
)


def absent_status(duid):
    """The status of a Family the source no longer lists."""
    return FamilyStatus(duid, False, False, False, False, "absent", "ineligible")


def _row_current(row, status, source_generation):
    """Whether reconciliation at ``source_generation`` leaves ``row`` alone.

    ``row`` is a FamilyCampaign or a mapping of its columns. The one predicate
    shared by ``reconcile_families`` and ``population_current``.
    """
    value = row.get if isinstance(row, dict) else partial(getattr, row)
    return (
        source_generation == value("source_generation")
        and all(value(key) == getattr(status, key) for key in STATUS_FIELDS)
        and (not status.portal_eligible or value("first_eligible_at") is not None)
    )


def population_current(campaign, *, source_snapshot_id, source_generation, statuses):
    """Whether ``reconcile_families`` would write nothing for these statuses.

    Used before skipping the promotion of a quick update whose corpus equals
    the current one (#630): every Family row already carries the current
    generation and exactly these statuses (so a new mail bounce, which
    changes a status, still promotes), no eligible Family lacks its code or,
    under an active token generation, its link, and the population evidence
    is clean and names the current snapshot. Read-only, under the caller's
    work-order lock; any doubt answers False, which promotes as before.
    """
    population = CampaignCredentialState.objects.filter(campaign=campaign).first()
    if (
        population is None
        or population.population_dirty
        or population.source_snapshot_id != source_snapshot_id
        or population.source_generation != source_generation
    ):
        return False
    targets = {item.duid: item for item in statuses}
    rows = {
        row["family_duid"]: row
        for row in FamilyCampaign.objects.filter(campaign=campaign).values(
            "family_duid",
            *STATUS_FIELDS,
            "source_generation",
            "first_eligible_at",
            "code_ciphertext",
        )
    }
    if targets.keys() - rows.keys():
        return False
    for duid, row in rows.items():
        status = targets.get(duid) or absent_status(duid)
        if not _row_current(row, status, source_generation) or (
            duid in targets
            and status.portal_eligible
            and row["code_ciphertext"] is None
        ):
            return False
    if campaign.active_token_generation_id is not None:
        tokens = FamilyAccessToken.objects.filter(
            generation_id=campaign.active_token_generation_id, family_id=OuterRef("pk")
        )
        if (
            FamilyCampaign.objects.filter(campaign=campaign, portal_eligible=True)
            .filter(~Exists(tokens))
            .exists()
        ):
            return False
    return True


def reconcile_families(
    *,
    campaign_id,
    source_snapshot_id,
    source_generation,
    statuses,
    general,
    mac,
    admit,
    public=None,
    actor_id=None,
):
    """Caller owns an atomic source promotion; no partial corpus can become visible.

    DAT-03 supplies the validated promoted generation and admission callback,
    which must return exactly True under its owning authorization locks.
    Only this campaign-specific identity overlay is stored here, not source rows.
    """
    if not connection.in_atomic_block or connection.vendor != "postgresql":
        raise StorageInvariantError(
            "Family reconciliation requires an outer PostgreSQL transaction."
        )
    with connection.cursor() as cursor:
        cursor.execute("SHOW transaction_isolation")
        if cursor.fetchone()[0] != "read committed":
            raise StorageInvariantError(
                "Family reconciliation requires READ COMMITTED."
            )
    if (
        type(source_generation) is not int
        or not 1 <= source_generation <= 2**63 - 1
        or not callable(admit)
    ):
        raise ValueError(
            "A promoted source generation and owning admission are required."
        )
    if not isinstance(source_snapshot_id, UUID) or not isinstance(campaign_id, UUID):
        raise TypeError("Campaign and source snapshot identities must be UUIDs.")
    if actor_id is not None and not isinstance(actor_id, UUID):
        raise TypeError("Source promotion actor must be a UUID or system identity.")
    correlation_id = current_correlation()
    statuses = tuple(statuses)
    if any(not isinstance(item, FamilyStatus) for item in statuses) or len(
        {item.duid for item in statuses}
    ) != len(statuses):
        raise ValueError("Family reconciliation requires unique validated statuses.")
    with key_set_lock(general, mac):
        # Follow lifecycle/restore order before locking the population or any
        # Family row. Token preparation/rotation takes deployment before those
        # same rows; reversing that order can deadlock a source promotion.
        with connection.cursor() as cursor:
            cursor.execute("SELECT id FROM stewardship_system_configuration FOR SHARE")
            cursor.execute("SELECT id FROM stewardship_credential_deployment FOR SHARE")
        campaign = Campaign.objects.select_for_update().get(pk=campaign_id)
        if admit(campaign) is not True:
            raise PermissionError("Family reconciliation is not admitted.")
        if campaign.active_token_generation_id is not None and not isinstance(
            public, TokenPublicKeyring
        ):
            raise CryptographicError("Active token issuance requires its public ring.")
        CampaignCredentialState.objects.get_or_create(campaign=campaign)
        population = CampaignCredentialState.objects.select_for_update().get(
            campaign=campaign
        )
        if (
            population.source_generation is not None
            and source_generation < population.source_generation
        ):
            raise StorageInvariantError(
                "A stale source generation cannot replace Family population."
            )
        # Both bulk updates below write an in-memory row.version + 1. Family
        # activity (authenticated_family) bumps the same version outside the
        # work order, so an unlocked read let a concurrent bump commit first
        # and the version guard (23514) roll back the whole promotion. Lock
        # now, after Campaign and population, in pk order. NO KEY UPDATE is
        # the mode those UPDATEs already take, so this only moves the same
        # row locks earlier; activity locks one row and nothing after it.
        # Activity holds its FamilySession row while it waits here, so the
        # rest of this promotion must never lock or update a FamilySession.
        existing = {
            row.family_duid: row
            for row in FamilyCampaign.objects.filter(campaign=campaign)
            .select_for_update(no_key=True)
            .order_by("pk")
        }
        now = _now()
        new = []
        for status in statuses:
            if status.duid not in existing:
                row = FamilyCampaign(
                    id=uuid4(),
                    campaign=campaign,
                    family_duid=status.duid,
                    active=False,
                    portal_eligible=False,
                    email_eligible=False,
                    email_deliverable=False,
                    status_reason="population_pending",
                    deliverability_reason="population_pending",
                    source_generation=source_generation,
                    eligibility_changed_at=now,
                    actor_id=actor_id,
                    correlation_id=correlation_id,
                )
                existing[status.duid] = row
                new.append(row)
        FamilyCampaign.objects.bulk_create(new)
        targets = {item.duid: item for item in statuses}
        missing = [
            row
            for duid, row in existing.items()
            if duid in targets
            and targets[duid].portal_eligible
            and row.code_ciphertext is None
        ]
        for row in missing:
            row.version += 1
            row.actor_id, row.correlation_id = actor_id, correlation_id
        # Limit statement/candidate memory while preserving one surrounding commit.
        for start in range(0, len(missing), 500):
            _allocate(campaign_id, missing[start : start + 500], general, mac)
        changed = []
        fields = STATUS_FIELDS
        for duid, row in existing.items():
            status = targets.get(duid) or absent_status(duid)
            if source_generation < row.source_generation:
                raise StorageInvariantError(
                    "A stale source generation cannot replace Family identity."
                )
            if _row_current(row, status, source_generation):
                continue
            if any(getattr(row, key) != getattr(status, key) for key in fields):
                row.eligibility_changed_at = now
            for key in fields:
                setattr(row, key, getattr(status, key))
            if status.portal_eligible and row.first_eligible_at is None:
                row.first_eligible_at = now
                row.first_eligible_source_generation = source_generation
            row.source_generation = source_generation
            row.version += 1
            row.actor_id, row.correlation_id = actor_id, correlation_id
            changed.append(row)
        _write_families(
            changed,
            [
                *fields,
                "eligibility_changed_at",
                "first_eligible_at",
                "first_eligible_source_generation",
                "source_generation",
                "version",
                "actor_id",
                "correlation_id",
            ],
        )
        eligible = (
            FamilyCampaign.objects.filter(campaign=campaign, portal_eligible=True)
            .order_by("family_duid")
            .values_list("id", flat=True)
        )
        identifiers = tuple(eligible)
        digest = hashlib.sha256(
            b"family-token-coverage-v1\x00"
            + b"".join(item.bytes for item in identifiers)
        ).hexdigest()
        if population.source_generation == source_generation and (
            population.eligibility_digest != digest
            or population.source_snapshot_id != source_snapshot_id
        ):
            raise StorageInvariantError(
                "An existing source generation cannot change population identity."
            )
        population.source_snapshot_id = source_snapshot_id
        population.source_generation = source_generation
        population.eligibility_digest, population.eligible_count = (
            digest,
            len(identifiers),
        )
        population.refresh_from_db(fields=["version"])
        population.population_dirty = False
        population.version += 1
        # Source refresh owns population evidence, never go-live/restore gates.
        # Explicit columns let its SQL login retain that same boundary.
        population.save(
            update_fields=[
                "source_snapshot_id",
                "source_generation",
                "eligibility_digest",
                "eligible_count",
                "population_dirty",
                "version",
            ]
        )
        if campaign.active_token_generation_id is not None:
            from .link_tokens import extend_active_generation

            extend_active_generation(campaign, public=public)
        return len(changed)

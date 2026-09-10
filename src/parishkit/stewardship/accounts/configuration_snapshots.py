"""Database preparation of strict non-secret configuration snapshots.

Internal storage only, not an authorized web API or a complete Materializer.
There is deliberately no activate method: DAT-01's request/runtime records and
ARC-02/ARC-06 must install their atomic activation/audit effects first. Prepared
rows alone neither make the application ready nor grant anyone access.
"""

import hashlib
import json
from uuid import UUID

from django.db import connection, transaction
from django.db.models import prefetch_related_objects

from parishkit.config import ConfigError
from parishkit.stewardship.observability import correlation
from parishkit.stewardship.storage import StorageInvariantError

from .authority import ConfigurationVersion, parse_version
from .configuration_models import (
    AppliedConfigurationVersion,
    AppliedIntegration,
    Parish,
)
from .configuration_schema import (
    schema_for,
    validate_sections,
    validator_for,
)


def _normalized(document, schema=None):
    """Extract just the projections, retaining deterministic authoritative IDs."""
    sections = document["sections"]
    names = ("parish", "integrations")
    selected = schema or schema_for(document)
    if selected in {"foundation-policy-v2", "campaign-foundation-v3"}:
        names += ("login_rules",)
    if selected == "campaign-foundation-v3":
        names += ("campaigns", "schedules")
    return {name: sections.get(name, []) for name in names}


def _digest(value):
    """Digest the exact normalized values independently of envelope metadata."""
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()


def _stored_projections(snapshot):
    """Reconstruct YAML-shaped records from the actual persisted projection rows."""
    parish = snapshot.parish
    result = {
        "parish": [
            {
                "id": str(parish.record_id),
                "values": {
                    "name": parish.name,
                    "website": parish.website,
                    "timezone": parish.timezone,
                    "phone": parish.phone,
                    "branding": {
                        "large": str(parish.large_logo_id),
                        "menu": str(parish.menu_logo_id),
                        "icon": str(parish.icon_logo_id),
                        "favicon": str(parish.favicon_id),
                    },
                },
            }
        ],
        "integrations": [
            {
                "id": str(row.record_id),
                "values": {
                    "kind": row.kind,
                    "settings": row.settings,
                    "credential_fingerprint": row.credential_fingerprint,
                },
            }
            for row in sorted(
                snapshot.integrations.all(), key=lambda item: str(item.record_id)
            )
        ],
    }
    if snapshot.validation_schema in {"foundation-policy-v2", "campaign-foundation-v3"}:
        from .policy_projections import stored_policy

        result["login_rules"] = stored_policy(snapshot)
    if snapshot.validation_schema == "campaign-foundation-v3":
        from parishkit.stewardship.campaigns.projections import stored_campaigns

        result.update(stored_campaigns(snapshot))
    return result


def _history(snapshot):
    """Walk immutable ancestry iteratively, rejecting cycles without recursion.

    No fixed depth limit may strand legitimate long-lived autosave history. This
    storage verification is linear in history, not a per-request readiness API.
    """
    seen = set()
    while snapshot is not None:
        if snapshot.pk in seen:
            raise ConfigError("Configuration history contains a cycle.")
        seen.add(snapshot.pk)
        yield snapshot
        snapshot = snapshot.predecessor


def _load_history(digest):
    """Load an immutable lineage with bounded query count, independent of depth.

    UNION deduplicates identity pairs, so even a forged cycle terminates in SQL;
    the Python verifier then explicitly rejects it. Projection prefetches avoid
    per-version round trips. Only internal SQL identifiers are interpolated.
    """
    table = connection.ops.quote_name(AppliedConfigurationVersion._meta.db_table)
    rows = list(
        AppliedConfigurationVersion.objects.raw(
            f"""WITH RECURSIVE chain(id, predecessor_id) AS (
            SELECT id, predecessor_id FROM {table} WHERE digest = %s
            UNION
            SELECT parent.id, parent.predecessor_id FROM {table} parent
            JOIN chain child ON parent.id = child.predecessor_id
        ) SELECT entry.* FROM {table} entry JOIN chain USING (id)""",
            [digest],
        )
    )
    if not rows:
        return None
    by_id = {row.pk: row for row in rows}
    for row in rows:
        if row.predecessor_id is not None and row.predecessor_id not in by_id:
            return None
        # Populate the ordinary FK cache without lazy per-ancestor SELECTs.
        row.predecessor = by_id.get(row.predecessor_id)
    prefetch_related_objects(rows, "parish", "integrations")
    policy_rows = [
        row
        for row in rows
        if row.validation_schema in {"foundation-policy-v2", "campaign-foundation-v3"}
    ]
    prefetch_related_objects(
        policy_rows,
        "domainrule_set",
        "addressrule_set__grants",
        "ministryassignment_set",
    )
    campaign_rows = [
        row for row in rows if row.validation_schema == "campaign-foundation-v3"
    ]
    prefetch_related_objects(
        campaign_rows,
        "campaign_configurations",
        "schedule_revisions",
    )
    if campaign_rows:
        from parishkit.stewardship.campaigns.projections import sql_boundaries_match

        if not sql_boundaries_match([row.pk for row in campaign_rows]):
            return None
    return next(row for row in rows if row.digest == digest)


def _remember_integrations(document, by_kind, by_id):
    """Reject identity replacement/reuse, including after removal and re-addition."""
    for record in document["sections"].get("integrations", []):
        kind, identifier = record["values"]["kind"], record["id"]
        if (
            by_kind.setdefault(kind, identifier) != identifier
            or by_id.setdefault(identifier, kind) != kind
        ):
            raise ConfigError(
                "Integration identities must remain stable across history."
            )


def verified_snapshot_version(snapshot, *, predecessor_digest):
    """Verify one canonical snapshot and its actual projections, not its ancestry.

    Intake uses this bounded check, while preparation still verifies every
    ancestor through _verify_history. Callers load the predecessor's digest only
    and may prefetch the two projections; no hidden recursive reads occur here.
    Installation/database errors propagate distinctly from invalid content.
    """
    try:
        version = parse_version(
            snapshot.canonical_document,
            validate_sections=validator_for(snapshot.validation_schema),
        )
        if (
            version.version_id == snapshot.pk
            and version.digest == snapshot.digest
            and version.predecessor_digest == predecessor_digest
            and snapshot.schema_version == 1
            and _digest(_normalized(version.document(), snapshot.validation_schema))
            == snapshot.normalized_digest
            and _digest(_stored_projections(snapshot)) == snapshot.normalized_digest
        ):
            return version
    except Parish.DoesNotExist:
        pass
    raise ConfigError("Configuration snapshot is incomplete or invalid.")


def _verify_history(snapshot, candidate=None):
    """Verify loaded rows without further I/O, optionally admitting a successor."""
    if snapshot is None:
        return False
    try:
        parish_id = str(snapshot.parish.record_id)
        by_kind, by_id, policy_ids = {}, {}, {}
        newer_policy = None
        if candidate is not None:
            if candidate["sections"]["parish"][0]["id"] != parish_id:
                return False
            _remember_integrations(candidate, by_kind, by_id)
            newer_policy = _remember_policy(candidate, policy_ids)
        for entry in _history(snapshot):
            predecessor = entry.predecessor.digest if entry.predecessor_id else None
            version = verified_snapshot_version(entry, predecessor_digest=predecessor)
            if str(entry.parish.record_id) != parish_id:
                return False
            _remember_integrations(version.document(), by_kind, by_id)
            has_policy = _remember_policy(version.document(), policy_ids)
            if newer_policy is False and has_policy:
                return False
            newer_policy = has_policy
        return True
    except (ConfigError, Parish.DoesNotExist):
        return False


def _remember_policy(document, identities):
    """Keep a retired UUID's immutable identity/provenance across the entire chain."""
    records = document["sections"].get("login_rules", [])
    frozen = {
        "domain": ("domain",),
        "address": ("email", "creation_origin", "creation_operation"),
        "assignment": ("email", "ministry_duid", "source", "operation_id"),
    }
    for record in records:
        values = record["values"]
        identity = (values["kind"], *(values[name] for name in frozen[values["kind"]]))
        if identities.setdefault(record["id"], identity) != identity:
            raise ConfigError(
                "Policy record identities must remain stable across history."
            )
    return bool(records)


def is_prepared(digest):
    """Revalidate canonical bytes and every projection; absence is not readiness.

    Database availability errors propagate to the caller's fail-closed readiness
    boundary. Malformed stored content returns False without exposing its values.
    """
    return _verify_history(_load_history(digest))


def prepare_snapshot(version, *, actor_id, correlation_id):
    """Prepare one candidate atomically and idempotently on PostgreSQL.

    All cooperating preparations serialize on a dedicated transaction advisory
    lock, including first insertion. This is not the session-level installer lock
    across file activation. It enforces one root and stable parish identity while
    allowing competing candidates from an existing predecessor; the future
    installer must still reject a stale base before changing the active manifest.
    """
    if not isinstance(version, ConfigurationVersion):
        raise TypeError("An explicit configuration version is required.")
    if not isinstance(correlation_id, UUID) or (
        actor_id is not None and not isinstance(actor_id, UUID)
    ):
        raise TypeError("Actor and correlation identifiers must be UUIDs.")
    validated = parse_version(version.document(), validate_sections=validate_sections)
    if validated != version:
        raise ConfigError("Configuration metadata does not match its document.")
    if connection.in_atomic_block or not connection.get_autocommit():
        raise StorageInvariantError("Snapshot preparation must own its transaction.")
    document = version.document()
    # Verification is intentionally outside the global preparation lock. History
    # is immutable; cooperating writers only append complete new versions. The
    # lock protects the bounded publication step, not history-length parsing.
    existing = AppliedConfigurationVersion.objects.filter(pk=version.version_id).first()
    if existing is not None:
        if existing.digest != version.digest or not is_prepared(version.digest):
            raise ConfigError("Cannot replace an immutable configuration snapshot.")
        return existing
    predecessor = None
    if version.predecessor_digest is not None:
        predecessor = _load_history(version.predecessor_digest)
        if not _verify_history(predecessor, candidate=document):
            raise ConfigError(
                "Configuration predecessor or stable parish identity is invalid."
            )
    with correlation(correlation_id), transaction.atomic(durable=True):
        with connection.cursor() as cursor:
            # Stable, internal namespace; never derive this key from user input.
            cursor.execute("SELECT pg_advisory_xact_lock(%s, %s)", [736210, 1])
        existing = AppliedConfigurationVersion.objects.filter(
            pk=version.version_id
        ).first()
        if existing is not None:
            # A concurrent exact preparation may have won after our preflight.
            # Compare its complete local data to our already-verified candidate;
            # do not redo the ancestry walk while holding the global lock.
            if (
                existing.digest != version.digest
                or existing.canonical_document != document
                or existing.predecessor_id != (predecessor.pk if predecessor else None)
                or existing.normalized_digest != _digest(_normalized(document))
            ):
                raise ConfigError("Cannot replace an immutable configuration snapshot.")
            try:
                if _digest(_stored_projections(existing)) != existing.normalized_digest:
                    raise ConfigError("Configuration projections are incomplete.")
            except Parish.DoesNotExist:
                raise ConfigError("Configuration projections are incomplete.") from None
            return existing
        if predecessor is None and AppliedConfigurationVersion.objects.exists():
            raise ConfigError("A configuration root already exists.")
        parish_record = document["sections"]["parish"][0]
        attribution = {"actor_id": actor_id, "correlation_id": correlation_id}
        snapshot = AppliedConfigurationVersion.objects.create(
            id=version.version_id,
            digest=version.digest,
            schema_version=1,
            predecessor=predecessor,
            canonical_document=document,
            normalized_digest=_digest(_normalized(document)),
            validation_schema=schema_for(document),
            **attribution,
        )
        values = parish_record["values"]
        Parish.objects.create(
            configuration=snapshot,
            record_id=parish_record["id"],
            name=values["name"],
            website=values["website"],
            timezone=values["timezone"],
            phone=values["phone"],
            large_logo_id=values["branding"]["large"],
            menu_logo_id=values["branding"]["menu"],
            icon_logo_id=values["branding"]["icon"],
            favicon_id=values["branding"]["favicon"],
            **attribution,
        )
        for record in document["sections"].get("integrations", []):
            AppliedIntegration.objects.create(
                configuration=snapshot,
                record_id=record["id"],
                **record["values"],
                **attribution,
            )
        from .policy_projections import prepare_policy

        prepare_policy(
            snapshot, document["sections"].get("login_rules", []), attribution
        )
        from parishkit.stewardship.campaigns.projections import prepare_campaigns

        prepare_campaigns(snapshot, document, attribution)
        return snapshot

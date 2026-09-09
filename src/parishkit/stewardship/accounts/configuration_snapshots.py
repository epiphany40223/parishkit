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
from .configuration_schema import VALIDATION_SCHEMA, validate_sections, validator_for


def _normalized(document):
    """Extract just the projections, retaining deterministic authoritative IDs."""
    sections = document["sections"]
    return {name: sections.get(name, []) for name in ("parish", "integrations")}


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
    return {
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
    """Load an entire immutable lineage in three queries, independent of depth.

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


def _verify_history(snapshot, candidate=None):
    """Verify loaded rows without further I/O, optionally admitting a successor."""
    if snapshot is None:
        return False
    try:
        parish_id = str(snapshot.parish.record_id)
        by_kind, by_id = {}, {}
        if candidate is not None:
            if candidate["sections"]["parish"][0]["id"] != parish_id:
                return False
            _remember_integrations(candidate, by_kind, by_id)
        for entry in _history(snapshot):
            version = parse_version(
                entry.canonical_document,
                validate_sections=validator_for(entry.validation_schema),
            )
            predecessor = entry.predecessor.digest if entry.predecessor_id else None
            if not (
                version.version_id == entry.pk
                and version.digest == entry.digest
                and version.predecessor_digest == predecessor
                and entry.schema_version == 1
                and str(entry.parish.record_id) == parish_id
                and _digest(_normalized(version.document())) == entry.normalized_digest
                and _digest(_stored_projections(entry)) == entry.normalized_digest
            ):
                return False
            _remember_integrations(version.document(), by_kind, by_id)
        return True
    except (ConfigError, Parish.DoesNotExist):
        return False


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
            validation_schema=VALIDATION_SCHEMA,
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
        return snapshot

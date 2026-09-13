"""Fenced source staging and atomic publication of one coherent normalized corpus.

These are internal worker services, not HTTP endpoints. Admission and source-
promotion effects are explicit callbacks so setup, campaign gates and Family/
policy reconciliation participate in the same caller-owned transactions. No
provider call or mutable cache is part of publication.
"""

import hashlib
import json
from contextlib import contextmanager

from django.db import connection, transaction

from parishkit.stewardship.audit.schemas import Action, ActorKind, Outcome
from parishkit.stewardship.audit.services import record_action
from parishkit.stewardship.storage import StorageInvariantError

from .canonical import InvalidSourcePayload, canonical_payload
from .leases import _now, verify_source
from .snapshot_models import SourceCurrent, SourceSnapshot
from .version_models import ENTITY_MODELS

RELATIONSHIPS = {
    "member": {"family_key": "family"},
    "roster": {"member_key": "member", "ministry_key": "ministry"},
    "pledge": {"family_key": "family", "fund_key": "fund"},
    "contribution": {"family_key": "family", "fund_key": "fund"},
}


def _admit(admit, action, snapshot):
    """Require an explicit positive owning-workflow decision, including on retries."""
    if admit(action, snapshot) is not True:
        raise PermissionError("Source workflow admission was denied.")


def _current():
    """Do not manufacture the current pointer after missing schema/bootstrap state."""
    try:
        return SourceCurrent.objects.select_for_update().get(singleton=True)
    except SourceCurrent.DoesNotExist:
        raise StorageInvariantError(
            "Source current pointer is not initialized."
        ) from None


def begin_snapshot(claim, *, organization_id, admit):
    """Bind a staging manifest to the exact current base and live source owner."""
    if type(organization_id) is not int or not 1 <= organization_id < 2**63:
        raise InvalidSourcePayload("A positive source organization is required.")
    if claim.phase not in {"full", "delta"}:
        raise InvalidSourcePayload(
            "Only refresh ownership can stage a source snapshot."
        )
    with transaction.atomic():
        verify_source(claim)
        current = _current()
        if current.organization_id not in (None, organization_id):
            raise InvalidSourcePayload(
                "Source organization differs from current truth."
            )
        if claim.phase == "delta" and current.snapshot_id is None:
            raise InvalidSourcePayload(
                "Delta staging requires a complete current corpus."
            )
        _admit(admit, "stage", None)
        return SourceSnapshot.objects.create(
            organization_id=organization_id,
            kind=claim.phase,
            task_id=claim.task_id,
            source_fence=claim.fence,
            base_id=current.snapshot_id,
            started_at=_now(),
            actor_id=claim.worker_id,
        )


def _staging(snapshot_id, claim, admit):
    """Lock staged ownership after the common task/source fence lock order."""
    verify_source(claim)
    snapshot = SourceSnapshot.objects.select_for_update().get(pk=snapshot_id)
    if snapshot.task_id != claim.task_id or snapshot.source_fence != claim.fence:
        raise StorageInvariantError("Snapshot belongs to another source owner.")
    if snapshot.state != "staging":
        raise InvalidSourcePayload("Snapshot is no longer accepting staged entities.")
    _admit(admit, "stage", snapshot)
    return snapshot


def stage_entities(snapshot_id, claim, *, kind, entities, admit):
    """Write a bounded idempotent batch, reusing immutable identity/digest versions.

    Input maps stable string source keys to already-normalized entity payloads.
    A retry with identical content is harmless; changed content under the same
    snapshot identity is rejected. The loader must explicitly start a new
    snapshot after such an inconsistent provider read, not overwrite staging.
    """
    if kind not in ENTITY_MODELS or type(entities) is not dict or len(entities) > 500:
        raise InvalidSourcePayload(
            "Source staging requires a bounded known collection."
        )
    model, membership = ENTITY_MODELS[kind]
    normalized = {}
    for key, payload in entities.items():
        if (
            type(key) is not str
            or not 1 <= len(key) <= 200
            or any(ord(c) < 32 for c in key)
        ):
            raise InvalidSourcePayload("Source entity identity is invalid.")
        canonical, digest = canonical_payload(payload)
        fields = tuple(RELATIONSHIPS.get(kind, {}))
        if kind in {"contact", "address"}:
            fields = ("owner_kind", "owner_key")
        relations = {field: payload.get(field) for field in fields}
        if any(
            type(value) is not str or not 1 <= len(value) <= 200
            for value in relations.values()
        ):
            raise InvalidSourcePayload("Source relationship identity is invalid.")
        if kind in {"contact", "address"} and relations["owner_kind"] not in {
            "family",
            "member",
        }:
            raise InvalidSourcePayload("Source contact/address owner kind is invalid.")
        normalized[key] = (canonical, digest, relations)
    with transaction.atomic():
        snapshot = _staging(snapshot_id, claim, admit)
        model.objects.bulk_create(
            [
                model(
                    organization_id=snapshot.organization_id,
                    source_key=key,
                    canonical=text,
                    digest=digest,
                    **relations,
                )
                for key, (text, digest, relations) in normalized.items()
            ],
            ignore_conflicts=True,
            batch_size=250,
        )
        versions = {
            (row.source_key, row.digest): row
            for row in model.objects.filter(
                organization_id=snapshot.organization_id,
                source_key__in=normalized,
                digest__in=[item[1] for item in normalized.values()],
            )
        }
        existing = dict(
            membership.objects.filter(
                snapshot=snapshot, source_key__in=normalized
            ).values_list("source_key", "payload_id")
        )
        pending = []
        for key, (text, digest, _) in normalized.items():
            row = versions[(key, digest)]
            if row.canonical != text or (key in existing and existing[key] != row.pk):
                raise InvalidSourcePayload("Snapshot entity content is inconsistent.")
            if key not in existing:
                pending.append(
                    membership(snapshot=snapshot, source_key=key, payload=row)
                )
        membership.objects.bulk_create(pending, batch_size=250)
        return len(pending)


def snapshot_manifest(snapshot_id):
    """Return stable per-kind identity/digest metadata, never source field values."""
    return {
        kind: dict(
            membership.objects.filter(snapshot_id=snapshot_id).values_list(
                "source_key", "payload__digest"
            )
        )
        for kind, (_, membership) in ENTITY_MODELS.items()
    }


def _validate_relationships(snapshot_id, manifest):
    """Resolve every edge inside this snapshot, not mutable current data."""
    for kind, relations in RELATIONSHIPS.items():
        membership = ENTITY_MODELS[kind][1]
        rows = membership.objects.filter(snapshot_id=snapshot_id).values_list(
            *("payload__" + field for field in relations)
        )
        for row in rows.iterator(chunk_size=500):
            if any(
                key not in manifest[parent]
                for key, parent in zip(row, relations.values(), strict=True)
            ):
                raise InvalidSourcePayload(
                    "Source snapshot contains an unresolved relationship."
                )
    for kind in ("contact", "address"):
        rows = (
            ENTITY_MODELS[kind][1]
            .objects.filter(snapshot_id=snapshot_id)
            .values_list("payload__owner_kind", "payload__owner_key")
        )
        if any(
            key not in manifest[parent] for parent, key in rows.iterator(chunk_size=500)
        ):
            raise InvalidSourcePayload(
                "Source snapshot contains an unresolved contact owner."
            )


def finish_snapshot(snapshot_id, claim, *, expected_counts, cursor, admit):
    """Validate a complete corpus and freeze its exact count/digest/cursor evidence."""
    if type(expected_counts) is not dict or set(expected_counts) != set(ENTITY_MODELS):
        raise InvalidSourcePayload(
            "Every source collection requires a completeness count."
        )
    if any(type(value) is not int or value < 0 for value in expected_counts.values()):
        raise InvalidSourcePayload("Source counts must be nonnegative integers.")
    cursor_text, _ = canonical_payload(cursor)
    with transaction.atomic():
        snapshot = _staging(snapshot_id, claim, admit)
        manifest = snapshot_manifest(snapshot_id)
        counts = {kind: len(items) for kind, items in manifest.items()}
        if counts != expected_counts:
            raise InvalidSourcePayload(
                "Source staging counts differ from completeness evidence."
            )
        _validate_relationships(snapshot_id, manifest)
        text = json.dumps(
            manifest, sort_keys=True, ensure_ascii=False, separators=(",", ":")
        )
        snapshot.content_digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        snapshot.counts = counts
        snapshot.validation = {"schema": "source-corpus-v1", "complete": True}
        snapshot.cursor = json.loads(cursor_text)
        snapshot.completed_at = _now()
        snapshot.state = "ready"
        snapshot.version += 1
        snapshot.save()
        return snapshot


def promote_snapshot(snapshot_id, claim, *, admit, reconcile):
    """Advance source truth and all owning-domain effects in exactly one transaction.

    Reconciliation must return exactly True after applying the real Phase 2
    Family/policy effects. Failure rolls back the pointer and every derived
    change, leaving the validated staging corpus available for a safe retry.
    """
    with transaction.atomic():
        verify_source(claim)
        current = _current()
        snapshot = SourceSnapshot.objects.select_for_update().get(pk=snapshot_id)
        if snapshot.task_id != claim.task_id or snapshot.source_fence != claim.fence:
            raise StorageInvariantError("Snapshot belongs to another source owner.")
        _admit(admit, "promote", snapshot)
        if snapshot.state != "ready" or snapshot.base_id != current.snapshot_id:
            raise InvalidSourcePayload(
                "Source promotion requires a ready current-base snapshot."
            )
        snapshot.generation = current.generation + 1
        snapshot.promoted_at = _now()
        snapshot.state = "promoted"
        snapshot.version += 1
        snapshot.save()
        current.snapshot = snapshot
        current.organization_id = snapshot.organization_id
        current.generation = snapshot.generation
        current.version += 1
        current.save()
        if reconcile(snapshot) is not True:
            raise StorageInvariantError("Source reconciliation did not complete.")
        # Operational generation metadata does not pin the entire source corpus.
        # Content-dependent audit parents acquire a separate explicit input pin.
        record_action(
            Action.SOURCE_PROMOTED,
            actor_kind=ActorKind.SYSTEM,
            actor_id=claim.worker_id,
            subject_id=claim.task_id,
            context={
                "before_version": snapshot.generation - 1,
                "after_version": snapshot.generation,
                "count": sum(snapshot.counts.values()),
                "source_fingerprint": snapshot.content_digest,
                "outcome": Outcome.SUCCEEDED,
            },
        )
        # Long derived work cannot publish after its owner silently expires.
        verify_source(claim)
        return snapshot


@contextmanager
def read_snapshot(snapshot_id=None):
    """Keep a shared row lock through every lazy corpus query and serialization.

    Concurrent readers share this lock. Compaction and new protection references
    use the same snapshot row. A selector losing a race to compaction receives
    an explicit unavailable result, never a partial or substituted pinned corpus.
    """
    with transaction.atomic():
        if snapshot_id is None:
            snapshot_id = SourceCurrent.objects.get(singleton=True).snapshot_id
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT id FROM stewardship_source_snapshot WHERE id=%s "
                "AND state='promoted' AND compacted_at IS NULL FOR SHARE",
                (snapshot_id,),
            )
            if cursor.fetchone() is None:
                raise InvalidSourcePayload(
                    "The requested source corpus is unavailable."
                )
        yield SourceSnapshot.objects.get(pk=snapshot_id)


def reconstruct_snapshot(snapshot_id=None):
    """Materialize one complete corpus while its snapshot is protected from cleanup."""
    with read_snapshot(snapshot_id) as snapshot:
        return {
            kind: {
                row.source_key: row.payload.payload
                for row in membership.objects.filter(snapshot=snapshot)
                .select_related("payload")
                .iterator(chunk_size=500)
            }
            for kind, (_, membership) in ENTITY_MODELS.items()
        }

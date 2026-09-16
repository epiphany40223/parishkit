"""Closed SMTP evidence in the existing immutable, numbered outbox journal."""

import hashlib
import json

from parishkit.stewardship.accounts.integration_candidates import _object
from parishkit.stewardship.family_delivery import FamilyDeliveryResult

from .outbox_validation import DeliveryEvidence

PROTOCOL = "workspace_smtp_v1"


def result_evidence(result, *, semantic_key):
    """Retain only status and envelope indices, never SMTP responses or secrets."""
    if not isinstance(result, FamilyDeliveryResult):
        raise TypeError("An explicit Family provider result is required.")
    note = json.dumps(
        {"protocol": PROTOCOL, **result.payload()},
        sort_keys=True,
        separators=(",", ":"),
    )
    return DeliveryEvidence(
        provider_key_digest=hashlib.sha256(
            str(semantic_key).encode("ascii")
        ).hexdigest(),
        evidence_digest=hashlib.sha256(note.encode("utf-8")).hexdigest(),
        evidence_note=note,
        reason="smtp_" + result.status.value,
    )


def event_result(event):
    """Restore a typed result only from a compatible immutable rendering/outcome."""
    try:
        value = json.loads(event.evidence_note, object_pairs_hook=_object)
        if type(value) is not dict or value.pop("protocol", None) != PROTOCOL:
            raise ValueError
        result = FamilyDeliveryResult.from_payload(
            value, recipient_count=len(event.render.routed_recipients)
        )
        evidence = result_evidence(result, semantic_key=event.message.semantic_key)
        if any(
            getattr(event, key) != getattr(evidence, key)
            for key in (
                "provider_key_digest",
                "evidence_digest",
                "evidence_note",
                "reason",
            )
        ):
            raise ValueError
        states = {
            "accepted": {"delivered"},
            "transient": {"retry_wait", "permanent_failure"},
            "unavailable": {"retry_wait", "permanent_failure"},
            "permanent": {"permanent_failure"},
            "delivery_unknown": {"delivery_unknown"},
            "systemic": {"permanent_failure"},
        }
        if (
            event.state not in states[result.status.value]
            or event.previous_state != "submitting"
        ):
            raise ValueError
        return result
    except (ValueError, TypeError, KeyError, RecursionError):
        raise PermissionError("Family provider evidence is unavailable.") from None

"""Dry-run ParishSoft publication planning: a pure value, never a write.

This module implements the evaluation half of the data spec's publication
preflight (re-evaluate each approved field against a freshly read payload,
block an entity with any conflict, merge approved values into the full
payload, and digest it). It performs no network or database operation, and
uses no ParishSoft client or transport: it cannot write to ParishSoft.
Fetching the uncached payload, persisting the plan and the fenced PUT belong
to later, separately reviewed slices.

The ParishSoft v2 API writes only two shapes, recorded in the
credential-free capability fixture: the Member contact PUT and the Family
contact PUT. Family addresses stay by hand until a verified address read
exists, so only Member contact fields are ever planned here.

The provider value encodings below (text as is, ISO civil dates, phone
display text, email addresses joined by ``"; "``) are provisional until the
human-run smoke test records a real Member contact read and write. Gender is
the clearest open question: the census holds ``Male``/``Female``/
``Unspecified`` but the recorded request schema allows one character. A read
in a different form never matches the baseline, so it plans a conflict rather
than a write; the smoke test must settle the mapping before any real PUT.
"""

import hashlib
import json
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum

from parishkit.parishsoft import split_email_addresses

from .capabilities import Handling, capability
from .comparison import ComparisonValueError, ValueKind
from .member_census import MEMBER_FIELDS, InvalidMemberSource, source_value
from .member_requests import REQUEST_FIELDS
from .merge import KnownValue, MergeState, PriorChange, merge_value

# The complete MemberContactUpdateRequestModel property set, pinned to the
# recorded public schema by a test. The PUT replaces the contact record, so a
# payload is planned only when every one of these was read: a field we did
# not read could otherwise be cleared.
MEMBER_CONTACT_FIELDS = (
    "cellPhone",
    "dateOfBirth",
    "dateOfDeath",
    "emailAddress",
    "fax",
    "firstName",
    "gender",
    "homePhone",
    "language",
    "lastName",
    "maidenName",
    "middleName",
    "nickName",
    "pager",
    "workPhone",
)
# Executions a publication may still act on. Queued rows already belong to a
# plan; published, resolved, superseded and cancelled rows are outcomes.
PLANNABLE_EXECUTIONS = frozenset({"pending", "failed", "conflict"})
_DEFINITIONS = {field.name: field for field in (*MEMBER_FIELDS, *REQUEST_FIELDS)}


class Outcome(StrEnum):
    """What a dry run would do with one proposed field."""

    WRITE = "write"
    ALREADY_IN_PARISHSOFT = "already_in_parishsoft"
    CONFLICT = "conflict"
    CANNOT_CHECK = "cannot_check"
    NOT_APPROVED = "not_approved"
    BY_HAND = "by_hand"


# Approved fields with these outcomes stop every write to their entity.
BLOCKING = frozenset({Outcome.CONFLICT, Outcome.CANNOT_CHECK})


@dataclass(frozen=True)
class FieldPlan:
    """One proposal's dry-run outcome; it holds no value."""

    proposal_id: object
    field: str
    outcome: Outcome


@dataclass(frozen=True)
class EntityPlan:
    """One entity's dry-run plan.

    ``payload`` is the full planned PUT body, present only when the entity is
    not blocked and at least one field would be written. The digests cover
    the payload read and the payload planned, so a later step can refuse a
    PUT whose fresh read no longer matches the confirmed plan.
    """

    entity_kind: str
    entity_key: str
    fields: tuple[FieldPlan, ...]
    blocked: bool
    payload: dict | None = None
    current_digest: str | None = None
    planned_digest: str | None = None


def payload_digest(payload):
    """SHA-256 of canonical JSON, so equal payloads always share a digest."""
    return hashlib.sha256(
        json.dumps(
            payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True
        ).encode()
    ).hexdigest()


def _civil_date(value):
    """Read a provider date or date-time as an ISO civil date (None if blank)."""
    if value in (None, ""):
        return None
    if type(value) is not str or len(value) > 40:
        raise ValueError
    return datetime.fromisoformat(value).date().isoformat()


def decode_member_value(field, raw):
    """Read one provider contact value in the census comparison form.

    This mirrors how source snapshots feed the Family form (dates as civil
    dates, email addresses split, lowercased and joined by ``", "``), so a
    fresh read compares exactly as the proposal's baseline did. A value that
    cannot be read raises ValueError and is never echoed.
    """
    definition = _DEFINITIONS[field]
    try:
        if definition.kind is ValueKind.DATE:
            return source_value(definition, _civil_date(raw))
        if definition.kind is ValueKind.EMAIL and raw is not None:
            if type(raw) is not str:
                raise ValueError
            raw = ", ".join(sorted(split_email_addresses(raw)))
        return source_value(definition, raw)
    except (InvalidMemberSource, ComparisonValueError):
        raise ValueError("The ParishSoft value cannot be read.") from None


def encode_member_value(field, value):
    """Convert a proposed census value to the provider's request form.

    Provisional until the smoke test records a real write: phones send their
    display text, email addresses are joined by ``"; "`` as ParishSoft stores
    them, and civil dates and text are sent as they are.
    """
    kind = _DEFINITIONS[field].kind
    if kind is ValueKind.PHONE and isinstance(value, dict):
        return value["display"]
    if kind is ValueKind.EMAIL and value:
        return "; ".join(part.strip() for part in value.split(","))
    if isinstance(value, date):
        return value.isoformat()
    return value


def _proposed(row):
    """The value to publish: the Administrator's edit when set, else the Family's."""
    return row.admin_value if row.admin_value_set else row.submitted_value


_MERGE_OUTCOMES = {
    MergeState.FAMILY_CHANGED: Outcome.WRITE,
    MergeState.UPSTREAM_CAUGHT_UP: Outcome.ALREADY_IN_PARISHSOFT,
    # An edit back to the baseline value: nothing to write if ParishSoft still
    # holds it, a conflict if ParishSoft has changed since.
    MergeState.CURRENT: Outcome.ALREADY_IN_PARISHSOFT,
    MergeState.SOURCE_CHANGED: Outcome.CONFLICT,
    MergeState.CONFLICT: Outcome.CONFLICT,
    MergeState.UNAVAILABLE: Outcome.CANNOT_CHECK,
}


def _base(row):
    """The value ParishSoft must still hold for the proposal to be written.

    Normally the Family's baseline (what they saw). After an Administrator
    resolved a conflict (#528, ADM-09.03), the ParishSoft value they
    accepted, which is the row's current value while the base is active
    (``accepted_base``; the caller annotates ``conflict_accepted``). A row
    without the annotation uses the baseline and so plans a conflict.
    """
    if getattr(row, "conflict_accepted", False):
        return KnownValue(True, row.current_value)
    return KnownValue(row.baseline_available, row.baseline_value)


def _evaluate(row, provider, payload):
    """Classify one approved, API-writable Member proposal against ``payload``."""
    if row.execution == "conflict":
        # Reconciliation already found ParishSoft and the Family disagreeing.
        # The approval predates that, so it needs re-review, not a write.
        return Outcome.CONFLICT
    if payload is None or provider not in payload:
        return Outcome.CANNOT_CHECK
    try:
        current = KnownValue(True, decode_member_value(row.field, payload[provider]))
        merge = merge_value(
            _DEFINITIONS[row.field].kind,
            current,
            PriorChange(_base(row), _proposed(row)),
        )
    except ValueError:
        return Outcome.CANNOT_CHECK
    return _MERGE_OUTCOMES[merge.state]


def plan_entity(entity_kind, entity_key, rows, payload=None):
    """Build one entity's dry-run plan from its proposals and a fresh read.

    ``rows`` are the entity's proposals (ProposedChange rows or equivalents,
    annotated by ``accepted_base.with_accepted_base`` so a resolved conflict
    merges against its accepted value); rows no publication may act on are
    left out. ``payload`` is the entity's
    freshly read Member contact record, keyed by request-schema field, or
    None when it could not be read. A caller mapping a read onto those keys
    must leave a field out, never fill it with None, when the read lacks it:
    the completeness check is what keeps a PUT from clearing unread data.
    Only approved, API-writable Member fields are evaluated; everything else
    is reported as not approved or by hand and is left at its current
    ParishSoft value. Any approved field that conflicts or cannot be checked
    blocks the whole entity, and a payload missing any request-schema field
    blocks it too.
    """
    rows = [row for row in rows if row.execution in PLANNABLE_EXECUTIONS]
    if len({row.field for row in rows}) != len(rows):
        raise ValueError("An entity has more than one open proposal for a field.")
    complete = payload is not None and set(payload) == set(MEMBER_CONTACT_FIELDS)
    fields, writes = [], {}
    for row in sorted(rows, key=lambda item: item.field):
        known = capability(entity_kind, row.field)
        if (
            entity_kind != "member"
            or row.handling != Handling.API.value
            or known.handling is not Handling.API
        ):
            outcome = Outcome.BY_HAND
        elif row.decision != "approved":
            outcome = Outcome.NOT_APPROVED
        else:
            (provider,) = known.provider_fields
            outcome = _evaluate(row, provider, payload if complete else None)
            if outcome is Outcome.WRITE:
                writes[provider] = encode_member_value(row.field, _proposed(row))
        fields.append(FieldPlan(row.pk, row.field, outcome))
    blocked = any(item.outcome in BLOCKING for item in fields)
    if blocked or not writes:
        return EntityPlan(entity_kind, str(entity_key), tuple(fields), blocked)
    planned = {**payload, **writes}
    return EntityPlan(
        entity_kind,
        str(entity_key),
        tuple(fields),
        False,
        planned,
        payload_digest(payload),
        payload_digest(planned),
    )


def dry_run_summary(plans):
    """Counts for an Administrator's dry-run report, with no values or names."""
    plans = list(plans)
    return {
        "entities": len(plans),
        "entities_to_write": sum(plan.payload is not None for plan in plans),
        "entities_blocked": sum(plan.blocked for plan in plans),
        "outcomes": dict(
            Counter(item.outcome.value for plan in plans for item in plan.fields)
        ),
        "fields": dict(
            Counter(
                f"{item.field}:{item.outcome.value}"
                for plan in plans
                for item in plan.fields
            )
        ),
    }

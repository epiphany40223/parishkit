"""Dry-run ParishSoft publication planning: a pure value, never a write.

This module implements the evaluation half of the data spec's publication
preflight (re-evaluate each approved field against a freshly read record,
block an entity with any conflict, build the PUT body from the approved
changes, and digest the record read and the record expected after the PUT).
It performs no network or database operation, and uses no ParishSoft client
or transport: it cannot write to ParishSoft. Fetching the uncached record,
persisting the plan and the fenced PUT live in ``publication_plans`` and
``publication_worker``.

The ParishSoft v2 API writes only two shapes, recorded in the
credential-free capability fixture: the Member contact PUT and the Family
contact PUT. Family addresses stay by hand until a verified address read
exists, so only Member contact fields are ever planned here.

The live v2 write test of 2026-10-17 (#528) observed partial updates on
both contact PUTs (``nickName`` on a Member, ``primaryPhone`` on a Family:
a field left out of the body was left unchanged) and, on ``dateOfDeath``,
``null`` ignored and ``""`` refused with HTTP 400. So a PUT body holds only
the approved, Automatic changes to write, never a field as read (#1600), and
a blank value is never planned: the API is taken to be unable to clear a
field. Nor is a value the recorded PUT schema would refuse
(``contact_schema.sendable``), so the API never sees a write it would reject.

The provider value encodings below (text as is, ISO civil dates, phone
display text, email addresses joined by ``"; "``) are provisional until the
human-run smoke test records a real Member contact read and write. Gender is
the clearest open question: the census holds ``Male``/``Female``/
``Unspecified`` but the recorded request schema allows one character and
records no codes. The encoder maps gender through ``GENDER_CODES``, whose
codes are a guess, so gender stays by hand until ``GENDER_CODES_CONFIRMED``
is set (#1603); the smoke test must settle the codes before any real PUT.
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
from .contact_schema import sendable
from .member_census import MEMBER_FIELDS, InvalidMemberSource, source_value
from .member_requests import REQUEST_FIELDS
from .merge import KnownValue, MergeState, PriorChange, merge_value

# The complete MemberContactUpdateRequestModel property set, pinned to the
# recorded public schema by a test. A Member contact read is keyed by these
# names (the gateway maps the record onto them), and a PUT body holds some of
# them. The PUT is a partial update (#528), so a field that was not read is
# simply left out of the body and cannot be cleared.
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
# TODO(#1603, #528 slice 3): confirm these codes with the human-run smoke
# test before setting GENDER_CODES_CONFIRMED. The recorded request schema
# only says ``gender`` is a nullable string of at most one character: it
# lists no codes. Nothing offline shows which codes ParishSoft accepts or
# returns: the ``F`` in the smoke tool's test record is synthetic, and the
# Member record's ``sex`` (which the census reads) holds whole words. M and F
# are the likely codes; Unspecified has no known code, so it is left out and
# stays by hand even once the codes are confirmed (a blank cannot be sent).
# Confirming must also record the read-back form: if a contact read returns
# the whole word for a written code, the worker's raw read-back never
# matches the planned record, which is the #528 slice-3 raw read-back item.
GENDER_CODES = {"Male": "M", "Female": "F"}
GENDER_CODES_CONFIRMED = False
_GENDER_WORDS = {code: word for word, code in GENDER_CODES.items()}
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

    ``payload`` is the PUT body: only the fields to write, under the API's
    names, present only when the entity is not blocked and at least one
    field would be written. ``current_digest`` covers the record read and
    ``planned_digest`` the record a read must return after the PUT (the
    record read with the writes applied), so the worker can refuse a PUT
    whose fresh read no longer matches the confirmed plan and verify the
    write, and every field it left alone, by reading back.
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
        if field == "gender" and raw in _GENDER_WORDS:
            # A one-character code compares as its census word.
            return _GENDER_WORDS[raw]
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
    them, gender sends its one-character code (None, so by hand, for a value
    with no code), and civil dates and text are sent as they are.
    """
    if field == "gender":
        return GENDER_CODES.get(value)
    kind = _DEFINITIONS[field].kind
    if kind is ValueKind.PHONE and isinstance(value, dict):
        return value["display"]
    if kind is ValueKind.EMAIL and value:
        # Empty parts are dropped, so " , " encodes as blank, never "; ".
        parts = (part.strip() for part in value.split(","))
        return "; ".join(part for part in parts if part)
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


def _unwritable(row, provider):
    """Whether the API could not write this change, so it is worked by hand.

    A blank value (None, or text that is empty or only whitespace) would ask
    to clear the field, which the API cannot do (``null`` is ignored and
    ``""`` refused, #528). A value the recorded PUT schema refuses (a phone
    under ten characters, an over-long name, a gender longer than one
    character) would be refused with HTTP 400. The encoded form is checked,
    so a phone whose display text is empty counts as blank too. Gender is by
    hand until its codes are confirmed (``GENDER_CODES_CONFIRMED``), and a
    gender with no code (Unspecified) encodes as blank.
    """
    if row.field == "gender" and not GENDER_CODES_CONFIRMED:
        return True
    value = encode_member_value(row.field, _proposed(row))
    if value is None or (isinstance(value, str) and not value.strip()):
        return True
    return not sendable(provider, value)


def plan_entity(entity_kind, entity_key, rows, payload=None):
    """Build one entity's dry-run plan from its proposals and a fresh read.

    ``rows`` are the entity's proposals (ProposedChange rows or equivalents,
    annotated by ``accepted_base.with_accepted_base`` so a resolved conflict
    merges against its accepted value); rows no publication may act on are
    left out. ``payload`` is the entity's freshly read Member contact
    record, keyed by request-schema field, or None when it could not be
    read. A caller mapping a read onto those keys must leave a field out,
    never fill it with None, when the read lacks it, so an approved field
    that was not read cannot be checked rather than looking blank.

    Only approved, API-writable Member fields with a non-blank value the
    PUT schema accepts are evaluated; everything else is reported as not
    approved or by hand and is left out of the PUT body, so ParishSoft
    keeps its value. Any approved
    field that conflicts or cannot be checked blocks the whole entity. The
    planned body holds exactly the fields to write (#1600): the contact PUT
    is a partial update, so fields not written, or not read, are never sent.
    A Member's fields are read back under the request schema's own names, so
    the record expected after the PUT is the read with the writes applied.
    The Family PUT's names differ from its read's (``primaryPhone`` reads
    back as ``familyHomePhone``), so planning Family fields would need an
    explicit read-back map; none is planned today.
    """
    rows = [row for row in rows if row.execution in PLANNABLE_EXECUTIONS]
    if len({row.field for row in rows}) != len(rows):
        raise ValueError("An entity has more than one open proposal for a field.")
    fields, writes = [], {}
    for row in sorted(rows, key=lambda item: item.field):
        known = capability(entity_kind, row.field)
        # Defence in depth: the stored handling, the registry and the value
        # must all allow a write (#1596). Keep _unwritable last: the ``or``
        # short-circuit is what makes it run only for a Member API field, the
        # only kind encode_member_value and its provider name apply to.
        if (
            entity_kind != "member"
            or row.handling != Handling.API.value
            or known.handling is not Handling.API
            or _unwritable(row, known.provider_fields[0])
        ):
            outcome = Outcome.BY_HAND
        elif row.decision != "approved":
            outcome = Outcome.NOT_APPROVED
        else:
            (provider,) = known.provider_fields
            outcome = _evaluate(row, provider, payload)
            if outcome is Outcome.WRITE:
                writes[provider] = encode_member_value(row.field, _proposed(row))
        fields.append(FieldPlan(row.pk, row.field, outcome))
    blocked = any(item.outcome in BLOCKING for item in fields)
    if blocked or not writes:
        return EntityPlan(entity_kind, str(entity_key), tuple(fields), blocked)
    # Every written field was read (or _evaluate could not have planned a
    # write), so the expected record never gains a field the read lacked.
    expected = {**payload, **writes}
    return EntityPlan(
        entity_kind,
        str(entity_key),
        tuple(fields),
        False,
        writes,
        payload_digest(payload),
        payload_digest(expected),
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

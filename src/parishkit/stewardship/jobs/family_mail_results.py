"""Closed SMTP evidence in the existing immutable, numbered outbox journal."""

import hashlib
import json
import logging
import re
import time
from functools import cache
from pathlib import Path

from django.db import DatabaseError, connection, transaction

from parishkit.stewardship.accounts.integration_candidates import _object
from parishkit.stewardship.family_delivery import FamilyDeliveryResult, send_stats

from .outbox_validation import DeliveryEvidence

LOG = logging.getLogger(__name__)
PROTOCOL = "workspace_smtp_v1"
# The evidence column's bound (varchar(2000)); statistics never push past it.
MAX_NOTE = 2000
# How often a worker whose database does not yet admit statistics rechecks.
STATS_RECHECK_SECONDS = 300
_admitted = {"value": None, "checked": 0.0, "refused": False}
VALIDATOR = "public.stewardship_family_smtp_result_v1(uuid)"


@cache
def expected_validator_md5():
    """md5 of this release's validator body, exactly as PostgreSQL keeps it.

    The body is read from the packaged schema (schema/delivery.sql), the same
    text a fresh install runs and the in-place SQL copies byte for byte, so
    it equals md5(prosrc) only on a database running this release's rule.
    None if it cannot be read, which admits no statistics.
    """
    try:
        schema = (Path(__file__).parents[1] / "schema" / "delivery.sql").read_text()
    except OSError:
        return None
    match = re.search(
        r"^CREATE FUNCTION public\.stewardship_family_smtp_result_v1\(event uuid\)"
        r".*?AS \$\$(.*?)\$\$;$",
        schema,
        re.S | re.M,
    )
    return None if match is None else hashlib.md5(match.group(1).encode()).hexdigest()


def stats_admitted():
    """Whether the installed result validator is this release's (#284).

    A release that records statistics may start before its in-place SQL is
    applied. An older validator rejects an unknown key, or a word it does not
    know yet, and the Family result trigger then refuses the whole
    settlement. So statistics are written only while the installed function
    body is exactly this release's (its md5). Otherwise nothing is recorded,
    with one WARNING, and the check is repeated every STATS_RECHECK_SECONDS;
    admission is then kept. The check runs in its own savepoint inside the
    settlement, and any failure counts as not admitted, so it can never
    abort a settlement either. ``settle_with_stats`` covers what this cannot
    see, a validator replaced after the check.
    """
    now = time.monotonic()
    if _admitted["value"] or (
        _admitted["value"] is False
        and now - _admitted["checked"] < STATS_RECHECK_SECONDS
    ):
        return _admitted["value"]
    try:
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute(
                "SELECT md5(prosrc) FROM pg_proc WHERE oid=%s::regprocedure",
                [VALIDATOR],
            )
            row = cursor.fetchone()
        expected = expected_validator_md5()
        value = expected is not None and row is not None and row[0] == expected
    except Exception:
        value = False
    if not value and _admitted["value"] is None:
        LOG.warning(
            "Mail send statistics are not recorded: this database's result "
            "validator differs from this release's (#284). Apply the "
            "release's in-place SQL."
        )
    _admitted.update(value=value, checked=now)
    return value


def settle_with_stats(settle, result, *, semantic_key):
    """Run ``settle(evidence)``; if the database refuses the statistics, retry.

    ``settle`` records the outcome with the given DeliveryEvidence inside the
    caller's transaction. Evidence that carries statistics is tried inside a
    savepoint: if the Family result validator refuses it (SQLSTATE 23514,
    for example a validator older than this release's word list), the
    savepoint is rolled back and the very same settlement is recorded
    without statistics, with one WARNING, and statistics stop until the next
    admission check. The outcome, above all a message Gmail has accepted,
    never fails because of its statistics. A refusal of the outcome itself
    still fails the retry exactly as it always did.
    """
    evidence = result_evidence(result, semantic_key=semantic_key)
    plain = result_evidence(result, semantic_key=semantic_key, with_stats=False)
    if evidence == plain:
        return settle(evidence)
    try:
        with transaction.atomic():
            return settle(evidence)
    except DatabaseError as error:
        if getattr(error.__cause__, "sqlstate", None) != "23514":
            raise
    if not _admitted["refused"]:
        LOG.warning(
            "The database refused this release's mail send statistics; the "
            "outcome was recorded without them (#284). Apply the release's "
            "in-place SQL."
        )
    _admitted.update(value=False, checked=time.monotonic(), refused=True)
    return settle(plain)


def _note(result, stats):
    """The sorted compact evidence note, with statistics when given."""
    value = {"protocol": PROTOCOL, **result.payload()}
    if stats is not None:
        value["stats"] = stats
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def result_evidence(result, *, semantic_key, stored_stats=None, with_stats=True):
    """Retain only status and envelope indices, never SMTP responses or secrets.

    The result's send statistics (#284) are kept beside it under "stats",
    which sorts after "health", so readers that match the note's
    '{"health":...' prefix are unaffected. They are dropped, never allowed
    to fail a settlement, when the database does not admit them yet or the
    note would exceed its bound. ``stored_stats`` re-derives an existing
    note exactly (see event_result) without consulting the database;
    ``with_stats=False`` leaves them out (see settle_with_stats).
    """
    if not isinstance(result, FamilyDeliveryResult):
        raise TypeError("An explicit Family provider result is required.")
    stats = stored_stats
    if stats is None and with_stats and result.stats and stats_admitted():
        stats = result.stats
    note = _note(result, stats)
    if len(note) > MAX_NOTE and stored_stats is None:
        note = _note(result, None)
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
        stats = value.pop("stats", None)
        if stats is not None:
            stats = send_stats(stats)
        result = FamilyDeliveryResult.from_payload(
            value, recipient_count=len(event.render.routed_recipients)
        )
        evidence = result_evidence(
            result, semantic_key=event.message.semantic_key, stored_stats=stats
        )
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

"""Which stored content records are exempt from today's authoring text rules.

Content text rules (sanitizer canonical form, placeholder contracts) evolve
between releases. A configuration version that was already validated and
applied is integrity-checked by its digests, so re-verifying it must not
re-run today's text rules: a sanitizer improvement would otherwise make every
older version "invalid" and block all later configuration changes (#187).

Only content that is being authored or changed must meet the current rules.
Structural rules (fields, owners, slots, references) always apply.

The same trust covers the refresh schedule's row cap (#936): a ParishSoft
``refresh_rules`` document already stored, or carried unchanged from the
base or applied predecessor, keeps the older row bound; a new or changed
one meets the current cap (``is_carried_schedule``).
"""

import json
from contextlib import contextmanager
from contextvars import ContextVar

# None: strict (every record meets current text rules).
# ALL: every record is trusted (applied history, stored versions).
# dict: only records whose ID and exact values appear in the mapping.
ALL = object()
# The trust mapping's key for carried refresh schedules' fingerprints. A
# tuple never equals a content record's string ID.
SCHEDULES = ("refresh_rules",)
_TRUSTED = ContextVar("stewardship_trusted_content", default=None)


def fingerprint(values):
    """Canonical text of one record's values, for exact-match trust."""
    return json.dumps(values, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def records_of(document):
    """Map each content record ID in a document to its value fingerprint,
    and ``SCHEDULES`` to the fingerprints of its refresh schedules."""
    trust = {
        record["id"]: fingerprint(record["values"])
        for record in document["sections"].get("content", [])
    }
    trust[SCHEDULES] = frozenset(
        fingerprint(row["values"]["settings"]["refresh_rules"])
        for row in document["sections"].get("integrations", [])
        if isinstance(row.get("values"), dict)
        and isinstance(row["values"].get("settings"), dict)
        and "refresh_rules" in row["values"]["settings"]
    )
    return trust


@contextmanager
def trusted_content(trust=ALL):
    """Trust all records (default) or only exact records from a prior version."""
    token = _TRUSTED.set(trust)
    try:
        yield
    finally:
        _TRUSTED.reset(token)


def is_trusted(record):
    """Whether this record may skip the current authoring text rules."""
    trust = _TRUSTED.get()
    if trust is ALL:
        return True
    if isinstance(trust, dict):
        return trust.get(record["id"]) == fingerprint(record["values"])
    return False


def is_carried_schedule(rules):
    """Whether a ``refresh_rules`` document may keep the older row bound.

    True when everything is trusted (stored versions, applied history) or
    when the exact document was in the base or applied predecessor.
    """
    trust = _TRUSTED.get()
    if trust is ALL:
        return True
    if not isinstance(trust, dict):
        return False
    try:
        return fingerprint(rules) in trust.get(SCHEDULES, ())
    except (TypeError, ValueError):
        # Not JSON-shaped, so never carried; the shape check refuses it.
        return False

"""Which stored content records are exempt from today's authoring text rules.

Content text rules (sanitizer canonical form, placeholder contracts) evolve
between releases. A configuration version that was already validated and
applied is integrity-checked by its digests, so re-verifying it must not
re-run today's text rules: a sanitizer improvement would otherwise make every
older version "invalid" and block all later configuration changes (#187).

Only content that is being authored or changed must meet the current rules.
Structural rules (fields, owners, slots, references) always apply.
"""

import json
from contextlib import contextmanager
from contextvars import ContextVar

# None: strict (every record meets current text rules).
# ALL: every record is trusted (applied history, stored versions).
# dict: only records whose ID and exact values appear in the mapping.
ALL = object()
_TRUSTED = ContextVar("stewardship_trusted_content", default=None)


def fingerprint(values):
    """Canonical text of one record's values, for exact-match trust."""
    return json.dumps(values, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def records_of(document):
    """Map each content record ID in a document to its value fingerprint."""
    return {
        record["id"]: fingerprint(record["values"])
        for record in document["sections"].get("content", [])
    }


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

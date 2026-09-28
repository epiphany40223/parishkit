"""Applied content keeps its validation; authored content meets current rules (#187)."""

import pytest

from parishkit.config import ConfigError
from parishkit.stewardship.accounts import content_schema
from parishkit.stewardship.accounts.content_trust import (
    is_trusted,
    records_of,
    trusted_content,
)

from .content_factory import content_document

LEGACY_HTML = "<div>Welcome to {{ parish_name }}.</div>"


def legacy_document():
    """A complete document whose welcome text is not in today's canonical form."""
    document = content_document()
    document["sections"]["content"][0]["values"]["html"] = LEGACY_HTML
    return document


def test_strict_by_default_rejects_non_canonical_text():
    """Outside any trust scope, today's sanitizer form is required."""
    with pytest.raises(ConfigError):
        content_schema.validate_content_records(legacy_document())


def test_full_trust_accepts_applied_text_but_keeps_structure():
    """History is trusted for text, never for structure."""
    document = legacy_document()
    with trusted_content():
        content_schema.validate_content_records(document)
    document["sections"]["content"][0]["values"]["slot"] = "not-a-slot"
    with trusted_content(), pytest.raises(ConfigError):
        content_schema.validate_content_records(document)


def test_exact_trust_covers_only_unchanged_records():
    """A record trusted by exact values loses trust once edited."""
    base = legacy_document()
    trust = records_of(base)
    with trusted_content(trust):
        content_schema.validate_content_records(base)
    edited = legacy_document()
    edited["sections"]["content"][0] = base["sections"]["content"][0] | {
        "values": base["sections"]["content"][0]["values"]
        | {"html": "<div>Edited {{ parish_name }}.</div>"}
    }
    with trusted_content(trust), pytest.raises(ConfigError):
        content_schema.validate_content_records(edited)


def test_trust_scope_is_restored_after_use():
    """Leaving a scope returns to strict validation."""
    record = {"id": "x", "values": {"html": "a"}}
    with trusted_content():
        assert is_trusted(record)
    assert not is_trusted(record)

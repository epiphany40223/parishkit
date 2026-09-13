"""Canonical sanitized revisions, strict historical parsers and safe replacement."""

from copy import deepcopy
from uuid import uuid4

import pytest

from parishkit.config import ConfigError
from parishkit.stewardship.accounts.authority import parse_version
from parishkit.stewardship.accounts.configuration_schema import (
    schema_for,
    validator_for,
)
from parishkit.stewardship.accounts.content_schema import (
    RECOVERY_SCHEMA,
    REQUEST_SCHEMA,
    SCHEMA,
    remember_content,
)
from parishkit.stewardship.accounts.request_patch import build_candidate, default_schema

from .campaign_factory import schedule
from .configuration_factory import configuration_version
from .content_factory import content, content_document
from .policy_factory import address, assignment


@pytest.mark.parametrize(
    "field,value",
    [
        pytest.param("campaign_id", str(uuid4()), id="campaign_id-unrelated"),
        ("kind", "other"),
        ("kind", []),
        ("slot", "unknown"),
        ("slot", []),
        ("subject", "unexpected"),
        ("html", "<p onclick='steal()'>Hello</p>"),
        ("html", None),
        ("text", 42),
        ("text", "{{ unknown }}"),
        ("html", "{% include 'private' %}"),
    ],
)
def test_reject_invalid_content(field, value):
    """Malformed, executable and unsupported content never reaches canonical YAML."""
    document = content_document()
    document["sections"]["content"][0]["values"][field] = value
    with pytest.raises(ConfigError):
        configuration_version(document)


@pytest.mark.parametrize("defect", ["missing", "extra", "duplicate", "reference"])
def test_content_shape_and_selected_reference(defect):
    """Exactly one typed revision owns each campaign/kind/slot selection."""
    document = content_document()
    row = document["sections"]["content"][0]
    if defect == "missing":
        del row["values"]["text"]
    elif defect == "extra":
        row["values"]["unknown"] = True
    elif defect == "duplicate":
        document["sections"]["content"].append(content(row["values"]["campaign_id"]))
    else:
        row["values"]["slot"] = "review"
    with pytest.raises(ConfigError):
        configuration_version(document)


@pytest.mark.parametrize("subject", [None, "", "   ", "bad\r\nheader", "x" * 255])
def test_email_requires_valid_subject(subject):
    """Email revisions store their own nonempty bounded single-line subject."""
    document = content_document()
    owner = document["sections"]["campaigns"][0]["id"]
    document["sections"]["content"].append(
        content(
            owner,
            kind="email",
            slot="initial",
            subject=subject,
        )
    )
    with pytest.raises(ConfigError):
        configuration_version(document)


@pytest.mark.parametrize("mismatch", [None, "subject", "kind", "owner"])
def test_schedule_template_reference(mismatch):
    """A resolved schedule template has the same owner, kind and exact subject."""
    document = content_document()
    owner = document["sections"]["campaigns"][0]["id"]
    row = content(owner, kind="email", slot="initial")
    document["sections"]["content"].append(row)
    values = {"template_version": row["id"], "subject": row["values"]["subject"]}
    document["sections"]["schedules"] = [schedule(owner, **values)]
    if mismatch == "subject":
        row["values"]["subject"] = "Different"
    elif mismatch == "kind":
        row["values"]["slot"] = "reminder"
    elif mismatch == "owner":
        from .campaign_factory import campaign

        second = campaign()
        document["sections"]["campaigns"].append(second)
        row["values"]["campaign_id"] = second["id"]
    if mismatch is None:
        assert schema_for(configuration_version(document).document()) == SCHEMA
    else:
        with pytest.raises(ConfigError):
            configuration_version(document)


@pytest.mark.parametrize(
    "schema",
    [
        "parish-integrations-v1",
        "foundation-policy-v2",
        "campaign-foundation-v3",
        "ministry-activity-v4",
    ],
)
def test_old_schemas_remain_frozen(schema):
    """Historical discriminators cannot silently accept newly introduced content."""
    with pytest.raises(ConfigError):
        parse_version(content_document(), validate_sections=validator_for(schema))


def test_replacement_reconciles_reference_and_preserves_old_revision():
    """Replacing selected content creates a fresh revision; exact retries are stable."""
    base = configuration_version(content_document())
    owner = base.document()["sections"]["campaigns"][0]
    old = base.document()["sections"]["content"][0]
    new = content(owner["id"], html="<p>Changed</p>", text="Changed")
    patch = [
        {"operation": "remove", "section": "content", "id": old["id"]},
        {"operation": "add", "section": "content", **new},
        {
            "operation": "update",
            "section": "campaigns",
            "id": owner["id"],
            "values": {"content_versions": {"welcome": new["id"]}},
        },
    ]
    candidate_id = uuid4()
    result = build_candidate(base, patch, candidate_id=candidate_id)
    assert default_schema(base, patch) == REQUEST_SCHEMA
    assert result == build_candidate(
        base,
        patch,
        candidate_id=candidate_id,
        request_schema=REQUEST_SCHEMA,
    )
    assert result.candidate.document()["sections"]["content"] == [new]
    assert base.document()["sections"]["content"] == [old]
    with pytest.raises(ConfigError):
        build_candidate(base, patch[:2], candidate_id=uuid4())


def test_content_revision_cannot_be_edited_in_place():
    """Even a valid new payload cannot reuse an existing immutable revision ID."""
    base = configuration_version(content_document())
    row = base.document()["sections"]["content"][0]
    with pytest.raises(ConfigError, match="immutable"):
        build_candidate(
            base,
            [
                {
                    "operation": "update",
                    "section": "content",
                    "id": row["id"],
                    "values": {"text": "Changed"},
                }
            ],
            candidate_id=uuid4(),
        )


def test_historical_content_identity_retains_only_fixed_size_evidence():
    """An ancestry batch can release full content without weakening ID checks."""
    document = content_document()
    identities = {}
    remember_content(document, identities)
    remember_content(deepcopy(document), identities)
    assert all(
        type(value) is bytes and len(value) == 32 for value in identities.values()
    )
    document["sections"]["content"][0]["values"]["text"] = "Changed"
    with pytest.raises(ConfigError, match="immutable"):
        remember_content(document, identities)


def test_clear_last_content_keeps_v5_and_requires_reference_removal():
    """The empty selected set retains an unambiguous discriminator after deletion."""
    base = configuration_version(content_document())
    document = base.document()
    row, owner = (
        document["sections"]["content"][0],
        document["sections"]["campaigns"][0],
    )
    patch = [
        {"operation": "remove", "section": "content", "id": row["id"]},
        {
            "operation": "update",
            "section": "campaigns",
            "id": owner["id"],
            "values": {"content_versions": {}},
        },
    ]
    result = build_candidate(base, patch, candidate_id=uuid4())
    assert schema_for(result.candidate.document()) == SCHEMA
    assert default_schema(result.candidate, []) == REQUEST_SCHEMA


def test_content_adoption_from_legacy_and_recovery_preserve_other_sections():
    """New schema adoption does not drop policy or permit synthetic seeded grants."""
    document = content_document()
    row = document["sections"].pop("content")[0]
    base = configuration_version(document)
    patch = [{"operation": "add", "section": "content", **row}]
    result = build_candidate(base, patch, candidate_id=uuid4())
    assert default_schema(base, patch) == REQUEST_SCHEMA
    admin = address("recovery@example.org")
    admin["values"]["grants"]["administrator"]["manual"] = admin["values"][
        "creation_operation"
    ]
    recovery = build_candidate(
        result.candidate,
        [{"operation": "add", "section": "login_rules", **admin}],
        candidate_id=uuid4(),
        request_schema=RECOVERY_SCHEMA,
    )
    assert recovery.candidate.document()["sections"]["content"] == [row]
    with pytest.raises(ConfigError):
        build_candidate(
            result.candidate,
            [{"operation": "add", "section": "login_rules", **assignment(seeded=True)}],
            candidate_id=uuid4(),
        )
    extra = deepcopy(row)
    extra["id"] = str(uuid4())
    extra["values"]["slot"] = "review"
    with pytest.raises(ConfigError):
        build_candidate(
            result.candidate,
            [{"operation": "add", "section": "content", **extra}],
            candidate_id=uuid4(),
            request_schema=RECOVERY_SCHEMA,
        )

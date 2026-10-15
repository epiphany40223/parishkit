"""The retired local Ministry activity section (#1267).

New and changed configurations may not hold a ``ministries`` record; applied
history that recorded one still verifies, with its stored shape checked.
"""

from uuid import uuid4

import pytest

from parishkit.config import ConfigError
from parishkit.stewardship.accounts.authority import parse_version
from parishkit.stewardship.accounts.configuration_schema import (
    schema_for,
    validate_sections,
    validator_for,
)
from parishkit.stewardship.accounts.content_trust import trusted_content
from parishkit.stewardship.accounts.ministry_activity import RETIRED_MESSAGE, SCHEMA
from parishkit.stewardship.accounts.request_patch import build_candidate

from .configuration_factory import configuration_document, configuration_version
from .policy_factory import address


def activity(*, organization_id=12345, ministry_duid=1, active=False):
    """One record of the retired section, as older applied history holds."""
    return {
        "id": str(uuid4()),
        "values": {
            "organization_id": organization_id,
            "ministry_duid": ministry_duid,
            "active": active,
        },
    }


def policy_document(*records):
    """Complete synthetic authority with these ``ministries`` records."""
    document = configuration_document()
    document["sections"]["login_rules"] = [address()]
    document["sections"]["ministries"] = list(records)
    return document


def history_version(document):
    """Parse ``document`` as applied history is verified: everything trusted."""
    with trusted_content():
        return parse_version(document, validate_sections=validate_sections)


@pytest.mark.parametrize("active", [False, True])
def test_a_new_configuration_with_a_ministries_record_is_refused(active):
    """The refusal names the retired section and says what to do."""
    with pytest.raises(ConfigError) as refused:
        configuration_version(policy_document(activity(active=active)))
    assert str(refused.value) == RETIRED_MESSAGE
    assert "ministries section" in RETIRED_MESSAGE


def test_an_empty_section_is_still_admitted():
    """A document that keeps the key with no records is a valid configuration."""
    document = configuration_version(policy_document()).document()
    assert document["sections"]["ministries"] == []


def test_applied_history_with_a_record_still_verifies():
    """A stored version keeps the schema and records it was applied with."""
    version = history_version(policy_document(activity()))
    assert schema_for(version.document()) == SCHEMA
    assert len(version.document()["sections"]["ministries"]) == 1


@pytest.mark.parametrize("field", ["organization_id", "ministry_duid"])
@pytest.mark.parametrize("value", [True, False, 0, -1, 2**31, "1", None, 1.0])
def test_history_still_rejects_inexact_identities(field, value):
    """Trust skips the refusal of records, never the stored shape checks."""
    row = activity()
    row["values"][field] = value
    with pytest.raises(ConfigError):
        history_version(policy_document(row))


@pytest.mark.parametrize("defect", ["missing", "extra", "duplicate", "untyped"])
def test_history_still_rejects_malformed_records(defect):
    """A stored record has exactly its three fields, once per Ministry."""
    row = activity()
    document = policy_document(row)
    if defect == "missing":
        del row["values"]["active"]
    elif defect == "extra":
        row["values"]["name"] = "Not an identity"
    elif defect == "duplicate":
        document["sections"]["ministries"].append(activity())
    else:
        row["values"]["active"] = "false"
    with pytest.raises(ConfigError):
        history_version(document)


@pytest.mark.parametrize(
    "old_schema",
    ["parish-integrations-v1", "foundation-policy-v2", "campaign-foundation-v3"],
)
def test_old_schema_never_reinterprets_the_section(old_schema):
    """Retained parsers keep their earlier accepted vocabulary."""
    document = policy_document(activity())
    with pytest.raises(ConfigError), trusted_content():
        parse_version(document, validate_sections=validator_for(old_schema))


def test_no_request_adds_a_ministries_record():
    """Neither the Admin nor the recovery request formats can add one."""
    base = configuration_version(policy_document())
    patch = [{"operation": "add", "section": "ministries", **activity()}]
    with pytest.raises(ConfigError, match="ministries section"):
        build_candidate(base, patch, candidate_id=uuid4())


def test_a_change_to_older_history_must_remove_its_records():
    """A base that recorded the section blocks every edit until it is emptied.

    Carrying the records forward unchanged is refused like adding them; a
    patch that removes them is admitted, and its candidate holds none.
    """
    row = activity()
    base = history_version(policy_document(row))
    admin = address("second@example.org")
    with pytest.raises(ConfigError, match="ministries section"):
        build_candidate(
            base,
            [{"operation": "add", "section": "login_rules", **admin}],
            candidate_id=uuid4(),
        )
    removed = build_candidate(
        base,
        [{"operation": "remove", "section": "ministries", "id": row["id"]}],
        candidate_id=uuid4(),
    ).candidate
    assert removed.document()["sections"].get("ministries", []) == []

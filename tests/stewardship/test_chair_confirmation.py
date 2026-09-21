"""Database-free confirmation patches and the seed-change rule."""

from copy import deepcopy
from uuid import uuid4

import pytest

from parishkit.config import ConfigError
from parishkit.stewardship.accounts.chair_confirmation import (
    ConfirmationRefused,
    seed_patch,
    seeded_additions,
    validate_seed_change,
)
from parishkit.stewardship.accounts.policy_schema import (
    validate_policy_change,
    validate_policy_records,
)

from .policy_factory import address, assignment, domain

OP = str(uuid4())


def applied(records, patch):
    """Apply a patch the way the candidate builder does, records only."""
    result = deepcopy(records)
    for item in patch:
        if item["operation"] == "add":
            result.append({"id": item["id"], "values": item["values"]})
        else:
            existing = next(row for row in result if row["id"] == item["id"])
            existing["values"].update(item["values"])
    validate_policy_records(result)
    return result


def test_new_address_inherits_domain_roles_as_manual_beside_the_seed():
    """A new exact rule replaces the domain rule, so its roles are preselected."""
    records = [address(), domain("example.org", roles=("staff", "ministry_leader"))]
    patch, (change,) = seed_patch(records, [("chair@example.org", 4)], operation_id=OP)
    rule, seeded = patch
    assert rule["values"]["creation_origin"] == "chair-seed"
    assert rule["values"]["roles"] == ["ministry_leader", "staff"]
    assert rule["values"]["grants"] == {
        "staff": {"manual": OP},
        "ministry_leader": {"manual": OP, "chair-seed": OP},
    }
    assert seeded["values"] == {
        "kind": "assignment",
        "email": "chair@example.org",
        "ministry_duid": 4,
        "source": "chair-seed",
        "operation_id": OP,
    }
    assert change.created and change.before == ["ministry_leader", "staff"]
    assert change.ministries == (4,) and change.assignment_ids == (seeded["id"],)
    assert seeded_additions(patch) == [seeded["id"]]
    after = applied(records, patch)
    validate_seed_change(records, after, OP)
    # The ordinary policy schema refuses exactly this change.
    with pytest.raises(ConfigError):
        validate_policy_change(records, after)


def test_existing_rule_keeps_grants_and_gains_only_the_seeded_origin():
    """Nothing the address had is rewritten; a manual assignment stays beside."""
    rule = address("chair@example.org", ("staff",))
    manual = assignment("chair@example.org", ministry=4)
    records = [address(), rule, manual]
    patch, (change,) = seed_patch(
        records,
        [("chair@example.org", 4), ("chair@example.org", 9)],
        operation_id=OP,
    )
    update, first, second = patch
    assert update["id"] == rule["id"]
    assert update["values"]["roles"] == ["ministry_leader", "staff"]
    assert update["values"]["grants"]["staff"] == rule["values"]["grants"]["staff"]
    assert update["values"]["grants"]["ministry_leader"] == {"chair-seed": OP}
    assert [item["values"]["ministry_duid"] for item in (first, second)] == [4, 9]
    assert not change.created and change.ministries == (4, 9)
    validate_seed_change(records, applied(records, patch), OP)


def test_an_already_seeded_ministry_and_an_empty_selection_are_refused():
    """A seed is never duplicated; nothing selected is nothing to confirm."""
    records = [
        address(),
        address("c@example.org", ("ministry_leader",), seeded=True),
        assignment("c@example.org", ministry=4, seeded=True),
    ]
    with pytest.raises(ConfirmationRefused) as refused:
        seed_patch(records, [("c@example.org", 4)], operation_id=OP)
    assert refused.value.code == "already"
    with pytest.raises(ConfirmationRefused) as refused:
        seed_patch(records, [], operation_id=OP)
    assert refused.value.code == "empty"


@pytest.mark.parametrize(
    "tamper",
    [
        "other_operation",
        "manual_origin_only",
        "extra_role",
        "manual_assignment",
        "no_assignment",
        "remove_rule",
        "new_domain",
        "assignment_without_grant",
        "creation_manual",
        "duplicate_seed",
    ],
)
def test_the_seed_rule_admits_only_a_confirmation(tamper):
    """Every other shape of change is refused, whatever else it resembles."""
    records = [address(), domain("example.org", roles=("staff",))]
    patch, _ = seed_patch(records, [("chair@example.org", 4)], operation_id=OP)
    after = applied(records, patch)
    rule = next(
        row
        for row in after
        if row["values"]["kind"] == "address"
        and row["values"]["email"] == "chair@example.org"
    )
    seeded = next(row for row in after if row["values"]["kind"] == "assignment")
    if tamper == "other_operation":
        rule["values"]["grants"]["ministry_leader"]["chair-seed"] = str(uuid4())
    elif tamper == "manual_origin_only":
        rule["values"]["grants"]["ministry_leader"] = {"manual": OP}
    elif tamper == "extra_role":
        rule["values"]["roles"] = ["administrator", "ministry_leader", "staff"]
        rule["values"]["grants"]["administrator"] = {"manual": OP}
    elif tamper == "manual_assignment":
        seeded["values"]["source"] = "manual"
    elif tamper == "no_assignment":
        after.remove(seeded)
    elif tamper == "remove_rule":
        after.remove(next(row for row in after if row["id"] == records[0]["id"]))
    elif tamper == "new_domain":
        after.append(domain("other.example", roles=("staff",)))
    elif tamper == "assignment_without_grant":
        after.remove(rule)
    elif tamper == "creation_manual":
        rule["values"]["creation_origin"] = "manual"
        rule["values"]["creation_operation"] = OP
    elif tamper == "duplicate_seed":
        after.append({"id": str(uuid4()), "values": dict(seeded["values"])})
    with pytest.raises(ConfigError):
        validate_seed_change(records, after, OP)


def test_the_confirmation_schema_carries_no_other_section():
    """Even a valid parish edit cannot travel in a confirmation request."""
    from parishkit.stewardship.accounts.request_patch import build_candidate
    from parishkit.stewardship.accounts.request_patch import (
        default_schema as ordinary_schema,
    )

    from .configuration_factory import configuration_document, configuration_version

    records = [address(), domain("example.org", roles=("staff",))]
    document = configuration_document()
    document["sections"]["login_rules"] = records
    base = configuration_version(document)
    patch, _ = seed_patch(records, [("chair@example.org", 4)], operation_id=OP)
    build_candidate(
        base, patch, candidate_id=uuid4(), request_schema="chair-seed-patch-v9"
    )
    parish = next(row["id"] for row in document["sections"]["parish"])
    widened = patch + [
        {
            "operation": "update",
            "section": "parish",
            "id": parish,
            "values": {"name": "Renamed Parish"},
        }
    ]
    with pytest.raises(ConfigError):
        build_candidate(
            base, widened, candidate_id=uuid4(), request_schema="chair-seed-patch-v9"
        )
    # And the ordinary schema the patch would otherwise take refuses the seed.
    schema = ordinary_schema(base, patch)
    with pytest.raises(ConfigError):
        build_candidate(base, patch, candidate_id=uuid4(), request_schema=schema)


def test_a_confirmed_seed_survives_an_ordinary_change_afterwards():
    """Once applied, the seeded records are ordinary retained provenance."""
    records = [address(), domain("example.org", roles=("staff",))]
    patch, _ = seed_patch(records, [("chair@example.org", 4)], operation_id=OP)
    after = applied(records, patch)
    later = deepcopy(after)
    later.append(address("new@example.org", ("staff",)))
    validate_policy_change(after, later)

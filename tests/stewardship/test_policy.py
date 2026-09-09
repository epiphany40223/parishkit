"""Role provenance, capability matrix and privacy are credential-free policies."""

from copy import deepcopy
from uuid import uuid4

import pytest

from parishkit.config import ConfigError
from parishkit.stewardship.accounts.configuration_schema import schema_for
from parishkit.stewardship.accounts.policy import (
    Capability,
    Principal,
    allows,
    report_columns,
    resolve_roles,
)
from parishkit.stewardship.accounts.policy_schema import (
    normalized_domain,
    normalized_email,
    validate_policy_change,
    validate_policy_records,
)
from parishkit.stewardship.accounts.request_patch import build_candidate

from .configuration_factory import configuration_document, configuration_version
from .policy_factory import address, assignment, domain


@pytest.mark.parametrize("hosted", [None, "other.org", "example.org"])
def test_hosted_domain_requires_claim_and_email_suffix(hosted):
    """A verified email suffix alone never authorizes Workspace domain policy."""
    roles, _ = resolve_roles("member@example.org", hosted, [domain()])
    assert roles == (frozenset({"staff"}) if hosted == "example.org" else frozenset())


@pytest.mark.parametrize("roles", [(), ("ministry_leader",), ("administrator",)])
def test_exact_rule_replaces_domain_including_denial(roles):
    """Explicit address policy never unions roles inherited from its domain."""
    actual, _ = resolve_roles(
        "member@example.org",
        "example.org",
        [domain(), address("member@example.org", roles)],
    )
    expected = set(roles)
    if "administrator" in expected:
        expected.update(("staff", "ministry_leader"))
    assert actual == expected


def test_seeded_suppression_requires_every_provenance_condition():
    """Missing scope suppresses only an exclusively seeded role on a seeded rule."""
    rule = address("leader@example.org", ("ministry_leader",), seeded=True)
    seed = assignment(seeded=True)
    assert not resolve_roles("leader@example.org", None, [rule, seed])[0]
    assert resolve_roles(
        "leader@example.org", None, [rule, seed], frozenset({seed["id"]})
    )[0] == {"ministry_leader"}
    rule["values"]["grants"]["ministry_leader"]["manual"] = str(uuid4())
    roles, ministries = resolve_roles("leader@example.org", None, [rule, seed])
    assert roles == {"ministry_leader"} and not ministries
    rule["values"]["grants"]["ministry_leader"].pop("manual")
    rule["values"]["creation_origin"] = "manual"
    assert resolve_roles("leader@example.org", None, [rule, seed])[0] == {
        "ministry_leader"
    }


def test_manual_assignment_survives_missing_seed_overlay():
    """A manual assignment independently supplies Ministry scope."""
    role = address("leader@example.org", ("ministry_leader",), seeded=True)
    assert resolve_roles("leader@example.org", None, [role, assignment()]) == (
        frozenset({"ministry_leader"}),
        frozenset({123}),
    )


STAFF = {
    Capability.FAMILY_CODES,
    Capability.REPORT_EXPORT,
    Capability.CAMPAIGN_REPORT,
    Capability.MINISTRY_REPORT,
    Capability.FINANCIAL_DETAIL,
    Capability.ADDITIONAL_FOLLOWUP,
    Capability.MINISTRY_FOLLOWUP,
    Capability.MANUAL_CENSUS,
    Capability.VIEW_CENSUS,
}
MINISTER = {
    Capability.MINISTRY_REPORT,
    Capability.MINISTRY_FOLLOWUP,
    Capability.REPORT_EXPORT,
}


@pytest.mark.parametrize("role", [None, "administrator", "staff", "ministry_leader"])
@pytest.mark.parametrize("capability", list(Capability))
@pytest.mark.parametrize("ministry", [None, 123, 456])
def test_complete_administration_capability_matrix(role, capability, ministry):
    """Every named capability is exercised with no, assigned and unrelated scope."""
    principal = Principal(
        uuid4(), frozenset({role}) if role else frozenset(), frozenset({123})
    )
    expected = role == "administrator" and capability not in {
        Capability.FAMILY_READ,
        Capability.FAMILY_SUBMIT,
    }
    expected |= role == "staff" and capability in STAFF
    expected |= role == "ministry_leader" and ministry == 123 and capability in MINISTER
    assert allows(principal, capability, ministry_id=ministry) is expected


@pytest.mark.parametrize("capability", list(Capability))
def test_family_scope_is_separate_from_admin_roles(capability):
    """Family identity grants only its own submission/read/financial capabilities."""
    family = uuid4()
    principal = Principal(uuid4(), family_id=family)
    assert allows(principal, capability, family_id=family) is (
        capability
        in {
            Capability.FAMILY_READ,
            Capability.FAMILY_SUBMIT,
            Capability.FINANCIAL_DETAIL,
        }
    )
    assert not allows(principal, capability, family_id=uuid4())


def test_report_column_privacy():
    """Leader exports cannot widen scope through requested column names."""
    columns = ("member_name", "phones", "manual_code", "pledge", "private_unknown")
    minister = Principal(uuid4(), frozenset({"ministry_leader"}), frozenset({123}))
    assert report_columns(minister, columns, ministry_id=123) == (
        "member_name",
        "phones",
    )
    assert report_columns(minister, columns, ministry_id=456) == ()
    assert report_columns(Principal(uuid4(), frozenset({"staff"})), columns) == columns
    assert not allows(minister, "ministry_report", ministry_id=123)
    assert not allows(None, Capability.CONFIGURE)
    assert report_columns(None, columns) == ()


@pytest.mark.parametrize(
    "kind,value",
    [
        ("email", None),
        ("email", " private "),
        ("email", "bad"),
        ("domain", "gmail"),
        ("domain", "-bad.org"),
        ("domain", None),
    ],
)
def test_invalid_identity_inputs_fail_safely(kind, value):
    """Malformed identity metadata is never echoed in errors."""
    with pytest.raises(ConfigError):
        (normalized_email if kind == "email" else normalized_domain)(value)


def test_case_normalization_is_explicit():
    """Case folds without Gmail dot/plus alias assumptions."""
    assert normalized_email("User+Tag@Example.org") == "user+tag@example.org"
    assert normalized_domain("Example.ORG") == "example.org"


@pytest.mark.parametrize(
    "changes",
    [
        {"roles": None},
        {"roles": [[]]},
        {"roles": ["staff", "staff"]},
        {"roles": ["unknown"]},
        {"grants": {}},
        {"creation_origin": "guessed"},
        {"creation_operation": "secret"},
        {"email": "UPPER@example.org"},
        {"unknown": "secret"},
    ],
)
def test_rejects_bad_or_inconsistent_address_provenance(changes):
    """No role is inferred from missing or malformed provenance."""
    rule = address()
    rule["values"].update(changes)
    with pytest.raises(ConfigError):
        validate_policy_records([rule])


@pytest.mark.parametrize(
    "rule",
    [
        domain("gmail.com"),
        domain(roles=("administrator",)),
        domain(roles=()),
        assignment(ministry=0),
        assignment(ministry=True),
    ],
)
def test_policy_rejects_invalid_domain_and_assignment(rule):
    """SQL-backed policy starts from an equally strict pure schema."""
    with pytest.raises(ConfigError):
        validate_policy_records([address(), rule])


def test_last_admin_and_duplicate_rule_checks():
    """Empty exact denial rules are legal only while an explicit Admin remains."""
    admin, deny = address(), address("denied@example.org", ())
    validate_policy_records([admin, deny])
    for records in ([deny], [domain()], [admin, deepcopy(admin)]):
        with pytest.raises(ConfigError):
            validate_policy_records(records)


def test_manual_patch_preserves_origins_and_blocks_seed_forgery():
    """Checked unchanged roles are not silently converted into manual grants."""
    admin, leader = (
        address(),
        address("leader@example.org", ("ministry_leader",), seeded=True),
    )
    before = [admin, leader]
    after = deepcopy(before)
    validate_policy_change(before, after)
    after[1]["values"]["grants"]["ministry_leader"]["manual"] = str(uuid4())
    validate_policy_change(before, after)
    with pytest.raises(ConfigError):
        validate_policy_change(before, [*after, assignment(seeded=True)])
    after[1]["values"]["creation_origin"] = "manual"
    with pytest.raises(ConfigError):
        validate_policy_change(before, after)


def test_policy_patch_uses_new_schema_without_reinterpreting_old_requests():
    """A policy-enabled successor retains unrelated parish/integration settings."""
    base = configuration_version()
    rule = address()
    intent = build_candidate(
        base,
        [{"operation": "add", "section": "login_rules", **rule}],
        candidate_id=uuid4(),
    )
    assert intent.candidate.document()["sections"]["login_rules"] == [rule]
    with pytest.raises(ConfigError):
        build_candidate(
            intent.candidate,
            [{"operation": "remove", "section": "login_rules", "id": rule["id"]}],
            candidate_id=uuid4(),
        )


def test_seeded_configuration_requires_explicit_schema_provenance():
    """Snapshots may retain seeded policy; only dedicated commands may create it."""
    document = configuration_document()
    document["sections"]["login_rules"] = [
        address(),
        address("leader@example.org", ("ministry_leader",), seeded=True),
        assignment(seeded=True),
    ]
    version = configuration_version(document)
    assert schema_for(version.document()) == "foundation-policy-v2"
    for record in document["sections"]["login_rules"][1:]:
        with pytest.raises(ConfigError):
            build_candidate(
                configuration_version(),
                [{"operation": "add", "section": "login_rules", **record}],
                candidate_id=uuid4(),
            )


@pytest.mark.parametrize(
    "section",
    ["campaigns", "content", "schedules", "share_options", "ministries", "funds"],
)
def test_policy_schema_does_not_admit_later_sections(section):
    """Delegated v1 validation must still reject other nonempty sections."""
    document = configuration_document()
    document["sections"]["login_rules"] = [address()]
    document["sections"][section] = [
        {"id": str(uuid4()), "values": {"name": "Unsupported"}}
    ]
    with pytest.raises(ConfigError):
        configuration_version(document)


@pytest.mark.parametrize("include_empty", [False, True])
def test_empty_policy_retains_legacy_schema(include_empty):
    """Absent and explicitly empty rules preserve the original stored discriminator."""
    document = configuration_document()
    document["sections"].pop("login_rules", None)
    if include_empty:
        document["sections"]["login_rules"] = []
    assert schema_for(document) == "parish-integrations-v1"


@pytest.mark.parametrize("hosted", ["bad_domain", "example.org.", "éxample.org"])
def test_bad_optional_hosted_claim_cannot_override_exact_rule(hosted):
    """Bad hosted-domain evidence grants nothing and cannot erase exact grants."""
    assert resolve_roles("admin@example.org", hosted, [address()])[0] == {
        "administrator",
        "staff",
        "ministry_leader",
    }
    assert not resolve_roles("other@example.org", hosted, [domain()])[0]


def test_same_patch_cannot_recreate_address_to_reclassify_provenance():
    """Replacing the record UUID is still an update to the same address override."""
    original = address("leader@example.org", ("ministry_leader",), seeded=True)
    replacement = address("leader@example.org", ("ministry_leader",))
    with pytest.raises(ConfigError):
        validate_policy_change([address(), original], [address(), replacement])


def test_recovery_new_rule_creation_matches_grant_origin():
    """A valid UUID is insufficient when the creation and recovery operation differ."""
    rule = address()
    rule["values"]["creation_operation"] = str(uuid4())
    with pytest.raises(ConfigError):
        build_candidate(
            configuration_version(),
            [{"operation": "add", "section": "login_rules", **rule}],
            candidate_id=uuid4(),
            request_schema="operator-recovery-patch-v1",
        )


@pytest.mark.parametrize("ministry", [True, False, 123.0, "123", [], {}, 0, -1, 2**63])
@pytest.mark.parametrize(
    "capability",
    [
        Capability.MINISTRY_REPORT,
        Capability.MINISTRY_FOLLOWUP,
        Capability.REPORT_EXPORT,
    ],
)
def test_ministry_scope_rejects_noncanonical_identifiers(ministry, capability):
    """Python bool/float equality is not proof of a canonical Ministry DUID."""
    leader = Principal(uuid4(), frozenset({"ministry_leader"}), frozenset({1, 123}))
    assert not allows(leader, capability, ministry_id=ministry)
    assert report_columns(leader, ("member_name",), ministry_id=ministry) == ()


def test_principal_scope_cannot_exceed_persisted_duid_range():
    """In-memory principal scopes obey the same bigint range as stored assignments."""
    with pytest.raises(ValueError):
        Principal(uuid4(), frozenset({"ministry_leader"}), frozenset({2**63}))

"""The users form of the sign-in policy and its conversion, without a database (#952).

The operations specification's "Users upgrade from sign-in rules" section is
the contract: every Administrator is kept, address rules keep their highest
role, a hosted-domain rule's signed-in people become users at its highest
role, deny rules are dropped, and disabled or never-signed-in identities are
not converted.
"""

from uuid import uuid4

import pytest

from parishkit.config import ConfigError
from parishkit.stewardship.accounts import users_schema
from parishkit.stewardship.accounts.authority import parse_version
from parishkit.stewardship.accounts.configuration_schema import (
    schema_for,
    validate_sections,
    validator_for,
)
from parishkit.stewardship.accounts.policy import resolve_roles
from parishkit.stewardship.accounts.request_patch import build_candidate
from parishkit.stewardship.accounts.users_upgrade import (
    Identity,
    default_name,
    highest_role,
    plan_upgrade,
    upgrade_patch,
)

from .configuration_factory import configuration_document
from .policy_factory import address, domain


def user(email="admin@example.org", role="administrator", name="Admin"):
    """One users-form record."""
    return {
        "id": str(uuid4()),
        "values": {"kind": "user", "email": email, "name": name, "role": role},
    }


# No ParishSoft Member lists any address.
NO_NAMES = {}


def identity(email, hosted="example.org", *, disabled=False, signed_in=True):
    """One recorded Google identity."""
    return Identity(email, hosted, disabled, signed_in)


def version(records):
    """A validated configuration version with these sign-in rules."""
    document = configuration_document()
    document["sections"]["login_rules"] = records
    return parse_version(document, validate_sections=validate_sections)


def test_roles_are_cumulative_levels():
    """Each role holds itself and every role below it, sorted as stored."""
    assert users_schema.cumulative_roles("ministry_leader") == ["ministry_leader"]
    assert users_schema.cumulative_roles("staff") == ["ministry_leader", "staff"]
    assert users_schema.cumulative_roles("administrator") == [
        "administrator",
        "ministry_leader",
        "staff",
    ]
    with pytest.raises(ConfigError):
        users_schema.cumulative_roles("")
    assert highest_role(["ministry_leader", "staff"]) == "staff"
    assert highest_role(["administrator"]) == "administrator"
    assert highest_role([]) is None


def test_users_form_selects_its_own_schema_and_validates():
    """A document holding users is users-v10 and passes only as users."""
    document = configuration_document()
    document["sections"]["login_rules"] = [user(), user("staff@example.org", "staff")]
    assert schema_for(document) == users_schema.SCHEMA
    parse_version(document, validate_sections=validate_sections)
    # The older schemas never accept a user entry.
    with pytest.raises(ConfigError):
        validator_for("source-cadence-v8")(document)


@pytest.mark.parametrize(
    "records",
    [
        # No Administrator: the last-Administrator guard.
        [user("staff@example.org", "staff")],
        # The same address twice.
        [user(), user("admin@example.org", "staff")],
        # An address that is not normalized.
        [user("Admin@example.org")],
        # Names that are empty, padded, too long or hold a control character.
        [user(name="")],
        [user(name=" Admin")],
        [user(name="x" * 201)],
        [user(name="Ad\u0007min")],
        # No such role, and no empty role.
        [user(role="owner")],
        # An older rule in the users form.
        [user(), address("other@example.org")],
    ],
)
def test_users_form_refuses_invalid_entries(records):
    """Every user rule of the data specification is enforced."""
    document = configuration_document()
    document["sections"]["login_rules"] = records
    with pytest.raises(ConfigError):
        validator_for(users_schema.SCHEMA)(document)


def test_users_form_refuses_extra_fields():
    """A user carries exactly kind, email, name and role."""
    record = user()
    record["values"]["roles"] = ["administrator"]
    document = configuration_document()
    document["sections"]["login_rules"] = [record]
    with pytest.raises(ConfigError):
        validator_for(users_schema.SCHEMA)(document)


def test_a_user_resolves_to_its_cumulative_roles():
    """Sign-in reads the address's one user; there is no domain fallback."""
    records = [user(), user("staff@example.org", "staff")]
    roles, _ = resolve_roles("admin@example.org", None, records)
    assert roles == {"administrator", "staff", "ministry_leader"}
    roles, _ = resolve_roles("staff@example.org", "example.org", records)
    assert roles == {"staff", "ministry_leader"}
    roles, _ = resolve_roles("someone@example.org", "example.org", records)
    assert roles == frozenset()


def test_default_name_prefers_a_usable_parishsoft_name():
    """The first valid Member name wins; otherwise the address's local part."""
    assert default_name("ann@example.org", ["Smith, Ann", "Smith, Bob"]) == (
        "Smith, Ann"
    )
    assert default_name("ann@example.org", ["x" * 201, "Smith, Bob"]) == "Smith, Bob"
    assert default_name("ann.smith@example.org", []) == "ann.smith"


def test_plan_keeps_administrators_and_highest_roles():
    """Every address rule keeps its highest role; Administrators come first."""
    records = [
        address("admin@example.org"),
        address("second@example.org", ("administrator", "staff")),
        address("staff@example.org", ("ministry_leader", "staff")),
        address("leader@example.org", ("ministry_leader",)),
    ]
    plan = plan_upgrade(records, [], NO_NAMES)
    assert plan["administrators"] == ["admin@example.org", "second@example.org"]
    assert {item["email"]: item["role"] for item in plan["users"]} == {
        "admin@example.org": "administrator",
        "second@example.org": "administrator",
        "staff@example.org": "staff",
        "leader@example.org": "ministry_leader",
    }
    assert [item["name"] for item in plan["users"]] == [
        "admin",
        "leader",
        "second",
        "staff",
    ]


def test_plan_converts_signed_in_domain_identities_only():
    """A domain rule's people convert when admitted today and signed in before."""
    records = [
        address("admin@example.org"),
        address("denied@example.org", ()),
        address("leader@example.org", ("ministry_leader",)),
        domain("example.org", ("ministry_leader", "staff")),
    ]
    identities = [
        identity("signed@example.org"),
        # Two Google subjects for one address: one qualifies, so it converts.
        identity("twice@example.org", disabled=True),
        identity("twice@example.org"),
        identity("never@example.org", signed_in=False),
        identity("disabled@example.org", disabled=True),
        # The hosted-domain claim must match the rule, not just the suffix.
        identity("consumer@example.org", hosted=None),
        # Another domain has no rule.
        identity("outside@other.org", hosted="other.org"),
        # An address rule decides these, never the domain rule.
        identity("denied@example.org"),
        identity("leader@example.org"),
    ]
    # Every address the plan names has one ParishSoft name; an address that
    # converts nowhere still has one, which the plan never reads.
    names = {
        f"{local}@example.org": ["Smith, Ann"]
        for local in ("admin", "denied", "leader", "signed", "twice", "never")
    }
    plan = plan_upgrade(records, identities, names)
    roles = {item["email"]: item["role"] for item in plan["users"]}
    assert roles == {
        "admin@example.org": "administrator",
        "leader@example.org": "ministry_leader",
        "signed@example.org": "staff",
        "twice@example.org": "staff",
    }
    assert plan["domain_rules"] == [
        {
            "domain": "example.org",
            "roles": ["ministry_leader", "staff"],
            "role": "staff",
            "users": ["signed@example.org", "twice@example.org"],
        }
    ]
    assert plan["not_converted"] == {
        "disabled": ["disabled@example.org"],
        "never_signed_in": ["never@example.org"],
    }
    assert plan["dropped_deny_rules"] == [
        {
            "email": "denied@example.org",
            "overrides_identity": True,
            "name": "Smith, Ann",
        }
    ]
    assert {item["name"] for item in plan["users"]} == {"Smith, Ann"}


def test_plan_keeps_administrators_whatever_their_identities():
    """An Administrator stays one with only a disabled identity, or with several."""
    records = [
        address("admin@example.org"),
        address("second@example.org"),
        domain("example.org", ("staff",)),
    ]
    identities = [
        identity("admin@example.org", disabled=True),
        identity("second@example.org"),
        identity("second@example.org", disabled=True),
        identity("second@example.org", hosted=None, signed_in=False),
    ]
    plan = plan_upgrade(records, identities, NO_NAMES)
    assert plan["administrators"] == ["admin@example.org", "second@example.org"]
    assert {item["email"]: item["role"] for item in plan["users"]} == {
        "admin@example.org": "administrator",
        "second@example.org": "administrator",
    }
    assert plan["not_converted"] == {"disabled": [], "never_signed_in": []}


@pytest.mark.parametrize(
    "email", ["no-at-sign", " padded@example.org", "two@@example.org", ""]
)
def test_plan_fails_closed_on_an_invalid_identity_email(email):
    """One stored address the validator refuses stops the whole conversion."""
    identities = [identity("signed@example.org"), identity(email, disabled=True)]
    with pytest.raises(ConfigError, match="invalid email address") as caught:
        plan_upgrade(
            [address("admin@example.org"), domain("example.org", ("staff",))],
            identities,
            NO_NAMES,
        )
    if email.strip():
        assert email.strip() not in str(caught.value)


def test_upgrade_patch_replaces_every_rule_with_new_user_ids():
    """The one request removes each old rule and adds each user with a new ID."""
    records = [address(), domain()]
    plan = plan_upgrade(records, [identity("staff@example.org")], NO_NAMES)
    patch = upgrade_patch(records, plan)
    assert [item["operation"] for item in patch] == ["remove", "remove", "add", "add"]
    assert {item["id"] for item in patch[:2]} == {record["id"] for record in records}
    added = patch[2:]
    assert not {item["id"] for item in added} & {record["id"] for record in records}
    assert [item["values"] for item in added] == [
        {
            "kind": "user",
            "email": "admin@example.org",
            "name": "admin",
            "role": "administrator",
        },
        {
            "kind": "user",
            "email": "staff@example.org",
            "name": "staff",
            "role": "staff",
        },
    ]


def test_upgrade_builder_converts_into_the_users_form():
    """The upgrade format writes a users-v10 candidate, other sections unchanged."""
    records = [address(), address("staff@example.org", ("staff",)), domain()]
    base = version(records)
    plan = plan_upgrade(records, [], NO_NAMES)
    result = build_candidate(
        base,
        upgrade_patch(records, plan),
        candidate_id=uuid4(),
        request_schema=users_schema.UPGRADE_REQUEST_SCHEMA,
    )
    document = result.candidate.document()
    assert schema_for(document) == users_schema.SCHEMA
    assert {
        record["values"]["email"]: record["values"]["role"]
        for record in document["sections"]["login_rules"]
    } == {"admin@example.org": "administrator", "staff@example.org": "staff"}
    before = base.document()["sections"]
    assert {
        name: rows
        for name, rows in document["sections"].items()
        if name != "login_rules"
    } == {name: rows for name, rows in before.items() if name != "login_rules"}


def test_upgrade_builder_refuses_to_drop_an_administrator():
    """Independently of the plan, an Administrator cannot be lost."""
    records = [address(), address("second@example.org")]
    base = version(records)
    plan = plan_upgrade(records, [], NO_NAMES)
    plan["users"] = [
        item | {"role": "staff"} if item["email"] == "second@example.org" else item
        for item in plan["users"]
    ]
    with pytest.raises(ConfigError):
        build_candidate(
            base,
            upgrade_patch(records, plan),
            candidate_id=uuid4(),
            request_schema=users_schema.UPGRADE_REQUEST_SCHEMA,
        )
    plan["users"] = [item for item in plan["users"] if item["role"] != "staff"]
    with pytest.raises(ConfigError):
        build_candidate(
            base,
            upgrade_patch(records, plan),
            candidate_id=uuid4(),
            request_schema=users_schema.UPGRADE_REQUEST_SCHEMA,
        )


def test_upgrade_builder_refuses_a_new_administrator():
    """The upgrade never grants Administrator to an address that lacked it."""
    records = [address(), address("staff@example.org", ("staff",))]
    base = version(records)
    plan = plan_upgrade(records, [], NO_NAMES)
    plan["users"] = [item | {"role": "administrator"} for item in plan["users"]]
    with pytest.raises(ConfigError):
        build_candidate(
            base,
            upgrade_patch(records, plan),
            candidate_id=uuid4(),
            request_schema=users_schema.UPGRADE_REQUEST_SCHEMA,
        )


def test_upgrade_builder_refuses_a_partial_conversion():
    """Every old rule goes: a leftover rule is not the users form."""
    records = [address(), domain()]
    base = version(records)
    plan = plan_upgrade(records, [], NO_NAMES)
    patch = [
        item for item in upgrade_patch(records, plan) if item["id"] != records[1]["id"]
    ]
    with pytest.raises(ConfigError):
        build_candidate(
            base,
            patch,
            candidate_id=uuid4(),
            request_schema=users_schema.UPGRADE_REQUEST_SCHEMA,
        )


def test_upgrade_builder_refuses_a_users_base():
    """A configuration already in the users form is never converted again."""
    document = configuration_document()
    document["sections"]["login_rules"] = [user()]
    base = parse_version(document, validate_sections=validate_sections)
    with pytest.raises(ConfigError):
        build_candidate(
            base,
            [{"operation": "remove", "section": "login_rules", "id": str(uuid4())}],
            candidate_id=uuid4(),
            request_schema=users_schema.UPGRADE_REQUEST_SCHEMA,
        )


def test_upgrade_users_cli_requires_the_offline_profile(capsys):
    """A host invocation without a configuration file is refused."""
    from parishkit.stewardship.cli import main

    assert main(["upgrade-users"]) == 2
    result = capsys.readouterr()
    assert result.out == "" and "offline operation refused" in result.err


def test_upgrade_users_cli_refuses_foreign_options(capsys):
    """Only the command's own options are accepted, named without values."""
    from parishkit.stewardship.cli import main

    with pytest.raises(SystemExit):
        main(["upgrade-users", "--config", "x", "--target-email", "a@example.org"])
    assert "target-email" in capsys.readouterr().err


def test_upgrade_users_cli_forwards_preview_and_conversion(monkeypatch, capsys):
    """Dispatch passes the typed inputs to the command, preview or convert."""
    from parishkit.stewardship import operator_commands
    from parishkit.stewardship.cli import main

    marker, calls = object(), []
    monkeypatch.setattr(operator_commands, "load_deployment", lambda path: marker)

    def command(configuration, **values):
        """Record the call instead of opening a database."""
        calls.append((configuration, values))
        return {"users_upgrade_preview": {}}

    monkeypatch.setattr(operator_commands, "upgrade_users_command", command)
    deployment, operation = str(uuid4()), str(uuid4())
    argv = ["upgrade-users", "--config", "c", "--confirm-deployment", deployment]
    assert main([*argv, "--preview"]) == 0
    assert (
        main(
            [
                *argv,
                "--operation-id",
                operation,
                "--operator-name",
                "Operator",
                "--reason",
                "Users revamp",
                "--confirm-administrators",
                "admin@example.org,second@example.org",
            ]
        )
        == 0
    )
    capsys.readouterr()
    assert calls == [
        (
            marker,
            dict(
                deployment_id=deployment,
                preview=True,
                operation_id=None,
                operator_name=None,
                reason=None,
                confirmed_administrators=None,
            ),
        ),
        (
            marker,
            dict(
                deployment_id=deployment,
                preview=False,
                operation_id=operation,
                operator_name="Operator",
                reason="Users revamp",
                confirmed_administrators="admin@example.org,second@example.org",
            ),
        ),
    ]


def test_upgrade_users_command_previews_only_until_enabled(monkeypatch):
    """This release refuses to convert; the preview takes no conversion input."""
    from parishkit.stewardship import operator_commands
    from parishkit.stewardship.deployment import ServiceRole

    monkeypatch.setattr(
        operator_commands,
        "admit_offline_service",
        lambda configuration: ServiceRole.ADMIN_RECOVERY,
    )
    assert operator_commands.USERS_UPGRADE_ENABLED is False
    with pytest.raises(ConfigError, match="previews the users upgrade only"):
        operator_commands.upgrade_users_command(
            object(),
            deployment_id=str(uuid4()),
            preview=False,
            operation_id=str(uuid4()),
            operator_name="Operator",
            reason="Users revamp",
            confirmed_administrators="admin@example.org",
        )
    with pytest.raises(ConfigError, match="no conversion inputs"):
        operator_commands.upgrade_users_command(
            object(), deployment_id=str(uuid4()), preview=True, reason="x"
        )
    monkeypatch.setattr(
        operator_commands, "admit_offline_service", lambda configuration: None
    )
    with pytest.raises(ConfigError, match="admin-recovery profile"):
        operator_commands.upgrade_users_command(
            object(), deployment_id=str(uuid4()), preview=True
        )


def test_only_admin_recovery_reads_identities_and_member_names():
    """The two upgrade definers are granted to the offline login alone."""
    from parishkit.stewardship.deployment import ServiceRole
    from parishkit.stewardship.runtime_grants import (
        MEMBER_NAMES_FUNCTION,
        PORTAL_IDENTITIES_FUNCTION,
        runtime_functions,
    )

    assert runtime_functions(ServiceRole.ADMIN_RECOVERY) == {
        PORTAL_IDENTITIES_FUNCTION,
        MEMBER_NAMES_FUNCTION,
    }
    for role in (
        ServiceRole.WEB,
        ServiceRole.WORKER,
        ServiceRole.SCHEDULER,
        ServiceRole.MAIL_DISPATCH,
        ServiceRole.CONFIG_INSTALLER,
        ServiceRole.BOOTSTRAP,
        "download",
    ):
        assert not runtime_functions(role) & {
            PORTAL_IDENTITIES_FUNCTION,
            MEMBER_NAMES_FUNCTION,
        }

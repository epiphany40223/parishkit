"""Editing a users configuration, without a database (#952 slice 3).

Once converted, a configuration in the users form takes ordinary Admin edits,
credential updates and offline Admin-access recovery in its own request
formats. These tests cover the formats' rules and recovery's patch, preview
and command-line Name.
"""

from uuid import uuid4

import pytest

from parishkit.config import ConfigError
from parishkit.stewardship.accounts import operator_recovery
from parishkit.stewardship.accounts import users_schema as users
from parishkit.stewardship.accounts.configuration_schema import schema_for
from parishkit.stewardship.accounts.request_patch import (
    build_candidate,
    credential_request_schema,
    default_schema,
)

from .policy_factory import address
from .test_users_upgrade import user, version

ADMIN = user()
STAFF = user("staff@example.org", "staff", "Staff, Sam")


def users_version(*records):
    """A validated users configuration with these users (Admin and Staff by default)."""
    return version([dict(record) for record in (records or (ADMIN, STAFF))])


def edit(base, *operations, schema=None):
    """Build the candidate of these login-rule operations, or raise."""
    return build_candidate(
        base,
        [{"section": "login_rules", **operation} for operation in operations],
        candidate_id=uuid4(),
        request_schema=schema,
    )


def roles_of(result):
    """The candidate's users as {email: (name, role)}."""
    return {
        record["values"]["email"]: (record["values"]["name"], record["values"]["role"])
        for record in result.candidate.document()["sections"]["login_rules"]
    }


def test_a_users_configuration_has_its_own_request_formats():
    """Only the base's form chooses them; sign-in rules keep theirs."""
    base = users_version()
    assert default_schema(base, []) == users.REQUEST_SCHEMA
    assert credential_request_schema(base.document()) == users.CREDENTIAL_SCHEMA
    rules = version([address()])
    assert default_schema(rules, []) != users.REQUEST_SCHEMA
    assert credential_request_schema(rules.document()) != users.CREDENTIAL_SCHEMA


def test_admin_edits_add_change_and_remove_users():
    """Ordinary edits stay in the users form, other sections unchanged."""
    base = users_version()
    result = edit(
        base,
        {
            "operation": "update",
            "id": STAFF["id"],
            "values": {"role": "administrator", "name": "Staff, Samuel"},
        },
        {
            "operation": "add",
            "id": str(uuid4()),
            "values": {
                "kind": "user",
                "email": "leader@example.org",
                "name": "Leader",
                "role": "ministry_leader",
            },
        },
    )
    assert schema_for(result.candidate.document()) == users.SCHEMA
    assert roles_of(result) == {
        "admin@example.org": ("Admin", "administrator"),
        "staff@example.org": ("Staff, Samuel", "administrator"),
        "leader@example.org": ("Leader", "ministry_leader"),
    }
    # An Administrator may demote or remove themselves while another remains.
    second = users_version(ADMIN, user("second@example.org"))
    removed = edit(second, {"operation": "remove", "id": ADMIN["id"]})
    assert list(roles_of(removed)) == ["second@example.org"]


def test_admin_edits_reach_the_other_sections():
    """A Parish edit of a users configuration keeps the users unchanged."""
    base = users_version()
    parish = base.document()["sections"]["parish"][0]["id"]
    result = build_candidate(
        base,
        [
            {
                "section": "parish",
                "id": parish,
                "operation": "update",
                "values": {"name": "Renamed Parish"},
            }
        ],
        candidate_id=uuid4(),
    )
    sections = result.candidate.document()["sections"]
    assert sections["parish"][0]["values"]["name"] == "Renamed Parish"
    assert sections["login_rules"] == base.document()["sections"]["login_rules"]


@pytest.mark.parametrize(
    "operation",
    [
        # The last Administrator is never demoted or removed.
        {"operation": "update", "id": ADMIN["id"], "values": {"role": "staff"}},
        {"operation": "remove", "id": ADMIN["id"]},
        # Changing the address is Delete and then New.
        {
            "operation": "update",
            "id": STAFF["id"],
            "values": {"email": "moved@example.org"},
        },
        # A user's address is unique.
        {
            "operation": "add",
            "id": str(uuid4()),
            "values": {
                "kind": "user",
                "email": "staff@example.org",
                "name": "Again",
                "role": "staff",
            },
        },
        # No sign-in rule, deny or empty Name comes back.
        {"operation": "add", "id": str(uuid4()), "values": address()["values"]},
        {"operation": "update", "id": STAFF["id"], "values": {"role": None}},
        {"operation": "update", "id": STAFF["id"], "values": {"name": " "}},
    ],
)
def test_admin_edits_refuse_what_the_users_form_forbids(operation):
    """Each refusal is the generic one, never echoing the submitted values."""
    with pytest.raises(ConfigError):
        edit(users_version(), operation)


def test_an_ordinary_edit_never_converts_sign_in_rules():
    """Adding a user to a configuration of sign-in rules is refused."""
    with pytest.raises(ConfigError):
        edit(
            version([address()]),
            {"operation": "add", "id": str(uuid4()), "values": dict(STAFF["values"])},
        )
    # Nor can a users format be named for a sign-in-rules base: the base
    # itself fails the users schema, even for an edit that keeps no rule.
    rule = address("staff@example.org", ("staff",))
    for schema in (users.REQUEST_SCHEMA, users.RECOVERY_SCHEMA):
        with pytest.raises(ConfigError):
            edit(
                version([address(), rule]),
                {
                    "operation": "update",
                    "id": rule["id"],
                    "values": {"roles": ["administrator"]},
                },
                schema=schema,
            )


def test_a_credential_update_keeps_the_users():
    """The users credential format changes one fingerprint and nothing else."""
    base = users_version()
    integration = base.document()["sections"]["integrations"][0]["id"]
    result = build_candidate(
        base,
        [
            {
                "section": "integrations",
                "id": integration,
                "operation": "update",
                "values": {"credential_fingerprint": "b" * 64},
            }
        ],
        candidate_id=uuid4(),
        request_schema=users.CREDENTIAL_SCHEMA,
    )
    sections = result.candidate.document()["sections"]
    assert sections["integrations"][0]["values"]["credential_fingerprint"] == "b" * 64
    assert sections["login_rules"] == base.document()["sections"]["login_rules"]


def recover(base, email="replacement@example.org", name=None):
    """Build the users recovery candidate the operator command would."""
    patch = operator_recovery._patch(base, email, uuid4(), name)
    return patch, build_candidate(
        base, patch, candidate_id=uuid4(), request_schema=users.RECOVERY_SCHEMA
    )


def test_recovery_adds_an_administrator_user_named_by_default_or_by_the_operator():
    """A new user's Name is ``--name`` or the address's local part."""
    _, result = recover(users_version())
    assert roles_of(result)["replacement@example.org"] == (
        "replacement",
        "administrator",
    )
    _, named = recover(users_version(), name="Doe, Jane")
    assert roles_of(named)["replacement@example.org"] == ("Doe, Jane", "administrator")
    with pytest.raises(ConfigError, match="1 to 200 characters"):
        recover(users_version(), name=" Doe ")


def test_recovery_raises_an_existing_user_and_keeps_everything_else():
    """Only the role changes; the Name is kept and other users are untouched."""
    patch, result = recover(users_version(), "staff@example.org")
    assert patch[0]["values"] == {"role": "administrator"}
    assert roles_of(result) == {
        "admin@example.org": ("Admin", "administrator"),
        "staff@example.org": ("Staff, Sam", "administrator"),
    }
    # Naming the same Name is harmless; another Name is refused.
    recover(users_version(), "staff@example.org", name="Staff, Sam")
    with pytest.raises(ConfigError, match="keeps an existing user's Name"):
        recover(users_version(), "staff@example.org", name="Someone Else")
    with pytest.raises(ConfigError, match="already an Administrator"):
        recover(users_version(), "admin@example.org")


@pytest.mark.parametrize(
    "patch",
    [
        # Not an Administrator.
        [
            {
                "operation": "add",
                "section": "login_rules",
                "id": str(uuid4()),
                "values": {
                    "kind": "user",
                    "email": "new@example.org",
                    "name": "New",
                    "role": "staff",
                },
            }
        ],
        # Raising, but also renaming.
        [
            {
                "operation": "update",
                "section": "login_rules",
                "id": STAFF["id"],
                "values": {"role": "administrator", "name": "Renamed"},
            }
        ],
        # Removing anyone.
        [{"operation": "remove", "section": "login_rules", "id": STAFF["id"]}],
        # Anything outside the users list.
        [
            {
                "operation": "update",
                "section": "parish",
                "id": str(uuid4()),
                "values": {"name": "Renamed"},
            }
        ],
    ],
)
def test_the_users_recovery_format_admits_only_an_administrator_addition(patch):
    """The format repeats the command's rule for any stored or forged patch."""
    with pytest.raises(ConfigError):
        build_candidate(
            users_version(),
            patch,
            candidate_id=uuid4(),
            request_schema=users.RECOVERY_SCHEMA,
        )


def test_the_users_recovery_format_refuses_raising_an_administrator():
    """An existing Administrator has nothing to recover."""
    with pytest.raises(ConfigError):
        build_candidate(
            users_version(),
            [
                {
                    "operation": "update",
                    "section": "login_rules",
                    "id": ADMIN["id"],
                    "values": {"role": "administrator"},
                }
            ],
            candidate_id=uuid4(),
            request_schema=users.RECOVERY_SCHEMA,
        )


def test_recovery_preview_shows_the_users_form():
    """The preview names the form, the Administrators and the resulting Name."""
    base = users_version()
    preview = operator_recovery.recovery_preview(base, uuid4(), "Staff@Example.org")
    assert preview["form"] == "users"
    assert preview["current_admin_rules"] == ["admin@example.org"]
    assert preview["target_email"] == "staff@example.org"
    assert preview["before_roles"] == ["ministry_leader", "staff"]
    assert preview["after_roles"] == ["administrator", "ministry_leader", "staff"]
    assert preview["name"] == "Staff, Sam" and not preview["already_granted"]
    new = operator_recovery.recovery_preview(
        base, uuid4(), "new@example.org", name="New, Person"
    )
    assert (new["before_roles"], new["name"]) == ([], "New, Person")
    assert operator_recovery.recovery_preview(base, uuid4(), "admin@example.org")[
        "already_granted"
    ]


def test_sign_in_rules_recovery_has_no_name():
    """An older configuration's recovery is unchanged and refuses a Name."""
    base = version([address()])
    preview = operator_recovery.recovery_preview(base, uuid4(), "new@example.org")
    assert (preview["form"], preview["name"]) == ("sign_in_rules", None)
    with pytest.raises(ConfigError, match="users form keeps a Name"):
        operator_recovery.recovery_preview(base, uuid4(), "new@example.org", "Name")
    with pytest.raises(ConfigError, match="users form keeps a Name"):
        operator_recovery._patch(base, "new@example.org", uuid4(), "Name")
    assert operator_recovery._patch(base, "new@example.org", uuid4())[0]["values"][
        "kind"
    ] == ("address")


def test_recovery_cli_forwards_the_name(monkeypatch, capsys):
    """``--name`` reaches recover-admin and its preview; it is optional."""
    from parishkit.stewardship import operator_commands
    from parishkit.stewardship.cli import main

    marker, calls = object(), []
    monkeypatch.setattr(operator_commands, "load_deployment", lambda path: marker)
    for name in ("recover_admin_command", "preview_recovery_command"):
        monkeypatch.setattr(
            operator_commands,
            name,
            lambda configuration, _name=name, **values: (
                calls.append((_name, values)) or {"ok": True}
            ),
        )
    deployment = str(uuid4())
    common = ["--config", "c", "--confirm-deployment", deployment]
    common += ["--target-email", "new@example.org"]
    assert main(["preview-admin-recovery", *common, "--name", "New, Person"]) == 0
    assert main(["preview-admin-recovery", *common]) == 0
    capsys.readouterr()
    assert [values["name"] for _, values in calls] == ["New, Person", None]

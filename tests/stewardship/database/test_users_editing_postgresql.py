"""Editing a users configuration on PostgreSQL (#952 slice 3, migration 0051).

After the offline upgrade, Admin edits, credential updates and offline
Admin-access recovery each have a users request format. These tests run them
through the real request intake, configuration installer and SQL guards, and
prove the D2 requirement for recovery: it works on a converted configuration,
and every Administrator before it is still one after it.
"""

from uuid import uuid4

import pytest
from django.db import IntegrityError

from parishkit.config import ConfigError
from parishkit.stewardship.accounts import request_patch
from parishkit.stewardship.accounts import users_upgrade as upgrade
from parishkit.stewardship.accounts.configuration_installation import (
    coherent_configuration,
)
from parishkit.stewardship.accounts.configuration_requests import record_request
from parishkit.stewardship.accounts.operator_recovery import _patch, recover_admin
from parishkit.stewardship.accounts.policy_models import (
    AddressRule,
    AdminRevocation,
    PolicySecurityEvent,
)
from parishkit.stewardship.accounts.request_models import ConfigurationChangeRequest
from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.accounts.users_schema import (
    CREDENTIAL_SCHEMA,
    RECOVERY_SCHEMA,
    REQUEST_SCHEMA,
)

from ..policy_factory import address
from .campaign_builders import change, initialized
from .role_grants import seed_user
from .test_portal_identity_guards_postgresql import forge, refused
from .test_recovery_postgresql import arguments as recovery_arguments
from .test_users_upgrade_postgresql import arguments as upgrade_arguments

pytestmark = pytest.mark.django_db(transaction=True)


def converted(tmp_path):
    """A deployment converted to users: one Administrator and one Staff user."""
    store, _, actor = initialized(
        tmp_path, [address(), address("staff@example.org", ("staff",))]
    )
    assert (
        upgrade.upgrade_users(store, **upgrade_arguments(["admin@example.org"])).state
        == "applied"
    )
    return store, actor


def users(store):
    """The active users as {email: (name, role)}."""
    return {
        record["values"]["email"]: (record["values"]["name"], record["values"]["role"])
        for record in store.active().document()["sections"]["login_rules"]
    }


def record_id(store, email):
    """The active users-list record ID of one address."""
    return next(
        record["id"]
        for record in store.active().document()["sections"]["login_rules"]
        if record["values"]["email"] == email
    )


def portal(email):
    """One enabled recorded Google identity of this address."""
    return seed_user(google_subject=str(uuid4()), email=email)


def test_admin_edits_apply_in_the_users_format(tmp_path):
    """Add, change and remove users; SQL admits exactly the resulting users."""
    store, actor = converted(tmp_path)
    leader = portal("leader@example.org")
    with refused():
        forge(leader.pk)
    result = change(
        store,
        store.active(),
        actor,
        [
            {
                "operation": "add",
                "section": "login_rules",
                "id": str(uuid4()),
                "values": {
                    "kind": "user",
                    "email": "leader@example.org",
                    "name": "Leader, Lee",
                    "role": "ministry_leader",
                },
            },
            {
                "operation": "update",
                "section": "login_rules",
                "id": record_id(store, "staff@example.org"),
                "values": {"role": "administrator", "name": "Staff, Sam"},
            },
        ],
    )
    assert result.state == "applied"
    request = ConfigurationChangeRequest.objects.get(pk=result.request_id)
    assert request.request_schema == REQUEST_SCHEMA
    assert users(store) == {
        "admin@example.org": ("admin", "administrator"),
        "staff@example.org": ("Staff, Sam", "administrator"),
        "leader@example.org": ("Leader, Lee", "ministry_leader"),
    }
    assert forge(leader.pk).principal_id == leader.pk
    # Raising Staff to Administrator raises the usual security event.
    assert PolicySecurityEvent.objects.filter(
        target="staff@example.org", kind="administrator_granted"
    ).exists()
    # Removing the user removes the access.
    removed = change(
        store,
        store.active(),
        actor,
        [
            {
                "operation": "remove",
                "section": "login_rules",
                "id": record_id(store, "leader@example.org"),
            }
        ],
    )
    assert removed.state == "applied"
    with refused():
        forge(leader.pk)


def test_the_last_administrator_is_kept_at_intake(tmp_path):
    """No request that leaves no Administrator is even recorded."""
    store, actor = converted(tmp_path)
    with pytest.raises(ConfigError):
        change(
            store,
            store.active(),
            actor,
            [
                {
                    "operation": "update",
                    "section": "login_rules",
                    "id": record_id(store, "admin@example.org"),
                    "values": {"role": "staff"},
                }
            ],
        )
    assert users(store)["admin@example.org"] == ("admin", "administrator")


def test_a_users_credential_request_is_admitted(tmp_path):
    """The users credential format passes intake and the request CHECKs."""
    store, actor = converted(tmp_path)
    integration = store.active().document()["sections"]["integrations"][0]["id"]
    receipt = record_request(
        base_digest=store.active().digest,
        patch=[
            {
                "operation": "update",
                "section": "integrations",
                "id": integration,
                "values": {"credential_fingerprint": "b" * 64},
            }
        ],
        actor_id=actor,
        request_key=uuid4(),
        correlation_id=uuid4(),
        request_schema=CREDENTIAL_SCHEMA,
    )
    request = ConfigurationChangeRequest.objects.get(pk=receipt.request_id)
    assert request.request_schema == CREDENTIAL_SCHEMA


def test_recovery_adds_an_administrator_user_and_keeps_every_administrator(tmp_path):
    """D2: recovery on a converted configuration, and nobody loses Administrator."""
    store, _ = converted(tmp_path)
    admin = portal("admin@example.org")
    replacement = portal("replacement@example.org")
    with refused():
        forge(replacement.pk)
    previews = []
    result = recover_admin(
        store,
        **recovery_arguments(),
        name="Doe, Jane",
        before_apply=previews.append,
    )
    assert result.state == "applied"
    request = ConfigurationChangeRequest.objects.get(pk=result.request_id)
    assert request.request_schema == RECOVERY_SCHEMA
    assert previews[0]["form"] == "users" and previews[0]["name"] == "Doe, Jane"
    assert previews[0]["current_admin_rules"] == ["admin@example.org"]
    assert users(store) == {
        "admin@example.org": ("admin", "administrator"),
        "staff@example.org": ("staff", "staff"),
        "replacement@example.org": ("Doe, Jane", "administrator"),
    }
    # Both Administrators may hold an Admin session; recovery's evidence and
    # security event are those of any recovery.
    for identity in (admin, replacement):
        assert forge(identity.pk).principal_id == identity.pk
    assert AdminRevocation.objects.filter(activation__request=request).exists()
    assert PolicySecurityEvent.objects.filter(
        target="replacement@example.org", kind="administrator_granted"
    ).exists()
    row = AddressRule.objects.get(
        configuration=SystemConfiguration.objects.get().active_configuration,
        email="replacement@example.org",
    )
    assert (row.role, row.name, row.creation_origin) == (
        "administrator",
        "Doe, Jane",
        None,
    )


def test_a_recovery_replay_previews_the_stored_name(tmp_path):
    """A resumed recovery previews the Name it applies, not this run's --name."""
    store, _ = converted(tmp_path)
    kwargs = recovery_arguments()

    def interrupted(_preview):
        """Stop after the request is stored, before it installs."""
        raise RuntimeError("synthetic operator interruption")

    with pytest.raises(RuntimeError, match="interruption"):
        recover_admin(store, **kwargs, name="Doe, Jane", before_apply=interrupted)
    previews = []
    result = recover_admin(store, **kwargs, before_apply=previews.append)
    assert result.state == "applied"
    assert previews[0]["name"] == "Doe, Jane"
    assert users(store)["replacement@example.org"] == ("Doe, Jane", "administrator")


def test_recovery_raises_an_existing_user(tmp_path):
    """A Staff user becomes an Administrator and keeps their Name."""
    store, _ = converted(tmp_path)
    kwargs = recovery_arguments() | {
        "target_email": "staff@example.org",
        "confirmed_email": "staff@example.org",
    }
    assert recover_admin(store, **kwargs).state == "applied"
    assert users(store)["staff@example.org"] == ("staff", "administrator")
    # A second recovery of an Administrator has nothing to do.
    with pytest.raises(ConfigError, match="already an Administrator"):
        recover_admin(store, **(kwargs | {"operation_id": uuid4()}))


def test_sql_refuses_a_users_recovery_that_changes_more_than_the_role(
    tmp_path, monkeypatch
):
    """The recovery request trigger repeats the format's rule (migration 0051).

    With Python's patch and format checks bypassed, a recovery that renames
    the user it raises is refused when the request is inserted.
    """
    store, _ = converted(tmp_path)

    def renaming(version, email, operation_id, name=None):
        """The real patch, renaming the user as well."""
        patch = _patch(version, email, operation_id, name)
        patch[0]["values"]["name"] = "Renamed"
        return patch

    def unchecked(base, patch, *, candidate_id):
        """The users recovery format without its own rule."""
        return request_patch._build_records(
            base,
            patch,
            candidate_id=candidate_id,
            schema="users-v10",
            sections={"login_rules"},
        )

    from parishkit.stewardship.accounts import operator_recovery

    monkeypatch.setattr(operator_recovery, "_patch", renaming)
    monkeypatch.setattr(
        request_patch,
        "BUILDERS",
        dict(request_patch.BUILDERS) | {RECOVERY_SCHEMA: unchecked},
    )
    kwargs = recovery_arguments() | {
        "target_email": "staff@example.org",
        "confirmed_email": "staff@example.org",
    }
    with pytest.raises(IntegrityError, match="Invalid offline recovery attribution"):
        recover_admin(store, **kwargs)
    assert users(store)["staff@example.org"] == ("staff", "staff")
    assert coherent_configuration(store)


def test_recovery_runs_as_the_admin_recovery_login_in_every_state(
    tmp_path, monkeypatch
):
    """D2 as the real offline login: after a refused upgrade, and after one.

    The admin-recovery login records SQL's refusal of an upgrade and
    restores the base, recovers an Administrator on the sign-in rules, then
    converts and recovers another as a users Administrator. Every
    Administrator before each step is still one after it.
    """
    from parishkit.stewardship.deployment import ServiceRole

    from .test_background_grants_postgresql import task_login
    from .test_recovery_session_grants_postgresql import admit
    from .test_users_upgrade_postgresql import refuse_in_sql

    store, _, _ = initialized(
        tmp_path, [address(), address("second@example.org", ("administrator",))]
    )
    with task_login(ServiceRole.ADMIN_RECOVERY):
        admit()
        with monkeypatch.context() as patched:
            refuse_in_sql(patched)
            refused_result = upgrade.upgrade_users(
                store, **upgrade_arguments(["admin@example.org"])
            )
        assert refused_result.state == "failed"
        coherent_configuration(store)
        kwargs = recovery_arguments()
        assert recover_admin(store, **kwargs).state == "applied"
        administrators = [
            "admin@example.org",
            "replacement@example.org",
            "second@example.org",
        ]
        assert (
            upgrade.upgrade_users(store, **upgrade_arguments(administrators)).state
            == "applied"
        )
        again = recovery_arguments() | {
            "target_email": "third@example.org",
            "confirmed_email": "third@example.org",
        }
        assert recover_admin(store, **again).state == "applied"
    assert {
        email for email, (_, role) in users(store).items() if role == "administrator"
    } == {*administrators, "third@example.org"}

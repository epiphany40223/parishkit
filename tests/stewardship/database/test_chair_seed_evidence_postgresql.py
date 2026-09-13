"""Confirmed seed identity cannot be guessed, rebound, or rewritten by refresh."""

from importlib import import_module
from uuid import uuid4

import pytest
from django.db import DatabaseError, IntegrityError, connection, transaction

from parishkit.stewardship.accounts.chair_models import ChairSeedEvidence
from parishkit.stewardship.accounts.policy_models import MinistryAssignment
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.runtime_grants import runtime_grants
from parishkit.stewardship.storage import StorageInvariantError

from ..policy_factory import address, assignment
from ..test_source_corpus import source
from .campaign_builders import initialized
from .test_configuration_service_postgresql import (
    as_config_installer,
    config_role,  # noqa: F401
)
from .test_current_chair_postgresql import publish, rows
from .test_source_families_postgresql import source_singletons  # noqa: F401

pytestmark = pytest.mark.django_db(transaction=True)


def inputs(tmp_path, *, seeded=True):
    """Represent a previously confirmed seed, not a replacement for ADM-07's UI.

    Schema-owner fixtures can install a seeded root. No deployed online role
    currently creates seeds/evidence; the confirmed request owner is still due.
    """
    _, _, actor = initialized(
        tmp_path,
        [address(), assignment("valid@example.org", 4, seeded=seeded)],
    )
    snapshot = publish(source())
    seed = MinistryAssignment.objects.get()
    return {
        "assignment": seed,
        "assignment_record_id": seed.record_id,
        "snapshot": snapshot,
        "organization_id": 12345,
        "member_duid": 3,
        "roster_keys": [row[-1] for row in rows()],
        "actor_id": actor,
    }


def test_original_identity_and_exact_source_evidence_are_retained(tmp_path):
    """Later contact disappearance leaves the original Member binding intact."""
    values = inputs(tmp_path)
    with work_transaction():
        evidence = ChairSeedEvidence.objects.create(**values)
    data = source()
    data.members[3]["emailAddress"] = ""
    publish(data)
    evidence.refresh_from_db()
    assert evidence.member_duid == 3
    assert evidence.snapshot_id == values["snapshot"].pk
    assert evidence.roster_keys == values["roster_keys"]
    with pytest.raises(StorageInvariantError):
        evidence.save()
    for statement in (
        "UPDATE stewardship_chair_seed_evidence SET member_duid=6",
        "DELETE FROM stewardship_chair_seed_evidence",
    ):
        with (
            pytest.raises(IntegrityError, match="append-only"),
            transaction.atomic(),
            connection.cursor() as cursor,
        ):
            cursor.execute(statement)


@pytest.mark.parametrize(
    "defect",
    [
        "missing_owner",
        "wrong_member",
        "wrong_tenant",
        "wrong_assignment",
        "empty_roster",
        "extra_roster",
        "no_actor",
        "manual_assignment",
        "stale_source",
    ],
)
def test_sql_rejects_unproven_or_misbound_identity(tmp_path, defect):
    """A caller cannot store a plausible-looking Member without exact evidence."""
    values = inputs(tmp_path, seeded=defect != "manual_assignment")
    if defect == "wrong_member":
        values["member_duid"] = 6
    elif defect == "wrong_tenant":
        values["organization_id"] = 98765
    elif defect == "wrong_assignment":
        values["assignment_record_id"] = uuid4()
    elif defect == "empty_roster":
        values["roster_keys"] = []
    elif defect == "extra_roster":
        values["roster_keys"].append("a" * 64)
    elif defect == "no_actor":
        values["actor_id"] = None
    elif defect == "stale_source":
        publish(source())
    with pytest.raises(IntegrityError):
        if defect == "missing_owner":
            ChairSeedEvidence.objects.create(**values)
        else:
            with work_transaction():
                ChairSeedEvidence.objects.create(**values)
    assert not ChairSeedEvidence.objects.exists()


def test_populated_downgrade_keeps_original_binding_and_guards(tmp_path):
    """Retained identity cannot be dropped by silently reversing this schema."""
    values = inputs(tmp_path)
    with work_transaction():
        ChairSeedEvidence.objects.create(**values)
    migration = import_module(
        "parishkit.stewardship.accounts.migrations.0041_chair_seed_evidence_guards"
    )
    with (
        pytest.raises(IntegrityError, match="downgrade"),
        transaction.atomic(),
        connection.cursor() as cursor,
    ):
        cursor.execute(migration.REVERSE)
    with (
        pytest.raises(IntegrityError, match="append-only"),
        transaction.atomic(),
        connection.cursor() as cursor,
    ):
        cursor.execute("UPDATE stewardship_chair_seed_evidence SET member_duid=6")


@pytest.mark.usefixtures("config_role")
def test_online_roles_cannot_create_confirmation_evidence(tmp_path):
    """Source refresh or generic installation must never infer an Admin selection."""
    values = inputs(tmp_path)
    with as_config_installer(), pytest.raises(DatabaseError), work_transaction():
        ChairSeedEvidence.objects.create(**values)
    for role in (ServiceRole.WEB, ServiceRole.WORKER, ServiceRole.CONFIG_INSTALLER):
        tables, _ = runtime_grants(role)
        assert "INSERT" not in tables.get("stewardship_chair_seed_evidence", set())

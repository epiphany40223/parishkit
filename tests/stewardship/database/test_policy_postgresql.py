"""Versioned policy installation, live decisions, atomic alerts and SQL defenses."""

from uuid import UUID, uuid4

import pytest
from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.db.models import F
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from parishkit.config import ConfigError
from parishkit.stewardship.accounts.authority import AuthorityStore
from parishkit.stewardship.accounts.configuration_installation import (
    install_request,
    prepare_initial_configuration,
)
from parishkit.stewardship.accounts.configuration_models import (
    AppliedConfigurationVersion,
)
from parishkit.stewardship.accounts.configuration_requests import (
    policy_operation_id,
    record_request,
)
from parishkit.stewardship.accounts.configuration_schema import validate_sections
from parishkit.stewardship.accounts.configuration_snapshots import (
    is_prepared,
    prepare_snapshot,
)
from parishkit.stewardship.accounts.policy import Capability, allows, current_principal
from parishkit.stewardship.accounts.policy_models import (
    AssignmentOverlay,
    DomainRule,
    PolicyEpoch,
    PolicySecurityEvent,
    PortalUser,
)
from parishkit.stewardship.accounts.policy_projections import stored_policy
from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.storage import StorageInvariantError

from ..configuration_factory import configuration_document, configuration_version
from ..policy_factory import address, assignment, domain

pytestmark = pytest.mark.django_db(transaction=True)


def initialized(tmp_path, records=None):
    """Install synthetic policy through the real manifest/database protocol."""
    document = configuration_document()
    document["sections"]["login_rules"] = [address()] if records is None else records
    version = configuration_version(document)
    store, actor = AuthorityStore(tmp_path, validate_sections), uuid4()
    prepare_initial_configuration(
        store,
        version,
        testing_recipient="test@example.org",
        actor_id=actor,
        correlation_id=uuid4(),
    )
    return store, version, actor


def user(email, hosted=None):
    """Synthetic verified identity metadata; no real OAuth response is consumed."""
    return PortalUser.objects.create(
        google_subject=str(uuid4()),
        email=email,
        hosted_domain=hosted,
        verified_at=timezone.now(),
    )


def change(store, version, actor, patch):
    """Record and install one exact-base intent, retaining its durable receipt."""
    key = uuid4()
    bind_operations(patch, policy_operation_id(actor, key))
    receipt = record_request(
        base_digest=version.digest,
        patch=patch,
        actor_id=actor,
        request_key=key,
        correlation_id=uuid4(),
    )
    result = install_request(
        store, request_id=receipt.request_id, correlation_id=uuid4()
    )
    return result


def bind_operations(patch, operation_id):
    """Make valid new manual test records refer to their actual request identity."""
    for operation in patch:
        if operation["section"] != "login_rules" or operation["operation"] != "add":
            continue
        values = operation["values"]
        if values["kind"] == "address":
            values["creation_operation"] = str(operation_id)
            for origins in values["grants"].values():
                if "manual" in origins:
                    origins["manual"] = str(operation_id)
        elif values["kind"] == "assignment":
            values["operation_id"] = str(operation_id)


def test_prepared_policy_is_exact_and_immutable(tmp_path):
    """All normalized policy rows round-trip to their authoritative YAML record IDs."""
    store, version, actor = initialized(tmp_path, [address(), domain(), assignment()])
    snapshot = AppliedConfigurationVersion.objects.get(pk=version.version_id)
    assert snapshot.validation_schema == "foundation-policy-v2"
    assert stored_policy(snapshot) == version.document()["sections"]["login_rules"]
    assert is_prepared(version.digest)
    with pytest.raises(StorageInvariantError):
        DomainRule.objects.all().delete()
    with (
        pytest.raises(IntegrityError),
        transaction.atomic(),
        connection.cursor() as cursor,
    ):
        cursor.execute("UPDATE stewardship_domain_rule SET domain = 'other.org'")
    with pytest.raises(IntegrityError, match="matching YAML"), transaction.atomic():
        DomainRule.objects.create(
            configuration=snapshot,
            record_id=uuid4(),
            domain="other.org",
            roles=["staff"],
        )


def test_domain_and_exact_policy_take_effect_on_next_lookup(tmp_path):
    """A newly applied exact denial supersedes previously usable domain policy."""
    store, version, actor = initialized(tmp_path, [address(), domain()])
    member = user("member@example.org", "example.org")
    with CaptureQueriesContext(connection) as queries:
        assert allows(current_principal(store, member.pk), Capability.FAMILY_CODES)
    assert (
        sum('FROM "stewardship_address_rule"' in query["sql"] for query in queries) == 1
    )
    denial = address("member@example.org", ())
    result = change(
        store,
        version,
        actor,
        [{"operation": "add", "section": "login_rules", **denial}],
    )
    assert result.state == "applied"
    assert not current_principal(store, member.pk).roles
    assert not current_principal(store, user("external@example.org").pk).roles


def test_seed_overlay_immediately_removes_scope_without_rewriting_yaml(tmp_path):
    """Source-derived scope changes independently of configured grant provenance."""
    seed = assignment(seeded=True)
    store, version, _ = initialized(
        tmp_path,
        [
            address(),
            address("leader@example.org", ("ministry_leader",), seeded=True),
            seed,
        ],
    )
    leader = user("leader@example.org")
    assert not current_principal(store, leader.pk).roles
    overlay = AssignmentOverlay.objects.create(
        assignment_record_id=UUID(seed["id"]),
        active=True,
        source_snapshot_id=uuid4(),
        reason="chair_present",
    )
    assert allows(
        current_principal(store, leader.pk), Capability.MINISTRY_REPORT, ministry_id=123
    )
    AssignmentOverlay.objects.filter(pk=overlay.pk).update(
        active=False, reason="chair_missing", version=F("version") + 1
    )
    assert not current_principal(store, leader.pk).roles
    assert store.active().digest == version.digest


def test_admin_expansion_creates_atomic_alert_and_namespace(tmp_path):
    """Committed high-impact changes leave one alert even after a receipt retry."""
    store, version, actor = initialized(tmp_path)
    before = PolicyEpoch.objects.count()
    addition = address("replacement@example.org")
    result = change(
        store,
        version,
        actor,
        [{"operation": "add", "section": "login_rules", **addition}],
    )
    event = PolicySecurityEvent.objects.get(target="replacement@example.org")
    assert event.recipients == ["admin@example.org"]
    assert event.actor_id == actor and event.before_roles == []
    assert event.after_roles == ["administrator"]
    assert PolicyEpoch.objects.count() == before + 1
    assert event.activation.configuration_id == result.applied_version_id
    # Acknowledgement loss cannot generate a second notification intent.
    install_request(store, request_id=result.request_id, correlation_id=uuid4())
    assert (
        PolicySecurityEvent.objects.filter(target="replacement@example.org").count()
        == 1
    )


def test_last_admin_removal_is_rejected_before_intake(tmp_path):
    """The configured deployment cannot remove its final explicit Administrator."""
    store, version, actor = initialized(tmp_path)
    admin = version.document()["sections"]["login_rules"][0]
    with pytest.raises(ConfigError):
        change(
            store,
            version,
            actor,
            [{"operation": "remove", "section": "login_rules", "id": admin["id"]}],
        )
    assert store.active().digest == version.digest


def test_policy_notification_failure_rolls_back_activation(tmp_path):
    """A failed durable intent cannot leave expanded authority silently active."""
    store, version, actor = initialized(tmp_path)
    addition = address("next@example.org")
    key = uuid4()
    patch = [{"operation": "add", "section": "login_rules", **addition}]
    bind_operations(patch, policy_operation_id(actor, key))
    receipt = record_request(
        base_digest=version.digest,
        patch=patch,
        actor_id=actor,
        request_key=key,
        correlation_id=uuid4(),
    )
    with connection.cursor() as cursor:
        cursor.execute(
            "ALTER TABLE stewardship_policy_security_event "
            "ADD CONSTRAINT synthetic_fail "
            "CHECK (target <> 'next@example.org') NOT VALID"
        )
    try:
        with pytest.raises(IntegrityError):
            install_request(
                store, request_id=receipt.request_id, correlation_id=uuid4()
            )
        assert (
            SystemConfiguration.objects.get().active_configuration_id
            == version.version_id
        )
        assert not PolicySecurityEvent.objects.filter(
            target="next@example.org"
        ).exists()
    finally:
        with connection.cursor() as cursor:
            cursor.execute(
                "ALTER TABLE stewardship_policy_security_event "
                "DROP CONSTRAINT synthetic_fail"
            )
    assert (
        install_request(
            store, request_id=receipt.request_id, correlation_id=uuid4()
        ).state
        == "applied"
    )


def test_partial_policy_cannot_commit(tmp_path):
    """Deferred completeness prevents even raw preparation missing grant records."""
    document = configuration_document()
    document["sections"]["login_rules"] = [address()]
    version = configuration_version(document)
    with pytest.raises(IntegrityError, match="complete"), transaction.atomic():
        AppliedConfigurationVersion.objects.create(
            id=version.version_id,
            digest=version.digest,
            schema_version=1,
            canonical_document=version.document(),
            normalized_digest="a" * 64,
            validation_schema="foundation-policy-v2",
        )
    assert not AppliedConfigurationVersion.objects.exists()


def test_policy_downgrade_refuses_before_removing_guards(tmp_path):
    """Populated policy evidence survives a refused historical schema rollback."""
    store, version, _ = initialized(tmp_path)
    leaves = MigrationExecutor(connection).loader.graph.leaf_nodes()
    try:
        with pytest.raises(IntegrityError, match="Policy security history"):
            MigrationExecutor(connection).migrate(
                [("stewardship_accounts", "0018_policy_activation_evidence")]
            )
        assert is_prepared(version.digest)
        with (
            pytest.raises(IntegrityError),
            transaction.atomic(),
            connection.cursor() as cursor,
        ):
            cursor.execute("DELETE FROM stewardship_policy_security_event")
    finally:
        MigrationExecutor(connection).migrate(leaves)


def test_legacy_preparation_can_introduce_policy(tmp_path):
    """Old schema materializations remain verifiable across an explicit upgrade."""
    store, actor, old = (
        AuthorityStore(tmp_path, validate_sections),
        uuid4(),
        configuration_version(),
    )
    prepare_initial_configuration(
        store,
        old,
        testing_recipient="test@example.org",
        actor_id=actor,
        correlation_id=uuid4(),
    )
    rule = address()
    result = change(
        store, old, actor, [{"operation": "add", "section": "login_rules", **rule}]
    )
    assert is_prepared(old.digest) and is_prepared(result.applied_digest)
    # Pre-policy historical fixtures have no prior Admin recipients. Preserve
    # that fact rather than treating the Testing routing address as an Admin.
    event = PolicySecurityEvent.objects.get(target="admin@example.org")
    assert event.recipients == [] and event.kind == "administrator_granted"
    assert (
        prepare_snapshot(old, actor_id=actor, correlation_id=uuid4()).validation_schema
        == "parish-integrations-v1"
    )


def test_mixed_case_seed_lookup_uses_same_normalized_identity(tmp_path):
    """Google email case cannot make the overlay query disagree with policy matching."""
    seed = assignment(seeded=True)
    store, _, _ = initialized(
        tmp_path,
        [
            address(),
            address("leader@example.org", ("ministry_leader",), seeded=True),
            seed,
        ],
    )
    leader = user("Leader@Example.org")
    AssignmentOverlay.objects.create(
        assignment_record_id=UUID(seed["id"]),
        active=True,
        source_snapshot_id=uuid4(),
        reason="chair_present",
    )
    assert current_principal(store, leader.pk).ministries == {123}


def test_manual_provenance_must_identify_its_request(tmp_path):
    """A syntactically valid arbitrary UUID cannot impersonate an originating intent."""
    store, version, actor = initialized(tmp_path)
    for rule in (address("next@example.org"), assignment()):
        with pytest.raises(ConfigError):
            record_request(
                base_digest=version.digest,
                patch=[{"operation": "add", "section": "login_rules", **rule}],
                actor_id=actor,
                request_key=uuid4(),
                correlation_id=uuid4(),
            )


def test_retired_policy_id_rebinding_is_rejected_at_intake(tmp_path):
    """Invalid ancestry cannot leave an uncancellable validating request."""
    retired = domain()
    store, version, actor = initialized(tmp_path, [address(), retired])
    result = change(
        store,
        version,
        actor,
        [{"operation": "remove", "section": "login_rules", "id": retired["id"]}],
    )
    retired["values"]["domain"] = "different.org"
    with pytest.raises(ConfigError, match="identities"):
        record_request(
            base_digest=result.applied_digest,
            patch=[{"operation": "add", "section": "login_rules", **retired}],
            actor_id=actor,
            request_key=uuid4(),
            correlation_id=uuid4(),
        )


def test_installer_rechecks_policy_ancestry_and_records_terminal_failure(
    tmp_path, monkeypatch
):
    """A request inserted through a defective intake still cannot wedge installation."""
    from parishkit.stewardship.accounts import configuration_requests

    retired = domain()
    store, version, actor = initialized(tmp_path, [address(), retired])
    change(
        store,
        version,
        actor,
        [{"operation": "remove", "section": "login_rules", "id": retired["id"]}],
    )
    retired["values"]["domain"] = "different.org"
    with monkeypatch.context() as patcher:
        patcher.setattr(
            configuration_requests, "check_historical_additions", lambda *args: None
        )
        receipt = record_request(
            base_digest=store.active().digest,
            patch=[{"operation": "add", "section": "login_rules", **retired}],
            actor_id=actor,
            request_key=uuid4(),
            correlation_id=uuid4(),
        )
    before = store.active().digest
    result = install_request(
        store, request_id=receipt.request_id, correlation_id=uuid4()
    )
    assert result.state == "failed" and result.failure_code == "invalid_candidate"
    assert store.active().digest == before


def test_manual_admin_update_binds_new_origin_only(tmp_path):
    """Admin additions cite the actual request without changing existing origins."""
    member = address("staff@example.org", ("staff",))
    store, version, actor = initialized(tmp_path, [address(), member])
    key = uuid4()
    values = {
        "roles": ["administrator", "staff"],
        "grants": member["values"]["grants"]
        | {"administrator": {"manual": str(uuid4())}},
    }
    patch = [
        {
            "operation": "update",
            "section": "login_rules",
            "id": member["id"],
            "values": values,
        }
    ]
    kwargs = dict(
        base_digest=version.digest,
        patch=patch,
        actor_id=actor,
        request_key=key,
        correlation_id=uuid4(),
    )
    with pytest.raises(ConfigError):
        record_request(**kwargs)
    values["grants"]["administrator"]["manual"] = str(policy_operation_id(actor, key))
    receipt = record_request(**kwargs)
    assert receipt.request_id == policy_operation_id(actor, key)
    assert (
        install_request(
            store, request_id=receipt.request_id, correlation_id=uuid4()
        ).state
        == "applied"
    )


def test_missing_grant_projection_cannot_commit(tmp_path, monkeypatch):
    """Matching rule counts do not compensate for an absent per-role origin row."""
    from parishkit.stewardship.accounts.policy_models import AddressRoleGrant

    monkeypatch.setattr(AddressRoleGrant.objects, "bulk_create", lambda records: [])
    with pytest.raises(IntegrityError, match="grant provenance is incomplete"):
        initialized(tmp_path)
    assert not AppliedConfigurationVersion.objects.exists()


@pytest.mark.parametrize("restoration", ["assignment", "independent_role"])
def test_unsuppressing_seeded_login_advances_denial_epoch(tmp_path, restoration):
    """Unchanged role arrays can still become effective through scope or provenance."""
    leader = address("leader@example.org", ("ministry_leader",), seeded=True)
    store, version, actor = initialized(tmp_path, [address(), leader])
    identity = user("leader@example.org")
    assert not current_principal(store, identity.pk).roles
    before = PolicyEpoch.objects.count()
    if restoration == "assignment":
        change(
            store,
            version,
            actor,
            [{"operation": "add", "section": "login_rules", **assignment()}],
        )
    else:
        key = uuid4()
        grants = {
            "ministry_leader": leader["values"]["grants"]["ministry_leader"]
            | {"manual": str(policy_operation_id(actor, key))}
        }
        receipt = record_request(
            base_digest=version.digest,
            patch=[
                {
                    "operation": "update",
                    "section": "login_rules",
                    "id": leader["id"],
                    "values": {"grants": grants},
                }
            ],
            actor_id=actor,
            request_key=key,
            correlation_id=uuid4(),
        )
        assert (
            install_request(
                store, request_id=receipt.request_id, correlation_id=uuid4()
            ).state
            == "applied"
        )
    assert "ministry_leader" in current_principal(store, identity.pk).roles
    assert PolicyEpoch.objects.count() == before + 1

"""The offline users upgrade on PostgreSQL (#952, migration 0047).

The Administrator's acceptance criterion: every address that holds
Administrator before the upgrade can still sign in as Administrator after
it, and the conversion refuses rather than drop one. These tests run the real
conversion through the configuration installer and prove the result both in
Python (the sign-in evaluator) and in SQL (the Admin session guard), and that
SQL's own activation guard refuses an upgrade that would lose an
Administrator even if Python let one through.
"""

from uuid import uuid4

import pytest
from django.db import DatabaseError, IntegrityError, connection, transaction

from parishkit.config import ConfigError
from parishkit.stewardship.accounts import request_patch
from parishkit.stewardship.accounts import users_upgrade as upgrade
from parishkit.stewardship.accounts.configuration_installation import (
    coherent_configuration,
)
from parishkit.stewardship.accounts.configuration_models import (
    AppliedConfigurationVersion,
)
from parishkit.stewardship.accounts.policy import resolve_roles
from parishkit.stewardship.accounts.policy_models import AddressRule
from parishkit.stewardship.accounts.policy_projections import stored_policy
from parishkit.stewardship.accounts.request_models import ConfigurationChangeRequest
from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.accounts.users_schema import (
    SCHEMA,
    UPGRADE_REQUEST_SCHEMA,
)
from parishkit.stewardship.audit.models import AuditEvent
from parishkit.stewardship.deployment import ServiceRole

from ..policy_factory import address, domain
from .campaign_builders import add_draft, change, initialized
from .role_grants import seed_user
from .test_background_grants_postgresql import task_login
from .test_portal_identity_guards_postgresql import forge, refused
from .test_recovery_session_grants_postgresql import admit

pytestmark = pytest.mark.django_db(transaction=True)

ADMINS = ["admin@example.org", "second@example.org"]


def rules():
    """Sign-in rules of every kind the upgrade converts or drops."""
    return [
        address("admin@example.org"),
        address("second@example.org", ("administrator", "staff")),
        address("staff@example.org", ("staff",)),
        address("denied@example.org", ()),
        domain("example.org", ("ministry_leader", "staff")),
    ]


def identity(email, hosted="example.org", *, disabled=False, signed_in=True):
    """One recorded Google identity, with an Admin sign-in when it had one."""
    portal = seed_user(
        google_subject=str(uuid4()),
        email=email,
        hosted_domain=hosted,
        disabled=disabled,
    )
    if signed_in:
        AuditEvent.objects.create(event_type="admin_login", actor_id=portal.pk)
    return portal


def arguments(administrators=ADMINS, operation_id=None):
    """Bind one operation to a synthetic operator and confirmed deployment."""
    return dict(
        operation_id=uuid4() if operation_id is None else operation_id,
        operator_name="Test operator",
        reason="Users revamp",
        deployment_id=SystemConfiguration.objects.get().pk,
        confirmed_administrators=administrators,
        correlation_id=uuid4(),
        offline_interlock=_offline,
    )


def _offline():
    """No application services run against this disposable test deployment."""
    from contextlib import nullcontext

    return nullcontext()


def active_users(store):
    """The active users as {email: (name, role)}."""
    return {
        record["values"]["email"]: (record["values"]["name"], record["values"]["role"])
        for record in store.active().document()["sections"]["login_rules"]
    }


def test_upgrade_keeps_every_administrator_and_converts_the_rules(tmp_path):
    """Administrators keep Administrator in Python and in SQL; the rest converts."""
    store, _, _ = initialized(tmp_path, rules())
    portals = {
        "admin@example.org": identity("admin@example.org"),
        # An Administrator who never signed in, without a hosted claim, keeps
        # Administrator too: an address rule always converts.
        "second@example.org": identity("second@example.org", None, signed_in=False),
        "signed@example.org": identity("signed@example.org"),
        "never@example.org": identity("never@example.org", signed_in=False),
        "disabled@example.org": identity("disabled@example.org", disabled=True),
        "denied@example.org": identity("denied@example.org"),
    }
    # Before: both Administrators may hold an Admin session.
    for email in ADMINS:
        forge(portals[email].pk)
    previews = []
    result = upgrade.upgrade_users(store, **arguments(), before_apply=previews.append)
    assert result.state == "applied"
    preview = previews[0]
    assert preview["administrators"] == ADMINS
    assert preview["not_converted"] == {
        "disabled": ["disabled@example.org"],
        "never_signed_in": ["never@example.org"],
    }
    assert preview["dropped_deny_rules"] == [
        {"email": "denied@example.org", "overrides_identity": True, "name": "denied"}
    ]
    runtime = SystemConfiguration.objects.get()
    assert runtime.active_configuration.validation_schema == SCHEMA
    assert active_users(store) == {
        "admin@example.org": ("admin", "administrator"),
        "second@example.org": ("second", "administrator"),
        "staff@example.org": ("staff", "staff"),
        "signed@example.org": ("signed", "staff"),
    }
    # The projection holds each user's cumulative roles and round-trips.
    rows = AddressRule.objects.filter(configuration=runtime.active_configuration)
    assert {row.email: row.roles for row in rows if row.role == "administrator"} == {
        email: ["administrator", "ministry_leader", "staff"] for email in ADMINS
    }
    snapshot = AppliedConfigurationVersion.objects.get(
        pk=runtime.active_configuration_id
    )
    assert stored_policy(snapshot) == sorted(
        store.active().document()["sections"]["login_rules"],
        key=lambda record: record["id"],
    )
    # After: every Administrator still resolves to Administrator, and SQL's
    # Admin session guard still admits each of them.
    records = store.active().document()["sections"]["login_rules"]
    for email in ADMINS:
        roles, _ = resolve_roles(email, portals[email].hosted_domain, records)
        assert "administrator" in roles
        assert forge(portals[email].pk).principal_id == portals[email].pk
    # A converted domain person signs in as Staff; the rest are denied.
    assert forge(portals["signed@example.org"].pk)
    for email in ("never@example.org", "denied@example.org"):
        with refused():
            forge(portals[email].pk)
    # The request is the operator's, not a portal user's, and one audit
    # event records the conversion.
    request = ConfigurationChangeRequest.objects.get(pk=result.request_id)
    assert request.authority == "operator_upgrade" and request.actor_id is None
    assert request.request_schema == UPGRADE_REQUEST_SCHEMA
    assert (request.operator_name, request.operator_reason) == (
        "Test operator",
        "Users revamp",
    )
    assert (
        AuditEvent.objects.filter(
            event_type="operator_users_upgraded", subject_id=request.pk
        ).count()
        == 1
    )


def test_a_repeat_resumes_and_a_converted_configuration_is_left_alone(tmp_path):
    """The same operation replays its receipt; a new one is a no-op."""
    store, _, _ = initialized(tmp_path, rules())
    kwargs = arguments()
    result = upgrade.upgrade_users(store, **kwargs)
    assert upgrade.upgrade_users(store, **kwargs) == result
    digest = store.active().digest
    assert upgrade.upgrade_users(store, **arguments()) is None
    assert store.active().digest == digest
    assert AuditEvent.objects.filter(event_type="operator_users_upgraded").count() == 1
    # A replay must name the same operator, reason and deployment.
    with pytest.raises(ConfigError, match="bound to another intent"):
        upgrade.upgrade_users(store, **(kwargs | {"reason": "Something else"}))


@pytest.mark.parametrize(
    "administrators",
    [["admin@example.org"], [*ADMINS, "staff@example.org"], ["Admin@Example.org"]],
)
def test_the_operator_must_confirm_exactly_the_administrators(tmp_path, administrators):
    """A list that differs from the preview's refuses before any request."""
    store, version, _ = initialized(tmp_path, rules())
    with pytest.raises(ConfigError, match="confirmed Administrators"):
        upgrade.upgrade_users(store, **arguments(administrators))
    assert not ConfigurationChangeRequest.objects.filter(
        authority="operator_upgrade"
    ).exists()
    assert store.active().digest == version.digest


def refuse_in_sql(monkeypatch):
    """Let one demoted Administrator past Python, so only SQL refuses (#952).

    The plan demotes the second Administrator and the request builder is
    replaced by one that does not check, so Python and SQL disagree.
    """
    plan = upgrade.plan_upgrade

    def demoting(records, identities, names):
        """The real plan with the second Administrator demoted to Staff."""
        result = plan(records, identities, names)
        result["users"] = [
            item | {"role": "staff"} if item["email"] == ADMINS[1] else item
            for item in result["users"]
        ]
        result["administrators"] = ADMINS[:1]
        return result

    def unchecked(base, patch, *, candidate_id):
        """The upgrade format without its Administrator check."""
        return request_patch._build_records(
            base,
            patch,
            candidate_id=candidate_id,
            schema=SCHEMA,
            sections={"login_rules"},
            base_schema="foundation-policy-v2",
            limit=request_patch.UPGRADE_LIMIT,
        )

    monkeypatch.setattr(upgrade, "plan_upgrade", demoting)
    monkeypatch.setattr(
        request_patch,
        "BUILDERS",
        dict(request_patch.BUILDERS) | {UPGRADE_REQUEST_SCHEMA: unchecked},
    )


def test_sql_refuses_an_upgrade_that_drops_an_administrator(tmp_path, monkeypatch):
    """Even with Python's checks bypassed, the activation guard keeps every Admin.

    SQL's stewardship_users_form_v1 refuses, the refusal is recorded as the
    request's failure, and the base manifest is selected again (migration
    0051): the deployment stays coherent on its sign-in rules, so offline
    Admin-access recovery still runs, and the upgrade itself fails closed.
    """
    from parishkit.stewardship.accounts.operator_recovery import recover_admin

    from .test_recovery_postgresql import arguments as recovery_arguments

    store, version, _ = initialized(tmp_path, rules())
    refuse_in_sql(monkeypatch)
    result = upgrade.upgrade_users(store, **arguments(ADMINS[:1]))
    assert (result.state, result.failure_code) == ("failed", "invalid_candidate")
    runtime = coherent_configuration(store)
    assert runtime.active_configuration.validation_schema == "foundation-policy-v2"
    assert runtime.active_configuration_id == version.version_id
    assert store.active().digest == version.digest
    assert not AuditEvent.objects.filter(event_type="operator_users_upgraded").exists()
    # The failed receipt is terminal for its operation.
    request = ConfigurationChangeRequest.objects.get(pk=result.request_id)
    assert (
        upgrade.upgrade_users(
            store, **arguments(ADMINS[:1], operation_id=request.request_key)
        )
        == result
    )
    assert recover_admin(store, **recovery_arguments()).state == "applied"


def test_recovery_runs_with_a_refused_upgrade_still_selected(tmp_path, monkeypatch):
    """A crash between the recorded refusal and the restore cannot lock anyone out.

    The manifest is put back on the refused candidate, as a crash right
    after the refusal committed would leave it: nothing is coherent, yet the
    recovery preview reads the base and recovery itself restores the base
    first, then applies. Another disagreement still refuses. All of it runs
    as the real admin-recovery login.
    """
    from parishkit.stewardship.accounts.operator_recovery import (
        recover_admin,
        recovery_preview,
        recovery_runtime,
    )
    from parishkit.stewardship.accounts.request_admission import intake_base

    from .test_recovery_postgresql import arguments as recovery_arguments

    store, version, _ = initialized(tmp_path, rules())
    refuse_in_sql(monkeypatch)
    with task_login(ServiceRole.ADMIN_RECOVERY):
        admit()
        result = upgrade.upgrade_users(store, **arguments(ADMINS[:1]))
        request = ConfigurationChangeRequest.objects.get(pk=result.request_id)
        store.select(store.read_version(request.candidate_version_id))
        with pytest.raises(ConfigError):
            coherent_configuration(store)
        runtime = recovery_runtime(store)
        assert runtime.active_configuration_id == version.version_id
        _, base = intake_base(runtime.active_configuration.digest)
        preview = recovery_preview(base, runtime.pk, "replacement@example.org")
        assert preview["current_admin_rules"] == ADMINS
        previews = []
        applied = recover_admin(
            store, **recovery_arguments(), before_apply=previews.append
        )
        assert applied.state == "applied" and previews[0]["form"] == "sign_in_rules"
        active = coherent_configuration(store).active_configuration
        assert active.predecessor_id == version.version_id
        # Only a refused request's candidate is tolerated: any other
        # selection still refuses, as the installer protocol must resolve it
        # first.
        store.select(store.read_version(version.version_id))
        with pytest.raises(ConfigError):
            recovery_runtime(store)


class Crash(BaseException):
    """A process death: nothing in the installer may catch or record it."""


def abort_activation(monkeypatch, failure):
    """Make the upgrade's activation transaction abort with ``failure``.

    ``failure`` runs inside the activation, in place of the users
    conversion, so the transaction rolls back with the candidate selected.
    """
    from parishkit.stewardship.accounts.configuration_installation import (
        DatabaseMaterializer,
    )

    monkeypatch.setattr(
        DatabaseMaterializer,
        "_apply_upgrade",
        lambda self, runtime, selected: failure(),
    )


def statements(sql):
    """A failure that runs SQL statements inside the activation."""

    def run():
        """Execute each statement on the activation's connection."""
        with connection.cursor() as cursor:
            for each in sql:
                cursor.execute(each)

    return run


@pytest.mark.parametrize(
    "sql",
    [
        # A raise other than 23514: division by zero (22012).
        ["SELECT 1/0"],
        # A statement timeout (57014).
        ["SET LOCAL statement_timeout = '10ms'", "SELECT pg_sleep(1)"],
    ],
)
def test_an_unrefused_activation_abort_restores_the_base(tmp_path, monkeypatch, sql):
    """Any other abort fails the upgrade and selects the base again (#952).

    The manifest already names the candidate when the activation aborts, so
    without this the deployment stays incoherent and recovery cannot run.
    """
    from parishkit.stewardship.accounts.operator_recovery import recover_admin

    from .test_recovery_postgresql import arguments as recovery_arguments

    store, version, _ = initialized(tmp_path, rules())
    admin = identity("admin@example.org")
    abort_activation(monkeypatch, statements(sql))
    with task_login(ServiceRole.ADMIN_RECOVERY):
        admit()
        result = upgrade.upgrade_users(store, **arguments())
        assert (result.state, result.failure_code) == ("failed", "invalid_candidate")
        runtime = coherent_configuration(store)
        assert runtime.active_configuration_id == version.version_id
        assert store.active().digest == version.digest
        assert recover_admin(store, **recovery_arguments()).state == "applied"
    assert forge(admin.pk).principal_id == admin.pk


def test_recovery_undoes_an_upgrade_interrupted_before_activation(
    tmp_path, monkeypatch
):
    """A crash after the YAML switch leaves recovery able to run (#952).

    The activation dies without recording anything, as a process death or
    a lost connection would leave it: the request stays yaml_activated with
    its candidate selected over the unchanged base. A new upgrade says to
    resume or recover; recovery, as the real admin-recovery login, fails
    the upgrade, restores the base and applies, and the Administrator keeps
    access.
    """
    from parishkit.stewardship.accounts.configuration_requests import _status
    from parishkit.stewardship.accounts.operator_recovery import (
        recover_admin,
        recovery_runtime,
    )

    from .test_recovery_postgresql import arguments as recovery_arguments

    store, version, _ = initialized(tmp_path, rules())
    admin = identity("admin@example.org")

    def crash():
        """Die inside the activation transaction."""
        raise Crash

    abort_activation(monkeypatch, crash)
    with task_login(ServiceRole.ADMIN_RECOVERY):
        admit()
        kwargs = arguments()
        with pytest.raises(Crash):
            upgrade.upgrade_users(store, **kwargs)
        request = ConfigurationChangeRequest.objects.get(
            authority="operator_upgrade", request_key=kwargs["operation_id"]
        )
        assert _status(request).state == "yaml_activated"
        assert store.active().version_id == request.candidate_version_id
        with pytest.raises(ConfigError):
            coherent_configuration(store)
        with pytest.raises(ConfigError, match="resume it, or recover-admin"):
            upgrade.upgrade_users(store, **arguments())
        assert recovery_runtime(store).active_configuration_id == version.version_id
        recovery = recover_admin(store, **recovery_arguments())
        assert recovery.state == "applied"
        status = _status(request)
        assert (status.state, status.failure_code) == ("failed", "invalid_candidate")
        active = coherent_configuration(store).active_configuration
        assert active.predecessor_id == version.version_id
        assert active.validation_schema == "foundation-policy-v2"
        # The failed receipt is terminal for its operation.
        assert upgrade.upgrade_users(store, **kwargs).state == "failed"
    assert forge(admin.pk).principal_id == admin.pk


def test_an_interrupted_upgrade_resumes(tmp_path, monkeypatch):
    """The other way out: the same operation ID finishes the upgrade."""
    store, _, _ = initialized(tmp_path, rules())

    def crash():
        """Die inside the activation transaction."""
        raise Crash

    with monkeypatch.context() as patch:
        abort_activation(patch, crash)
        kwargs = arguments()
        with pytest.raises(Crash):
            upgrade.upgrade_users(store, **kwargs)
    assert upgrade.upgrade_users(store, **kwargs).state == "applied"
    assert coherent_configuration(store).active_configuration.validation_schema == (
        SCHEMA
    )


def test_sql_refuses_an_ordinary_activation_into_the_users_form(tmp_path, monkeypatch):
    """Only an operator upgrade may activate a users configuration over rules.

    An ordinary Admin request whose builder is replaced by one that converts
    (so Python lets it through) is refused by SQL's activation guard, and the
    deployment stays on its sign-in rules.
    """
    store, version, actor = initialized(tmp_path, rules())
    records = version.document()["sections"]["login_rules"]
    plan = upgrade.plan_upgrade(records, [], {})

    def converting(base, patch, *, candidate_id):
        """An ordinary policy format that writes the users form."""
        return request_patch._build_records(
            base,
            patch,
            candidate_id=candidate_id,
            schema=SCHEMA,
            sections={"login_rules"},
            base_schema="foundation-policy-v2",
            limit=request_patch.UPGRADE_LIMIT,
        )

    monkeypatch.setattr(
        request_patch,
        "BUILDERS",
        dict(request_patch.BUILDERS)
        | {request_patch.POLICY_REQUEST_SCHEMA: converting},
    )
    with pytest.raises(IntegrityError, match="Only the offline users upgrade"):
        change(store, version, actor, upgrade.upgrade_patch(records, plan))
    # The YAML manifest already names the refused candidate while the
    # database keeps the sign-in rules, so the deployment is incoherent and
    # no service starts on it: the upgrade fails closed.
    with pytest.raises(ConfigError):
        coherent_configuration(store)
    runtime = SystemConfiguration.objects.get()
    assert runtime.active_configuration_id == version.version_id


def test_the_upgrade_runs_as_the_admin_recovery_login(tmp_path):
    """Its narrow definers stand in for audit-log and source reads it lacks."""
    store, _, _ = initialized(tmp_path, rules())
    identity("signed@example.org")
    with task_login(ServiceRole.ADMIN_RECOVERY):
        admit()
        for statement in (
            "SELECT actor_id FROM stewardship_audit_event",
            "SELECT 1 FROM stewardship_source_member",
        ):
            with (
                pytest.raises(DatabaseError, match="permission denied"),
                transaction.atomic(),
                connection.cursor() as cursor,
            ):
                cursor.execute(statement)
        result = upgrade.upgrade_users(store, **arguments())
        assert result.state == "applied"
    assert "signed@example.org" in active_users(store)


def test_member_names_follow_the_default_order(tmp_path):
    """Active Family first; the address matches trimmed and case-insensitively.

    The one lookup covers exactly the active rules' addresses (a deny rule's
    too) and the recorded identities' addresses: a Member address outside
    them yields nothing, so no caller can use it to learn who an arbitrary
    address belongs to.
    """
    from parishkit.stewardship.campaigns.models import Campaign
    from parishkit.stewardship.source.models import SourceCurrent, SourceMutationLease

    from .credential_builders import keys
    from .leader_builders import leader, with_leaders
    from .response_builders import response_source
    from .test_source_families_postgresql import prepare, promote

    store, _, _ = initialized(
        tmp_path,
        [
            address(),
            address("shared@example.org", ("staff",)),
            address("denied@example.org", ()),
        ],
    )
    assert upgrade.member_names() == {}
    add_draft(store, store.active(), uuid4())
    campaign = Campaign.objects.get(
        pk=SystemConfiguration.objects.get().current_campaign_id
    )
    identity("signed@example.org")
    SourceMutationLease.objects.get_or_create(singleton=True)
    SourceCurrent.objects.get_or_create(singleton=True)
    snapshot, claim = prepare(
        with_leaders(
            response_source(),
            [
                leader("shared@example.org", 9, family="inactive"),
                leader(" Shared@Example.org ", 9),
                leader("denied@example.org", 9),
                leader("signed@example.org", 9),
                # A Member address no rule or identity names.
                leader("outside@example.org", 9),
            ],
        )
    )
    promote(snapshot, claim, campaign, keys())
    names = upgrade.member_names()
    assert names == {
        "shared@example.org": ["Example 1, Leader", "Example 0, Leader"],
        "denied@example.org": ["Example 2, Leader"],
        "signed@example.org": ["Example 3, Leader"],
    }
    # The conversion names the user from ParishSoft.
    assert upgrade.upgrade_users(store, **arguments(["admin@example.org"])).state == (
        "applied"
    )
    assert active_users(store)["shared@example.org"] == ("Example 1, Leader", "staff")
    assert active_users(store)["admin@example.org"] == ("admin", "administrator")


def test_administrators_convert_whatever_their_recorded_identities(tmp_path):
    """Only a disabled identity, or several, never cost an Administrator."""
    store, _, _ = initialized(tmp_path, rules())
    disabled = identity("admin@example.org", disabled=True)
    enabled = identity("second@example.org")
    identity("second@example.org", disabled=True)
    identity("second@example.org", None, signed_in=False)
    assert upgrade.upgrade_users(store, **arguments()).state == "applied"
    users = active_users(store)
    assert {email: users[email][1] for email in ADMINS} == dict.fromkeys(
        ADMINS, "administrator"
    )
    # The enabled identity still opens an Admin session; the disabled one
    # stays refused by its own flag, not by the conversion.
    assert forge(enabled.pk).principal_id == enabled.pk
    with refused():
        forge(disabled.pk)


@pytest.mark.parametrize("email", ["not-an-address", "two@@example.org"])
def test_an_invalid_identity_email_fails_the_whole_conversion(tmp_path, email):
    """One stored address the validator refuses leaves the deployment unchanged."""
    store, version, _ = initialized(tmp_path, rules())
    identity("signed@example.org")
    identity(email)
    with pytest.raises(ConfigError, match="invalid email address"):
        upgrade.upgrade_users(store, **arguments())
    assert not ConfigurationChangeRequest.objects.filter(
        authority="operator_upgrade"
    ).exists()
    assert store.active().digest == version.digest
    assert coherent_configuration(store).active_configuration_id == (version.version_id)


def test_admin_recovery_never_needs_the_upgrade_grants(tmp_path, monkeypatch):
    """Recovery and its preview admit without the #952 grants; the upgrade refuses.

    With 0047 migrated but ``database-grants`` not yet run (the two upgrade
    definers not granted), the lockout fallback must still work, while
    ``upgrade-users`` refuses with an instruction. The admission's other
    checks (schema version, capacity, TEMP) are covered elsewhere and
    stubbed here, so this proves only the function-grant boundary.
    """
    from types import SimpleNamespace

    from parishkit.stewardship import operator_commands, runtime_database
    from parishkit.stewardship.runtime_grants import (
        MEMBER_NAMES_FUNCTION,
        PORTAL_IDENTITIES_FUNCTION,
    )

    from .test_recovery_postgresql import arguments as recovery_arguments

    store, _, _ = initialized(tmp_path, rules())
    deployment = str(SystemConfiguration.objects.get().pk)
    configuration = SimpleNamespace(
        service_role=ServiceRole.ADMIN_RECOVERY,
        paths={"authority": store.root},
    )
    # The test's own connection already runs as the login (task_login).
    monkeypatch.setattr(
        operator_commands,
        "admit_offline_service",
        lambda configuration: ServiceRole.ADMIN_RECOVERY,
    )
    monkeypatch.setattr(
        operator_commands, "configure_operator_database", lambda configuration: None
    )
    monkeypatch.setattr(operator_commands, "StartupLease", lambda *a, **k: _offline())
    monkeypatch.setattr(
        operator_commands, "RuntimeLayout", lambda _: SimpleNamespace(interlock=None)
    )
    for name in (
        "require_no_temporary_authority",
        "require_current_schema",
        "require_capacity",
        "require_role_capacity",
    ):
        monkeypatch.setattr(runtime_database, name, lambda *a, **k: None)
    with task_login(ServiceRole.ADMIN_RECOVERY):
        with connection.cursor() as cursor:
            cursor.execute("RESET SESSION AUTHORIZATION")
            for function in (PORTAL_IDENTITIES_FUNCTION, MEMBER_NAMES_FUNCTION):
                cursor.execute(
                    f"REVOKE EXECUTE ON FUNCTION public.{function} "
                    "FROM pk_stewardship_admin_recovery"
                )
            cursor.execute("SET SESSION AUTHORIZATION pk_stewardship_admin_recovery")
        preview = operator_commands.preview_recovery_command(
            configuration,
            deployment_id=deployment,
            target_email="replacement@example.org",
        )
        assert preview["recovery_preview"]
        kwargs = recovery_arguments()
        result = operator_commands.recover_admin_command(
            configuration,
            deployment_id=deployment,
            operation_id=str(kwargs["operation_id"]),
            target_email=kwargs["target_email"],
            confirmed_email=kwargs["confirmed_email"],
            operator_name=kwargs["operator_name"],
            reason=kwargs["reason"],
        )
        assert result["recovery_applied"] is True
        with pytest.raises(ConfigError, match="run database-grants"):
            operator_commands.upgrade_users_command(
                configuration, deployment_id=deployment, preview=True
            )
        # Once database-grants restores them, the upgrade preview admits.
        with connection.cursor() as cursor:
            cursor.execute("RESET SESSION AUTHORIZATION")
            for function in (PORTAL_IDENTITIES_FUNCTION, MEMBER_NAMES_FUNCTION):
                cursor.execute(
                    f"GRANT EXECUTE ON FUNCTION public.{function} "
                    "TO pk_stewardship_admin_recovery"
                )
            cursor.execute("SET SESSION AUTHORIZATION pk_stewardship_admin_recovery")
        assert (
            operator_commands.upgrade_users_command(
                configuration, deployment_id=deployment, preview=True
            )["users_upgrade_preview"]["already_users"]
            is False
        )


@pytest.mark.parametrize(
    "email", [" admin@example.org", "admin@example.org ", "Admin@example.org"]
)
def test_sql_refuses_a_user_row_with_an_unnormalized_email(tmp_path, email):
    """A user row's email must already be trimmed and lowercase (#952).

    The row copies a real converted user with only its email changed, so
    the projection trigger's user check is what refuses it.
    """
    store, _, _ = initialized(tmp_path, rules())
    upgrade.upgrade_users(store, **arguments())
    row = AddressRule.objects.get(
        configuration=SystemConfiguration.objects.get().active_configuration,
        email="admin@example.org",
    )
    with (
        pytest.raises(IntegrityError, match="Invalid user"),
        transaction.atomic(),
        connection.cursor() as cursor,
    ):
        cursor.execute(
            "INSERT INTO stewardship_address_rule "
            "SELECT (jsonb_populate_record(r, jsonb_build_object("
            "'id', gen_random_uuid(), 'email', %s::text))).* "
            "FROM stewardship_address_rule r WHERE r.id = %s",
            [email, row.pk],
        )

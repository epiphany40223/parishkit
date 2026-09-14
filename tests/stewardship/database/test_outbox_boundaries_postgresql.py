"""Sealed-value, preparation, pause and runtime permission boundaries."""

import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Barrier
from uuid import uuid4

import pytest
from django.db import (
    IntegrityError,
    ProgrammingError,
    connection,
    connections,
    transaction,
)
from django.db.models import F

from parishkit.config import ConfigError
from parishkit.stewardship.accounts.cryptography import (
    CryptographicError,
    Key,
    TokenPrivateKeyring,
)
from parishkit.stewardship.accounts.secret_models import SECRET_TARGETS
from parishkit.stewardship.audit.models import AuditEvent
from parishkit.stewardship.campaigns.cipher_rotation import reencrypt_batch
from parishkit.stewardship.campaigns.credential_keys import (
    add_rotation_key,
    key_set_lock,
)
from parishkit.stewardship.campaigns.credential_models import (
    CampaignCredentialState,
    FamilyCampaign,
    RehearsalCredential,
)
from parishkit.stewardship.campaigns.key_retirement import retire_keys
from parishkit.stewardship.campaigns.rehearsals import invalidate_rehearsal
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.jobs.delivery_states import DeliveryAction as Action
from parishkit.stewardship.jobs.outbox_models import OutboxMessage, OutboxRender
from parishkit.stewardship.jobs.outbox_storage import (
    create_message,
    hold_message,
    prepare_message,
    release_message_hold,
)
from parishkit.stewardship.jobs.outbox_validation import (
    DeliveryIdentity,
    SealedSubstitutions,
    substitution_context,
)
from parishkit.stewardship.runtime_grants import runtime_grants

from ..test_outbox_validation import rendering
from .test_family_auth_postgresql import family_service  # noqa: F401
from .test_key_retirement_postgresql import proof_owner
from .test_outbox_postgresql import (  # noqa: F401
    change,
    claim,
    inputs,
    permit,
    provider_evidence,
    submit,
)
from .test_production_journal_postgresql import intent  # noqa: F401

pytestmark = pytest.mark.django_db(transaction=True)


def test_retained_delivery_prevents_key_retirement_and_rejects_old_writer(
    family_service,  # noqa: F811
):
    """A pending outbox dependency is checked under the same rotation lock."""
    family = FamilyCampaign.objects.get()
    campaign = family_service.campaign
    identity = DeliveryIdentity(
        scope_id=campaign.pk,
        campaign_id=campaign.pk,
        family_id=family.pk,
        semantic_key=uuid4(),
        mode="testing",
        routing="testing_override",
        purpose="initial",
        credential_namespace="rehearsal",
        rehearsal_epoch_id=RehearsalCredential.objects.get().epoch_id,
    )
    render = rendering(configuration_id=campaign.active_configuration.configuration_id)
    sealed = SealedSubstitutions(
        family_service.rings.public.encrypt(
            b"synthetic-token", context=substitution_context(identity, render)
        )
    )
    options = dict(
        identity=identity,
        render=render,
        sealed=sealed,
        actor_id=uuid4(),
        correlation_id=uuid4(),
        command_id=uuid4(),
        admit=permit,
    )
    status = create_message(**options)
    rotated = TokenPrivateKeyring(
        [Key("t1", "decrypt-only", b"t" * 32), Key("t2", "active", b"u" * 32)]
    )
    add_rotation_key(family_service.rings.public, rotated.public())
    replacement = TokenPrivateKeyring([rotated.active])
    reencrypt_batch(rotated, admit=permit)
    with pytest.raises(CryptographicError, match="Retained delivery requires"):
        retire_keys(rotated, replacement, admit=permit, dependencies=proof_owner())
    with pytest.raises(IntegrityError, match="encryption key"):
        create_message(
            **(options | {"identity": replace(identity, semantic_key=uuid4())})
        )
    change(status, Action.CANCEL_UNSENT)
    assert retire_keys(
        rotated, replacement, admit=permit, dependencies=proof_owner()
    ) == {"t1"}


def test_preparation_keeps_prior_content_and_admits_replays(inputs):  # noqa: F811
    """Changing an unsent rendering never changes a previously selected version."""
    first = create_message(**inputs)
    options = dict(
        message_id=first.message_id,
        expected_version=first.version,
        command_id=uuid4(),
        actor_id=uuid4(),
        correlation_id=uuid4(),
        admit=permit,
        render=replace(inputs["render"], text="Updated"),
    )
    second = prepare_message(**options)
    assert prepare_message(**options) == second
    assert OutboxRender.objects.get(pk=first.render_id).text == "Hello"
    assert OutboxRender.objects.get(pk=second.render_id).text == "Updated"
    with pytest.raises(PermissionError):
        prepare_message(**(options | {"admit": lambda *args: False}))
    with pytest.raises(ValueError):
        prepare_message(**(options | {"render": inputs["render"]}))
    assert OutboxRender.objects.count() == 2


@pytest.mark.parametrize(
    "outcome", [Action.CANCEL_UNSENT, Action.ACCEPT, Action.FAIL_UNACCEPTED]
)
def test_terminal_outcomes_scrub_sealed_values_but_not_history(family_service, outcome):  # noqa: F811
    """A real rehearsal scope supplies sealed substitutions, never live credentials."""
    family = FamilyCampaign.objects.get()
    credential = RehearsalCredential.objects.get()
    campaign = family_service.campaign
    identity = DeliveryIdentity(
        scope_id=campaign.pk,
        campaign_id=campaign.pk,
        family_id=family.pk,
        semantic_key=uuid4(),
        mode="testing",
        routing="testing_override",
        purpose="initial",
        credential_namespace="rehearsal",
        rehearsal_epoch_id=credential.epoch_id,
    )
    render = rendering(configuration_id=campaign.active_configuration.configuration_id)
    context = substitution_context(identity, render)
    sealed = SealedSubstitutions(
        family_service.rings.public.encrypt(b"synthetic-token", context=context)
    )
    first = create_message(
        identity=identity,
        render=render,
        sealed=sealed,
        actor_id=uuid4(),
        command_id=uuid4(),
        correlation_id=uuid4(),
        admit=permit,
    )
    retained = OutboxMessage.objects.get(pk=first.message_id)
    assert (
        family_service.rings.private.decrypt(
            retained.sealed_substitutions, context=context
        )
        == b"synthetic-token"
    )
    if outcome is not Action.CANCEL_UNSENT:
        first = submit(first)
    terminal = change(first, outcome, evidence=provider_evidence())
    retained.refresh_from_db()
    assert retained.sealed_substitutions is None and retained.sealed_key_id is None
    assert retained.finished_at is not None and retained.version == terminal.version
    assert retained.events.exists() and retained.renders.exists()
    history = json.dumps(
        [
            list(retained.events.values()),
            list(AuditEvent.objects.filter(subject_id=retained.pk).values()),
        ],
        default=str,
    )
    assert (
        json.dumps(sealed.envelope) not in history and "synthetic-token" not in history
    )
    assert set(
        AuditEvent.objects.filter(subject_id=retained.pk).values_list(
            "campaign_reference", flat=True
        )
    ) == {campaign.pk}
    assert "synthetic-token" not in repr(terminal)


def test_pause_is_orthogonal_and_stale_dispatch_cannot_cross_it(family_service):  # noqa: F811
    """Synthetic Production routing proves hold mechanics, not live-mode admission."""
    family = FamilyCampaign.objects.get()
    campaign = family_service.campaign
    first = create_message(
        identity=DeliveryIdentity(
            scope_id=campaign.pk,
            campaign_id=campaign.pk,
            family_id=family.pk,
            semantic_key=uuid4(),
            mode="production",
            routing="production",
            purpose="receipt",
        ),
        render=rendering(
            configuration_id=campaign.active_configuration.configuration_id
        ),
        actor_id=uuid4(),
        command_id=uuid4(),
        correlation_id=uuid4(),
        admit=permit,
    )
    hold = dict(
        message_id=first.message_id,
        expected_version=first.version,
        command_id=uuid4(),
        actor_id=uuid4(),
        correlation_id=uuid4(),
        pause_version=1,
        admit=permit,
    )
    held = hold_message(**hold)
    assert (
        held.state == first.state
        and held.attempt == 0
        and held.pause_hold_id is not None
    )
    assert hold_message(**hold) == held
    task = claim(held)
    with pytest.raises(IntegrityError, match="Delivery is paused"):
        submit(held, task=task)
    resumed = release_message_hold(
        message_id=held.message_id,
        expected_version=held.version,
        command_id=uuid4(),
        actor_id=uuid4(),
        correlation_id=uuid4(),
        admit=permit,
    )
    assert resumed.pause_hold_id is None and resumed.attempt == 0
    assert submit(resumed, task=task).attempt == 1


@pytest.mark.parametrize(
    "role,target",
    [
        (role, None)
        for role in (
            ServiceRole.WEB,
            ServiceRole.WORKER,
            ServiceRole.MAIL_DISPATCH,
            ServiceRole.SCHEDULER,
            ServiceRole.CONFIG_INSTALLER,
            "download",
        )
    ]
    + [(ServiceRole.CREDENTIAL_INSTALLER, target) for target in SECRET_TARGETS],
)
def test_actual_runtime_grants_do_not_expose_generic_delivery_mutation(
    inputs,  # noqa: F811
    role,
    target,
):
    """Every online owner still needs a later compiled narrow delivery entry point."""
    first = create_message(**inputs)
    name = "delivery_probe_" + uuid4().hex
    with connection.cursor() as cursor:
        cursor.execute(f'CREATE ROLE "{name}" NOLOGIN NOINHERIT')
    try:
        with connection.cursor() as cursor:
            cursor.execute(f'GRANT USAGE ON SCHEMA public TO "{name}"')
            tables, columns = runtime_grants(role, target=target)
            for table, privileges in tables.items():
                cursor.execute(
                    f'GRANT {", ".join(sorted(privileges))} ON "{table}" TO "{name}"'
                )
            for table, privileges in columns.items():
                for privilege, names in privileges.items():
                    fields = ",".join(f'"{field}"' for field in sorted(names))
                    cursor.execute(
                        f'GRANT {privilege} ({fields}) ON "{table}" TO "{name}"'
                    )
        for table in (
            "stewardship_outbox_message",
            "stewardship_outbox_render",
            "stewardship_outbox_event",
            "stewardship_delivery_pause_hold",
            "stewardship_testing_aggregate",
            "stewardship_production_request",
            "stewardship_production_checkpoint",
            "stewardship_production_event",
        ):
            # UPDATE ... WHERE id also needs SELECT. Check actual privilege
            # bits so an accidental write grant cannot hide behind denied reads.
            with connection.cursor() as cursor:
                for privilege in ("INSERT", "UPDATE", "DELETE"):
                    cursor.execute(
                        "SELECT has_table_privilege(%s,%s,%s)",
                        [name, table, privilege],
                    )
                    assert cursor.fetchone() == (False,), (role, table, privilege)
                    if privilege != "DELETE":
                        cursor.execute(
                            "SELECT has_any_column_privilege(%s,%s,%s)",
                            [name, table, privilege],
                        )
                        assert cursor.fetchone() == (False,), (role, table, privilege)
            with (
                pytest.raises(ProgrammingError, match="permission denied"),
                transaction.atomic(),
                connection.cursor() as cursor,
            ):
                cursor.execute(f'SET LOCAL ROLE "{name}"')
                cursor.execute(
                    f'UPDATE "{table}" SET actor_id=%s WHERE id=%s',
                    [uuid4(), first.message_id],
                )
    finally:
        with connection.cursor() as cursor:
            cursor.execute(f'DROP OWNED BY "{name}"')
            cursor.execute(f'DROP ROLE "{name}"')


@pytest.mark.parametrize(
    "role", [ServiceRole.BACKUP_WORKER, ServiceRole.TOKEN_KEY_ROTATION]
)
def test_future_owners_have_no_runtime_grant_bundle(role):
    """Unimplemented operational owners cannot acquire generic fallback authority."""
    with pytest.raises(ConfigError, match="not implemented"):
        runtime_grants(role)


@pytest.fixture
def sealed_inputs(family_service):  # noqa: F811
    """An active synthetic rehearsal supplies a stable credential identity."""
    campaign = family_service.campaign
    identity = DeliveryIdentity(
        scope_id=campaign.pk,
        campaign_id=campaign.pk,
        family_id=FamilyCampaign.objects.get().pk,
        semantic_key=uuid4(),
        mode="testing",
        routing="testing_override",
        purpose="initial",
        credential_namespace="rehearsal",
        rehearsal_epoch_id=RehearsalCredential.objects.get().epoch_id,
    )
    render = rendering(configuration_id=campaign.active_configuration.configuration_id)

    def reseal(content=render):
        """The same secret and binding produce different ciphertext every time."""
        return SealedSubstitutions(
            family_service.rings.public.encrypt(
                b"synthetic-token", context=substitution_context(identity, content)
            )
        )

    return dict(
        identity=identity,
        render=render,
        sealed=reseal(),
        actor_id=uuid4(),
        command_id=uuid4(),
        correlation_id=uuid4(),
        admit=permit,
    ), reseal


def test_resealed_concurrent_creation_and_preparation_replays_keep_first_envelope(
    sealed_inputs,
):
    """Random encryption bytes never fork or overwrite a semantic delivery."""
    options, reseal = sealed_inputs
    barrier = Barrier(2)

    def create(sealed):
        """Run independently sealed producers on distinct database connections."""
        connections.close_all()
        try:
            barrier.wait(timeout=10)
            return create_message(**(options | {"sealed": sealed}))
        finally:
            connections.close_all()

    second_envelope = reseal()
    assert second_envelope.envelope != options["sealed"].envelope
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(create, [options["sealed"], second_envelope]))
    assert results[0] == results[1]
    first = results[0]
    retained = OutboxMessage.objects.get(pk=first.message_id).sealed_substitutions
    assert create_message(**(options | {"sealed": reseal()})) == first
    assert (
        OutboxMessage.objects.get(pk=first.message_id).sealed_substitutions == retained
    )
    updated_render = replace(options["render"], text="Updated")
    prepared = dict(
        message_id=first.message_id,
        expected_version=first.version,
        command_id=uuid4(),
        actor_id=options["actor_id"],
        correlation_id=uuid4(),
        admit=permit,
        render=updated_render,
        sealed=reseal(updated_render),
    )
    updated = prepare_message(**prepared)
    assert prepare_message(**(prepared | {"sealed": reseal(updated_render)})) == updated
    assert (
        OutboxMessage.objects.get(pk=first.message_id).sealed_substitutions
        == prepared["sealed"].envelope
    )


def test_metadata_only_key_rebinding_is_rejected_by_sql(sealed_inputs, family_service):  # noqa: F811
    """A valid second key cannot conceal dependency on the first ciphertext key."""
    options, _ = sealed_inputs
    first = create_message(**options)
    rotated = TokenPrivateKeyring(
        [Key("t1", "decrypt-only", b"t" * 32), Key("t2", "active", b"u" * 32)]
    )
    add_rotation_key(family_service.rings.public, rotated.public())
    with (
        pytest.raises(IntegrityError, match="Invalid delivery envelope"),
        transaction.atomic(),
    ):
        OutboxMessage.objects.filter(pk=first.message_id).update(
            action="prepared",
            command_id=uuid4(),
            version=F("version") + 1,
            sealed_key_id="t2",
        )
    assert OutboxMessage.objects.get(pk=first.message_id).sealed_key_id == "t1"


def test_key_inventory_lock_excludes_concurrent_sealed_writer(
    sealed_inputs,
    family_service,  # noqa: F811
):
    """Real independent transactions prove the shared/exclusive retirement fence."""
    options, _ = sealed_inputs

    def create():
        """The second connection must not join the parent's exclusive key lock."""
        connections.close_all()
        try:
            with pytest.raises(IntegrityError, match="encryption key"):
                create_message(**options)
        finally:
            connections.close_all()

    with (
        transaction.atomic(),
        key_set_lock(family_service.rings.public, exclusive=True),
        ThreadPoolExecutor(max_workers=1) as pool,
    ):
        pool.submit(create).result(timeout=10)
    assert not OutboxMessage.objects.exists()


def test_go_live_and_invalidated_epoch_block_testing_dispatch(sealed_inputs):
    """A previously claimed task cannot submit credentials invalidated by go-live."""
    options, _ = sealed_inputs
    first = create_message(**options)
    task = claim(first)
    invalidate_rehearsal(campaign_id=options["identity"].campaign_id, admit=permit)
    with pytest.raises(
        IntegrityError, match="Testing delivery is not currently admitted"
    ):
        submit(first, task=task)
    with pytest.raises(
        IntegrityError, match="Testing delivery is not currently admitted"
    ):
        create_message(
            **(
                options
                | {"identity": replace(options["identity"], semantic_key=uuid4())}
            )
        )
    assert change(first, Action.CANCEL_UNSENT).state == "cancelled"


def test_pending_testing_mail_blocks_cleanup_until_terminal(sealed_inputs, intent):  # noqa: F811
    """Readiness rejection rolls back gate acquisition; actual terminal totals pass."""
    from parishkit.stewardship.campaigns.production_storage import begin_transition

    options, _ = sealed_inputs
    first = create_message(**options)
    with pytest.raises(IntegrityError, match="exact terminal Testing"):
        begin_transition(**intent)
    scope = CampaignCredentialState.objects.get(campaign_id=intent["campaign_id"])
    assert not scope.go_live_gate and scope.rehearsal_epoch_id is not None
    change(first, Action.CANCEL_UNSENT)
    intent["summary"] = replace(intent["summary"], messages=1, cancelled=1)
    assert begin_transition(**intent).state == "cleanup_queued"

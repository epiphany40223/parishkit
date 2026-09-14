"""Sealed-value, preparation, pause and runtime permission boundaries."""

from dataclasses import replace
from uuid import uuid4

import pytest
from django.db import IntegrityError, ProgrammingError, connection, transaction

from parishkit.stewardship.accounts.cryptography import (
    CryptographicError,
    Key,
    TokenPrivateKeyring,
)
from parishkit.stewardship.campaigns.cipher_rotation import reencrypt_batch
from parishkit.stewardship.campaigns.credential_keys import add_rotation_key
from parishkit.stewardship.campaigns.credential_models import (
    FamilyCampaign,
    RehearsalCredential,
)
from parishkit.stewardship.campaigns.key_retirement import retire_keys
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
from .test_outbox_postgresql import change, claim, inputs, permit, submit  # noqa: F401

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
    terminal = change(first, outcome)
    retained.refresh_from_db()
    assert retained.sealed_substitutions is None and retained.sealed_key_id is None
    assert retained.finished_at is not None and retained.version == terminal.version
    assert retained.events.exists() and retained.renders.exists()
    assert all(
        "sealed_substitutions" not in event for event in retained.events.values()
    )
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
    with pytest.raises(IntegrityError, match="current claim"):
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
    "role",
    [
        ServiceRole.WEB,
        ServiceRole.WORKER,
        ServiceRole.MAIL_DISPATCH,
        ServiceRole.SCHEDULER,
    ],
)
def test_actual_runtime_grants_do_not_expose_generic_delivery_mutation(inputs, role):  # noqa: F811
    """Every online owner still needs a later compiled narrow delivery entry point."""
    first = create_message(**inputs)
    name = "delivery_probe_" + uuid4().hex
    with connection.cursor() as cursor:
        cursor.execute(f'CREATE ROLE "{name}" NOLOGIN NOINHERIT')
    try:
        with connection.cursor() as cursor:
            cursor.execute(f'GRANT USAGE ON SCHEMA public TO "{name}"')
            tables, columns = runtime_grants(role)
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
        ):
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

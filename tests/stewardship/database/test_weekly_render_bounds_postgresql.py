"""The actual message purpose independently controls PostgreSQL body capacity."""

from dataclasses import asdict, replace
from uuid import uuid4

import pytest
from django.db import DatabaseError

from parishkit.stewardship.accounts.models import Parish
from parishkit.stewardship.jobs.outbox_models import OutboxRender
from parishkit.stewardship.jobs.outbox_storage import create_message, prepare_message
from parishkit.stewardship.jobs.outbox_validation import WeeklyRenderInput

from ..test_outbox_validation import rendering
from ..test_weekly_render_journal import identity

pytestmark = pytest.mark.django_db(transaction=True)


def arguments(harness, purpose="weekly_digest", *, large=True):
    """Exercise the journal seam, not an operational digest or provider owner."""
    key = identity(purpose, testing=True)
    key = replace(
        key,
        campaign_id=harness.campaign.pk,
        scope_id=Parish.objects.get(
            configuration_id=harness.service.store.active().version_id
        ).pk
        if purpose == "operational"
        else harness.campaign.pk,
    )
    render = rendering(configuration_id=harness.service.store.active().version_id)
    if large:
        render = WeeklyRenderInput(**(asdict(render) | {"text": "x" * 1_048_577}))
    return dict(
        identity=key,
        render=render,
        actor_id=uuid4(),
        correlation_id=uuid4(),
        command_id=uuid4(),
        admit=lambda *args: True,
    )


def test_weekly_journal_can_retain_a_body_larger_than_daily_limit(response_service):
    values = arguments(response_service)
    message = create_message(**values)
    assert OutboxRender.objects.get(pk=message.render_id).text == values["render"].text


@pytest.mark.parametrize("purpose", ["daily_digest", "operational"])
def test_python_allocation_rejects_weekly_render_for_another_purpose(
    response_service, purpose
):
    with pytest.raises(ValueError, match="weekly delivery identity"):
        create_message(**arguments(response_service, purpose))
    assert not OutboxRender.objects.exists()


@pytest.mark.parametrize("purpose", ["daily_digest", "operational"])
def test_preparation_cannot_rebind_weekly_type_to_an_existing_other_message(
    response_service, purpose
):
    message = create_message(**arguments(response_service, purpose, large=False))
    count = OutboxRender.objects.count()
    value = arguments(response_service)["render"]
    with pytest.raises(ValueError, match="weekly delivery identity"):
        prepare_message(
            message_id=message.message_id,
            expected_version=message.version,
            command_id=uuid4(),
            actor_id=uuid4(),
            correlation_id=uuid4(),
            render=value,
            admit=lambda *args: True,
        )
    assert OutboxRender.objects.count() == count


@pytest.mark.parametrize(
    "purpose,size,accepted",
    [
        ("weekly_digest", 8_388_608, True),
        ("weekly_digest", 8_388_609, False),
        ("daily_digest", 1_048_577, False),
        ("operational", 1_048_577, False),
    ],
)
def test_sql_size_guard_uses_actual_message_purpose(
    response_service, purpose, size, accepted
):
    """Bypass Python's value constructor, never its SQL guard, for negative cases."""
    values = arguments(response_service, purpose, large=False)
    message = create_message(**values)
    fields = values["render"].fields() | {"text": "x" * size}
    # The boundary runs before content digest verification for rejected shapes.
    # Accepted input needs the canonical fingerprint of the actual large content.
    if accepted:
        fields = WeeklyRenderInput(
            **(asdict(values["render"]) | {"text": "x" * size})
        ).fields()
    if not accepted:
        with pytest.raises(DatabaseError, match="Invalid delivery render"):
            OutboxRender.objects.create(message_id=message.message_id, **fields)
    else:
        assert (
            len(
                OutboxRender.objects.create(
                    message_id=message.message_id, **fields
                ).text
            )
            == size
        )

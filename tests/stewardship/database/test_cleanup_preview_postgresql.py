"""The Admin preview sees exact Testing impact, without acquiring deletion power."""

import pytest
from django.db import DatabaseError, connection, transaction

from parishkit.stewardship.campaigns.cleanup_catalog import CleanupCategory
from parishkit.stewardship.campaigns.cleanup_preview import (
    cleanup_families,
    cleanup_preview,
)
from parishkit.stewardship.campaigns.credential_models import CampaignCredentialState
from parishkit.stewardship.campaigns.production_models import (
    ProductionTransitionRequest,
)
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.jobs.delivery_states import DeliveryAction
from parishkit.stewardship.source.models import SourceCurrent
from parishkit.stewardship.web.contracts import PageWindow

from .test_background_grants_postgresql import task_login
from .test_cleanup_inventory_postgresql import mixed_mail
from .test_outbox_postgresql import change
from .test_response_submission_postgresql import form_and_answers, submit

pytestmark = pytest.mark.django_db(transaction=True)


def test_web_preview_reports_unresolved_testing_only_without_starting_cleanup(
    response_service,
):
    """Live/operational rows are excluded; pending Testing remains an explicit block."""
    form, answers = form_and_answers(response_service)
    submit(response_service, form, answers)
    messages = mixed_mail(response_service)
    with task_login(ServiceRole.WEB), work_transaction():
        before = cleanup_preview(response_service.campaign.pk)
        families, has_next = cleanup_families(
            response_service.campaign.pk,
            source_id=SourceCurrent.objects.get().snapshot_id,
            window=PageWindow(1, 50),
        )
        assert len(families) == 1 and not has_next
        assert set(families[0]) == {"name", "duid"}
        assert families[0]["duid"] == 1
    assert before.submissions == before.families == 1
    assert before.messages >= 1 and before.unresolved == before.messages
    assert before.inventory.counts[CleanupCategory.SUBMISSION] == 1
    assert not ProductionTransitionRequest.objects.exists()
    assert not CampaignCredentialState.objects.get().go_live_gate
    change(messages["testing_override"], DeliveryAction.CANCEL_UNSENT)
    with task_login(ServiceRole.WEB), work_transaction():
        after = cleanup_preview(response_service.campaign.pk)
    assert dict(after.message_states)["cancelled"] == 1
    assert after.unresolved == before.unresolved - 1
    # The additional cancellation event is itself sensitive Testing detail.
    assert after.inventory.total == before.inventory.total + 1


def test_inventory_grants_do_not_expose_rendered_mail_or_allow_deletion():
    """The new identifier/aggregate reads do not extend to content or mutations."""
    with task_login(ServiceRole.WEB):
        for statement in (
            "SELECT html FROM stewardship_outbox_render",
            "DELETE FROM stewardship_production_request",
            "DELETE FROM stewardship_outbox_message",
        ):
            with (
                pytest.raises(DatabaseError) as error,
                transaction.atomic(),
                connection.cursor() as cursor,
            ):
                cursor.execute(statement)
            assert error.value.__cause__.sqlstate == "42501"

"""Application impact inputs advance one private transactional revision."""

import pytest
from django.db import DatabaseError, connection, transaction
from django.db.models import F
from django.utils import timezone

from parishkit.stewardship.campaigns.confirmation_models import ActivationImpactRevision
from parishkit.stewardship.campaigns.credential_models import FamilyCampaign
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.deployment import ServiceRole

from .credential_builders import family_campaign
from .test_background_grants_postgresql import task_login

pytestmark = pytest.mark.django_db(transaction=True)


def revision():
    """Read only the constant-size metadata, never enumerate Family inputs."""
    return ActivationImpactRevision.objects.get().version


def test_bulk_eligibility_changes_tick_once_but_activity_does_not(tmp_path):
    """A preview notices a changed input; a passive visit does not invalidate it."""
    family_campaign(tmp_path, count=25)
    before = revision()
    with work_transaction():
        FamilyCampaign.objects.update(
            last_activity_at=timezone.now(), version=F("version") + 1
        )
    assert revision() == before
    with work_transaction():
        FamilyCampaign.objects.update(email_deliverable=False, version=F("version") + 1)
    assert revision() == before + 1
    assert FamilyCampaign.objects.filter(email_deliverable=False).count() == 25


def test_rolled_back_changes_do_not_invalidate_committed_preview(tmp_path):
    """The clock commits or rolls back with the exact underlying application write."""
    family_campaign(tmp_path)
    before = revision()
    with work_transaction():
        FamilyCampaign.objects.update(email_deliverable=False, version=F("version") + 1)
        assert revision() == before + 1
        transaction.set_rollback(True)
    assert revision() == before
    assert FamilyCampaign.objects.get().email_deliverable


def test_web_can_read_but_cannot_forge_or_reset_impact_clock(tmp_path):
    """Actual restricted grants expose metadata without giving it write authority."""
    family_campaign(tmp_path)
    before = revision()
    with task_login(ServiceRole.WEB):
        assert revision() == before
        for statement in (
            "UPDATE stewardship_activation_impact SET version=version+1",
            "DELETE FROM stewardship_activation_impact",
            "INSERT INTO stewardship_activation_impact(singleton) VALUES(true)",
        ):
            with (
                pytest.raises(DatabaseError) as error,
                transaction.atomic(),
                connection.cursor() as cursor,
            ):
                cursor.execute(statement)
            assert error.value.__cause__.sqlstate == "42501"
    assert revision() == before

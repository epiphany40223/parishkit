"""The Admin preview sees exact Testing impact, without acquiring deletion power."""

import pytest
from django.db import DatabaseError, connection, transaction
from django.db.models import BigIntegerField, F
from django.db.models.functions import Cast

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
from parishkit.stewardship.source.snapshot_name_sql import SnapshotFamilyName
from parishkit.stewardship.source.snapshot_names import snapshot_family_names
from parishkit.stewardship.source.version_models import SnapshotFamily
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
        _, families, has_next, total = cleanup_families(
            response_service.campaign.pk,
            source_id=SourceCurrent.objects.get().snapshot_id,
            window=PageWindow(1, 50),
        )
        assert len(families) == 1 and not has_next and total == (1, False)
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


def test_testing_families_sort_on_the_server_by_name_or_duid(
    response_service, monkeypatch
):
    """Both columns order the whole inventory before it is paged."""
    from parishkit.stewardship.campaigns import cleanup_preview as module
    from parishkit.stewardship.campaigns.credential_models import FamilyCampaign

    form, answers = form_and_answers(response_service)
    submit(response_service, form, answers)
    campaign_id = response_service.campaign.pk
    # Treat every campaign Family as affected, so the order is observable.
    monkeypatch.setattr(
        module,
        "inventory_queries",
        lambda _campaign: {
            CleanupCategory.SUBMISSION: FamilyCampaign.objects.annotate(
                family_id=F("pk")
            )
        },
    )
    source_id = SourceCurrent.objects.get().snapshot_id

    def read(sort, size=50):
        """One page of the inventory under the given sort token."""
        with task_login(ServiceRole.WEB), work_transaction():
            return cleanup_families(
                campaign_id, source_id=source_id, window=PageWindow(1, size), sort=sort
            )[1:]

    rows, _, (total, capped) = read("duid")
    assert total == len(rows) > 1 and not capped
    duids = [row["duid"] for row in rows]
    assert duids == sorted(duids)
    assert [row["duid"] for row in read("-duid")[0]] == duids[::-1]
    # Shown as "Surname, heads" (snapshot_family_names), ordered by surname
    # and then the whole name, as the Family codes directory orders them.
    names = snapshot_family_names(source_id, duids)
    assert any(", " in name for name in names.values())

    def key(row):
        """The page's order: surname, then the whole shown name."""
        surname = row["name"].split(", ", 1)[0]
        return surname.lower(), row["name"].lower()

    by_name = read("name")[0]
    assert [row["name"] for row in by_name] == [names[row["duid"]] for row in by_name]
    assert [key(row) for row in by_name] == sorted(map(key, by_name))
    assert len({row["name"] for row in by_name}) > 1
    assert [key(row) for row in read("-name")[0]] == sorted(
        map(key, by_name), reverse=True
    )
    first, has_next, _ = read("-duid", size=1)
    assert has_next and first[0]["duid"] == duids[-1]


def test_sql_family_name_matches_the_shown_name(response_service):
    """SnapshotFamilyName builds, in SQL and as the web role, the very name
    snapshot_family_names shows, for every Family of the snapshot."""
    source_id = SourceCurrent.objects.get().snapshot_id
    with task_login(ServiceRole.WEB), work_transaction():
        rows = list(
            SnapshotFamily.objects.filter(snapshot_id=source_id)
            .annotate(duid=Cast("source_key", BigIntegerField()))
            .annotate(
                name=SnapshotFamilyName(source_id, F("duid")),
                surname=SnapshotFamilyName(source_id, F("duid"), surname_only=True),
            )
            .values_list("duid", "name", "surname")
        )
        shown = snapshot_family_names(source_id, [duid for duid, _, _ in rows])
    assert len(rows) > 1 and any(", " in name for name in shown.values())
    for duid, name, surname in rows:
        assert name == shown[duid]
        assert name.split(", ", 1)[0] == surname


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

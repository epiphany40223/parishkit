"""The engagement backfill (#477) against a real Production-mode database."""

from datetime import timedelta
from uuid import uuid4

import pytest

from parishkit.stewardship import engagement_backfill
from parishkit.stewardship.audit.models import AuditContext, AuditEvent
from parishkit.stewardship.campaigns.engagement_models import FamilyEngagement
from parishkit.stewardship.responses.baselines import issue_baseline

from .auth_builders import unguarded
from .test_runtime_auth_grants_postgresql import web_login

pytestmark = pytest.mark.django_db(transaction=True)


def row_values():
    """Every row's funnel columns and version, in a stable order."""
    return list(
        FamilyEngagement.objects.order_by("pk").values_list(
            "pk",
            "mode",
            "first_link_at",
            "first_form_at",
            "first_progress_at",
            "furthest_section",
            "furthest_at",
            "last_seen_at",
            "actor_id",
            "version",
        )
    )


def test_backfill_under_web_grants_recovers_history_and_repeats_without_writes(
    live_response_service,
):
    """Activation removed the Testing row; the live sign-in made a live one."""
    harness = live_response_service
    live = FamilyEngagement.objects.get()
    assert (live.mode, live.rehearsal_epoch_id) == ("live", None)
    assert live.first_link_at is not None and live.first_form_at is None
    issue_baseline(harness.request, harness.service)
    live = FamilyEngagement.objects.get()
    assert live.first_form_at is not None

    # Two earlier sign-ins that only the audit trail remembers, between the
    # activation and the live login above (so they are in the past), and one
    # before activation that belonged to the deleted rehearsal.
    activation = engagement_backfill.production_activation()
    assert activation is not None and activation < live.first_link_at
    step = (live.first_link_at - activation) / 3
    with unguarded():
        for shift in (-timedelta(hours=1), step, 2 * step):
            AuditEvent.objects.create(
                event_type="family_login",
                subject_id=uuid4(),
                actor_id=live.family_id,
                created_at=activation + shift,
            )
    runs = AuditEvent.objects.filter(event_type="family_engagement_backfilled")
    with web_login():
        first = engagement_backfill.backfill()
        assert first["result"] == "backfilled" and first["rows_changed"] == 1
        assert (first["families_linked"], first["families_with_form"]) == (1, 1)
        recovered = FamilyEngagement.objects.get()
        assert recovered.first_link_at == activation + step
        assert recovered.last_seen_at == live.last_seen_at
        assert recovered.first_form_at == live.first_form_at
        assert recovered.actor_id is None and recovered.version == live.version + 1
        assert runs.count() == 1
        context = AuditContext.objects.get(event=runs.get()).context
        assert context == {"count": 1, "outcome": "succeeded"}
        unchanged = row_values()
        second = engagement_backfill.backfill()
        assert second == first | {"rows_changed": 0}
        # A repeated run writes nothing but its own audit event.
        assert row_values() == unchanged
        assert runs.count() == 2
        assert AuditContext.objects.get(event=runs.latest("created_at")).context == {
            "count": 0,
            "outcome": "succeeded",
        }


def test_backfill_refuses_outside_production(response_service):
    with pytest.raises(engagement_backfill.BackfillRefused, match="Production"):
        engagement_backfill.backfill()
    assert FamilyEngagement.objects.count() == 1
    assert not AuditEvent.objects.filter(
        event_type="family_engagement_backfilled"
    ).exists()

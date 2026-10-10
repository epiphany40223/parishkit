"""Changing a live campaign's end date (#912): pages, command line, installer.

The intent is bound with its request, the configuration installer applies it
with its owning admission, the close boundary moves, every Family credential
keeps working, and the shortened date never strands a Reminder.
"""

from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from django.test import Client

from parishkit.stewardship.accounts.configuration_installation import install_request
from parishkit.stewardship.accounts.configuration_requests import record_request
from parishkit.stewardship.audit.models import AuditEvent
from parishkit.stewardship.campaigns.lifecycle import Action
from parishkit.stewardship.campaigns.live_end_date import (
    admit_end_edit,
    bind_reviewed_end_edit,
)
from parishkit.stewardship.campaigns.models import (
    Campaign,
    CampaignConfigurationIntent,
)

from .automation_builders import paired
from .campaign_builders import campaign_clock, command
from .response_builders import activate_response_service
from .test_admin_schedule_cli_postgresql import admin, confirm, events, preview
from .test_ministry_responses_postgresql import revisit
from .test_schedule_views_postgresql import setup as campaign_with_reminder

pytestmark = pytest.mark.django_db(transaction=True)

__all__ = ["admin"]  # The command-line fixture, shared with its own tests.

EVENT = "campaign_end_date_requested"


def live(store):
    """A scheduled Production campaign with an invitation and a 2054-10-25 reminder."""
    campaign, _ = campaign_with_reminder(store)
    command(campaign, uuid4(), Action.ACTIVATE)
    campaign.refresh_from_db()
    assert campaign.state == "scheduled" and campaign.structural_locked
    return campaign


def installed(store, request_id):
    """Apply a request as the configuration installer service does."""
    return install_request(
        store,
        request_id=request_id,
        correlation_id=uuid4(),
        admit_campaign=admit_end_edit,
    )


@pytest.mark.parametrize("end_date", ["2054-11-15", "2054-10-29"])
def test_family_codes_and_links_keep_working_after_the_change(
    response_service, end_date
):
    """Extending or shortening keeps the same credentials and token generation.

    An extended campaign's code still signs its Family in after the old end
    (the HARD rule: emailed Production codes and links are never broken).
    """
    harness = activate_response_service(response_service)
    campaign = Campaign.objects.get(pk=harness.campaign.pk)
    generation = campaign.active_token_generation_id
    old_end = campaign.active_configuration.ends_at
    # Before the change, the portal is closed after the old end.
    assert b'name="code"' in Client().get("/").content
    with campaign_clock(old_end + timedelta(hours=1)):
        assert b'name="code"' not in Client().get("/").content
    store = harness.service.store
    request = record_request(
        base_digest=store.active().digest,
        patch=[
            {
                "operation": "update",
                "section": "campaigns",
                "id": str(campaign.pk),
                "values": {"end_date": end_date},
            }
        ],
        actor_id=uuid4(),
        request_key=uuid4(),
        correlation_id=uuid4(),
        attach=bind_reviewed_end_edit,
    )
    assert installed(store, request.request_id).state == "applied"
    campaign.refresh_from_db()
    assert campaign.active_configuration.end_date.isoformat() == end_date
    assert campaign.active_token_generation_id == generation
    # The same emailed code signs the Family in again.
    assert revisit(harness)
    if campaign.active_configuration.ends_at > old_end:
        with campaign_clock(old_end + timedelta(hours=1)):
            assert revisit(harness)


def test_a_draft_end_date_binds_no_exceptional_intent(auth_service):
    """Ordinary draft date edits stay ordinary requests."""
    store = auth_service.store
    campaign, _ = campaign_with_reminder(store)
    request = record_request(
        base_digest=store.active().digest,
        patch=[
            {
                "operation": "update",
                "section": "campaigns",
                "id": str(campaign.pk),
                "values": {"end_date": "2054-11-15"},
            }
        ],
        actor_id=uuid4(),
        request_key=uuid4(),
        correlation_id=uuid4(),
        attach=bind_reviewed_end_edit,
    )
    assert not CampaignConfigurationIntent.objects.exists()
    assert not AuditEvent.objects.filter(event_type=EVENT).exists()
    assert (
        install_request(
            store, request_id=request.request_id, correlation_id=uuid4()
        ).state
        == "applied"
    )


def test_the_command_line_changes_a_live_end_date(admin, google):
    """``schedule preview`` and ``schedule confirm`` mirror the page (#463)."""
    store = admin.service.store
    campaign = live(store)
    _, secret, row = paired(admin.service)
    code, document = preview(admin, secret, {"window": {"start_date": "2054-10-02"}})
    assert code == 1 and document["error"]["code"] == "stale_version"
    code, document = preview(admin, secret, {"window": {"end_date": "2054-11-15"}})
    assert code == 0, document
    result = document["result"]
    assert result["window"]["changed"] == ["end_date"]
    assert result["window"]["after"]["end_date"] == "2054-11-15"
    code, confirmed = confirm(admin, secret, result["preview"]["token"])
    assert code == 0, confirmed
    request_id = UUID(confirmed["result"]["request"]["request_id"])
    intent = CampaignConfigurationIntent.objects.get(request_id=request_id)
    assert intent.actor_id == row.principal_id
    assert len(events()) == 1
    assert AuditEvent.objects.filter(event_type=EVENT, subject_id=request_id).exists()
    assert installed(store, request_id).state == "applied"
    campaign.refresh_from_db()
    assert campaign.active_configuration.end_date.isoformat() == "2054-11-15"


def test_the_installer_needs_the_owning_admission(auth_service, google):
    """Without the owner a bound request stays staged; with it, it applies."""
    from parishkit.stewardship.storage import StorageInvariantError

    store = auth_service.store
    campaign = live(store)
    request = record_request(
        base_digest=store.active().digest,
        patch=[
            {
                "operation": "update",
                "section": "campaigns",
                "id": str(campaign.pk),
                "values": {"end_date": "2054-11-15"},
            }
        ],
        actor_id=uuid4(),
        request_key=uuid4(),
        correlation_id=uuid4(),
        attach=bind_reviewed_end_edit,
    )
    with pytest.raises(StorageInvariantError, match="current admission"):
        install_request(store, request_id=request.request_id, correlation_id=uuid4())
    assert installed(store, request.request_id).state == "applied"

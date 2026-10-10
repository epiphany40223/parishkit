"""Changing a live campaign's end date (#912): pages, command line, installer.

The intent is bound with its request, the configuration installer applies it
with its owning admission, the close boundary moves, every Family credential
keeps working, and the shortened date never strands a Reminder.
"""

import re
from datetime import timedelta
from html import unescape
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import pytest
from django.test import Client
from django.urls import reverse

from parishkit.stewardship.accounts.configuration_installation import install_request
from parishkit.stewardship.accounts.configuration_requests import record_request
from parishkit.stewardship.accounts.request_models import ConfigurationChangeRequest
from parishkit.stewardship.audit.models import AuditEvent
from parishkit.stewardship.campaigns.lifecycle import Action
from parishkit.stewardship.campaigns.live_end_date import (
    admit_end_edit,
    bind_reviewed_end_edit,
)
from parishkit.stewardship.campaigns.models import (
    Campaign,
    CampaignBoundaryOccurrence,
    CampaignConfigurationIntent,
    ScheduleDefinition,
)

from .auth_builders import signed_in
from .automation_builders import paired
from .campaign_builders import campaign_clock, command
from .response_builders import activate_response_service
from .test_admin_schedule_cli_postgresql import admin, confirm, events, preview
from .test_campaign_views_postgresql import post, requested
from .test_ministry_responses_postgresql import revisit
from .test_parish_views_postgresql import token
from .test_schedule_views_postgresql import fields
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


def digest(response):
    """The page's hidden applied-configuration version."""
    return re.search(
        r'name="base_digest" value="([0-9a-f]{64})"', response.content.decode()
    ).group(1)


def installed(store, request_id):
    """Apply a request as the configuration installer service does."""
    return install_request(
        store,
        request_id=request_id,
        correlation_id=uuid4(),
        admit_campaign=admit_end_edit,
    )


def closes(campaign):
    """The pending close occurrences' due instants."""
    return list(
        CampaignBoundaryOccurrence.objects.filter(
            campaign=campaign, kind="close", state="pending"
        ).values_list("due_at", flat=True)
    )


def test_campaign_settings_reviews_and_applies_a_later_end_in_place(
    auth_service, google
):
    """Review, Apply in place, intent and audit with the request, then applied."""
    store = auth_service.store
    campaign = live(store)
    browser, _ = signed_in()
    url = reverse("admin:campaign_settings")
    page = browser.get(url)
    assert page.status_code == 200, page.content
    body = page.content.decode()
    assert 'id="end-date"' in body and 'name="end_date"' in body
    assert 'value="2054-10-31"' in body
    review = post(
        browser,
        url,
        {"action": "preview", "end_date": "2054-11-15", "base_digest": digest(page)},
    )
    assert review.status_code == 200, review.content
    text = unescape(review.content.decode())
    assert "Review your changes" in text and "Campaign end date" in text
    assert "Family codes and links already sent keep working" in text
    assert not ConfigurationChangeRequest.objects.filter(
        patch__0__values__end_date="2054-11-15"
    ).exists()
    accepted = post(browser, url, {"action": "confirm", "preview": token(review)})
    # Answered in place: the page again, naming the request (#532).
    assert accepted["Location"].startswith(url + "?request=")
    assert accepted["Location"].endswith("#settings-review")
    request = ConfigurationChangeRequest.objects.get(pk=requested(accepted))
    intent = CampaignConfigurationIntent.objects.get(request=request)
    assert (intent.action, intent.campaign_id) == ("edit_end", campaign.pk)
    assert intent.expected_version == campaign.version
    assert intent.prior_projection_id == campaign.active_configuration_id
    assert intent.token_generation_id is None
    [event] = AuditEvent.objects.filter(event_type=EVENT)
    assert event.subject_id == request.pk and event.actor_id == request.actor_id
    assert event.campaign_reference == campaign.pk
    status = browser.get(
        urlsplit(accepted["Location"]).path + "?request=" + str(request.pk)
    )
    assert status.status_code == 200 and b"Change status" in status.content

    assert installed(store, request.pk).state == "applied"
    campaign.refresh_from_db()
    assert campaign.active_configuration.end_date.isoformat() == "2054-11-15"
    assert campaign.state == "scheduled" and campaign.structural_locked
    assert closes(campaign) == [campaign.active_configuration.ends_at]
    # The repeated Apply returns the same request and binds nothing more.
    again = post(browser, url, {"action": "confirm", "preview": token(review)})
    assert requested(again) == request.pk
    assert CampaignConfigurationIntent.objects.count() == 1


def test_a_shortened_end_resolves_its_reminder_in_the_combined_review(
    auth_service, google
):
    """Campaign settings refuses and links; Dates and mail schedules applies both."""
    store = auth_service.store
    campaign = live(store)
    browser, _ = signed_in()
    url = reverse("admin:campaign_settings")
    page = browser.get(url)
    refused = post(
        browser,
        url,
        {"action": "preview", "end_date": "2054-10-20", "base_digest": digest(page)},
    )
    assert refused.status_code == 400
    text = unescape(refused.content.decode())
    assert "would no longer fit the campaign" in text
    schedules = reverse("admin:schedule_settings")
    link = f"{schedules}?end_date=2054-10-20"
    assert f'href="{link}"' in text and 'name="preview"' not in text
    # The combined review: only the end date is open, at the proposed date.
    review = browser.get(link)
    assert review.status_code == 200, review.content
    body = review.content.decode()
    assert re.search(r'name="window-end_date" value="2054-10-20"[^>]*>', body)
    assert re.search(r'name="window-start_date"[^>]*disabled', body)
    assert "only its end date can change" in body
    data, indexes = fields(store, campaign, editable=False)
    data["window-end_date"] = "2054-10-20"
    assert post(browser, schedules, data).status_code == 400
    data[f"schedules-{indexes['reminder']}-DELETE"] = "on"
    preview_page = post(browser, schedules, data)
    accepted = post(
        browser, schedules, {"action": "confirm", "preview": token(preview_page)}
    )
    request = ConfigurationChangeRequest.objects.get(pk=requested(accepted))
    assert CampaignConfigurationIntent.objects.filter(request=request).exists()
    assert installed(store, request.pk).state == "applied"
    campaign.refresh_from_db()
    assert campaign.active_configuration.end_date.isoformat() == "2054-10-20"
    assert ScheduleDefinition.objects.get(kind="reminder").current_revision_id is None
    assert closes(campaign) == [campaign.active_configuration.ends_at]


def test_a_past_or_unchanged_end_and_locked_fields_are_refused(auth_service, google):
    """Only a changed future end date is reviewed; the start stays locked."""
    store = auth_service.store
    live(store)
    browser, _ = signed_in()
    url = reverse("admin:campaign_settings")
    page = browser.get(url)
    before = ConfigurationChangeRequest.objects.count()
    for value, message in (
        ("2054-10-31", "Nothing has changed."),
        ("2020-01-01", "Check the campaign dates"),
    ):
        refused = post(
            browser,
            url,
            {"action": "preview", "end_date": value, "base_digest": digest(page)},
        )
        assert refused.status_code == 400
        assert message in unescape(refused.content.decode())
    # The start date may not be proposed for a live campaign.
    schedules = reverse("admin:schedule_settings")
    assert browser.get(f"{schedules}?start_date=2054-10-02").status_code == 400
    assert ConfigurationChangeRequest.objects.count() == before


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


# ---------------------------------------------------------------- refusals (#944)


def drain(store, wrap=None):
    """Run the configuration queue as the installer service does, until empty.

    Returns each request's settled status, by request id. ``wrap``, when
    given, is entered around each request's install.
    """
    from contextlib import nullcontext

    from parishkit.stewardship.runtime_process import next_configuration_request

    settled = {}
    while (identifier := next_configuration_request()) is not None:
        assert identifier not in settled, "the queue selected a request again"
        with wrap() if wrap else nullcontext():
            settled[identifier] = installed(store, identifier)
    return settled


def coherent(store):
    """The selected YAML is the applied configuration, and nothing is queued."""
    from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
    from parishkit.stewardship.runtime_process import next_configuration_request

    assert (
        store.active().version_id
        == SystemConfiguration.objects.get().active_configuration_id
    )
    assert next_configuration_request() is None


def rename_parish(store, actor):
    """Queue an ordinary change behind the end edit: the parish's name."""
    parish = store.active().document()["sections"]["parish"][0]
    return record_request(
        base_digest=store.active().digest,
        patch=[
            {
                "operation": "update",
                "section": "parish",
                "id": parish["id"],
                "values": {"name": "Renamed after the end edit"},
            }
        ],
        actor_id=actor,
        request_key=uuid4(),
        correlation_id=uuid4(),
    ).request_id


def journal_reason(request_id):
    """The abort journal's reason for ``request_id``'s end edit."""
    from parishkit.stewardship.campaigns.models import CampaignConfigurationAbort

    return CampaignConfigurationAbort.objects.get(intent__request_id=request_id).reason


@pytest.mark.parametrize("passed", ["old_end", "new_end"])
def test_a_date_passing_between_prepare_and_activate_refuses_cleanly(
    tmp_path, monkeypatch, passed
):
    """The clock passes the old (or the new) end after the YAML switch.

    The activation refuses the change under its own locks: the refusal is
    journaled, the previous YAML restored and the request failed, and the
    ordinary change queued behind it applies.
    """
    from parishkit.stewardship.accounts.configuration_installation import (
        DatabaseMaterializer,
    )
    from parishkit.stewardship.campaigns.live_end_date import REFUSALS

    from .campaign_builders import draft_campaign, end_request

    store, campaign, actor = draft_campaign(tmp_path)
    original = campaign.active_configuration
    with campaign_clock(original.starts_at):
        command(campaign, actor, Action.ACTIVATE)
        new_date = original.end_date + timedelta(days=10 if passed == "old_end" else -2)
        request, _ = end_request(
            store, campaign, actor, "edit_end", new_date.isoformat()
        )
        later = rename_parish(store, actor)
        before = store.active()
        late = (
            original.ends_at
            if passed == "old_end"
            else original.ends_at - timedelta(days=2)
        ) + timedelta(hours=1)
        activate = DatabaseMaterializer.activate

        def activate_late(materializer, digest):
            """Activate the end edit only once the date has passed."""
            if materializer.request.pk != request.request_id:
                return activate(materializer, digest)
            # The YAML is already switched to the candidate here.
            assert store.active().version_id == request.candidate_version_id
            with campaign_clock(late):
                return activate(materializer, digest)

        monkeypatch.setattr(DatabaseMaterializer, "activate", activate_late)
        settled = drain(store)
    refused = settled[request.request_id]
    assert (refused.state, refused.failure_code) == ("failed", "invalid_candidate")
    assert (
        journal_reason(request.request_id)
        == REFUSALS["ended" if passed == "old_end" else "past"]
    )
    assert settled[later].state == "applied"
    coherent(store)
    assert store.active().predecessor_digest == before.digest
    campaign.refresh_from_db()
    assert campaign.active_configuration.end_date == original.end_date


def test_a_campaign_change_before_pickup_refuses_cleanly(tmp_path):
    """The campaign opens after the review: the stale change fails at once."""
    from parishkit.stewardship.campaigns.boundaries import apply_due_boundaries

    from .campaign_builders import (
        admit_test_work,
        claimed_task,
        draft_campaign,
        end_request,
    )

    store, campaign, actor = draft_campaign(tmp_path)
    original = campaign.active_configuration
    with campaign_clock(original.starts_at - timedelta(days=1)):
        command(campaign, actor, Action.ACTIVATE)
        request, _ = end_request(store, campaign, actor, "edit_end")
        later = rename_parish(store, actor)
    with campaign_clock(original.starts_at):
        run = claimed_task("campaign_boundary", campaign.pk, actor)
        apply_due_boundaries(
            campaign_id=campaign.pk,
            task_id=run.run_id,
            fence=run.fence,
            actor_id=actor,
            correlation_id=uuid4(),
            admit=admit_test_work,
        )
        campaign.refresh_from_db()
        assert campaign.state == "active"
        settled = drain(store)
    refused = settled[request.request_id]
    assert (refused.state, refused.failure_code) == ("failed", "stale_base")
    assert settled[later].state == "applied"
    coherent(store)
    campaign.refresh_from_db()
    assert campaign.active_configuration.end_date == original.end_date


def test_claimed_close_work_refuses_cleanly_and_the_queue_moves_on(tmp_path):
    """A claimed close task refuses the change; the next request still applies."""
    from django.db import transaction
    from django.db.models import F

    from .campaign_builders import claimed_task, draft_campaign, end_request

    store, campaign, actor = draft_campaign(tmp_path)
    original = campaign.active_configuration
    with campaign_clock(original.starts_at):
        command(campaign, actor, Action.ACTIVATE)
        row = CampaignBoundaryOccurrence.objects.create(
            campaign=campaign,
            kind="close",
            due_at=original.ends_at,
            actor_id=actor,
            correlation_id=uuid4(),
        )
        run = claimed_task("campaign_boundary", campaign.pk, actor)
        with transaction.atomic():
            CampaignBoundaryOccurrence.objects.filter(pk=row.pk).update(
                task_id=run.run_id,
                task_fence=run.fence,
                version=F("version") + 1,
                actor_id=actor,
                correlation_id=uuid4(),
            )
        request, _ = end_request(store, campaign, actor, "edit_end")
        later = rename_parish(store, actor)
        settled = drain(store)
    refused = settled[request.request_id]
    assert (refused.state, refused.failure_code) == ("failed", "invalid_candidate")
    assert settled[later].state == "applied"
    coherent(store)


def test_change_status_shows_why_the_end_date_was_not_changed(auth_service, google):
    """Campaign settings' status of a refused change names its reason."""
    store = auth_service.store
    campaign = live(store)
    browser, _ = signed_in()
    url = reverse("admin:campaign_settings")
    page = browser.get(url)
    review = post(
        browser,
        url,
        {"action": "preview", "end_date": "2054-11-15", "base_digest": digest(page)},
    )
    accepted = post(browser, url, {"action": "confirm", "preview": token(review)})
    request = ConfigurationChangeRequest.objects.get(pk=requested(accepted))
    # The campaign closes (its old end passes) before the installer runs.
    with campaign_clock(campaign.active_configuration.ends_at + timedelta(hours=1)):
        status = installed(store, request.pk)
        assert (status.state, status.failure_code) == ("failed", "invalid_candidate")
        coherent(store)
        shown = browser.get(
            urlsplit(accepted["Location"]).path + "?request=" + str(request.pk)
        )
        text = unescape(shown.content.decode())
        assert shown.status_code == 200 and "Not applied" in text
        assert "The end date could no longer change" in text
        # Change status, which the page's region polls, says the same.
        polled = browser.get(reverse("admin:configuration_request", args=[request.pk]))
        assert "The end date could no longer change" in unescape(
            polled.content.decode()
        )


def test_the_command_line_cancels_a_stuck_end_change(admin, google, monkeypatch):
    """``config request cancel``: journaled, restored by the installer, audited.

    The change is stuck before its YAML switch: its candidate is prepared,
    but a failure that is not a refusal (here, a synthetic outage) stops
    every installer pass, so the queue would wait on it forever. The
    cancellation's journal is recovered before anything else is tried, so
    the next pass ends it even while that failure persists.
    """
    from parishkit.stewardship.accounts.configuration_installation import (
        DatabaseMaterializer,
    )
    from parishkit.stewardship.campaigns import admission
    from parishkit.stewardship.campaigns.live_end_date import refusal_text
    from parishkit.stewardship.runtime_process import next_configuration_request

    from .test_admin_schedule_cli_postgresql import one

    store = admin.service.store
    campaign = live(store)
    before = store.active()
    _, secret, row = paired(admin.service)
    code, document = preview(admin, secret, {"window": {"end_date": "2054-11-15"}})
    assert code == 0, document
    code, confirmed = confirm(admin, secret, document["result"]["preview"]["token"])
    request_id = UUID(confirmed["result"]["request"]["request_id"])
    checkpoint = DatabaseMaterializer.checkpoint

    def interrupted(materializer, state, **kwargs):
        """The installer stops once the candidate is prepared, before its receipt."""
        if state == "prepared":
            raise RuntimeError("synthetic interruption")
        return checkpoint(materializer, state, **kwargs)

    def outage(*args, **kwargs):
        """A failure that is not a refusal, on every pass."""
        raise RuntimeError("synthetic outage")

    with monkeypatch.context() as patch:
        patch.setattr(DatabaseMaterializer, "checkpoint", interrupted)
        with pytest.raises(RuntimeError, match="synthetic interruption"):
            installed(store, request_id)
    monkeypatch.setattr(admission, "validate_installation", outage)
    for _ in range(2):
        with pytest.raises(RuntimeError, match="synthetic outage"):
            drain(store)
    assert next_configuration_request() == request_id
    assert store.active() == before
    argv = ("config", "request", "cancel", str(request_id), "--yes")
    code, refused = one(admin, *argv, "--reason", " ", secret=secret)
    assert code == 1 and refused["error"]["code"] == "invalid", refused
    reason = "The parish decided to keep the date."
    code, cancelled = one(admin, *argv, "--reason", reason, secret=secret)
    assert code == 0, cancelled
    assert cancelled["result"]["cancelled"] is True
    assert cancelled["result"]["request"]["state"] == "validating"
    assert journal_reason(request_id) == reason
    assert AuditEvent.objects.filter(
        event_type="admin_cmd_config_request_cancel"
    ).values_list("actor_id", "subject_id").get() == (row.principal_id, row.pk)
    # Repeating the same cancellation changes nothing and records nothing.
    code, again = one(admin, *argv, "--reason", reason, secret=secret)
    assert code == 0 and again["result"]["cancelled"] is False
    assert (
        AuditEvent.objects.filter(event_type="admin_cmd_config_request_cancel").count()
        == 1
    )
    # The installer's next pass ends it first, though the outage persists.
    settled = drain(store)
    status = settled[request_id]
    assert (status.state, status.failure_code) == ("failed", "invalid_candidate")
    coherent(store)
    assert store.active() == before
    campaign.refresh_from_db()
    assert campaign.active_configuration.end_date.isoformat() == "2054-10-31"
    assert "An Administrator cancelled this change" in str(refusal_text(status))
    # A settled change can no longer be cancelled.
    code, late = one(admin, *argv, "--reason", "Another reason", secret=secret)
    assert code == 1 and late["error"]["code"] == "stale_version", late


def opened(store):
    """An open Production campaign (active), its reminder on 2054-10-25."""
    campaign, _ = campaign_with_reminder(store)
    with campaign_clock(campaign.active_configuration.starts_at):
        command(campaign, uuid4(), Action.ACTIVATE)
    campaign.refresh_from_db()
    assert campaign.state == "active" and campaign.structural_locked
    return campaign


def test_an_open_campaign_changes_its_end_date_on_the_page(auth_service, google):
    """Campaign settings offers and applies the end date of an open campaign."""
    store = auth_service.store
    campaign = opened(store)
    browser, _ = signed_in()
    url = reverse("admin:campaign_settings")
    page = browser.get(url)
    body = page.content.decode()
    assert 'id="end-date"' in body and 'name="end_date"' in body
    assert "This campaign is live, so these settings are locked" in body
    review = post(
        browser,
        url,
        {"action": "preview", "end_date": "2054-11-15", "base_digest": digest(page)},
    )
    assert review.status_code == 200, review.content
    accepted = post(browser, url, {"action": "confirm", "preview": token(review)})
    request = ConfigurationChangeRequest.objects.get(pk=requested(accepted))
    assert installed(store, request.pk).state == "applied"
    campaign.refresh_from_db()
    assert campaign.state == "active"
    assert campaign.active_configuration.end_date.isoformat() == "2054-11-15"
    coherent(store)


def test_an_open_campaign_changes_its_end_date_on_the_command_line(admin, google):
    """``schedule preview`` and ``confirm`` move an open campaign's end date."""
    store = admin.service.store
    campaign = opened(store)
    _, secret, _ = paired(admin.service)
    code, document = preview(admin, secret, {"window": {"end_date": "2054-11-15"}})
    assert code == 0, document
    code, confirmed = confirm(admin, secret, document["result"]["preview"]["token"])
    assert code == 0, confirmed
    request_id = UUID(confirmed["result"]["request"]["request_id"])
    assert installed(store, request_id).state == "applied"
    campaign.refresh_from_db()
    assert campaign.state == "active"
    assert campaign.active_configuration.end_date.isoformat() == "2054-11-15"


def test_held_work_notes_the_end_date_and_refuses_a_stale_review(auth_service, google):
    """While background work holds changes the panel says so; a post is refused."""
    from django.db import connection, transaction

    from parishkit.stewardship.campaigns.models import CampaignWorkGate

    store = auth_service.store
    campaign = live(store)
    browser, _ = signed_in()
    url = reverse("admin:campaign_settings")
    page = browser.get(url)
    base = digest(page)
    # A work gate (a purge reservation), the owner's sentinel only.
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(
            "ALTER TABLE stewardship_campaign_work_gate DISABLE TRIGGER USER"
        )
        CampaignWorkGate.objects.create(
            campaign=campaign,
            request_id=uuid4(),
            initiated_by_id=uuid4(),
            state="preparing",
        )
        cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
        cursor.execute("ALTER TABLE stewardship_campaign_work_gate ENABLE TRIGGER USER")
    held = browser.get(url)
    body = unescape(held.content.decode())
    assert held.status_code == 200
    panel = re.search(r'<section class="panel" id="end-date".*?</section>', body, re.S)
    assert panel is not None and 'name="end_date"' not in panel.group(0)
    assert "The end date can change once the background work" in body
    assert "They can change only on the current Testing-mode draft" not in body
    before = ConfigurationChangeRequest.objects.count()
    stale = post(
        browser,
        url,
        {"action": "preview", "end_date": "2054-11-15", "base_digest": base},
    )
    assert stale.status_code == 409
    assert "These campaign settings are locked" in unescape(stale.content.decode())
    assert ConfigurationChangeRequest.objects.count() == before


def test_a_past_end_date_is_refused_on_the_page(auth_service, google):
    """While the campaign runs, an end date already past is refused by name."""
    store = auth_service.store
    campaign = live(store)
    browser, _ = signed_in()
    url = reverse("admin:campaign_settings")
    with campaign_clock(campaign.active_configuration.starts_at + timedelta(days=10)):
        page = browser.get(url)
        assert 'name="end_date"' in page.content.decode()
        refused = post(
            browser,
            url,
            {
                "action": "preview",
                "end_date": "2054-10-05",
                "base_digest": digest(page),
            },
        )
    assert refused.status_code == 400
    text = unescape(refused.content.decode())
    assert "Choose an end date that has not already passed." in text
    assert "would no longer fit the campaign" not in text
    assert not CampaignConfigurationIntent.objects.exists()


def test_other_schedule_errors_are_shown_as_they_are(auth_service, google, monkeypatch):
    """Only a mailing outside the campaign links to the combined review (#944)."""
    from django import forms

    from parishkit.stewardship.accounts import schedule_forms

    store = auth_service.store
    live(store)
    browser, _ = signed_in()
    url = reverse("admin:campaign_settings")
    page = browser.get(url)

    def broken(self, rows):
        """A rule between schedules that the end date does not cause."""
        raise forms.ValidationError("Two emails to Families clash.")

    monkeypatch.setattr(schedule_forms.Schedules, "_check_collection", broken)
    refused = post(
        browser,
        url,
        {"action": "preview", "end_date": "2054-11-15", "base_digest": digest(page)},
    )
    assert refused.status_code == 400
    text = unescape(refused.content.decode())
    assert "Two emails to Families clash." in text
    assert "would no longer fit the campaign" not in text
    assert "?end_date=" not in text

"""Exact combined schedule/date previews through real Admin sessions and installer."""

from uuid import uuid4

import pytest

from parishkit.stewardship.accounts.request_models import ConfigurationChangeRequest
from parishkit.stewardship.campaigns.models import Campaign, ScheduleDefinition
from parishkit.stewardship.deployment import ServiceRole

from ..campaign_factory import schedule
from ..test_schedule_forms import data_for, window_data
from .auth_builders import signed_in
from .campaign_builders import (
    add_draft,
    advance,
    campaign_clock,
    change,
    claimed_task,
    occurrence,
)
from .test_background_grants_postgresql import task_login
from .test_campaign_views_postgresql import apply, post
from .test_parish_views_postgresql import token

pytestmark = pytest.mark.django_db(transaction=True)


def setup(store):
    """A real current draft with an initial invitation and a later reminder."""
    result, owner, _ = add_draft(store, store.active(), uuid4())
    assert result.state == "applied"
    reminder = schedule(owner["id"], kind="reminder", date="2026-10-25")
    assert (
        change(
            store,
            store.active(),
            uuid4(),
            [{"operation": "add", "section": "schedules", **reminder}],
        ).state
        == "applied"
    )
    return Campaign.objects.get(), f"/admin/campaign/{owner['id']}/schedules"


def fields(store, campaign, *, editable=True):
    """Post every server-selected logical ID, preserving explicit row order."""
    document = store.active().document()
    rows = [
        row
        for row in document["sections"].get("schedules", [])
        if row["values"]["campaign_id"] == str(campaign.pk)
    ]
    values = data_for(rows) | {"base_digest": store.active().digest}
    if editable:
        values.update(
            {
                f"window-{name}": value
                for name, value in window_data(
                    {"values": campaign.active_configuration.values}
                ).items()
            }
        )
    return values, {row["values"]["kind"]: index for index, row in enumerate(rows)}


def pending(definition, actor):
    """Create only due work through the real campaign-clock admission boundary."""
    with campaign_clock(definition.current_revision.due_at):
        return occurrence(definition, actor)


def test_shortened_draft_requires_every_stranded_mailing_to_be_reconciled(
    auth_service, google
):
    """No partial end-date request is accepted while its late reminder is unresolved."""
    store = auth_service.store
    campaign, path = setup(store)
    browser, _ = signed_in()
    assert browser.get(path).status_code == 200
    data, indexes = fields(store, campaign)
    data["window-end_date"] = "2026-10-20"
    before = ConfigurationChangeRequest.objects.count()
    assert post(browser, path, data).status_code == 400
    assert ConfigurationChangeRequest.objects.count() == before
    data[f"schedules-{indexes['reminder']}-DELETE"] = "on"
    proposal = token(post(browser, path, data))
    accepted = post(browser, path, {"action": "confirm", "preview": proposal})
    apply(store, accepted)
    campaign.refresh_from_db()
    assert campaign.active_configuration.values["end_date"] == "2026-10-20"
    assert ScheduleDefinition.objects.get(kind="reminder").current_revision_id is None
    assert (
        post(browser, path, {"action": "confirm", "preview": proposal})["Location"]
        == accepted["Location"]
    )


def test_schedule_pending_work_is_counted_then_cancelled_with_replacement(
    auth_service, google
):
    """A safe pending occurrence becomes skipped in the same activation transaction."""
    store = auth_service.store
    campaign, path = setup(store)
    definition = ScheduleDefinition.objects.get(kind="reminder")
    row = pending(definition, uuid4())
    browser, _ = signed_in()
    data, indexes = fields(store, campaign)
    data[f"schedules-{indexes['reminder']}-date"] = "2026-10-19"
    preview = post(browser, path, data)
    assert b"Safely cancellable occurrences" in preview.content
    apply(store, post(browser, path, {"action": "confirm", "preview": token(preview)}))
    row.refresh_from_db()
    assert row.state == "skipped" and row.reason == "schedule_replaced"


def test_new_occurrence_invalidates_a_previously_exact_schedule_preview(
    auth_service, google
):
    """Work generation is signed as well as configuration and source generations."""
    store = auth_service.store
    campaign, path = setup(store)
    browser, _ = signed_in()
    data, indexes = fields(store, campaign)
    data[f"schedules-{indexes['reminder']}-date"] = "2026-10-19"
    proposal = token(post(browser, path, data))
    pending(ScheduleDefinition.objects.get(kind="reminder"), uuid4())
    assert (
        post(browser, path, {"action": "confirm", "preview": proposal}).status_code
        == 409
    )


def test_running_work_blocks_confirmation_without_claiming_it_can_be_cancelled(
    auth_service, google
):
    """Display blocking work and withhold an Apply control until it is resolved."""
    store = auth_service.store
    campaign, path = setup(store)
    actor = uuid4()
    row = pending(ScheduleDefinition.objects.get(kind="reminder"), actor)
    with campaign_clock(row.due_at):
        run = claimed_task("schedule_occurrence", row.pk, actor)
        row = advance(row, actor, "running", task_id=run.run_id, fence=run.fence)
    browser, _ = signed_in()
    data, indexes = fields(store, campaign)
    data[f"schedules-{indexes['reminder']}-date"] = "2026-10-19"
    response = post(browser, path, data)
    assert response.status_code == 200
    assert b"blocks this change" in response.content
    assert b'name="preview"' not in response.content


def test_schedule_preview_works_under_web_grants_and_rejects_hidden_changes(
    auth_service, google
):
    """The web process counts metadata and stages intent without installation rights."""
    store = auth_service.store
    campaign, path = setup(store)
    browser, _ = signed_in()
    data, indexes = fields(store, campaign)
    data[f"schedules-{indexes['reminder']}-date"] = "2026-10-19"
    with task_login(ServiceRole.WEB):
        assert browser.get(path).status_code == 200
        proposal = token(post(browser, path, data))
        accepted = post(browser, path, {"action": "confirm", "preview": proposal})
    apply(store, accepted)
    assert (
        post(browser, path, data | {"window-modules": "financial"}).status_code == 400
    )
    assert (
        post(
            browser, path, data | {"schedules-0-subject": "Hidden subject"}
        ).status_code
        == 400
    )


@pytest.mark.parametrize(
    "change_field,new_value",
    [
        ("timezone", "America/Los_Angeles"),
        ("start_date", "2026-09-30"),
        ("end_date", "2026-10-30"),
    ],
)
def test_campaign_window_change_replaces_cadence_even_when_mail_fields_are_unchanged(
    auth_service,
    google,
    change_field,
    new_value,
):
    """A schedule cannot keep a revision resolved in the previous campaign window."""
    store = auth_service.store
    campaign, path = setup(store)
    definition = ScheduleDefinition.objects.select_related("current_revision").get(
        kind="reminder"
    )
    previous = definition.current_revision
    work = pending(definition, uuid4())
    browser, _ = signed_in()
    data, _ = fields(store, campaign)
    data[f"window-{change_field}"] = new_value
    preview = post(browser, path, data)
    if change_field == "timezone":
        assert b"Safely cancellable occurrences" in preview.content
    apply(store, post(browser, path, {"action": "confirm", "preview": token(preview)}))
    definition.refresh_from_db()
    work.refresh_from_db()
    assert definition.current_revision.values == previous.values
    if change_field == "timezone":
        from datetime import timedelta

        assert definition.current_revision.due_at == previous.due_at + timedelta(
            hours=3
        )
        assert definition.current_revision_id != previous.pk
        assert work.state == "skipped"
    else:
        assert definition.current_revision_id == previous.pk
        assert work.state == "pending"

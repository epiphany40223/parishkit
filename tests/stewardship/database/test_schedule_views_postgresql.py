"""Exact combined schedule/date previews through real Admin sessions and installer."""

from uuid import uuid4

import psycopg
import pytest
from django.db import connection

from parishkit.stewardship.accounts import schedule_views
from parishkit.stewardship.accounts.policy_models import PortalUser
from parishkit.stewardship.accounts.request_models import ConfigurationChangeRequest
from parishkit.stewardship.accounts.schedule_forms import schedule_order
from parishkit.stewardship.campaigns.models import Campaign, ScheduleDefinition
from parishkit.stewardship.campaigns.work_locks import WORK_ORDER_LOCK
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
from .test_admin_navigation_postgresql import STEPS, flow_steps
from .test_background_grants_postgresql import task_login
from .test_campaign_views_postgresql import apply, post
from .test_digest_schedule_planning_postgresql import add_digest
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
    """Post every server-selected logical ID in the order the page shows them.

    The page lists saved schedules in sending order (#448), and a browser
    posts its rows in that order, so the test does the same.
    """
    document = store.active().document()
    rows = sorted(
        (
            row
            for row in document["sections"].get("schedules", [])
            if row["values"]["campaign_id"] == str(campaign.pk)
        ),
        key=schedule_order,
    )
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
    page = browser.get(path)
    assert page.status_code == 200
    assert flow_steps(page.content) == (STEPS, "Make changes")
    data, indexes = fields(store, campaign)
    data["window-end_date"] = "2026-10-20"
    before = ConfigurationChangeRequest.objects.count()
    refused = post(browser, path, data)
    assert refused.status_code == 400
    # A refused review returns to the first step (#196).
    assert flow_steps(refused.content) == (STEPS, "Make changes")
    assert ConfigurationChangeRequest.objects.count() == before
    data[f"schedules-{indexes['reminder']}-DELETE"] = "on"
    preview = post(browser, path, data)
    assert flow_steps(preview.content) == (STEPS, "Review")
    proposal = token(preview)
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
    assert b"Planned sends not started yet" in preview.content
    apply(store, post(browser, path, {"action": "confirm", "preview": token(preview)}))
    row.refresh_from_db()
    assert row.state == "skipped" and row.reason == "schedule_replaced"


def test_date_only_digest_change_includes_cancellation_inventory(auth_service, google):
    """Unchanged digest fields still appear in the exact date-edit confirmation."""
    from datetime import UTC, datetime

    store = auth_service.store
    campaign, path = setup(store)
    identifier = add_digest(store, campaign)
    definition = ScheduleDefinition.objects.get(pk=identifier)
    due = datetime(2026, 10, 3, tzinfo=UTC)
    with campaign_clock(due):
        row = occurrence(
            definition, uuid4(), target="admins", slot="2026-10-01", due_at=due
        )
    browser, _ = signed_in()
    data, _ = fields(store, campaign)
    data["window-end_date"] = "2026-10-30"
    preview = post(browser, path, data)
    assert preview.status_code == 200
    assert b"Planned sends not started yet" in preview.content
    apply(store, post(browser, path, {"action": "confirm", "preview": token(preview)}))
    row.refresh_from_db()
    assert row.state == "skipped" and row.reason == "schedule_replaced"


def test_preview_resolves_applied_and_proposed_times_in_their_own_campaign_zones(
    auth_service, google
):
    """The signed Admin preview displays resolved instants, not just wall time."""
    store = auth_service.store
    campaign, path = setup(store)
    browser, _ = signed_in()
    data, _ = fields(store, campaign)
    data["window-timezone"] = "America/Los_Angeles"
    requests_before = ConfigurationChangeRequest.objects.count()
    response = post(browser, path, data)
    assert response.status_code == 200
    assert b"2026-10-01T13:00:00+00:00" in response.content
    assert b"2026-10-01T16:00:00+00:00" in response.content
    assert b"America/New_York" in response.content
    assert b"America/Los_Angeles" in response.content
    assert b"data-local-instant" in response.content
    assert b"not a list of who will receive it" in response.content
    # Merely inspecting candidate instants cannot allocate or apply anything.
    assert ConfigurationChangeRequest.objects.count() == requests_before
    campaign.refresh_from_db()
    assert campaign.active_configuration.values["timezone"] == "America/New_York"


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


def test_daily_preview_is_bounded_and_labels_reported_days(auth_service, google):
    """Recurring mail uses the same bounded evaluator as the future work planner."""
    store = auth_service.store
    campaign, path = setup(store)
    digest = schedule(str(campaign.pk), kind="daily_digest", date=None, time="00:15:00")
    assert (
        change(
            store,
            store.active(),
            uuid4(),
            [{"operation": "add", "section": "schedules", **digest}],
        ).state
        == "applied"
    )
    browser, _ = signed_in()
    data, indexes = fields(store, campaign)
    data[f"schedules-{indexes['daily_digest']}-time"] = "00:30:00"
    response = post(browser, path, data)
    assert response.status_code == 200
    assert b"Only the first five dates are shown" in response.content
    assert b"campaign day the digest reports on" in response.content
    assert b"2026-10-02T04:15:00+00:00" in response.content
    assert b"2026-10-02T04:30:00+00:00" in response.content
    assert response.content.count(b"data-local-instant") == 10


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
    assert b"Sends blocking the change" in response.content
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
        assert b"Planned sends not started yet" in preview.content
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


def test_proposed_dates_page_posts_to_its_clean_path(auth_service, google):
    """Campaign settings hands dates over in the query; the forms must drop it.

    A POST that still carries the query string is refused, so both the editor
    and its preview post to the bare path and carry the dates as fields.
    """
    store = auth_service.store
    campaign, path = setup(store)
    browser, _ = signed_in()
    page = browser.get(f"{path}?start_date=2026-09-30")
    assert page.status_code == 200
    assert f'action="{path}"'.encode() in page.content
    data, _ = fields(store, campaign)
    data["window-start_date"] = "2026-09-30"
    preview = post(browser, path, data)
    assert f'action="{path}"'.encode() in preview.content
    token(preview)


def test_read_pages_never_wait_behind_the_work_lock(auth_service, google):
    """A long writer (a source promotion, an installer) blocks no Admin read.

    Another session holds the work-order lock for the whole test. Read pages
    observe one read-only snapshot and still render; a preview that joins the
    writers' order waits for that lock and, under the short statement timeout,
    refuses instead of rendering, so a regression fails fast either way.
    """
    store = auth_service.store
    campaign, path = setup(store)
    browser, _ = signed_in()
    data, indexes = fields(store, campaign)
    data[f"schedules-{indexes['reminder']}-date"] = "2026-10-19"
    with other_session() as holder:
        holder.execute("SELECT pg_advisory_lock(%s,%s)", WORK_ORDER_LOCK)
        with connection.cursor() as cursor:
            cursor.execute("SET statement_timeout = '3s'")
        try:
            for url in (
                "/admin/",
                "/admin/users",
                path,
                f"/admin/campaign/{campaign.pk}/settings",
                f"/admin/campaign/{campaign.pk}/content",
                f"/admin/campaign/{campaign.pk}/content/email/initial",
            ):
                assert browser.get(url).status_code == 200, url
            assert post(browser, path, data).status_code == 503
        finally:
            with connection.cursor() as cursor:
                cursor.execute("RESET statement_timeout")
    # Once the writer releases the lock the same preview renders normally.
    assert post(browser, path, data).status_code == 200


def other_session():
    """A second autocommit session, as a concurrent writer really arrives."""
    settings = connection.settings_dict
    return psycopg.connect(
        host=settings["HOST"],
        port=settings["PORT"],
        user=settings["USER"],
        password=settings["PASSWORD"],
        dbname=settings["NAME"],
        autocommit=True,
    )


def test_access_revoked_while_a_read_page_renders_is_refused(
    auth_service, google, monkeypatch
):
    """A read-only snapshot cannot hide a revocation committed during the read.

    Another session disables the Administrator after the page's snapshot has
    begun; the access recheck runs after the snapshot ends and must refuse.
    """
    store = auth_service.store
    _, path = setup(store)
    browser, _ = signed_in()
    admin = PortalUser.objects.get(email="admin@example.org")
    genuine = schedule_views._page

    def revoking(*args, **kwargs):
        """Render as usual while a concurrent session disables the reader."""
        with other_session() as other:
            other.execute(
                "UPDATE stewardship_portal_user SET disabled=true, "
                "version=version+1 WHERE id=%s",
                [admin.pk],
            )
        return genuine(*args, **kwargs)

    monkeypatch.setattr(schedule_views, "_page", revoking)
    assert browser.get(path).status_code == 403

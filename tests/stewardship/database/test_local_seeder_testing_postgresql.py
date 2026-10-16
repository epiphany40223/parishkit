"""The seeder's ``testing`` step on real storage (#1413).

No fake clock and no running services here (the step itself is a documented
VM run): these prove that the step's configuration patch installs through
the real request path with every schedule after now, that its chosen-Family
Testing send goes through the page's own review and confirmation, that its
wait follows the real ticket and message rows, and that the rehearsal code
the Testing message carries signs the Family in for a Testing submission.
"""

# ruff: noqa: F811 -- imported pytest fixtures are injected by name.

from datetime import UTC, datetime
from uuid import UUID

import pytest
from django.db import transaction

from parishkit.stewardship.campaigns.credential_models import (
    CampaignCredentialState,
    FamilyCampaign,
    FamilySession,
)
from parishkit.stewardship.campaigns.models import Campaign, ScheduleDefinition
from parishkit.stewardship.local import seed_web, seeder
from parishkit.stewardship.responses.models import Submission

from ..content_factory import content
from .campaign_builders import campaign_clock, change, draft_campaign
from .test_family_mail_test_postgresql import (  # noqa: F401
    code_of,
    deliver,
    family_test,
    prepare_tests,
)
from .test_runtime_auth_grants_postgresql import web_login

pytestmark = pytest.mark.django_db(transaction=True)


def test_draft_patch_installs_with_now_inside_and_every_schedule_after_it(
    tmp_path, monkeypatch
):
    """The one patch the step records is valid and lands as future schedules."""
    store, campaign, actor = draft_campaign(tmp_path)
    timezone = campaign.active_configuration.timezone
    now = datetime(2054, 10, 7, 15, 30, tzinfo=UTC)
    cal = seeder.draft_calendar(now, timezone)
    # Post-setup has no schedules (Create the campaign adds none) but has the
    # default Initial and Reminder emails, so the step always takes the add
    # path: remove the fixture's Initial and add those two emails first.
    initials, reminders = seeder.campaign_schedules(
        store.active().document(), campaign.pk
    )
    assert len(initials) == 1 and not reminders
    emails = [
        content(str(campaign.pk), kind="email", slot=slot)
        for slot in ("initial", "reminder")
    ]
    removed = change(
        store,
        store.active(),
        actor,
        [
            {"operation": "remove", "section": "schedules", "id": initials[0]["id"]},
            *[{"operation": "add", "section": "content", **row} for row in emails],
        ],
    )
    assert removed.state == "applied", removed
    active = store.active()
    assert seeder.campaign_schedules(active.document(), campaign.pk) == ([], [])
    patch = seeder.schedule_patch(
        active.document(), campaign.pk, cal, initial_date=cal.initial
    )
    assert [step["operation"] for step in patch] == ["update", "add", "add", "add"]
    result = change(store, active, actor, patch)
    assert result.state == "applied", result
    campaign = Campaign.objects.select_related("active_configuration").get(
        pk=campaign.pk
    )
    configuration = campaign.active_configuration
    assert (configuration.start_date, configuration.end_date) == (cal.start, cal.end)
    assert configuration.starts_at <= now < configuration.ends_at
    schedules = store.active().document()["sections"]["schedules"]
    dates = sorted(
        (row["values"]["kind"], row["values"]["date"], row["values"]["time"])
        for row in schedules
    )
    assert dates == [
        ("initial", cal.initial.isoformat(), "10:00:00"),
        *[("reminder", r.date().isoformat(), "09:00:00") for r in cal.reminders],
    ]
    definitions = ScheduleDefinition.objects.filter(
        campaign=campaign,
        kind__in=("initial", "reminder"),
        current_revision__isnull=False,
    )
    assert definitions.count() == 1 + len(cal.reminders)
    assert all(row.current_revision.due_at > now for row in definitions)
    # The step's own reads: a fresh draft that never went live passes until
    # it has schedules. The fixture installs without the wizard, so its
    # setup marker is taken as complete here.
    monkeypatch.setattr(
        "parishkit.stewardship.accounts.setup_completion.setup_is_complete",
        lambda: True,
    )
    with transaction.atomic():
        state = seeder.draft_state(campaign, active.document())
    assert state["mode"] == "testing" and state["state"] == "draft"
    assert state["went_live"] is False and state["schedules"] is False
    # A rerun now refuses, since a schedule could be due.
    with transaction.atomic():
        state = seeder.draft_state(campaign, store.active().document())
    assert state["schedules"] is True
    assert "post-setup" in seeder.draft_refusal(**state)


def test_testing_send_and_rehearsal_sign_in_go_through_the_real_paths(family_test):
    """The page's review and confirm, the worker's code, a Testing submission."""
    from parishkit.stewardship.accounts.admin_session_store import SessionStore
    from parishkit.stewardship.accounts.authentication import runtime

    harness, browser, _, _ = family_test
    campaign = harness.campaign
    revision = UUID(
        ScheduleDefinition.objects.get().current_revision.values["template_version"]
    )
    family = FamilyCampaign.objects.get(campaign=campaign, family_duid=1)
    epoch = seeder.rehearsal_epoch(campaign.pk)
    assert (
        epoch
        == CampaignCredentialState.objects.get(campaign=campaign).rehearsal_epoch_id
    )
    request = seed_web.new_request(
        session=SessionStore(session_key=browser.cookies["pk_admin"].value)
    )
    # An ineligible choice is refused before any ticket is written.
    with web_login(), pytest.raises(seed_web.SeedWebRefused, match="unknown"):
        seed_web.family_tests(request, runtime(), campaign.pk, revision, [999999])
    with web_login():
        (ticket,) = seed_web.family_tests(
            request, runtime(), campaign.pk, revision, [1]
        )
    assert ticket.family_id == family.pk and ticket.rehearsal_epoch_id == epoch
    assert seeder.family_tests_sent([ticket.pk]) == "1 Testing send(s) still queued"
    (message,) = prepare_tests(harness)
    assert seeder.family_tests_sent([ticket.pk]) == (
        "1 Testing message(s) not yet delivered"
    )
    # The driver reads the same rehearsal code the Testing message carries
    # (read before delivery, which scrubs the sealed copy).
    driver = seed_web.RehearsalFamilyWebDriver(harness.service, campaign, epoch)
    assert driver._actor(family.pk).code == code_of(harness, message)
    deliver(harness, message)
    assert seeder.family_tests_sent([ticket.pk]) is True
    answers = seeder.draft_answers(
        campaign.active_configuration.values.get("ministry_duids", [])
    )
    with campaign_clock(campaign.active_configuration.starts_at), web_login():
        driver.session(family.pk, {})
        driver.submission(family.pk, {"answers": answers[0]})
    assert FamilySession.objects.filter(
        family=family, rehearsal_epoch_id=epoch
    ).exists()
    (submission,) = Submission.objects.filter(family=family, rehearsal_epoch_id=epoch)
    assert submission.mode != "live"

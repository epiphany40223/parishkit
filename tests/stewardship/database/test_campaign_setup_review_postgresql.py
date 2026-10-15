"""A Campaign setup step kept as is (#1404, frozen migration 0105).

Real Google sessions, the restricted web role, SQL guards and the installer:
Continue on an unchanged step records it as reviewed, with its audit event,
completes the step and opens the next one not done yet; SQL refuses the row
outside a Testing draft's setup, without its audit, and once written.
"""

import re
from pathlib import Path
from uuid import uuid4

import pytest
from django.db import DatabaseError, connection, transaction
from django.urls import reverse

from parishkit.stewardship.accounts import campaign_wizard
from parishkit.stewardship.accounts.campaign_wizard_models import CampaignSetupReview
from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.audit.models import AuditEvent
from parishkit.stewardship.audit.schemas import Action as AuditAction
from parishkit.stewardship.audit.schemas import ActorKind
from parishkit.stewardship.audit.services import record_action
from parishkit.stewardship.campaigns.lifecycle import Action
from parishkit.stewardship.deployment import ServiceRole

from .auth_builders import signed_in, unguarded
from .campaign_builders import command
from .test_background_grants_postgresql import task_login
from .test_campaign_stepper_postgresql import status_of, stepper
from .test_campaign_views_postgresql import post
from .test_share_views_postgresql import setup

pytestmark = pytest.mark.django_db(transaction=True)
CONTINUE = "/admin/campaign/setup/continuation/"
FROZEN = (
    Path(campaign_wizard.__file__).resolve().parents[1]
    / "schema"
    / "migrations"
    / "0105_campaign_setup_review.sql"
)


def web():
    """Every browser request runs under the real restricted web role."""
    return task_login(ServiceRole.WEB, exact=True, reconnect=True)


def keep(browser, step):
    """Press the step's Continue, as its action row's form posts it."""
    with web():
        return post(browser, CONTINUE, {"step": step})


def test_continue_keeps_an_unchanged_step_and_opens_the_next(auth_service, google):
    """Share options' defaults kept as is complete it; Continue goes on."""
    row, _, path = setup(auth_service.store)
    browser, _ = signed_in()
    with web():
        page = browser.get(path).content.decode()
    assert status_of(stepper(page.encode()), "Share options") == (
        "Current step — Not done yet"
    )
    # The action row: Back to Campaign settings and the Continue form.
    assert f'action="{CONTINUE}"' in page
    assert '<input type="hidden" name="step" value="share_options">' in page
    assert "Continue to Pages and emails" in page
    response = keep(browser, "share_options")
    assert response.status_code == 303
    assert response["Location"] == reverse("admin:content_catalog")
    review = CampaignSetupReview.objects.get()
    assert (review.campaign_id, review.step) == (row.pk, "share_options")
    assert review.item == ""
    event = AuditEvent.objects.get(event_type="campaign_setup_step_reviewed")
    assert (event.subject_id, event.actor_id, event.campaign_reference) == (
        review.pk,
        review.actor_id,
        row.pk,
    )
    with web():
        region = stepper(browser.get(path).content)
    assert status_of(region, "Share options") == "Current step — Completed"
    assert "2 of 7 steps completed" in region
    # Pressed again (Back, then Continue), nothing more is recorded; it still
    # opens the next step not done yet.
    again = keep(browser, "share_options")
    assert again["Location"] == reverse("admin:content_catalog")
    assert CampaignSetupReview.objects.count() == 1
    # Continue on Pages and emails skips nothing that is not done.
    assert keep(browser, "content")["Location"] == reverse("admin:schedule_settings")
    # Continue never accepts another step name, another field, or a GET.
    assert keep(browser, "mail_test").status_code == 400
    assert keep(browser, "go_live").status_code == 400
    with web():
        assert browser.get(CONTINUE).status_code == 405
    # Once the campaign leaves draft, Continue records nothing.
    command(row, uuid4(), Action.ACTIVATE)
    assert keep(browser, "images").status_code >= 400
    assert CampaignSetupReview.objects.count() == 2


def test_home_and_sub_pages_lead_back_into_the_stepper(auth_service, google):
    """Home offers Continue campaign setup; a sub-page links Return to its step."""
    setup(auth_service.store)
    browser, _ = signed_in()
    with web():
        home = browser.get("/admin/").content.decode()
        schedules = browser.get(reverse("admin:schedule_settings")).content.decode()
        calendar = browser.get(reverse("admin:schedule_calendar")).content.decode()
    assert (
        f'<a href="{reverse("admin:share_settings")}">'
        "Continue campaign setup: Share options</a>"
    ) in home
    # Dates and mail schedules with its schedule (the builder adds one): an
    # ordinary Continue (with none it says so, test_campaign_wizard).
    assert "Continue to Campaign images" in schedules
    assert "Continue without schedules for now" not in schedules
    # The Campaign calendar is Dates and mail schedules' sub-page.
    region = stepper(calendar.encode())
    assert "<strong>Dates and mail schedules</strong>" in region
    assert (
        f'href="{reverse("admin:schedule_settings")}"' in calendar
        and "Return to Dates and mail schedules" in calendar
    )
    assert f'action="{CONTINUE}"' not in calendar


def admin_id(browser):
    """The signed-in Administrator's user id, from a review it recorded."""
    keep(browser, "share_options")
    return CampaignSetupReview.objects.get().actor_id


def insert(campaign, actor, step="settings", item="", *, audited=True):
    """Insert one review directly, with its audit unless told not to."""
    with transaction.atomic():
        review = CampaignSetupReview.objects.create(
            campaign=campaign, step=step, item=item, actor_id=actor
        )
        if audited:
            record_action(
                AuditAction.CAMPAIGN_SETUP_STEP_REVIEWED,
                actor_kind=ActorKind.PORTAL_USER,
                actor_id=actor,
                subject_id=review.pk,
                campaign_id=campaign.pk,
                current_parish=True,
            )
    return review


def test_sql_admits_a_review_only_during_setup_with_its_audit(auth_service, google):
    """The guard and the deferred audit check refuse what Python never sends."""
    row, _, _ = setup(auth_service.store)
    browser, _ = signed_in()
    actor = admin_id(browser)
    review = insert(row, actor, "content", "page:welcome")
    assert review.created_at is not None
    refused = "Campaign setup review requires an Administrator and a Testing draft"
    # Not an Administrator.
    with pytest.raises(DatabaseError, match=refused):
        insert(row, uuid4())
    # No audit event in the same transaction.
    with pytest.raises(DatabaseError, match="requires its audit"):
        insert(row, actor, "images", audited=False)
    # An item only on Pages and emails, and only a slot.
    for step, item in (("images", "page:welcome"), ("content", "Welcome")):
        with pytest.raises(DatabaseError, match="campaign_setup_review_item"):
            insert(row, actor, step, item)
    # Immutable: no update or delete, even unguarded by the ORM.
    for statement in (
        "UPDATE stewardship_campaign_setup_review SET item=''",
        "DELETE FROM stewardship_campaign_setup_review",
    ):
        with (
            pytest.raises(DatabaseError, match="immutable"),
            transaction.atomic(),
            connection.cursor() as cursor,
        ):
            cursor.execute(statement)
    # A go-live started, Production mode, then a live campaign: refused.
    # The credential row with its go-live gate set, seeded as only a
    # superuser could (the gate's own journaled owner is go-live cleanup).
    with unguarded(), connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO stewardship_campaign_credentials (id, correlation_id,"
            " version, campaign_id, go_live_gate, eligibility_digest,"
            " eligible_count, population_dirty)"
            " VALUES (%s, %s, 1, %s, true, '', 0, true)",
            [uuid4(), uuid4(), row.pk],
        )
    with pytest.raises(DatabaseError, match=refused):
        insert(row, actor, "images")
    with unguarded(), connection.cursor() as cursor:
        cursor.execute(
            "DELETE FROM stewardship_campaign_credentials"
        )
    with unguarded():
        SystemConfiguration.objects.update(mode="production")
    with pytest.raises(DatabaseError, match=refused):
        insert(row, actor, "images")
    with unguarded():
        SystemConfiguration.objects.update(mode="testing")
    insert(row, actor, "images")
    command(row, uuid4(), Action.ACTIVATE)
    with pytest.raises(DatabaseError, match=refused):
        insert(row, actor, "schedules")


def test_the_do_block_refuses_an_incomplete_install(auth_service):
    """0105's check raises on a guard without its draft condition.

    It runs against the installed table with the guard put back to a body
    that lacks the go-live check, inside a transaction that is rolled back.
    """
    text = FROZEN.read_text(encoding="utf-8")
    check = text[text.index("DO $check$") :]
    with connection.cursor() as cursor:
        cursor.execute(check)  # The real install passes.
    start = text.index("CREATE FUNCTION public.stewardship_campaign_setup_review_guard")
    end = text.index("CREATE FUNCTION public.stewardship_campaign_setup_review_effect")
    weakened = re.sub(
        r"\s*OR EXISTS \(SELECT 1 FROM public\.stewardship_campaign_credentials k"
        r"\s*WHERE k\.campaign_id=NEW\.campaign_id AND k\.go_live_gate\)",
        "",
        text[start:end],
    ).replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1)
    assert "go_live_gate" not in weakened
    with (
        pytest.raises(DatabaseError, match="not guarded as declared"),
        transaction.atomic(),
        connection.cursor() as cursor,
    ):
        cursor.execute(weakened)
        cursor.execute(check)

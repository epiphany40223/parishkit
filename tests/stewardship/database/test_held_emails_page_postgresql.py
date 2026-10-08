"""The Held emails page through the real web stack (#757, migration 0029).

After a restore is released with undecided holds, an Administrator decides
them here per send. A resend is then planned by the ordinary planner, and an
undecided invitation's banner disappears once it is decided.
"""

from uuid import uuid4

import pytest

from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.campaigns.credential_models import FamilyCampaign
from parishkit.stewardship.campaigns.models import (
    RestoreDeliveryHold,
    ScheduleOccurrence,
)
from parishkit.stewardship.campaigns.restore_review import release_review
from parishkit.stewardship.deployment import ServiceRole

from .auth_builders import signed_in
from .campaign_builders import campaign_clock, complete_empty_catchup
from .test_background_grants_postgresql import task_login
from .test_restore_review_delivery_postgresql import plan
from .test_restore_review_postgresql import (
    NOW,
    as_web,
    begin,
    list_held_emails_for,
    live_campaign,
)

pytestmark = pytest.mark.django_db(transaction=True)

PAGE = "/admin/deliveries/held-emails"


def post(browser, **fields):
    """One step, posted the way the page's forms post it."""
    return browser.post(
        PAGE, fields | {"csrfmiddlewaretoken": browser.cookies["pk_admin_csrf"].value}
    )


@pytest.fixture
def released(auth_service, google):
    """A live campaign restored, listed and released with nothing decided."""
    campaign = live_campaign(auth_service)
    browser, _ = signed_in()
    with campaign_clock(NOW):
        complete_empty_catchup(campaign, uuid4())
        begin()
        as_web(list_held_emails_for)
        as_web(
            release_review,
            request_id=uuid4(),
            expected_runtime_version=SystemConfiguration.objects.get().version,
        )
    return browser


def invitation():
    """Family 3's invitation hold: reachable, undecided."""
    family = FamilyCampaign.objects.get(family_duid=3)
    return family, RestoreDeliveryHold.objects.get(
        target=f"family:{family.pk}", definition__kind="initial"
    )


def test_the_banner_and_page_name_the_held_invitations(released):
    """Every Admin page names the Families whose reminders wait."""
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        home = released.get("/admin/", follow=True)
        page = released.get(PAGE)
    # Family 3 only: Family 2's invitation hold is inert (it has no email).
    assert b"data-held-invitations" in home.content
    assert b"1 Family's invitation is still held" in home.content
    assert page.status_code == 200
    text = page.content.decode()
    assert "Held emails" in text and "Send these again" in text
    assert "1 Family gets no reminders until its held invitation is decided" in text


def test_send_again_after_release_lets_the_planner_send_once(released):
    """Decide the invitation per send; the planner then plans it, coalesced."""
    family, hold = invitation()
    with campaign_clock(NOW), task_login(ServiceRole.WEB, exact=True, reconnect=True):
        preview = post(
            released,
            action="group-preview",
            definition=hold.definition_id,
            group_action="resend",
        )
        assert b"Confirm: send again" in preview.content
        assert b"They are sent shortly" in preview.content
        # Families 2 and 3 have undecided invitations; both are settled.
        done = post(
            released,
            action="group-confirm",
            definition=hold.definition_id,
            group_action="resend",
            count="2",
            evidence="Not in the provider's log.",
        )
    assert done.status_code == 200 and b"2 held emails settled" in done.content
    hold.refresh_from_db()
    assert hold.state == "resend_authorized"
    # The reminders are still held undecided, so only the invitation is
    # planned for Family 3; deciding the reminders lets them coalesce.
    first = plan(family, uuid4())
    selected = ScheduleOccurrence.objects.get(pk=first.selected)
    assert selected.definition.kind == "initial" and not first.held
    assert plan(family, uuid4()).selected == first.selected


def test_a_stale_count_or_sign_in_changes_nothing(released):
    """A count that changed, or a stale sign-in, is answered in place."""
    from datetime import timedelta

    from parishkit.stewardship.accounts.models import PortalSession

    from .auth_builders import unguarded

    _, hold = invitation()
    with campaign_clock(NOW), task_login(ServiceRole.WEB, exact=True, reconnect=True):
        stale = post(
            released,
            action="group-confirm",
            definition=hold.definition_id,
            group_action="assume",
            count="1",
            evidence="Seen in the log.",
        )
    assert stale.status_code == 409
    session = PortalSession.objects.order_by("-authenticated_at").first()
    with unguarded():
        PortalSession.objects.filter(pk=session.pk).update(
            authenticated_at=session.authenticated_at - timedelta(minutes=10)
        )
    with campaign_clock(NOW), task_login(ServiceRole.WEB, exact=True, reconnect=True):
        refused = post(
            released,
            action="group-preview",
            definition=hold.definition_id,
            group_action="assume",
        )
    assert refused.status_code == 403 and b"Confirm with Google" in refused.content
    assert RestoreDeliveryHold.objects.filter(state="unreviewed").count() == 8


def test_under_review_the_page_sends_the_administrator_to_the_review(
    auth_service, google
):
    """During a review the Restore review page owns these decisions."""
    live_campaign(auth_service)
    browser, _ = signed_in()
    with campaign_clock(NOW):
        begin()
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        response = browser.get(PAGE)
    assert response.status_code == 302 and response["Location"] == "/admin/maintenance"


def test_the_page_states_its_own_count_and_updates_it_in_place(released):
    """No stale every-page banner here: the region's count follows a settle."""
    family, hold = invitation()
    with campaign_clock(NOW), task_login(ServiceRole.WEB, exact=True, reconnect=True):
        page = released.get(PAGE)
        assert b"data-held-invitations" not in page.content
        assert b"1 Family gets no reminders" in page.content
        done = post(
            released,
            action="group-confirm",
            definition=hold.definition_id,
            group_action="assume",
            count="2",
            evidence="The provider's log shows them.",
        )
    assert b"2 held emails settled" in done.content
    assert b"gets no reminders" not in done.content
    assert b"data-held-invitations" not in done.content


def test_a_sign_in_that_lapses_before_the_write_gets_the_step_up(released, monkeypatch):
    """The page's check passed but the guard's did not: the step-up, nothing else."""
    from datetime import timedelta

    from parishkit.stewardship.accounts import held_email_views
    from parishkit.stewardship.accounts.models import PortalSession

    _, hold = invitation()
    session = PortalSession.objects.order_by("-authenticated_at").first()
    # The instant the guard sees is older than five minutes, as if the
    # sign-in lapsed after the view's own freshness check.
    monkeypatch.setattr(
        held_email_views,
        "require_fresh",
        lambda request: session.authenticated_at - timedelta(minutes=10),
    )
    with campaign_clock(NOW), task_login(ServiceRole.WEB, exact=True, reconnect=True):
        refused = post(
            released,
            action="group-confirm",
            definition=hold.definition_id,
            group_action="assume",
            count="2",
            evidence="Seen.",
        )
    assert refused.status_code == 403 and b"Confirm with Google" in refused.content
    assert RestoreDeliveryHold.objects.filter(state="unreviewed").count() == 8


def test_outgoing_mail_links_to_the_page(released):
    """Outgoing mail names the waiting held emails and links to the page."""
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        response = released.get("/admin/deliveries")
    assert b"data-held-emails-link" in response.content
    assert b"8 emails held after a restore are waiting" in response.content


def test_staff_get_no_page_and_no_banner(auth_service, google):
    """Only an Administrator sees the page or the banner."""
    from ..policy_factory import address
    from .campaign_builders import change

    campaign = live_campaign(auth_service)
    rule = address("staff@example.org", ("staff",))
    change(
        auth_service.store,
        auth_service.store.active(),
        uuid4(),
        [{"operation": "add", "section": "login_rules", **rule}],
    )
    signed_in()
    with campaign_clock(NOW):
        complete_empty_catchup(campaign, uuid4())
        begin()
        as_web(list_held_emails_for)
        as_web(
            release_review,
            request_id=uuid4(),
            expected_runtime_version=SystemConfiguration.objects.get().version,
        )
    google[0].update(email="staff@example.org", sub="synthetic-staff")
    staff, _ = signed_in()
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        page = staff.get(PAGE)
        home = staff.get("/admin/", follow=True)
    assert page.status_code == 403
    assert b"data-held-invitations" not in home.content


def test_a_reminder_superseded_by_a_later_delivered_one_is_never_resent(
    auth_service, google
):
    """Send again on R1 when R2 already reached the Family: assumed instead."""
    from parishkit.stewardship.campaigns.models import ScheduleDefinition
    from parishkit.stewardship.campaigns.restore_review import (
        held_groups,
        settle_group,
        settle_held_email,
    )

    from .test_restore_review_postgresql import administrator, restored_occurrence

    campaign = live_campaign(auth_service)
    third = FamilyCampaign.objects.get(family_duid=3)
    first_reminder, second_reminder = ScheduleDefinition.objects.filter(
        kind="reminder"
    ).order_by("current_revision__due_at")[:2]
    # The backup already recorded Family 3's second reminder as delivered.
    restored_occurrence(second_reminder, third)
    signed_in()
    with campaign_clock(NOW):
        complete_empty_catchup(campaign, uuid4())
        begin()
        as_web(list_held_emails_for)
        as_web(
            release_review,
            request_id=uuid4(),
            expected_runtime_version=SystemConfiguration.objects.get().version,
        )
    runtime = SystemConfiguration.objects.get()
    groups, _ = held_groups(runtime)
    group = next(g for g in groups if g.definition_id == first_reminder.pk)
    # Family 3's first reminder is superseded, not counted as sendable.
    assert group.superseded == 1
    current = administrator()
    third_hold = RestoreDeliveryHold.objects.get(
        definition=first_reminder, target=f"family:{third.pk}"
    )
    # One hold at a time follows the same rule: no out-of-order resend.
    with (
        campaign_clock(NOW),
        task_login(ServiceRole.WEB, exact=True),
        pytest.raises(ValueError, match="later reminder"),
    ):
        settle_held_email(
            hold_id=third_hold.pk,
            expected_version=1,
            state="resend_authorized",
            evidence="Send again.",
            actor_id=current.principal_id,
            session_id=current.pk,
            authenticated_at=current.authenticated_at,
            correlation_id=uuid4(),
        )
    with campaign_clock(NOW), task_login(ServiceRole.WEB, exact=True):
        result = settle_group(
            definition_id=first_reminder.pk,
            action="resend",
            expected_count=group.unreviewed,
            evidence="Not in the provider's log.",
            actor_id=current.principal_id,
            session_id=current.pk,
            authenticated_at=current.authenticated_at,
            correlation_id=uuid4(),
        )
    assert result.assumed_instead == 1 and result.count == group.unreviewed
    hold = RestoreDeliveryHold.objects.get(
        definition=first_reminder, target=f"family:{third.pk}"
    )
    assert hold.state == "assumed_delivered"
    assert "later reminder was already delivered" in hold.evidence
    others = RestoreDeliveryHold.objects.filter(definition=first_reminder).exclude(
        pk=hold.pk
    )
    assert set(others.values_list("state", flat=True)) == {"resend_authorized"}


@pytest.mark.parametrize("later", ["failed", "held"])
def test_a_later_reminder_that_did_not_go_out_leaves_the_earlier_sendable(
    auth_service, google, later
):
    """Only a later reminder actually delivered supersedes an earlier one."""
    from parishkit.stewardship.campaigns.models import ScheduleDefinition
    from parishkit.stewardship.campaigns.restore_review import held_groups

    from .test_restore_review_postgresql import restored_occurrence

    campaign = live_campaign(auth_service)
    third = FamilyCampaign.objects.get(family_duid=3)
    first_reminder, second_reminder = ScheduleDefinition.objects.filter(
        kind="reminder"
    ).order_by("current_revision__due_at")[:2]
    if later == "failed":
        # The second reminder failed for good before the backup: not sent.
        restored_occurrence(second_reminder, third, "failed")
    signed_in()
    with campaign_clock(NOW):
        complete_empty_catchup(campaign, uuid4())
        begin()
        as_web(list_held_emails_for)
        as_web(
            release_review,
            request_id=uuid4(),
            expected_runtime_version=SystemConfiguration.objects.get().version,
        )
    if later == "held":
        # The second reminder is itself held undecided: not known to be sent.
        assert RestoreDeliveryHold.objects.filter(
            definition=second_reminder, target=f"family:{third.pk}", state="unreviewed"
        ).exists()
    groups, _ = held_groups(SystemConfiguration.objects.get())
    group = next(g for g in groups if g.definition_id == first_reminder.pk)
    assert group.superseded == 0

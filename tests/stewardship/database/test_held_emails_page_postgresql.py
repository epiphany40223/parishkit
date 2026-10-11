"""The Held emails page through the real web stack (#757).

After release, holds nobody decided stay in force. Home links an
Administrator to the page while any is undecided; the page lists every
restore's holds of the current campaign by send and settles a whole send at
once, with the Restore review page's previews, notes, exact counts and resend
rules. The SQL guards behind each decision are proven in
test_restore_review_postgresql.py, and what the planner does with a decision
after release in test_restore_review_delivery_postgresql.py.
"""

from datetime import timedelta
from uuid import uuid4

import pytest

from parishkit.stewardship.accounts.models import PortalSession
from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.campaigns.credential_models import FamilyCampaign
from parishkit.stewardship.campaigns.models import (
    RestoreDeliveryHold,
    RestoreHoldResolution,
    ScheduleDefinition,
)
from parishkit.stewardship.campaigns.restore_review import (
    release_review,
    settle_group,
)
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.storage import StaleRecordError

from ..policy_factory import address
from .auth_builders import signed_in, unguarded
from .campaign_builders import campaign_clock, change
from .leader_builders import grant_role
from .test_background_grants_postgresql import task_login
from .test_restore_review_postgresql import (
    BACKUP,
    NOW,
    administrator,
    as_web,
    begin,
    decide_directly,
    live_campaign,
    released_with_holds,
    replica,
    restored_occurrence,
)

pytestmark = pytest.mark.django_db(transaction=True)

PAGE = "/admin/mail/held-emails/"


def post(browser, **fields):
    """One step, posted the way the page's forms post it."""
    return browser.post(
        PAGE, fields | {"csrfmiddlewaretoken": browser.cookies["pk_admin_csrf"].value}
    )


def settle(
    browser, definition, action, count, evidence="Compared with the log.", restore=None
):
    """Confirm one send's decision with a note.

    ``restore`` is the latest restore the preview was read under; by default
    the current one, as the page posts it.
    """
    if restore is None:
        restore = SystemConfiguration.objects.get().restore_id
    return post(
        browser,
        action="group-confirm",
        definition=definition,
        group_action=action,
        count=str(count),
        evidence=evidence,
        restore=str(restore),
    )


def restore_again(family):
    """A second restore holds ``family``'s invitation again, then is released.

    The hold is loaded directly, as the Restore review page's test does: the
    first restore's undecided hold keeps the email from being found again.
    Returns a browser signed in again as the Administrator.
    """
    invitation = ScheduleDefinition.objects.get(kind="initial")
    with campaign_clock(NOW + timedelta(hours=1)):
        begin(BACKUP + timedelta(hours=1))
        runtime = SystemConfiguration.objects.get()
        RestoreDeliveryHold.objects.create(
            restore_id=runtime.restore_id,
            definition=invitation,
            mode="production",
            target=f"family:{family.pk}",
            slot="once",
            backup_at=runtime.restore_backup_at,
            window_start=runtime.restore_backup_at,
            window_end=NOW + timedelta(hours=1),
            discovery="restore_inventory",
            actor_id=administrator().principal_id,
            correlation_id=uuid4(),
        )
        as_web(
            release_review,
            request_id=uuid4(),
            expected_runtime_version=SystemConfiguration.objects.get().version,
        )
    browser, _ = signed_in()
    return browser


def portal_session(browser):
    """The browser's own Admin session row (the fixture signs in twice)."""
    return PortalSession.objects.get(session_id=browser.cookies["pk_admin"].value)


def undecided(definition):
    """How many of the send's holds nobody has decided, across restores."""
    return RestoreDeliveryHold.objects.filter(
        definition=definition, state="unreviewed"
    ).count()


@pytest.fixture
def released(auth_service, google):
    """A live campaign restored and released with all 8 holds undecided.

    Returns a browser signed in as the Administrator.
    """
    released_with_holds(auth_service)
    browser, _ = signed_in()
    return browser


def test_home_links_to_held_emails_until_each_is_decided(released):
    """Home's notice counts undecided holds; deciding every send ends it."""
    browser = released
    with campaign_clock(NOW), task_login(ServiceRole.WEB, exact=True, reconnect=True):
        home = browser.get("/admin/").content.decode()
        assert "8 emails held after a restore are not decided" in home
        assert f'href="{PAGE}"' in home
        page = browser.get(PAGE)
        assert page.status_code == 200
        text = page.content.decode()
        # Families 2 and 3 have undecided invitations; only Family 3 can be
        # emailed, so only its reminders are counted as kept back.
        assert "1 Family gets no reminders until its held invitation" in text
        assert "Assume these were sent…" in text
        for definition in ScheduleDefinition.objects.filter(
            restoredeliveryhold__isnull=False
        ).distinct():
            count = undecided(definition)
            done = settle(browser, definition.pk, "assume", count)
            assert done.status_code == 200
            assert f"{count} held emails settled".encode() in done.content
        assert not RestoreDeliveryHold.objects.filter(state="unreviewed").exists()
        # Each send stays listed, decided; Home no longer shows the notice.
        after = browser.get(PAGE).content.decode()
        assert "Every held email is decided." in after
        assert "Every email of this send is decided." in after
        assert "held after a restore" not in browser.get("/admin/").content.decode()


def test_send_these_again_after_release(released):
    """A send settled as "Send these again" records one audited decision each."""
    browser = released
    invitation = ScheduleDefinition.objects.get(kind="initial")
    with campaign_clock(NOW), task_login(ServiceRole.WEB, exact=True, reconnect=True):
        preview = post(
            browser,
            action="group-preview",
            definition=invitation.pk,
            group_action="resend",
        )
        assert preview.status_code == 200
        assert b"will get a second copy" in preview.content
        assert b"ordinary Family mail" in preview.content
        done = settle(browser, invitation.pk, "resend", 2, "Not in the sent log.")
    assert done.status_code == 200 and b"2 held emails settled" in done.content
    decisions = RestoreHoldResolution.objects.filter(hold__definition=invitation)
    assert decisions.count() == 2
    assert {(d.state, d.session_id) for d in decisions} == {
        ("resend_authorized", portal_session(browser).pk)
    }


def test_a_changed_count_settles_nothing(released):
    """A count that changed since the preview is refused in place."""
    browser = released
    reminder = (
        ScheduleDefinition.objects.filter(kind="reminder")
        .order_by("current_revision__due_at")
        .first()
    )
    with campaign_clock(NOW), task_login(ServiceRole.WEB, exact=True, reconnect=True):
        stale = settle(browser, reminder.pk, "assume", 1)
    assert stale.status_code == 409
    assert b'data-restore-refused="changed"' in stale.content
    assert not RestoreHoldResolution.objects.exists()


def test_a_superseded_resend_is_refused_in_place(released):
    """After release a later reminder can reach the Family first (migration 0048)."""
    browser = released
    first, second = ScheduleDefinition.objects.filter(kind="reminder").order_by(
        "current_revision__due_at"
    )[:2]
    restored_occurrence(second, FamilyCampaign.objects.get(family_duid=3))
    with campaign_clock(NOW), task_login(ServiceRole.WEB, exact=True, reconnect=True):
        shown = browser.get(PAGE).content.decode()
        assert 'data-restore-resend-blocked="superseded"' in shown
        refused = settle(browser, first.pk, "resend", undecided(first))
    assert refused.status_code == 409
    assert b"would arrive out of order" in refused.content
    assert not RestoreHoldResolution.objects.exists()


def test_two_restores_holding_one_email_can_only_assume_it(released):
    """Holds of every restore are listed together; two on one email block a resend.

    A second restore after release holds Family 3's invitation again. Both
    holds stay undecided at its release, so the invitation's send has three
    holds but two emails: the page and Home count two. "Send these again" is
    unavailable with its reason, and assuming the send settles all three
    holds, each restore's with its own decision.
    """
    invitation = ScheduleDefinition.objects.get(kind="initial")
    browser = restore_again(FamilyCampaign.objects.get(family_duid=3))
    assert undecided(invitation) == 3
    with (
        campaign_clock(NOW + timedelta(hours=1)),
        task_login(ServiceRole.WEB, exact=True, reconnect=True),
    ):
        shown = browser.get(PAGE).content.decode()
        assert 'data-restore-resend-blocked="held_elsewhere"' in shown
        # The row counts emails; the confirm posts the hold count.
        row = shown.split(f'data-restore-group="{invitation.pk}"')[1]
        assert '<td class="numeric">1 <span class="help">(and 1 that' in row
        home = browser.get("/admin/").content.decode()
        assert "8 emails held after a restore" in home
        refused = post(
            browser,
            action="group-preview",
            definition=invitation.pk,
            group_action="resend",
        )
        assert refused.status_code == 409
        assert b"An earlier restore still holds" in refused.content
        done = settle(browser, invitation.pk, "assume", 3)
    assert done.status_code == 200 and b"2 held emails settled" in done.content
    holds = RestoreDeliveryHold.objects.filter(definition=invitation)
    assert {hold.state for hold in holds} == {"assumed_delivered"}
    assert len({hold.restore_id for hold in holds}) == 2


def test_a_restore_since_the_preview_settles_nothing_at_the_same_count(released):
    """The count is not the only staleness check: the latest restore is too.

    Between the preview and the confirm, another Administrator assumes
    Family 2's invitation sent and a new restore holds Family 3's again, so
    the send still has two undecided holds. The confirm, carrying the restore
    the preview was read under, settles nothing; one read under the new
    restore is admitted.
    """
    invitation = ScheduleDefinition.objects.get(kind="initial")
    previewed = SystemConfiguration.objects.get().restore_id
    second = FamilyCampaign.objects.get(family_duid=2)
    decide_directly(
        RestoreDeliveryHold.objects.get(
            definition=invitation, target=f"family:{second.pk}"
        )
    )
    browser = restore_again(FamilyCampaign.objects.get(family_duid=3))
    assert undecided(invitation) == 2
    with (
        campaign_clock(NOW + timedelta(hours=1)),
        task_login(ServiceRole.WEB, exact=True, reconnect=True),
    ):
        refused = settle(browser, invitation.pk, "assume", 2, restore=previewed)
        assert refused.status_code == 409
        assert b'data-restore-refused="changed"' in refused.content
        assert undecided(invitation) == 2
        done = settle(browser, invitation.pk, "assume", 2)
    assert done.status_code == 200
    assert undecided(invitation) == 0


def test_a_review_begun_since_the_preview_settles_nothing(released):
    """During a review the page sends the Administrator to the Restore review.

    A decision whose preview came from before the review settles nothing:
    that review's own page decides its holds.
    """
    browser = released
    invitation = ScheduleDefinition.objects.get(kind="initial")
    with campaign_clock(NOW + timedelta(hours=1)):
        begin(BACKUP + timedelta(hours=1))
        with pytest.raises(StaleRecordError):
            as_web(
                settle_group,
                definition_id=invitation.pk,
                action="assume",
                expected_count=2,
                evidence="Compared with the log.",
                after_release=True,
            )
    with campaign_clock(NOW), task_login(ServiceRole.WEB, exact=True, reconnect=True):
        response = browser.get(PAGE)
    assert response.status_code == 302
    assert response["Location"] == "/admin/maintenance"
    assert not RestoreHoldResolution.objects.exists()


def test_a_stale_sign_in_gets_the_step_up_in_place(released):
    """A decision needs a sign-in within five minutes, before the note."""
    browser = released
    session = portal_session(browser)
    with unguarded():
        PortalSession.objects.filter(pk=session.pk).update(
            authenticated_at=session.authenticated_at - timedelta(minutes=10)
        )
    invitation = ScheduleDefinition.objects.get(kind="initial")
    with campaign_clock(NOW), task_login(ServiceRole.WEB, exact=True, reconnect=True):
        refused = post(
            browser,
            action="group-preview",
            definition=invitation.pk,
            group_action="assume",
        )
    assert refused.status_code == 403
    assert b"Confirm with Google" in refused.content
    assert b'data-restore-preview="assume"' not in refused.content


def test_a_malformed_step_is_refused_in_place(released):
    """Unknown fields, or the review's own steps, change nothing."""
    with campaign_clock(NOW), task_login(ServiceRole.WEB, exact=True, reconnect=True):
        extra = post(released, action="cancel", extra=str(uuid4()))
        release = post(released, action="release-preview")
    assert extra.status_code == 400 and release.status_code == 400
    assert not RestoreHoldResolution.objects.exists()


def test_staff_are_refused(released, auth_service, google):
    """Only an Administrator may open the page; Staff see no Home notice."""
    rule = address("staff@example.org", ("staff",))
    change(
        auth_service.store,
        auth_service.store.active(),
        uuid4(),
        [{"operation": "add", "section": "login_rules", **rule}],
    )
    google[0].update(email="staff@example.org", sub="synthetic-staff")
    staff, _ = signed_in()
    invitation = ScheduleDefinition.objects.get(kind="initial")
    with campaign_clock(NOW), task_login(ServiceRole.WEB, exact=True, reconnect=True):
        assert staff.get(PAGE).status_code == 403
        assert b"held after a restore" not in staff.get("/admin/").content
        assert settle(staff, invitation.pk, "assume", 2).status_code == 403
    assert not RestoreHoldResolution.objects.exists()


def test_ministry_leaders_are_refused(auth_service, google):
    """A Ministry leader can neither open the page nor post a step to it."""
    grant_role(auth_service.store, "leader@example.org", "ministry_leader")
    google[0].update(email="leader@example.org", sub="synthetic-leader")
    leader, _ = signed_in()
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        assert leader.get(PAGE).status_code == 403
        refused = post(
            leader, action="group-preview", definition=uuid4(), group_action="assume"
        )
    assert refused.status_code == 403


def test_another_campaigns_holds_are_left_out(released):
    """Only the current campaign's holds can be decided, so only they are shown.

    The held campaign is archived and another becomes current (a copy of its
    row, loaded as offline input like the other restored state here): the
    page and Home show none of the archived campaign's 8 undecided holds.
    """
    runtime = SystemConfiguration.objects.get()
    other = uuid4()
    replica(
        (
            "CREATE TEMP TABLE other_campaign AS SELECT * FROM stewardship_campaign "
            "WHERE id=%s",
            [runtime.current_campaign_id],
        ),
        ("UPDATE other_campaign SET id=%s", [other]),
        # One campaign at most is live: the held one is archived.
        (
            "UPDATE stewardship_campaign SET state='archived' WHERE id=%s",
            [runtime.current_campaign_id],
        ),
        ("INSERT INTO stewardship_campaign SELECT * FROM other_campaign", []),
        (
            "UPDATE stewardship_system_configuration SET current_campaign_id=%s",
            [other],
        ),
        ("DROP TABLE other_campaign", []),
    )
    with campaign_clock(NOW), task_login(ServiceRole.WEB, exact=True, reconnect=True):
        page = released.get(PAGE)
        home = released.get("/admin/")
    assert page.status_code == 200 and b"No held emails." in page.content
    assert home.status_code == 200
    assert b"held after a restore" not in home.content
    assert RestoreDeliveryHold.objects.filter(state="unreviewed").count() == 8


def test_without_holds_the_page_says_so(auth_service, google):
    """No restore held anything for this campaign: a short page, no notice."""
    live_campaign(auth_service)
    browser, _ = signed_in()
    with campaign_clock(NOW), task_login(ServiceRole.WEB, exact=True, reconnect=True):
        page = browser.get(PAGE)
        home = browser.get("/admin/")
    assert page.status_code == 200 and b"No held emails." in page.content
    assert b"<table" not in page.content
    assert b"held after a restore" not in home.content

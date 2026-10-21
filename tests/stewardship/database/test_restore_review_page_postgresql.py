"""The Restore review page through the real web stack (#537).

During a review the access gate sends an Administrator to the page and shows
Staff a plain notice; each step posts to the page and is answered with it,
and release ends on Home. The SQL guards behind each step are proven in
test_restore_review_postgresql.py.
"""

import re
from datetime import timedelta
from uuid import uuid4

import pytest
from django.db import connection, transaction

from parishkit.stewardship.accounts.models import PortalSession
from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.campaigns.credential_models import FamilyCampaign
from parishkit.stewardship.campaigns.models import (
    RestoreDeliveryHold,
    RestoreHoldResolution,
    ScheduleDefinition,
)
from parishkit.stewardship.campaigns.runtime_models import CampaignWorkGate
from parishkit.stewardship.deployment import ServiceRole

from ..policy_factory import address
from .auth_builders import signed_in, unguarded
from .campaign_builders import campaign_clock, change
from .test_background_grants_postgresql import task_login
from .test_restore_review_postgresql import (
    BACKUP,
    NOW,
    administrator,
    begin,
    live_campaign,
    restored_occurrence,
)

pytestmark = pytest.mark.django_db(transaction=True)

PAGE = "/admin/maintenance"
# "Release the site" drawn unavailable (the {% button %} tag's disabled).
RELEASE_UNAVAILABLE = re.compile(r"<button[^>]*\bdisabled\b[^>]*>\s*Release the site")


def post(browser, **fields):
    """One step, posted the way the page's forms post it."""
    return browser.post(
        PAGE, fields | {"csrfmiddlewaretoken": browser.cookies["pk_admin_csrf"].value}
    )


@pytest.fixture
def under_review(auth_service, google):
    """A live campaign restored from a backup, and a signed-in Administrator."""
    live_campaign(auth_service)
    browser, _ = signed_in()
    with campaign_clock(NOW):
        begin()
    return browser


def test_the_gate_sends_the_administrator_to_the_review(under_review):
    """Home redirects to the review; the page says what was restored."""
    browser = under_review
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        response = browser.get("/admin/")
        assert response.status_code == 302 and response["Location"] == PAGE
        page = browser.get(PAGE)
    assert page.status_code == 200
    text = page.content.decode()
    assert "Restore review" in text and "The backup was taken at" in text
    assert "Find emails that may have gone out" in text


def test_list_settle_and_release_end_on_home(under_review):
    """Find, assume one send, refuse a stale count, then release to Home."""
    browser = under_review
    with campaign_clock(NOW), task_login(ServiceRole.WEB, exact=True, reconnect=True):
        # Nothing found yet: release is shown unavailable, with its reason.
        before = browser.get(PAGE)
        assert b"8 emails still need a hold" in before.content
        assert RELEASE_UNAVAILABLE.search(before.content.decode())
        # A posted release preview is refused the same way, in place.
        early = post(browser, action="release-preview")
        assert early.status_code == 409 and b"Confirm: release" not in early.content
        found = post(browser, action="list")
        assert found.status_code == 200 and b"Found 8 more emails" in found.content
        reminder = (
            RestoreDeliveryHold.objects.filter(definition__kind="reminder")
            .order_by("definition__current_revision__due_at")
            .first()
            .definition_id
        )
        preview = post(
            browser, action="group-preview", definition=reminder, group_action="assume"
        )
        assert b"Confirm: assume sent" in preview.content
        done = post(
            browser,
            action="group-confirm",
            definition=reminder,
            group_action="assume",
            count="3",
            evidence="The provider's log shows both.",
        )
        assert b"3 held emails settled" in done.content
        assert (
            RestoreDeliveryHold.objects.filter(state="assumed_delivered").count() == 3
        )
        # A stale count (another Administrator, or more found) changes nothing.
        stale = post(
            browser,
            action="group-confirm",
            definition=reminder,
            group_action="resend",
            count="1",
            evidence="Send again.",
        )
        assert stale.status_code == 409
        assert not RestoreDeliveryHold.objects.filter(state="resend_authorized")
        assert not RELEASE_UNAVAILABLE.search(done.content.decode())
        release = post(browser, action="release-preview")
        assert b"Confirm: release the site" in release.content
        # Families 2 and 3 have undecided invitations, but only Family 3 can
        # be emailed: Family 2's hold is inert, so it is not counted.
        assert b"1 Family's invitation is not decided" in release.content
        key = release.content.decode().split('name="key" value="')[1].split('"')[0]
        version = SystemConfiguration.objects.get().version
        released = post(browser, action="release-confirm", key=key, version=version)
    assert released.status_code == 302 and released["Location"] == "/admin/"
    assert not SystemConfiguration.objects.get().restore_review_required
    # Undecided emails stay held.
    assert RestoreDeliveryHold.objects.filter(state="unreviewed").count() == 5


def test_an_old_sign_in_finds_held_emails(under_review):
    """A sign-in older than five minutes works the review (#1547).

    Release stays unavailable for its own reason only, and finding the
    emails that may have gone out proceeds.
    """
    browser = under_review
    session = PortalSession.objects.order_by("-authenticated_at").first()
    with unguarded():
        PortalSession.objects.filter(pk=session.pk).update(
            authenticated_at=session.authenticated_at - timedelta(minutes=10)
        )
    with campaign_clock(NOW), task_login(ServiceRole.WEB, exact=True, reconnect=True):
        shown = browser.get(PAGE).content.decode()
        found = post(browser, action="list")
    assert RELEASE_UNAVAILABLE.search(shown)
    assert "restore-fresh-reason" not in shown
    assert found.status_code == 200 and b"Found 8 more emails" in found.content
    assert SystemConfiguration.objects.get().restore_review_required


def test_staff_see_a_plain_notice(auth_service, google):
    """Anyone but an Administrator gets the notice, never the review."""
    live_campaign(auth_service)
    rule = address("staff@example.org", ("staff",))
    change(
        auth_service.store,
        auth_service.store.active(),
        uuid4(),
        [{"operation": "add", "section": "login_rules", **rule}],
    )
    with campaign_clock(NOW):
        begin()
    google[0].update(email="staff@example.org", sub="synthetic-staff")
    staff, _ = signed_in()
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        response = staff.get(PAGE)
    assert response.status_code == 503
    assert b"The site is being restored" in response.content
    assert b"Release the site" not in response.content


def test_a_malformed_step_is_refused_in_place(under_review):
    """Unknown fields or actions change nothing."""
    with campaign_clock(NOW), task_login(ServiceRole.WEB, exact=True, reconnect=True):
        refused = post(under_review, action="list", extra=str(uuid4()))
    assert refused.status_code == 400
    assert not RestoreDeliveryHold.objects.exists()


def test_a_resend_a_later_reminder_superseded_is_refused_in_place(under_review):
    """ "Send these again" after a later reminder reached a Family changes nothing.

    The send's resend would arrive out of order (#757, migration 0048), so the
    page says to assume it sent instead, before the decision guard would roll
    the whole send back.
    """
    browser = under_review
    first, second = ScheduleDefinition.objects.filter(kind="reminder").order_by(
        "current_revision__due_at"
    )[:2]
    with campaign_clock(NOW), task_login(ServiceRole.WEB, exact=True, reconnect=True):
        post(browser, action="list")
    count = RestoreDeliveryHold.objects.filter(
        definition=first, state="unreviewed"
    ).count()
    assert count
    # The later reminder reached Family 3 (restored input).
    restored_occurrence(second, FamilyCampaign.objects.get(family_duid=3))
    with campaign_clock(NOW), task_login(ServiceRole.WEB, exact=True, reconnect=True):
        # The page shows the choice unavailable, with its reason.
        shown = browser.get(PAGE).content.decode()
        assert 'data-restore-resend-blocked="superseded"' in shown
        refused = post(
            browser,
            action="group-confirm",
            definition=first.pk,
            group_action="resend",
            count=str(count),
            evidence="Not in the provider's log.",
        )
        assert refused.status_code == 409
        assert b"would arrive out of order" in refused.content
        assert not RestoreDeliveryHold.objects.exclude(state="unreviewed").exists()
        # Assuming the same send sent is still admitted.
        assumed = post(
            browser,
            action="group-confirm",
            definition=first.pk,
            group_action="assume",
            count=str(count),
            evidence="The provider's log shows them.",
        )
    assert assumed.status_code == 200
    assert (
        RestoreDeliveryHold.objects.filter(
            definition=first, state="assumed_delivered"
        ).count()
        == count
    )


def resend(browser, definition, count):
    """Confirm "Send these again" for one send, with a note."""
    return post(
        browser,
        action="group-confirm",
        definition=definition,
        group_action="resend",
        count=str(count),
        evidence="Not in the provider's log.",
    )


def test_send_these_again_authorizes_each_held_email(under_review):
    """A reachable send settled as "Send these again" records one decision each."""
    browser = under_review
    with campaign_clock(NOW), task_login(ServiceRole.WEB, exact=True, reconnect=True):
        post(browser, action="list")
        invitation = ScheduleDefinition.objects.get(kind="initial")
        count = RestoreDeliveryHold.objects.filter(
            definition=invitation, state="unreviewed"
        ).count()
        preview = post(
            browser,
            action="group-preview",
            definition=invitation.pk,
            group_action="resend",
        )
        assert b"will get a second copy" in preview.content
        done = resend(browser, invitation.pk, count)
    assert done.status_code == 200 and b"settled" in done.content
    holds = RestoreDeliveryHold.objects.filter(definition=invitation)
    assert {hold.state for hold in holds} == {"resend_authorized"}
    assert (
        RestoreHoldResolution.objects.filter(
            hold__definition=invitation, state="resend_authorized"
        ).count()
        == count
    )


def test_a_resend_another_restore_holds_is_unavailable_and_refused(under_review):
    """Another restore's undecided hold on the same email blocks "send again".

    The decision guard refuses it with the shared SQLSTATE, which the page
    used to answer as "changed" forever; it now names the reason (#537
    review), shows the choice unavailable, and changes nothing.
    """
    browser = under_review
    with campaign_clock(NOW), task_login(ServiceRole.WEB, exact=True, reconnect=True):
        post(browser, action="list")
    earlier = RestoreDeliveryHold.objects.filter(definition__kind="initial").first()
    with campaign_clock(NOW):
        # A second restore: the first one's undecided holds keep every email
        # back, so this one's own hold on the same email is loaded directly.
        begin(BACKUP - timedelta(hours=1))
        runtime = SystemConfiguration.objects.get()
        RestoreDeliveryHold.objects.create(
            restore_id=runtime.restore_id,
            definition_id=earlier.definition_id,
            mode="production",
            target=earlier.target,
            slot="once",
            backup_at=runtime.restore_backup_at,
            window_start=runtime.restore_backup_at,
            window_end=NOW,
            discovery="restore_inventory",
            actor_id=administrator().principal_id,
            correlation_id=uuid4(),
        )
    with campaign_clock(NOW), task_login(ServiceRole.WEB, exact=True, reconnect=True):
        shown = browser.get(PAGE).content.decode()
        assert 'data-restore-resend-blocked="held_elsewhere"' in shown
        preview = post(
            browser,
            action="group-preview",
            definition=earlier.definition_id,
            group_action="resend",
        )
        assert preview.status_code == 409
        assert b"An earlier restore still holds" in preview.content
        refused = resend(browser, earlier.definition_id, 1)
    assert refused.status_code == 409
    assert b"An earlier restore still holds" in refused.content
    assert not RestoreHoldResolution.objects.exists()


def test_release_refusals_name_their_reason(under_review):
    """Emails still needing a hold, or work still running, are named in place.

    Every release refusal of the SQL guard shares SQLSTATE 23514, so the page
    classifies these by its own checks under the same locks, never by the
    guard's wording.
    """
    browser = under_review
    version = SystemConfiguration.objects.get().version
    with campaign_clock(NOW), task_login(ServiceRole.WEB, exact=True, reconnect=True):
        # Nothing found yet: the confirm (posted directly) says so.
        early = post(
            browser, action="release-confirm", key=str(uuid4()), version=version
        )
        assert early.status_code == 409
        assert b"8 emails still need a hold" in early.content
        assert b'data-restore-refused="needs_holds"' in early.content
        post(browser, action="list")
    # A purge reservation still running (loaded as offline input).
    campaign = SystemConfiguration.objects.get().current_campaign_id
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute("SET LOCAL session_replication_role = replica")
        CampaignWorkGate.objects.create(
            campaign_id=campaign,
            request_id=uuid4(),
            state="running",
            correlation_id=uuid4(),
        )
        cursor.execute("SET LOCAL session_replication_role = origin")
    with campaign_clock(NOW), task_login(ServiceRole.WEB, exact=True, reconnect=True):
        # Found when the page opens: Release is unavailable with why (#1331).
        shown = browser.get(PAGE).content.decode()
        assert RELEASE_UNAVAILABLE.search(shown)
        assert "a campaign change or a purge is still running" in shown
        # The server still refuses a posted preview.
        preview = post(browser, action="release-preview")
        assert preview.status_code == 409
        assert b'data-restore-refused="blocked"' in preview.content
        blocked = post(
            browser,
            action="release-confirm",
            key=str(uuid4()),
            version=SystemConfiguration.objects.get().version,
        )
    assert blocked.status_code == 409
    assert b'data-restore-refused="blocked"' in blocked.content
    assert SystemConfiguration.objects.get().restore_review_required

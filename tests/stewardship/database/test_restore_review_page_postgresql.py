"""The Restore review page through the real web stack (#537).

During a review the access gate sends an Administrator to the page and shows
Staff a plain notice; each step posts to the page and is answered with it,
and release ends on Home. The SQL guards behind each step are proven in
test_restore_review_postgresql.py.
"""

from datetime import timedelta
from uuid import uuid4

import pytest

from parishkit.stewardship.accounts.models import PortalSession
from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.campaigns.models import RestoreDeliveryHold
from parishkit.stewardship.deployment import ServiceRole

from ..policy_factory import address
from .auth_builders import signed_in, unguarded
from .campaign_builders import campaign_clock, change
from .test_background_grants_postgresql import task_login
from .test_restore_review_postgresql import NOW, begin, live_campaign

pytestmark = pytest.mark.django_db(transaction=True)

PAGE = "/admin/maintenance"


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
    """Find, assume one send, send another again, then release to Home."""
    browser = under_review
    with campaign_clock(NOW), task_login(ServiceRole.WEB, exact=True, reconnect=True):
        # Nothing found yet: release is not offered.
        before = browser.get(PAGE)
        assert b"8 emails still need a hold" in before.content
        assert "Release the site…".encode() not in before.content
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


def test_a_stale_sign_in_gets_the_step_up_in_place(under_review):
    """Settling and releasing need a sign-in within five minutes."""
    browser = under_review
    session = PortalSession.objects.order_by("-authenticated_at").first()
    with unguarded():
        PortalSession.objects.filter(pk=session.pk).update(
            authenticated_at=session.authenticated_at - timedelta(minutes=10)
        )
    with campaign_clock(NOW), task_login(ServiceRole.WEB, exact=True, reconnect=True):
        refused = post(browser, action="release-preview")
    assert refused.status_code == 403
    assert b"Confirm with Google" in refused.content
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

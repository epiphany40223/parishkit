"""The Administrator's side of verified-address matches (#2343, slice 3).

A Google account whose sign-in address is no user's signs in as the user
whose address Google verified for it (slice 2). Here an Administrator sees
that match and ends it: Edit user's line and End match (route
``user_match_end``), ``pk-admin users end-match`` and ``users list``'s
``address_match``, all on the real restricted web login, with the match
guard of 0200 deciding the end. An automation session approved by a matched
Administrator never outlives the match's 30 days.
"""

from datetime import timedelta

import pytest

from parishkit.stewardship.accounts.automation_models import AutomationSession
from parishkit.stewardship.accounts.policy_models import PortalUser

from ..policy_factory import address
from .auth_builders import auth_runtime, signed_in
from .automation_builders import post, start
from .test_address_signin_postgresql import (  # noqa: F401
    PERSONAL,
    SUBJECT,
    check,
    lapse,
    people,
    standing_match,
    web,
)
from .user_builders import add_rules, convert

pytestmark = pytest.mark.django_db(transaction=True)

ADMIN = "admin@example.org"
# An Administrator who is not the first one, so its address can be matched.
SECOND = "second@example.org"
STAFF = "staff@example.org"
APPROVAL = "/admin/users/automation/approval/"


@pytest.fixture
def service(tmp_path, settings, real_limiter):
    """Two Administrators and a Staff user, as users, with the setting on."""
    runtime = auth_runtime(
        tmp_path,
        settings,
        real_limiter,
        [address(), address(STAFF, ("staff",))],
    )
    # Added after the root configuration, so it is no first-Administrator
    # address, which is never matched.
    add_rules(runtime.store, address(SECOND))
    convert(runtime.store)
    settings.STEWARDSHIP_ALTERNATE_ADDRESS_SIGN_IN = True
    return runtime


def matched(people, email):  # noqa: F811
    """The PERSONAL account checks its addresses and matches ``email``.

    Returns its signed-in browser and the match.
    """
    people.addresses = [(email, True, "ACCOUNT")]
    people.claims.update(sub=SUBJECT, email=PERSONAL)
    with web():
        client, response = check()
    assert response.status_code == 302
    return client, standing_match()


def administrator(people, email=ADMIN):  # noqa: F811
    """A browser signed in with the Administrator's own Google account."""
    people.claims.update(sub=f"{email}-subject", email=email)
    with web():
        browser, response = signed_in()
    assert response.status_code == 302
    return browser


def test_a_matched_administrator_gets_a_session_within_the_30_days(
    service,
    people,  # noqa: F811
):
    """Approval caps the session at the match's confirmation plus 30 days,
    and the review says that end before Approve."""
    matched_browser, made = matched(people, SECOND)
    lapse(made, days=25)
    made.refresh_from_db()
    limit = made.confirmed_at + timedelta(days=30)
    _, code = start(service, email=SECOND, days=30)
    with web():
        review = post(matched_browser, APPROVAL, action="lookup", code=code)
    body = review.content.decode()
    assert "which Google confirms every 30 days, so this session ends by" in body
    assert 'aria-describedby="automation-days-limit"' in body
    with web():
        approved = post(
            matched_browser,
            APPROVAL,
            action="approve",
            code=code,
            scope="full",
            days="30",
        )
    assert approved.status_code == 200 and b"Session approved" in approved.content
    assert AutomationSession.objects.get().expires_at == limit
    # A lifetime that ends before the limit (3 days of the 5 left) is not
    # shortened, so the review says no limit.
    _, code = start(service, email=SECOND, days=3)
    with web():
        review = post(matched_browser, APPROVAL, action="lookup", code=code)
    assert review.status_code == 200 and b'id="automation-days"' in review.content
    assert b"automation-days-limit" not in review.content
    # An Administrator who signs in with their own address gets what they ask.
    browser = administrator(people)
    _, code = start(service, days=7)
    with web():
        review = post(browser, APPROVAL, action="lookup", code=code)
        assert b"automation-days-limit" not in review.content
        post(browser, APPROVAL, action="approve", code=code, scope="full", days="7")
    own = AutomationSession.objects.get(
        principal_id=PortalUser.objects.get(email=ADMIN).pk
    )
    assert abs(own.expires_at - own.created_at - timedelta(days=7)) < timedelta(
        minutes=1
    )

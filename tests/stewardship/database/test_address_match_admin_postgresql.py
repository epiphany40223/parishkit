"""The Administrator's side of verified-address matches (#2343, slice 3).

A Google account whose sign-in address is no user's signs in as the user
whose address Google verified for it (slice 2). Here an Administrator sees
that match and ends it: Edit user's line and End match (route
``user_match_end``), ``pk-admin users end-match`` and ``users list``'s
``address_match``, all on the real restricted web login, with the match
guard of 0200 deciding the end. An automation session approved by a matched
Administrator never outlives the match's 30 days.
"""

import re
from datetime import timedelta
from html import unescape

import pytest

from parishkit.stewardship.accounts.automation_models import AutomationSession
from parishkit.stewardship.accounts.models import PortalSession
from parishkit.stewardship.accounts.policy_models import AddressMatch, PortalUser
from parishkit.stewardship.accounts.user_reads import policy_identities, user_list
from parishkit.stewardship.audit.models import AuditEvent

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
from .test_admin_status_cli_postgresql import one, runner
from .user_builders import add_rules, convert, user_id

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


def edit_page(service, browser, email):
    """Edit user of ``email``, as the browser sees it."""
    with web():
        response = browser.get(f"/admin/users/{user_id(service.store, email)}/edit/")
    assert response.status_code == 200, response.content
    return response.content.decode()


def end_match(service, browser, email, match_id):
    """Confirm End match for ``email``'s user, as the dialog posts it."""
    with web():
        return post(
            browser,
            f"/admin/users/{user_id(service.store, email)}/match/end/",
            match_id=str(match_id),
        )


def test_edit_user_shows_the_match_and_ends_it_in_place(service, people):  # noqa: F811
    """The line, End match, its dialog's answer and everything it ends."""
    matched_browser, made = matched(people, STAFF)
    browser = administrator(people)
    body = edit_page(service, browser, STAFF)
    assert "Google account matched through this address" in body
    assert f"<strong>{PERSONAL}</strong> signs in through this address since" in body
    assert "Google last confirmed it on" in body
    button = re.search(r"<button[^>]*data-confirm-open=\"user-match-end\"[^>]*>", body)
    assert button is not None
    assert 'class="button-danger"' in button.group(0)
    assert f'value="{made.pk}"' in button.group(0)
    assert f'data-confirm-name="{PERSONAL}"' in button.group(0)
    assert "End the match for {name}?" in body
    # The matched account is among the address's Sign-in activity accounts.
    portal = PortalUser.objects.get(sign_in_email=PERSONAL)
    assert f'name="actor" value="{portal.pk}"' in body
    answer = end_match(service, browser, STAFF, made.pk)
    assert answer.status_code == 200
    text = unescape(answer.content.decode())
    assert f"Ended the match for {PERSONAL}." in text
    assert 'id="user-match-status"' in text
    assert "data-confirm-announce" not in text
    assert 'data-confirm-open="user-match-end"' not in text
    assert answer["Cache-Control"] == "no-store"
    made.refresh_from_db()
    session = PortalSession.objects.filter(
        principal_id=PortalUser.objects.get(email=ADMIN).pk, revoked_at__isnull=True
    ).get()
    assert (made.end_reason, made.ended_by_id, made.ended_session_id) == (
        "ended_by_administrator",
        PortalUser.objects.get(email=ADMIN).pk,
        session.pk,
    )
    assert made.actor_id is None
    portal.refresh_from_db()
    assert portal.email == PERSONAL
    ended = AuditEvent.objects.get(event_type="admin_address_match_ended")
    assert ended.actor_id == PortalUser.objects.get(email=ADMIN).pk
    # The matched account's own session ended with the match.
    assert not PortalSession.objects.filter(
        principal_id=portal.pk, revoked_at__isnull=True
    ).exists()
    with web():
        assert matched_browser.get("/admin/").status_code != 200
    # A repeat changes nothing and says so.
    again = end_match(service, browser, STAFF, made.pk)
    assert again.status_code == 200
    text = unescape(again.content.decode())
    assert "This match had already ended; the page is up to date." in text
    assert "data-confirm-announce" in text
    assert (
        AuditEvent.objects.filter(event_type="admin_address_match_ended").count() == 1
    )
    # The line is gone once no match stands.
    assert "Google account matched through this address" not in edit_page(
        service, browser, STAFF
    )


def test_end_match_of_an_ended_match_leaves_a_newer_one(service, people):  # noqa: F811
    """A page loaded before the match ended and the account matched again
    ends nothing: its answer draws the newer match's line and, after it,
    the status line that it had already ended (#2343, Low 1)."""
    _, old = matched(people, STAFF)
    browser = administrator(people)
    assert end_match(service, browser, STAFF, old.pk).status_code == 200
    _, newer = matched(people, STAFF)
    assert newer.pk != old.pk
    answer = end_match(service, browser, STAFF, old.pk)
    assert answer.status_code == 200
    text = unescape(answer.content.decode())
    region = re.search(r'<div id="user-match".*?</div>', text, re.S).group(0)
    # The newer match stands, with its own End match.
    assert f'value="{newer.pk}"' in region
    assert 'data-confirm-open="user-match-end"' in region
    status = re.search(r'<p class="notice" id="user-match-status"[^>]*>([^<]*)', region)
    assert status.group(1) == "This match had already ended; the page is up to date."
    assert region.index(f'value="{newer.pk}"') < status.start()
    assert re.search(
        r"<p hidden data-confirm-announce>This match had already ended", region
    )
    assert standing_match().pk == newer.pk
    assert (
        AuditEvent.objects.filter(event_type="admin_address_match_ended").count() == 1
    )


def test_a_match_of_another_user_is_not_found(service, people):  # noqa: F811
    """End match names the user and the match; a mismatch changes nothing."""
    _, made = matched(people, STAFF)
    browser = administrator(people)
    answer = end_match(service, browser, SECOND, made.pk)
    assert answer.status_code == 404
    assert standing_match() is not None
    with web():
        malformed = post(
            browser,
            f"/admin/users/{user_id(service.store, STAFF)}/match/end/",
            match_id="not-a-uuid",
        )
    assert malformed.status_code == 400
    assert standing_match() is not None


def test_the_own_match_is_another_administrators_to_end(service, people):  # noqa: F811
    """A matched Administrator's End match is unavailable, and refused."""
    matched_browser, made = matched(people, SECOND)
    body = edit_page(service, matched_browser, SECOND)
    assert "Google account matched through this address" in body
    assert 'data-confirm-open="user-match-end"' not in body
    assert re.search(
        r'<button[^>]*disabled[^>]*aria-describedby="user-match-own"', body
    )
    assert (
        "You can&#x27;t end the match you are signed in through; another "
        "Administrator does that." in body
    )
    answer = end_match(service, matched_browser, SECOND, made.pk)
    assert answer.status_code == 403
    assert standing_match() is not None
    # Another Administrator can.
    browser = administrator(people)
    assert end_match(service, browser, SECOND, made.pk).status_code == 200
    assert standing_match() is None


def test_users_list_names_the_standing_match(service, people):  # noqa: F811
    """The matched account is the user's identity; its row names the match."""
    _, made = matched(people, STAFF)
    records = service.store.active().document()["sections"]["login_rules"]
    rows = {row["email"]: row for row in user_list(records, policy_identities(records))}
    portal = PortalUser.objects.get(sign_in_email=PERSONAL)
    assert rows[STAFF]["address_match"] == {
        "match_id": str(made.pk),
        "id": str(portal.pk),
        "sign_in_email": PERSONAL,
        "matched_at": made.matched_at,
        "confirmed_at": made.confirmed_at,
    }
    assert rows[STAFF]["last_login"] is not None
    assert {"id": str(portal.pk), "disabled": False} in rows[STAFF]["identities"]
    assert rows[ADMIN]["address_match"] is None


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


def test_users_end_match_is_the_pages_end_match(service, people, monkeypatch):  # noqa: F811
    """The command checks before its prompt, ends the match and records its
    own event beside the guard's; IDs only, never an address."""
    from .automation_builders import paired

    _, made = matched(people, STAFF)
    people.claims.update(sub="admin-subject", email=ADMIN)
    _, secret, _ = paired(service)
    admin = runner(service, monkeypatch)
    user = user_id(service.store, STAFF)
    # The wrong user's match is not available; nothing is asked.
    code, document = one(
        admin,
        "users",
        "end-match",
        "--user",
        user_id(service.store, SECOND),
        "--match",
        str(made.pk),
        "--yes",
        secret=secret,
    )
    assert (code, document["error"]["code"]) == (1, "not_available"), document
    # Without --yes and no answer, the prompt refuses and nothing changes.
    code, document = one(
        admin,
        "users",
        "end-match",
        "--user",
        user,
        "--match",
        str(made.pk),
        secret=secret,
    )
    assert code == 4 and standing_match() is not None, document
    code, document = one(
        admin,
        "users",
        "end-match",
        "--user",
        user,
        "--match",
        str(made.pk),
        "--yes",
        secret=secret,
    )
    assert code == 0 and document["ok"], document
    assert document["result"] == {
        "user_id": user,
        "match_id": str(made.pk),
        "ended": True,
    }
    assert PERSONAL not in str(document) and STAFF not in str(document)
    made.refresh_from_db()
    assert made.end_reason == "ended_by_administrator"
    assert (
        AuditEvent.objects.filter(event_type="admin_cmd_users_end_match").count() == 1
    )
    assert (
        AuditEvent.objects.filter(event_type="admin_address_match_ended").count() == 1
    )
    # An ended match is stale: an old users list never ends a newer one.
    code, document = one(
        admin,
        "users",
        "end-match",
        "--user",
        user,
        "--match",
        str(made.pk),
        "--yes",
        secret=secret,
    )
    assert (code, document["error"]["code"]) == (1, "stale_version"), document


def test_users_end_match_refuses_the_sessions_own_match(service, people, monkeypatch):  # noqa: F811
    """The match the command session signs in through is invalid, with the
    closed reason match_own_account, before the prompt."""
    from .automation_builders import approve_now

    _, made = matched(people, SECOND)
    secret, code = start(service, email=SECOND)
    approve_now(service, code, email=SECOND)
    admin = runner(service, monkeypatch)
    code, document = one(
        admin,
        "users",
        "end-match",
        "--user",
        user_id(service.store, SECOND),
        "--match",
        str(made.pk),
        "--yes",
        secret=secret,
    )
    assert code == 1, document
    assert document["error"]["code"] == "invalid"
    assert document["error"]["reason"] == "match_own_account"
    assert AddressMatch.objects.get(pk=made.pk).ended_at is None

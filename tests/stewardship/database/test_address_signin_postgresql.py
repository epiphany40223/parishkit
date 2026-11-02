"""Sign-in through another verified address, end to end (#2343, slice 2).

The whole two-step Google sign-in runs over HTTP against the fake Google of
``auth_builders.google`` (signed ID tokens, no real Google), extended here
with a fake People API and token revocation. Requests run under the real
restricted web login, so the match guard (frozen migration 0200) decides
and audits every match write as it does in Production. The admin-portal
specification's "Sign-in through another verified address",
"Verified-address outcomes" and "Verified-address matches" are the rules.
"""

import logging
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from html import unescape
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import pytest
import requests
from django.db import connection, connections, transaction
from django.db.models import F
from django.test import Client

from parishkit.stewardship.accounts import address_matching, authentication
from parishkit.stewardship.accounts.authentication import SignedGoogleAdapter
from parishkit.stewardship.accounts.policy_models import (
    AddressMatch,
    PolicySecurityEvent,
    PortalUser,
)
from parishkit.stewardship.audit.models import AuditContext, AuditEvent
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.runtime_grants import login_name

from ..policy_factory import address
from .auth_builders import auth_runtime, signed_in
from .campaign_builders import change
from .role_grants import seed_user
from .test_background_grants_postgresql import task_login

pytestmark = pytest.mark.django_db(transaction=True)

# The account that signs in with an address that is no user's.
PERSONAL = "person@personal.example"
SUBJECT = "personal-google-subject"
# The root configuration's Administrator: a first-Administrator address.
ADMIN = "admin@example.org"
STAFF = "staff@example.org"
LEADER = "minister@example.org"
NO_ACCESS = "<h1>No access with this account</h1>"
CHECK = "<h1>Check your other email addresses</h1>"
AGAIN = "<h1>Check your other email address again</h1>"
UNFINISHED = "<h1>Sign-in didn't finish</h1>"


def web():
    """Statements under the real restricted web login, new connections too."""
    return task_login(ServiceRole.WEB, exact=True, reconnect=True)


@pytest.fixture
def service(tmp_path, settings, real_limiter):
    """Three users and the setting on; the first Administrator is the root's."""
    records = [
        address(),
        address(STAFF, ("staff",)),
        address(LEADER, ("ministry_leader",)),
    ]
    runtime = auth_runtime(tmp_path, settings, real_limiter, records)
    settings.STEWARDSHIP_ALTERNATE_ADDRESS_SIGN_IN = True
    return runtime


class Answer:
    """One fake Google HTTP answer."""

    def __init__(self, status, body=None):
        self.status_code = status
        self.body = body

    def json(self):
        """The answer's JSON, or a parse error for a non-JSON body."""
        if self.body is None:
            raise ValueError("not JSON")
        return self.body


class FakeGoogle:
    """The People API and token revocation, as ``address_matching.HTTP``.

    ``addresses`` are ``(value, verified, source type)``; ``scope`` is what
    the second step's token response says was granted; ``people_error`` an
    exception the People API request raises.
    """

    def __init__(self):
        self.addresses = [(STAFF, True, "ACCOUNT")]
        self.scope = f"openid profile {address_matching.EMAILS_SCOPE}"
        self.people_error = None
        self.no_id_token = False
        self.people_calls = []
        self.revoked = []

    def get(self, url, *, headers, timeout):
        """people/me, with a bearer token and the 10-second limit."""
        self.people_calls.append((url, headers["Authorization"], timeout))
        if self.people_error is not None:
            raise self.people_error
        return Answer(
            200,
            {
                "emailAddresses": [
                    {
                        "value": value,
                        "metadata": {"verified": verified, "source": {"type": kind}},
                    }
                    for value, verified, kind in self.addresses
                ]
            },
        )

    def post(self, url, *, data, timeout):
        """The revocation endpoint."""
        self.revoked.append((url, data["token"], timeout))
        return Answer(200)


@pytest.fixture
def people(monkeypatch, google):
    """A fake Google for both steps: the shared ID tokens plus People API.

    The first step signs in as PERSONAL. The second step's token response
    also carries the granted ``scope`` and its own access token, and can
    leave the ID token out.
    """
    fake = FakeGoogle()
    monkeypatch.setattr(address_matching, "HTTP", fake)
    claims, _ = google
    claims.update(sub=SUBJECT, email=PERSONAL)
    claims.pop("hd")
    first_exchange = SignedGoogleAdapter.get_access_token_data

    def exchange(adapter, request, app, client, pkce_code_verifier=None):
        """The shared signed exchange, with the second step's extras."""
        data = first_exchange(adapter, request, app, client, pkce_code_verifier)
        if request.stewardship_oauth_state["data"].get("step") == "addresses":
            data["access_token"] = "second-step-access"
            data["scope"] = fake.scope
            if fake.no_id_token:
                data.pop("id_token")
        return data

    monkeypatch.setattr(SignedGoogleAdapter, "get_access_token_data", exchange)
    fake.claims = claims
    return fake


def second(client):
    """Check my other addresses, then Google's callback; (query, response)."""
    started = client.post(
        "/admin/login/addresses/",
        {"csrfmiddlewaretoken": client.cookies["pk_admin_csrf"].value},
    )
    assert started.status_code == 302
    query = parse_qs(urlsplit(started["Location"]).query)
    response = client.get(
        "/admin/oauth/callback", {"code": "second-code", "state": query["state"][0]}
    )
    return query, response


def check(client=None):
    """The first step's check page, then the second step: (client, response)."""
    client, response = signed_in(client)
    assert response.status_code == 403
    assert CHECK in response.content.decode()
    return client, second(client)[1]


def current(email=None):
    """The one portal user (the PERSONAL account unless ``email`` says)."""
    return PortalUser.objects.get(sign_in_email=email or PERSONAL)


def standing_match():
    """The one standing match, or None."""
    return AddressMatch.objects.filter(ended_at__isnull=True).first()


def lapse(made, days=31):
    """Move a match's last confirmation back, as only the owner may."""
    AddressMatch.objects.filter(pk=made.pk).update(
        confirmed_at=F("confirmed_at") - timedelta(days=days),
        version=F("version") + 1,
    )


def checks():
    """The recorded admin_address_checked outcomes, oldest first."""
    return [
        AuditContext.objects.get(event_id=event.pk).context["address_check"]
        for event in AuditEvent.objects.filter(
            event_type="admin_address_checked"
        ).order_by("created_at")
    ]


def line_of(body):
    """The text between the first sentence and the "If the parish" line."""
    found = re.search(r"Administration portal\.</p>\s*<p>(.*?)</p>", body, re.S)
    return unescape(found.group(1)) if found else None


def test_setting_off_is_todays_sign_in(service, people, settings):
    """With the setting off nothing new happens: no check page, no pending
    step, no Google request, no match read, and today's refusal."""
    settings.STEWARDSHIP_ALTERNATE_ADDRESS_SIGN_IN = False
    with web():
        client, response = signed_in()
    body = response.content.decode()
    assert response.status_code == 403
    assert NO_ACCESS in body and CHECK not in body
    assert "/admin/login/addresses/" not in body
    assert line_of(body) is not None and "other addresses" not in line_of(body)
    assert AuditEvent.objects.filter(event_type="admin_login_refused").count() == 1
    started = client.post(
        "/admin/login/addresses/",
        {"csrfmiddlewaretoken": client.cookies["pk_admin_csrf"].value},
    )
    assert started.status_code == 403
    assert UNFINISHED in started.content.decode()
    assert not people.people_calls and not people.revoked


def test_a_new_match_signs_in_as_the_user(service, people):
    """The first-check page, the second step's request, the match, its
    audit and notice, the revoked token and the matched session."""
    with web():
        client, response = signed_in()
        body = response.content.decode()
        assert response.status_code == 403
        assert CHECK in body
        assert (
            "You signed in with Google as <strong>person@personal.example</strong>"
            in body
        )
        assert '<form method="post" action="/admin/login/addresses/">' in body
        assert "Check my other addresses" in body
        # Showing it is the first step's refusal, counted and named.
        assert AuditEvent.objects.filter(event_type="admin_login_refused").count() == 1
        query, response = second(client)
    assert set(query["scope"][0].split()) == {
        "openid",
        "profile",
        address_matching.EMAILS_SCOPE,
    }
    assert query["include_granted_scopes"] == ["true"]
    assert query["login_hint"] == [PERSONAL]
    assert query["prompt"] == ["consent"]
    assert query["code_challenge_method"] == ["S256"]
    assert response.status_code == 302
    assert response["Location"] == "/admin/"
    made = standing_match()
    portal = current()
    assert (made.email, made.sign_in_email, made.portal_user_id) == (
        STAFF,
        PERSONAL,
        portal.pk,
    )
    assert made.actor_id is None
    assert portal.email == STAFF
    # Asked once, with the 10-second limit, and revoked whatever happened.
    assert len(people.people_calls) == 1
    assert people.people_calls[0][1:] == ("Bearer second-step-access", 10)
    assert [token for _, token, _ in people.revoked] == ["second-step-access"]
    matched = AuditEvent.objects.get(event_type="admin_address_matched")
    assert (matched.actor_id, matched.subject_id) == (portal.pk, made.pk)
    event = PolicySecurityEvent.objects.get(kind="address_matched")
    assert (event.target, event.account_email) == (STAFF, PERSONAL)
    login = AuditEvent.objects.get(event_type="admin_login")
    assert AuditContext.objects.get(event_id=login.pk).context == {
        "match_id": str(made.pk)
    }
    assert client.get("/admin/").status_code == 200
    assert "address_check" not in client.session
    # Nothing of the People API answer is kept: only the match's address.
    assert not AuditEvent.objects.filter(event_type="admin_address_checked").exists()


def test_a_remembered_match_signs_in_without_google(service, people):
    """A later sign-in uses the standing match by sub; no second step."""
    with web():
        check()
        made = standing_match()
        client, response = signed_in(Client(enforce_csrf_checks=True))
    assert response.status_code == 302
    assert len(people.people_calls) == 1
    assert current().email == STAFF
    logins = AuditEvent.objects.filter(event_type="admin_login").order_by("created_at")
    assert AuditContext.objects.get(event_id=logins.last().pk).context == {
        "match_id": str(made.pk)
    }
    assert client.get("/admin/").status_code == 200


def test_the_30_day_check_confirms_again(service, people):
    """A due match shows the "again" form, which records and counts
    nothing; Check again advances the confirmation and signs in."""
    with web():
        check()
    made = standing_match()
    lapse(made)
    refused = AuditEvent.objects.filter(event_type="admin_login_refused").count()
    with web():
        client, response = signed_in(Client(enforce_csrf_checks=True))
        body = response.content.decode()
        assert response.status_code == 200
        assert AGAIN in body and "Check again" in body
        assert (
            "This Google account signs in as <strong>staff@example.org</strong>" in body
        )
        assert "data-local-instant data-day-only" in body
        assert current().email == PERSONAL
        assert (
            AuditEvent.objects.filter(event_type="admin_login_refused").count()
            == refused
        )
        before = AddressMatch.objects.get(pk=made.pk).confirmed_at
        _, response = second(client)
    assert response.status_code == 302
    made.refresh_from_db()
    assert made.ended_at is None and made.confirmed_at > before
    assert current().email == STAFF
    assert checks() == ["rechecked"]
    event = AuditEvent.objects.get(event_type="admin_address_checked")
    assert AuditContext.objects.get(event_id=event.pk).context == {
        "address_check": "rechecked",
        "match_id": str(made.pk),
    }
    assert PolicySecurityEvent.objects.filter(kind="address_matched").count() == 1


@pytest.mark.parametrize(
    "outcome, line",
    [
        (
            "none",
            "None of the other addresses Google has confirmed for this account has "
            "access either.",
        ),
        ("several", "More than one of your Google Account's addresses has access"),
        ("own_account_only", "One of your other addresses has access, but it can"),
    ],
)
def test_a_due_check_that_gives_no_access_ends_the_match(
    service, people, outcome, line
):
    """A completed re-check that refuses (None, Several, or Own account only
    for another user's address) ends the due match as not_confirmed."""
    with web():
        check()
    made = standing_match()
    lapse(made)
    if outcome == "none":
        people.addresses = [(STAFF, False, "ACCOUNT"), (LEADER, True, "PROFILE")]
    elif outcome == "several":
        people.addresses = [(STAFF, True, "ACCOUNT"), (LEADER, True, "ACCOUNT")]
    else:
        # Google now confirms only LEADER, whose own account signs in.
        people.addresses = [(LEADER, True, "ACCOUNT")]
        leader = seed_user(google_subject="leader-subject", email=LEADER)
        AuditEvent.objects.create(event_type="admin_login", actor_id=leader.pk)
    with web():
        client, _ = signed_in(Client(enforce_csrf_checks=True))
        _, response = second(client)
    body = response.content.decode()
    assert response.status_code == 403 and NO_ACCESS in body
    assert line_of(body).startswith(line)
    made.refresh_from_db()
    assert made.end_reason == "not_confirmed"
    assert checks() == [outcome]
    assert current().email == PERSONAL
    assert "/admin/login/addresses/" not in body


def test_a_recheck_ends_once_the_addresss_own_account_signs_in(service, people):
    """A mail delegate matched while the address's own Google account was
    dormant: once that account signs in, the next 30-day check refuses with
    Own account only and ends the match, instead of confirming it again."""
    with web():
        check()
    made = standing_match()
    staff = seed_user(google_subject="staff-subject", email=STAFF)
    AuditEvent.objects.create(event_type="admin_login", actor_id=staff.pk)
    lapse(made)
    with web():
        client, response = signed_in(Client(enforce_csrf_checks=True))
        assert response.status_code == 200 and AGAIN in response.content.decode()
        _, response = second(client)
    body = response.content.decode()
    assert response.status_code == 403 and NO_ACCESS in body
    assert line_of(body).startswith(
        "One of your other addresses has access, but it can only be used with its "
        "own Google account."
    )
    made.refresh_from_db()
    assert made.end_reason == "not_confirmed"
    assert current().email == PERSONAL
    assert checks() == ["own_account_only"]


@pytest.mark.parametrize(
    "outcome, line",
    [
        ("declined", "Google didn't share your other addresses, so only "),
        ("several", "More than one of your Google Account's addresses has access"),
        ("first_administrator", "One of your other addresses has access, but it"),
    ],
)
def test_refusing_outcomes_show_their_line(service, people, outcome, line):
    """Declined, Several and a first-Administrator address: no access."""
    if outcome == "declined":
        people.scope = "openid https://www.googleapis.com/auth/userinfo.profile"
    elif outcome == "several":
        people.addresses = [(STAFF, True, "ACCOUNT"), (LEADER, True, "ACCOUNT")]
    else:
        people.addresses = [(ADMIN, True, "ACCOUNT")]
    with web():
        _, response = check()
    body = response.content.decode()
    assert response.status_code == 403 and NO_ACCESS in body
    assert line_of(body).startswith(line)
    assert not AddressMatch.objects.exists()
    assert current().email == PERSONAL
    expected = {"first_administrator": "own_account_only"}.get(outcome, outcome)
    assert checks() == [expected]
    # Declined never reaches the People API; every step revokes.
    assert len(people.people_calls) == (0 if outcome == "declined" else 1)
    assert len(people.revoked) == 1


def test_a_cancel_at_google_is_declined(service, people):
    """Google sending the person back with access_denied is Declined."""
    with web():
        client, _ = signed_in()
        started = client.post(
            "/admin/login/addresses/",
            {"csrfmiddlewaretoken": client.cookies["pk_admin_csrf"].value},
        )
        state = parse_qs(urlsplit(started["Location"]).query)["state"][0]
        response = client.get(
            "/admin/oauth/callback", {"error": "access_denied", "state": state}
        )
    body = response.content.decode()
    assert response.status_code == 403
    assert line_of(body) == (
        "Google didn't share your other addresses, so only "
        "<strong>person@personal.example</strong> "
        "was checked."
    )
    assert checks() == ["declined"]


def test_a_cancel_at_google_without_its_pending_step_is_unfinished(
    service, people, monkeypatch
):
    """A cancel at Google whose pending step is gone (here it expired; the
    second step's state is still this browser's) is "Sign-in didn't
    finish", not Declined."""
    monkeypatch.setattr(address_matching, "PENDING_SECONDS", 1)
    with web():
        client, _ = signed_in()
        started = client.post(
            "/admin/login/addresses/",
            {"csrfmiddlewaretoken": client.cookies["pk_admin_csrf"].value},
        )
        state = parse_qs(urlsplit(started["Location"]).query)["state"][0]
        time.sleep(2.1)
        response = client.get(
            "/admin/oauth/callback", {"error": "access_denied", "state": state}
        )
    assert response.status_code == 403
    assert UNFINISHED in response.content.decode()
    assert checks() == []


def test_an_address_whose_own_account_signs_in_is_refused(service, people):
    """Own account only: the address's own Google account signed in lately."""
    staff = seed_user(google_subject="staff-subject", email=STAFF)
    AuditEvent.objects.create(event_type="admin_login", actor_id=staff.pk)
    with web():
        _, response = check()
    assert line_of(response.content.decode()).startswith(
        "One of your other addresses has access, but it can only be used with its "
        "own Google account."
    )
    assert checks() == ["own_account_only"]
    assert not AddressMatch.objects.exists()


def test_another_accounts_match_is_taken_until_it_lapses(service, people):
    """Taken while the other match is confirmed; replaced once it lapsed and
    nobody uses it, with the notice naming the replaced account."""
    with web():
        check()
    old = standing_match()
    people.claims.update(sub="newer-subject", email="newer@personal.example")
    with web():
        _, response = check()
    assert line_of(response.content.decode()).startswith(
        "One of your other addresses already signs in through a different Google "
        "account."
    )
    assert checks() == ["taken"]
    lapse(old)
    # The old account's sign-in is more than 30 days old as well, and its
    # session is over.
    _older_logins(old.portal_user_id)
    with web():
        _, response = check(Client(enforce_csrf_checks=True))
    assert response.status_code == 302
    old.refresh_from_db()
    assert old.end_reason == "replaced"
    new = standing_match()
    assert new.sign_in_email == "newer@personal.example" and new.email == STAFF
    assert current().email == PERSONAL
    event = PolicySecurityEvent.objects.get(address_match_id=new.pk)
    assert event.replaced_account_email == PERSONAL


def test_a_lapsed_match_still_in_use_is_taken(service, people, monkeypatch):
    """Taken, too, when the other account's match has lapsed but that
    account still signs in; nothing tries to take the address over."""
    with web():
        check()
    old = standing_match()
    lapse(old)
    people.claims.update(sub="newer-subject", email="newer@personal.example")
    ends = []
    real_end = address_matching.end
    monkeypatch.setattr(
        address_matching,
        "end",
        lambda match, reason: ends.append(reason) or real_end(match, reason),
    )
    with web():
        _, response = check()
    assert response.status_code == 403
    assert line_of(response.content.decode()).startswith(
        "One of your other addresses already signs in through a different Google "
        "account."
    )
    assert checks() == ["taken"]
    assert "replaced" not in ends
    old.refresh_from_db()
    assert old.ended_at is None
    assert current().email == STAFF


def test_a_takeover_the_guard_refuses_is_taken(service, people, monkeypatch):
    """The old account comes into use after the Python check (sessions are
    not under the users' lock): the guard refuses the replaced end, and the
    check ends in Taken with nothing changed, not a server error."""
    with web():
        check()
    old = standing_match()
    lapse(old)
    people.claims.update(sub="newer-subject", email="newer@personal.example")
    # Python sees nobody using the address; the guard sees the old account's
    # live session and recent sign-in.
    monkeypatch.setattr(address_matching, "in_use", lambda address, excluding: False)
    with web():
        _, response = check()
    assert response.status_code == 403
    assert line_of(response.content.decode()).startswith(
        "One of your other addresses already signs in through a different Google "
        "account."
    )
    assert checks() == ["taken"]
    old.refresh_from_db()
    assert old.ended_at is None
    assert AddressMatch.objects.filter(ended_at__isnull=True).count() == 1
    assert current().email == STAFF


def _older_logins(user_id, days=31):
    """Move one account's successful sign-ins back, as only a superuser could."""
    from .auth_builders import unguarded

    with unguarded(), connection.cursor() as cursor:
        cursor.execute(
            "UPDATE stewardship_audit_event SET created_at=created_at-%s "
            "WHERE actor_id=%s AND event_type='admin_login'",
            [timedelta(days=days), user_id],
        )
        cursor.execute(
            "UPDATE stewardship_portal_session SET revoked_at=last_activity_at "
            "WHERE principal_id=%s AND revoked_at IS NULL",
            [user_id],
        )


@pytest.mark.parametrize("failure", ["other_sub", "no_id_token", "timeout", "error"])
def test_an_unfinished_check_leaves_the_match(service, people, failure, caplog):
    """Another sub, no ID token or a Google failure: "Sign-in didn't finish",
    the due match still standing, and the token revoked anyway."""
    with web():
        check()
    made = standing_match()
    lapse(made)
    if failure == "other_sub":
        people.claims["sub"] = "someone-else"
    elif failure == "no_id_token":
        people.no_id_token = True
    elif failure == "timeout":
        people.people_error = requests.Timeout("slow")
    else:
        people.people_error = requests.ConnectionError("down")
    with web(), caplog.at_level(logging.WARNING):
        client, response = signed_in(Client(enforce_csrf_checks=True))
        if failure == "other_sub":
            # The first step itself must be the right account.
            people.claims["sub"] = SUBJECT
            client, response = signed_in(client)
            people.claims["sub"] = "someone-else"
        assert response.status_code == 200
        _, response = second(client)
    assert response.status_code == 403
    assert UNFINISHED in response.content.decode()
    made.refresh_from_db()
    assert made.ended_at is None
    assert people.revoked[-1][1] == "second-step-access"
    if failure == "timeout":
        assert "Google People API request timed out (limit 10s, elapsed" in caplog.text
    assert "second-step-access" not in caplog.text
    assert STAFF not in caplog.text and PERSONAL not in caplog.text


def test_a_second_step_needs_its_pending_step(service, people):
    """A second-step state is refused without this browser's pending step,
    and a first-step state never takes the second-step branch."""
    with web():
        client, _ = signed_in()
        started = client.post(
            "/admin/login/addresses/",
            {"csrfmiddlewaretoken": client.cookies["pk_admin_csrf"].value},
        )
        state = parse_qs(urlsplit(started["Location"]).query)["state"][0]
        # A new first step in the same browser replaces the pending step.
        people.claims.update(sub="admin-subject", email=ADMIN)
        assert signed_in(client)[1].status_code == 302
        response = client.get(
            "/admin/oauth/callback", {"code": "second-code", "state": state}
        )
    assert response.status_code == 403
    assert UNFINISHED in response.content.decode()
    assert not AddressMatch.objects.exists()
    assert not people.people_calls


@pytest.mark.parametrize("where", ["at_the_button", "at_the_callback"])
def test_the_pending_step_expires(service, people, monkeypatch, where):
    """The second step stays open 15 minutes (here one second): after that
    the button, or Google's callback, is "Sign-in didn't finish"."""
    monkeypatch.setattr(address_matching, "PENDING_SECONDS", 1)
    with web():
        client, _ = signed_in()
        if where == "at_the_button":
            time.sleep(2.1)
            response = client.post(
                "/admin/login/addresses/",
                {"csrfmiddlewaretoken": client.cookies["pk_admin_csrf"].value},
            )
        else:
            started = client.post(
                "/admin/login/addresses/",
                {"csrfmiddlewaretoken": client.cookies["pk_admin_csrf"].value},
            )
            assert started.status_code == 302
            state = parse_qs(urlsplit(started["Location"]).query)["state"][0]
            time.sleep(2.1)
            response = client.get(
                "/admin/oauth/callback", {"code": "second-code", "state": state}
            )
    assert response.status_code == 403
    assert UNFINISHED in response.content.decode()
    assert not AddressMatch.objects.exists()
    assert not people.people_calls


def test_a_disabled_account_at_the_second_step_gets_nothing(service, people):
    """An account disabled between the two steps is refused as No access,
    with no match and no recorded check outcome."""
    with web():
        client, _ = signed_in()
        started = client.post(
            "/admin/login/addresses/",
            {"csrfmiddlewaretoken": client.cookies["pk_admin_csrf"].value},
        )
        state = parse_qs(urlsplit(started["Location"]).query)["state"][0]
    PortalUser.objects.filter(sign_in_email=PERSONAL).update(
        disabled=True, version=F("version") + 1
    )
    with web():
        response = client.get(
            "/admin/oauth/callback", {"code": "second-code", "state": state}
        )
    body = response.content.decode()
    assert response.status_code == 403 and NO_ACCESS in body
    assert not AddressMatch.objects.exists()
    assert checks() == []
    assert current().email == PERSONAL


def test_the_check_button_needs_its_csrf_token(service, people):
    """Check my other addresses without the CSRF token is refused (403)."""
    with web():
        client, _ = signed_in()
        response = client.post("/admin/login/addresses/", {})
        assert response.status_code == 403
        assert not response.has_header("Location")
        # The same browser's pending step still works with its token.
        started = client.post(
            "/admin/login/addresses/",
            {"csrfmiddlewaretoken": client.cookies["pk_admin_csrf"].value},
        )
    assert started.status_code == 302


@pytest.mark.parametrize("due", [False, True])
def test_one_failed_check_counts_once_per_identity(service, people, monkeypatch, due):
    """A refused check counts once against the identity limiter: the first-
    check page already counted, so its second step counts per source only;
    the 30-day page counted nothing, so its second step is the one count."""
    if due:
        with web():
            check()
        lapse(standing_match())
    people.addresses = []
    counted = []
    real_record = authentication.record_failure

    def record(request, **fields):
        """Note whether each recorded failure counts per identity."""
        counted.append(fields.get("counter") is not None)
        return real_record(request, **fields)

    monkeypatch.setattr(authentication, "record_failure", record)
    with web():
        client, response = signed_in(Client(enforce_csrf_checks=True))
        assert response.status_code == (200 if due else 403)
        _, response = second(client)
    assert response.status_code == 403
    assert counted == ([True] if due else [True, False])


def test_the_sign_in_address_wins(service, people):
    """A matched account whose own address becomes a user's signs in as
    itself, and its match ends."""
    with web():
        check()
    store = service.store
    actor = uuid4()
    patch = [
        {
            "operation": "add",
            "section": "login_rules",
            **address(PERSONAL, ("ministry_leader",)),
        }
    ]
    assert change(store, store.active(), actor, patch).state == "applied"
    with web():
        _, response = signed_in(Client(enforce_csrf_checks=True))
    assert response.status_code == 302
    assert AddressMatch.objects.get().end_reason == "sign_in_address_matches"
    assert current().email == PERSONAL


def test_a_gone_user_ends_the_match_and_offers_the_check(service, people):
    """The user deleted and added again: user_gone, then the first-check page."""
    with web():
        check()
    made = standing_match()
    store = service.store
    rule = next(
        record["id"]
        for record in store.active().document()["sections"]["login_rules"]
        if record["values"]["email"] == STAFF
    )
    for patch in (
        [{"operation": "remove", "section": "login_rules", "id": rule}],
        [{"operation": "add", "section": "login_rules", **address(STAFF, ("staff",))}],
    ):
        assert change(store, store.active(), uuid4(), patch).state == ("applied")
    with web():
        _, response = signed_in(Client(enforce_csrf_checks=True))
    assert response.status_code == 403 and CHECK in response.content.decode()
    made.refresh_from_db()
    assert made.end_reason == "user_gone"


def test_every_request_rechecks_the_match(service, people, settings):
    """The setting off, or a lapsed check, cuts an open matched session at
    its next request and puts the account's address back; the match stays."""
    with web():
        client, _ = check()
        assert client.get("/admin/").status_code == 200
    settings.STEWARDSHIP_ALTERNATE_ADDRESS_SIGN_IN = False
    with web():
        assert client.get("/admin/").status_code == 302
    assert current().email == PERSONAL
    assert standing_match() is not None
    # The setting off: a standing match gives no access at sign-in either.
    with web():
        _, response = signed_in(Client(enforce_csrf_checks=True))
    assert response.status_code == 403 and NO_ACCESS in response.content.decode()
    assert standing_match() is not None
    settings.STEWARDSHIP_ALTERNATE_ADDRESS_SIGN_IN = True
    with web():
        client, response = signed_in(Client(enforce_csrf_checks=True))
        assert response.status_code == 302
        assert client.get("/admin/").status_code == 200
    lapse(standing_match())
    with web():
        assert client.get("/admin/").status_code == 302
    assert current().email == PERSONAL
    assert standing_match() is not None


@pytest.mark.parametrize("path", ["remembered", "second_step", "put_back"])
def test_the_deployment_lock_comes_before_the_portal_user_row(service, people, path):
    """Every match write takes the users' ordering lock before it locks the
    portal user row, so it cannot deadlock against an activation, which
    takes the lock first and then share-locks a portal user row: the first
    callback's remembered sign-in, the second step's decision, and the
    address put back when a match is no longer usable."""
    with web():
        client, _ = check()
    portal = current()
    if path == "second_step":
        lapse(standing_match())
        with web():
            client, response = signed_in(Client(enforce_csrf_checks=True))
            assert response.status_code == 200
            started = client.post(
                "/admin/login/addresses/",
                {"csrfmiddlewaretoken": client.cookies["pk_admin_csrf"].value},
            )
        state = parse_qs(urlsplit(started["Location"]).query)["state"][0]
    elif path == "put_back":
        lapse(standing_match())
    held = threading.Event()
    release = threading.Event()

    def hold():
        """Hold the users' lock in another transaction until released."""
        try:
            with transaction.atomic(), connection.cursor() as cursor:
                cursor.execute("SELECT pg_advisory_xact_lock(736220, 1)")
                held.set()
                release.wait(30)
        finally:
            connection.close()

    def sign_in():
        """The path's write, in its own connection; its HTTP status."""
        try:
            with web():
                if path == "remembered":
                    return signed_in(Client(enforce_csrf_checks=True))[1].status_code
                if path == "second_step":
                    return client.get(
                        "/admin/oauth/callback", {"code": "second-code", "state": state}
                    ).status_code
                return client.get("/admin/").status_code
        finally:
            connection.close()

    with ThreadPoolExecutor(2) as pool:
        holder = pool.submit(hold)
        assert held.wait(30)
        signing = pool.submit(sign_in)
        deadline = time.monotonic() + 30
        while True:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT count(*) FROM pg_stat_activity "
                    "WHERE wait_event_type='Lock' AND wait_event='advisory'"
                )
                if cursor.fetchone()[0]:
                    break
            assert time.monotonic() < deadline
            time.sleep(0.05)
        # The waiting sign-in holds no lock on the account's row yet.
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute(
                "SELECT id FROM stewardship_portal_user WHERE id=%s FOR UPDATE NOWAIT",
                [portal.pk],
            )
        release.set()
        holder.result(30)
        assert signing.result(60) == 302
    if path == "put_back":
        assert current().email == PERSONAL
    for alias in connections:
        connections[alias].close_if_unusable_or_obsolete()


def test_a_login_without_the_match_grant_trusts_sql(service, people, settings):
    """The streaming download login, which reads portal users but not the
    match table, trusts the matched address as the SQL checks do; the next
    web request decides, and puts the address back. The trust is keyed on
    the login's name: another login with the same grants gets no role."""
    from parishkit.stewardship.accounts.policy import current_principal

    with web():
        check()
    portal = current()
    with task_login("download"):
        assert not current_principal(service.store, portal.pk).roles
    assert current().email == STAFF
    settings.STEWARDSHIP_ALTERNATE_ADDRESS_SIGN_IN = False
    with task_login("download", name=login_name("download")):
        principal = current_principal(service.store, portal.pk)
    assert "staff" in principal.roles
    assert current().email == STAFF
    with web():
        assert not current_principal(service.store, portal.pk).roles
    assert current().email == PERSONAL


def test_a_web_login_without_the_match_grant_refuses(service, people, caplog):
    """A web login that lost its grant on the match table (provisioning
    drift) gives a matched account no role, instead of trusting it."""
    from psycopg import sql

    from parishkit.stewardship.accounts.policy import current_principal

    with web():
        client, _ = check()
        portal = current()
        web_login = sql.Identifier(login_name(ServiceRole.WEB))
        with connection.cursor() as cursor:
            cursor.execute("RESET SESSION AUTHORIZATION")
            cursor.execute(
                sql.SQL("REVOKE SELECT ON stewardship_address_match FROM {}").format(
                    web_login
                )
            )
            cursor.execute(sql.SQL("SET SESSION AUTHORIZATION {}").format(web_login))
        with caplog.at_level(logging.WARNING):
            assert not current_principal(service.store, portal.pk).roles
    assert "cannot read verified-address matches" in caplog.text
    assert current().email == PERSONAL


def test_a_changed_sign_in_address_makes_a_new_match(service, people):
    """A re-check needs the match's own sign-in address: after Google's
    sign-in address changed, the check ends the old match and makes a new
    one, with a new notice, instead of confirming the old one again."""
    with web():
        check()
    old = standing_match()
    lapse(old)
    people.claims["email"] = "renamed@personal.example"
    with web():
        client, response = signed_in(Client(enforce_csrf_checks=True))
        assert response.status_code == 200
        _, response = second(client)
    assert response.status_code == 302
    old.refresh_from_db()
    assert old.end_reason == "not_confirmed"
    new = standing_match()
    assert (new.email, new.sign_in_email) == (STAFF, "renamed@personal.example")
    assert PolicySecurityEvent.objects.filter(kind="address_matched").count() == 2
    assert checks() == []

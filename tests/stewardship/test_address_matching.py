"""Sign-in through another verified address, without a database (#2343).

The deployment setting, Google's answers (which addresses count, which
scopes were granted, the People API read and the token revocation, with
their timeout logging) and the two sign-in pages. The whole flow runs in
``database/test_address_signin_postgresql.py``.
"""

import logging
import re
from html import unescape

import pytest
import requests

from parishkit.config import ConfigError
from parishkit.stewardship.accounts import address_matching
from parishkit.stewardship.audit.schemas import ContextKind, sanitize
from parishkit.stewardship.deployment import (
    ALTERNATE_ADDRESS_SIGN_IN_VARIABLE,
    load_deployment,
)
from parishkit.stewardship.web.security import login_denial

from .test_deployment import config_file

TOKEN = "synthetic-access-token"


def test_the_setting_is_off_by_default_and_survives_rendering(tmp_path):
    """Off unless the YAML or the environment turns it on; a rendered
    document keeps it on, and one with it off is what it was before."""
    from parishkit.stewardship.deployment_documents import deployment_document

    default = load_deployment(environ={})
    assert default.alternate_address_sign_in is False
    assert "alternate_address_sign_in" not in deployment_document(default)["deployment"]
    path = config_file(tmp_path, {"alternate_address_sign_in": True})
    configuration = load_deployment(path, environ={})
    assert configuration.alternate_address_sign_in is True
    (tmp_path / "rendered").mkdir()
    rendered = config_file(
        tmp_path / "rendered", deployment_document(configuration)["deployment"]
    )
    assert load_deployment(rendered, environ={}).alternate_address_sign_in is True
    name = ALTERNATE_ADDRESS_SIGN_IN_VARIABLE
    assert load_deployment(path, environ={name: "0"}).alternate_address_sign_in is False
    assert load_deployment(environ={name: "1"}).alternate_address_sign_in is True
    # Empty means unset, as Compose passes an unexported variable.
    assert load_deployment(path, environ={name: ""}).alternate_address_sign_in is True


@pytest.mark.parametrize("value", ["yes", "true", "2"])
def test_any_other_setting_value_refuses_to_start(value):
    """Only 1 and 0 (true and false in YAML) are admitted."""
    with pytest.raises(ConfigError, match="alternate_address_sign_in"):
        load_deployment(environ={ALTERNATE_ADDRESS_SIGN_IN_VARIABLE: value})


def test_a_yaml_string_is_refused(tmp_path):
    """The YAML key takes a boolean, not text."""
    path = config_file(tmp_path, {"alternate_address_sign_in": "yes"})
    with pytest.raises(ConfigError, match="alternate_address_sign_in"):
        load_deployment(path, environ={})


def test_only_the_web_runtime_turns_it_on(settings):
    """Tests and every other role read it off."""
    assert address_matching.enabled() is False
    settings.STEWARDSHIP_ALTERNATE_ADDRESS_SIGN_IN = True
    assert address_matching.enabled() is True
    settings.STEWARDSHIP_ALTERNATE_ADDRESS_SIGN_IN = "1"
    assert address_matching.enabled() is False


@pytest.mark.parametrize(
    "scope, expected",
    [
        (f"openid profile {address_matching.EMAILS_SCOPE}", True),
        (
            "openid https://www.googleapis.com/auth/userinfo.profile "
            "https://www.googleapis.com/auth/userinfo.email "
            + address_matching.EMAILS_SCOPE,
            True,
        ),
        ("openid profile", False),
        (f"openid {address_matching.EMAILS_SCOPE}", False),
        ("", False),
        (None, False),
    ],
)
def test_the_check_needs_both_granted_scopes(scope, expected):
    """Without the address or profile scope the person declined."""
    assert address_matching.granted(scope) is expected


def entry(value, verified=True, kind="ACCOUNT"):
    """One People API email address entry."""
    return {
        "value": value,
        "metadata": {"verified": verified, "source": {"type": kind}},
    }


def test_only_the_accounts_own_verified_addresses_count():
    """Verified ACCOUNT entries only, normalized, once each; junk is skipped."""
    person = {
        "emailAddresses": [
            entry(" Staff@Example.ORG "),
            entry("staff@example.org"),
            entry("unverified@example.org", verified=False),
            entry("string@example.org", verified="true"),
            entry("profile@example.org", kind="PROFILE"),
            entry("contact@example.org", kind="CONTACT"),
            entry("domain@example.org", kind="DOMAIN_PROFILE"),
            entry("not an address"),
            {"value": "nometa@example.org"},
            {"value": "nosource@example.org", "metadata": {"verified": True}},
            "junk",
        ]
    }
    assert address_matching.verified_addresses(person) == {"staff@example.org"}
    assert address_matching.verified_addresses({}) == frozenset()
    assert address_matching.verified_addresses({"emailAddresses": "x"}) == frozenset()
    assert address_matching.verified_addresses([]) == frozenset()


class Answer:
    """One fake HTTP answer."""

    def __init__(self, status, body=None):
        self.status_code = status
        self.body = body

    def json(self):
        """The JSON body, or a parse error."""
        if self.body is None:
            raise ValueError("not JSON")
        return self.body


class Google:
    """A fake Google: one answer (or exception) per request kind."""

    def __init__(self, people=None, revoke=None):
        self.people = people
        self.revocation = revoke if revoke is not None else Answer(200)
        self.calls = []

    def get(self, url, *, headers, timeout):
        """people/me."""
        self.calls.append(("get", url, headers, timeout))
        if isinstance(self.people, Exception):
            raise self.people
        return self.people

    def post(self, url, *, data, timeout):
        """The revocation endpoint."""
        self.calls.append(("post", url, data, timeout))
        if isinstance(self.revocation, Exception):
            raise self.revocation
        return self.revocation


def test_the_people_api_is_asked_once_with_the_limit():
    """One people/me read for emailAddresses, bearer token, 10 seconds."""
    google = Google(Answer(200, {"emailAddresses": [entry("staff@example.org")]}))
    assert address_matching.fetch_addresses(TOKEN, http=google) == {"staff@example.org"}
    assert google.calls == [
        (
            "get",
            "https://people.googleapis.com/v1/people/me?personFields=emailAddresses",
            {"Authorization": f"Bearer {TOKEN}"},
            10,
        )
    ]


@pytest.mark.parametrize(
    "answer, logged",
    [
        (requests.Timeout("slow"), "Google People API request timed out (limit 10s, "),
        (requests.ConnectionError("down"), "Google People API request failed ("),
        (Answer(403, {"error": "x"}), "Google People API answered HTTP 403"),
        (Answer(200), "Google People API answered something that is not JSON"),
    ],
)
def test_a_people_api_failure_is_logged_without_secrets(answer, logged, caplog):
    """Each failure says what, the limit and the time taken, never the token."""
    with (
        caplog.at_level(logging.WARNING),
        pytest.raises(address_matching.GoogleUnavailable),
    ):
        address_matching.fetch_addresses(TOKEN, http=Google(answer))
    assert logged in caplog.text
    if isinstance(answer, Exception):
        assert re.search(r"elapsed \d+\.\ds", caplog.text)
    assert TOKEN not in caplog.text


def test_the_token_is_revoked_and_a_failure_changes_nothing(caplog):
    """Revocation posts the token once; a refusal or outage is a warning."""
    google = Google()
    assert address_matching.revoke(TOKEN, http=google) is True
    assert google.calls == [
        ("post", "https://oauth2.googleapis.com/revoke", {"token": TOKEN}, 10)
    ]
    with caplog.at_level(logging.WARNING):
        assert address_matching.revoke(TOKEN, http=Google(revoke=Answer(400))) is False
        assert (
            address_matching.revoke(TOKEN, http=Google(revoke=requests.Timeout()))
            is False
        )
    assert "Google token revocation answered HTTP 400" in caplog.text
    assert "Google token revocation timed out (limit 10s" in caplog.text
    assert TOKEN not in caplog.text
    nothing = Google()
    assert address_matching.revoke(None, http=nothing) is False
    assert address_matching.revoke("", http=nothing) is False
    assert nothing.calls == []


def test_the_audit_context_admits_the_outcome_and_match_only():
    """A closed outcome word and a match ID; anything else is refused."""
    from uuid import uuid4

    match = uuid4()
    assert sanitize(
        ContextKind.ACTION, {"address_check": "rechecked", "match_id": match}
    ) == {"address_check": "rechecked", "match_id": str(match)}
    for word in ("declined", "none", "several", "own_account_only", "taken"):
        assert sanitize(ContextKind.ACTION, {"address_check": word})
    with pytest.raises(ValueError):
        sanitize(ContextKind.ACTION, {"address_check": "matched"})
    with pytest.raises(ValueError):
        sanitize(ContextKind.ACTION, {"match_id": "person@example.org"})
    with pytest.raises(ValueError):
        sanitize(ContextKind.REQUEST, {"address_check": "none"})


def page(**fields):
    """A rendered Admin sign-in page and its text."""
    response = login_denial(admin=True, csrf_token="t" * 64, **fields)
    return response, response.content.decode()


def test_the_first_check_page_reads_as_specified():
    """Its heading, the address Google verified, its three paragraphs and
    actions: Check my other addresses first, then another account."""
    _, body = page(kind="address_check", account="person@personal.example")
    assert "<title>Check your other email addresses" in body
    assert "<h1>Check your other email addresses</h1>" in body
    text = unescape(re.sub(r"\s+", " ", body))
    for sentence in (
        "You signed in with Google as <strong>person@personal.example</strong>, "
        "but that address doesn't have access to the Administration portal.",
        "Your Google Account may also have other email addresses that Google has "
        "confirmed are yours, such as a work or parish address. If the parish gave "
        "access to one of them, you can sign in through it with this Google account.",
        "To check, Google will ask you to let this portal see your Google Account's "
        "email addresses. The portal looks at them once and doesn't keep them. If "
        "one of them has access, it remembers only that one address, which the "
        "parish already has.",
    ):
        assert sentence in text
    forms = re.findall(r'<form method="post" action="([^"]+)">(.*?)</form>', body, re.S)
    assert [action for action, _ in forms] == [
        "/admin/login/addresses/",
        "/admin/login",
    ]
    assert "Check my other addresses" in forms[0][1]
    assert "button-secondary" not in forms[0][1]
    assert "Sign in with a different account" in unescape(forms[1][1])
    assert "button-secondary" in forms[1][1]
    assert '<a href="/">Family campaign portal</a>' in body
    assert "Need access?" not in body


def test_the_again_page_names_the_match_and_its_date():
    """The 30-day form: the matched address, the browser-local date of
    Google's last confirmation, and Check again."""
    from datetime import UTC, datetime
    from types import SimpleNamespace

    due = SimpleNamespace(
        email="staff@example.org", confirmed_at=datetime(2026, 9, 1, 12, tzinfo=UTC)
    )
    response, body = page(
        kind="address_check", account="person@personal.example", due=due
    )
    assert response.status_code == 403
    assert "<h1>Check your other email address again</h1>" in body
    text = unescape(re.sub(r"\s+", " ", body))
    assert (
        "This Google account signs in as <strong>staff@example.org</strong>, which "
        "Google confirmed on <time" in text
    )
    assert "data-local-instant data-day-only" in body
    assert (
        "Every 30 days the portal asks Google again whether that address is still "
        "yours." in text
    )
    assert "Check again" in body and "Check my other addresses" not in body


@pytest.mark.parametrize(
    "line, sentence",
    [
        (
            "declined",
            "Google didn't share your other addresses, so only "
            "<strong>person@personal.example</strong> was checked.",
        ),
        (
            "none",
            "None of the other addresses Google has confirmed for this account has "
            "access either.",
        ),
        (
            "several",
            "More than one of your Google Account's addresses has access, so the "
            "portal can't tell which one to use. Ask the parish staff member who "
            "manages access to this portal.",
        ),
        (
            "own_account_only",
            "One of your other addresses has access, but it can only be used with its "
            "own Google account. Sign in with that account.",
        ),
        (
            "taken",
            "One of your other addresses already signs in through a different Google "
            "account. Ask the parish staff member who manages access to end that "
            "match, then sign in again.",
        ),
    ],
)
def test_each_outcome_adds_its_one_line(line, sentence):
    """No access with this account, its line under the first sentence, and
    no Check my other addresses (signing in again offers it again)."""
    _, body = page(kind="no_access", account="person@personal.example", line=line)
    text = unescape(re.sub(r"\s+", " ", body))
    assert "<h1>No access with this account</h1>" in body
    first = "but that address doesn't have access to the Administration portal.</p>"
    assert f"{first} <p>{sentence}</p>" in text
    assert "/admin/login/addresses/" not in body


def test_the_plain_no_access_page_is_unchanged():
    """Without a line, No access with this account has no extra sentence."""
    _, body = page(kind="no_access", account="person@personal.example")
    text = unescape(re.sub(r"\s+", " ", body))
    assert (
        "Administration portal.</p> <p>If the parish gave access to a different "
        "address" in text
    )


@pytest.mark.parametrize(
    "fields",
    [
        {"kind": "no_access", "account": "a@example.org", "line": "matched"},
        {"kind": "unfinished", "line": "none"},
        {"kind": "no_access", "account": "a@example.org", "due": object()},
        {"kind": "address_check"},
    ],
)
def test_a_wrong_verified_address_page_is_refused(fields):
    """A line only on no_access, a due match only on the check page, and the
    check page only with its verified account."""
    with pytest.raises(ValueError):
        login_denial(admin=True, csrf_token="t" * 64, **fields)

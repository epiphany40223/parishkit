"""The Administrator-only portal users review under the real web database role."""

import re
from uuid import UUID, uuid4

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from parishkit.stewardship.accounts import user_views
from parishkit.stewardship.accounts.authentication import AuthRuntime
from parishkit.stewardship.accounts.policy import current_principal
from parishkit.stewardship.accounts.policy_models import AssignmentOverlay, PortalUser
from parishkit.stewardship.audit.models import AuditContext
from parishkit.stewardship.deployment import ServiceRole

from ..policy_factory import address, assignment, domain
from .auth_builders import signed_in
from .campaign_builders import change, initialized
from .test_background_grants_postgresql import task_login

pytestmark = pytest.mark.django_db(transaction=True)
URL = "/admin/users"


def add_rules(store, *records):
    """Install login rules through the real configuration owner, before sign-in.

    A policy change ends existing Admin sessions, so rules always come first.
    """
    receipt = change(
        store,
        store.active(),
        uuid4(),
        [
            {"operation": "add", "section": "login_rules", **record}
            for record in records
        ],
    )
    assert receipt.state == "applied"


def views():
    """Audit contexts for this page only."""
    return list(
        AuditContext.objects.filter(
            event__event_type="portal_users_viewed"
        ).values_list("context", flat=True)
    )


def row(body, key):
    """The one table row whose header cell starts with this address or domain."""
    found = re.findall(
        rf'<tr>\s*<th scope="row">{re.escape(key)}[<\s].*?</tr>', body, flags=re.S
    )
    assert len(found) == 1, key
    return found[0]


def identity(email, *, hosted=None, disabled=False):
    """A verified Google identity that never opened a session."""
    return PortalUser.objects.create(
        google_subject=str(uuid4()),
        email=email,
        hosted_domain=hosted,
        verified_at=timezone.now(),
        disabled=disabled,
    )


def test_administrator_reviews_rules_provenance_and_warnings(auth_service, google):
    """Real grants, real policy: every table row and warning, and a clean audit."""
    # Only rules an Administrator can really add: Chairperson-seeded authority
    # enters through its own reconciliation owner, never an ordinary patch.
    add_rules(
        auth_service.store,
        domain("example.org", roles=("staff",)),
        domain("workspace.example", roles=("staff",)),
        address("blocked@example.org", roles=()),
        address("leader@example.org", roles=("ministry_leader",)),
        assignment("leader@example.org", ministry=9),
        address("idle@example.org", roles=("ministry_leader",)),
        assignment("helper@workspace.example", ministry=4),
    )
    rules = auth_service.store.active().document()["sections"]["login_rules"]
    # Google verifies this person, then the explicit deny refuses them. That
    # attempt records an identity but must never read as a sign-in.
    google[0].update(email="blocked@example.org", sub="blocked-subject")
    assert signed_in()[1].status_code == 403
    assert PortalUser.objects.filter(email="blocked@example.org").exists()
    # A colleague with no exact rule really signs in through the domain rule.
    google[0].update(email="colleague@example.org", sub="colleague-subject")
    assert signed_in()[1].status_code == 302
    identity("idle@example.org", disabled=True)
    google[0].update(email="admin@example.org", sub="synthetic-google-subject")
    browser, login = signed_in()
    assert login.status_code == 302
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        response = browser.get(URL)
        assert response.status_code == 200
        assert response["Cache-Control"] == "no-store"
        body = response.content.decode()
        # Identifying values never travel in a URL.
        assert browser.get(URL + "?email=blocked@example.org").status_code == 400
        # With a genuine CSRF token, so the view itself refuses the method.
        token = browser.cookies["csrftoken"].value
        assert browser.post(URL, {"csrfmiddlewaretoken": token}).status_code == 405
    # Three identities presented the example.org claim, but the rule authorizes
    # only the colleague: the Administrator and the refused address each have an
    # exact rule that replaces it. The other domain has no evidence yet.
    claimed = row(body, "example.org")
    assert "<td>1</td>" in claimed and claimed.count("data-local-instant") == 1
    assert "No recorded Google account is authorized" in row(body, "workspace.example")
    assert "data-local-instant" in row(body, "admin@example.org")
    denied = row(body, "blocked@example.org")
    assert "Explicit deny" in denied and "None on record" in denied
    assert "data-local-instant" not in denied
    assert "Ministry DUID 9" in row(body, "leader@example.org")
    idle = row(body, "idle@example.org")
    assert "Ministry leader with no active Ministry assignment." in idle
    assert "is disabled and cannot sign in" in idle
    helper = row(body, "helper@workspace.example")
    assert "Ministry DUID 4" in helper
    assert "No login rule gives this person the Ministry leader role." in helper
    assert 'href="/admin/users"' in body
    contexts = views()
    # One audit row for the one successful view, and none for the refused query
    # string or POST. It counts every row of all three tables, naming none.
    listed = sum(record["values"]["kind"] != "assignment" for record in rules) + 1
    assert contexts == [{"outcome": "succeeded", "count": listed}] and listed >= 7
    assert "@" not in str(contexts) and "example" not in str(contexts)


@pytest.fixture
def seeded_service(tmp_path, settings, real_limiter):
    """The ordinary authentication runtime over a policy bootstrapped with chairs.

    Chairperson-seeded rules cannot be added by a patch, so they are installed
    the way the evaluator's own tests install them: in the initial policy.
    """
    confirmed = assignment("confirmed@example.org", ministry=9, seeded=True)
    missing = assignment("missing@example.org", ministry=4, seeded=True)
    store, _, _ = initialized(
        tmp_path,
        [
            address(),
            address("confirmed@example.org", ("ministry_leader",), seeded=True),
            confirmed,
            address("missing@example.org", ("ministry_leader",), seeded=True),
            missing,
        ],
    )
    AssignmentOverlay.objects.create(
        assignment_record_id=UUID(confirmed["id"]),
        active=True,
        source_snapshot_id=uuid4(),
        reason="chair_present",
    )
    AssignmentOverlay.objects.create(
        assignment_record_id=UUID(missing["id"]),
        active=False,
        source_snapshot_id=uuid4(),
        reason="chair_missing",
    )
    settings.STEWARDSHIP_AUTH_RUNTIME = AuthRuntime(store, real_limiter, lambda: True)
    settings.SOCIALACCOUNT_PROVIDERS = {
        "google": {
            "OAUTH_PKCE_ENABLED": True,
            "APPS": [
                {
                    "client_id": "synthetic-client",
                    "secret": "synthetic-secret",
                    "key": "",
                }
            ],
        },
    }
    return settings.STEWARDSHIP_AUTH_RUNTIME


def test_chairperson_suspension_matches_what_a_sign_in_receives(seeded_service, google):
    """The page and the evaluator agree, through the real overlay tables."""
    store = seeded_service.store
    chairs = {
        email: identity(email)
        for email in ("confirmed@example.org", "missing@example.org")
    }
    browser, login = signed_in()
    assert login.status_code == 302
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        body = browser.get(URL).content.decode()
        granted = {
            email: current_principal(store, user.pk) for email, user in chairs.items()
        }
    assert granted["confirmed@example.org"].ministries == frozenset({9})
    assert not granted["missing@example.org"].roles
    confirmed = row(body, "confirmed@example.org")
    assert "suspended" not in confirmed
    assert "Ministry DUID 9 (Parish source Chairperson)" in confirmed
    assert "<td>Ministry leader</td>" in confirmed
    missing = row(body, "missing@example.org")
    assert "The Ministry leader role is suspended" in missing
    assert "Ministry DUID 4 (Parish source Chairperson; suspended)" in missing
    assert "<td>None</td>" in missing


@pytest.mark.parametrize("role", ["staff", "ministry_leader"])
def test_portal_users_are_not_exposed_to_other_roles(auth_service, google, role):
    """Who else holds access is an Administrator-only disclosure."""
    add_rules(auth_service.store, address("reader@example.org", roles=(role,)))
    google[0]["email"] = "reader@example.org"
    browser, _ = signed_in()
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        response = browser.get(URL)
    assert response.status_code == 403
    assert b"admin@example.org" not in response.content
    assert views() == []


def test_access_lost_during_the_request_discloses_and_audits_nothing(
    auth_service, google, monkeypatch
):
    """The recheck inside the lock, not the admission before it, is what refuses."""
    browser, _ = signed_in()
    genuine, calls = user_views._principal, []

    def demoted(request, service, *, read_only=False):
        """Admit the request normally, then lose access before the recheck."""
        calls.append(read_only)
        if read_only:
            raise PermissionError("Access was revoked during the request.")
        return genuine(request, service)

    monkeypatch.setattr(user_views, "_principal", demoted)
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        response = browser.get(URL)
    assert calls == [False, True]
    assert response.status_code == 403
    assert b"admin@example.org" not in response.content
    assert views() == []


def test_a_revoked_administrator_loses_the_page(auth_service, google):
    """Current policy is reloaded on every request, not remembered by a session."""
    store = auth_service.store
    add_rules(store, address("second@example.org"))
    google[0].update(email="second@example.org", sub="second-subject")
    browser, _ = signed_in()
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        assert browser.get(URL).status_code == 200
    version = store.active()
    rule = next(
        record
        for record in version.document()["sections"]["login_rules"]
        if record["values"].get("email") == "second@example.org"
    )
    receipt = change(
        store,
        version,
        uuid4(),
        [{"operation": "remove", "section": "login_rules", "id": rule["id"]}],
    )
    assert receipt.state == "applied"
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        assert browser.get(URL).status_code == 403
    assert len(views()) == 1


def test_strangers_who_only_attempted_a_sign_in_cost_and_show_nothing(
    auth_service, google
):
    """The work under the global lock is bounded by policy, not by attempts."""
    browser, _ = signed_in()
    with CaptureQueriesContext(connection) as before:
        assert browser.get(URL).status_code == 200
    for index in range(40):
        identity(f"stranger{index}@elsewhere.example", hosted="elsewhere.example")
    with CaptureQueriesContext(connection) as after:
        response = browser.get(URL)
    assert response.status_code == 200 and b"stranger" not in response.content
    assert b"elsewhere.example" not in response.content
    assert len(after) == len(before)
    loaded = [
        query["sql"] for query in after if "stewardship_portal_user" in query["sql"]
    ]
    # Identities are selected by the addresses and domains the policy names.
    assert any("LOWER(" in sql.upper() and " IN (" in sql.upper() for sql in loaded)

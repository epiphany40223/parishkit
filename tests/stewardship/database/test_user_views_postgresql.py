"""The Administrator-only portal users review under the real web database role."""

from uuid import uuid4

import pytest

from parishkit.stewardship.audit.models import AuditContext
from parishkit.stewardship.deployment import ServiceRole

from ..policy_factory import address, assignment, domain
from .auth_builders import signed_in
from .campaign_builders import change
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


def test_administrator_reviews_rules_provenance_and_warnings(auth_service, google):
    """Real grants, real policy: every table row and warning, and a clean audit."""
    # Only rules an Administrator can really add: Chairperson-seeded authority
    # enters through its own reconciliation owner, never an ordinary patch.
    add_rules(
        auth_service.store,
        domain("workspace.example", roles=("staff",)),
        address("blocked@example.org", roles=()),
        address("leader@example.org", roles=("ministry_leader",)),
        assignment("leader@example.org", ministry=9),
        address("idle@example.org", roles=("ministry_leader",)),
        assignment("helper@workspace.example", ministry=4),
    )
    rules = auth_service.store.active().document()["sections"]["login_rules"]
    listed = sum(record["values"]["kind"] != "assignment" for record in rules)
    browser, login = signed_in()
    assert login.status_code == 302
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        response = browser.get(URL)
        assert response.status_code == 200
        assert response["Cache-Control"] == "no-store"
        body = response.content.decode()
        # Identifying values never travel in a URL, and the page cannot mutate.
        assert browser.get(URL + "?email=blocked@example.org").status_code == 400
        assert browser.post(URL, {}).status_code in {403, 405}
    assert "workspace.example" in body and "admin@example.org" in body
    assert "Explicit deny" in body and "blocked@example.org" in body
    assert "leader@example.org" in body and "Ministry DUID 9" in body
    assert "Ministry leader with no active Ministry assignment." in body
    assert "helper@workspace.example" in body and "Ministry DUID 4" in body
    assert "No login rule gives this person the Ministry leader role." in body
    assert "No sign-in has presented this Google hosted-domain claim yet." in body
    # The signed-in Administrator has a last sign-in; the others never have.
    assert body.count("data-local-instant") >= 1 and "Never" in body
    assert 'href="/admin/users"' in body
    contexts = views()
    # One audit row for the one successful view, counting rules, never naming them.
    assert contexts == [{"outcome": "succeeded", "count": listed}] and listed >= 5
    assert "@" not in str(contexts) and "example" not in str(contexts)


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


def test_a_revoked_administrator_loses_the_page(auth_service, google):
    """Current policy is reloaded on every request, not remembered by a session."""
    store = auth_service.store
    add_rules(store, address("second@example.org"))
    google[0]["email"] = "second@example.org"
    browser, _ = signed_in()
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
    assert browser.get(URL).status_code in {302, 403}
    assert len(views()) == 1

"""Login-rule edits as previewed, confirmed, installed requests under real roles."""

import re
from html import unescape
from uuid import uuid4

import pytest

from parishkit.stewardship.accounts.configuration_installation import install_request
from parishkit.stewardship.accounts.policy_models import PortalUser
from parishkit.stewardship.accounts.request_models import ConfigurationChangeRequest
from parishkit.stewardship.deployment import ServiceRole

from ..policy_factory import address, domain
from .auth_builders import signed_in
from .test_background_grants_postgresql import task_login
from .test_user_views_postgresql import add_rules, row

pytestmark = pytest.mark.django_db(transaction=True)
PAGE = "/admin/users"
URL = "/admin/users/rules"


def web():
    """Every browser request runs under the real restricted web role."""
    return task_login(ServiceRole.WEB, exact=True, reconnect=True)


def post(browser, values):
    """Use the genuine CSRF cookie; roles may repeat, as ticked boxes do."""
    return browser.post(
        URL, values | {"csrfmiddlewaretoken": browser.cookies["csrftoken"].value}
    )


def proposal(store, **values):
    """A preview submission against the applied digest, as the page's forms send it."""
    return {
        "action": "preview",
        "base_digest": store.active().digest,
        "operation": "set",
        "roles": [],
    } | values


def token(response):
    """Confirm only server-rendered signed intent rather than a fabricated patch."""
    assert response.status_code == 200, response.content
    return unescape(
        re.search(r'name="preview" value="([^"]+)"', response.content.decode()).group(1)
    )


def applied(store, browser, values):
    """Preview and confirm as the web role; install as the installer owner."""
    with web():
        signed = token(post(browser, values))
        response = post(browser, {"action": "confirm", "preview": signed})
    assert response.status_code == 302
    request = ConfigurationChangeRequest.objects.get(
        pk=response["Location"].rsplit("/", 1)[-1]
    )
    receipt = install_request(store, request_id=request.pk, correlation_id=uuid4())
    assert receipt.state == "applied"
    return request


def page(browser):
    """The Portal users page as the Administrator sees it now."""
    with web():
        response = browser.get(PAGE)
    assert response.status_code == 200
    return response.content.decode()


def rules(store):
    """The applied login rules by target."""
    return {
        record["values"].get("email") or record["values"]["domain"]: record["values"]
        for record in store.active().document()["sections"]["login_rules"]
    }


def test_rules_are_added_changed_and_removed_through_reviewed_requests(
    auth_service, google
):
    """Each change is one installed request; provenance names that request."""
    store = auth_service.store
    add_rules(store, domain("example.org", roles=("staff",)))
    browser, login = signed_in()
    assert login.status_code == 302
    body = page(browser)
    assert store.active().digest in body and "Review role change" in body
    assert 'name="operation" value="remove"' in body
    # A new exact address, with the roles ticked in any order and case.
    request = applied(
        store,
        browser,
        proposal(
            store,
            kind="address",
            identity="New.Staff@Example.org",
            roles=["staff", "ministry_leader"],
        ),
    )
    created = rules(store)["new.staff@example.org"]
    assert created["roles"] == ["ministry_leader", "staff"]
    assert created["creation_origin"] == "manual"
    assert created["creation_operation"] == str(request.pk)
    assert created["grants"] == {
        "ministry_leader": {"manual": str(request.pk)},
        "staff": {"manual": str(request.pk)},
    }
    # Widening the domain rule keeps the existing role and adds one.
    applied(
        store,
        browser,
        proposal(
            store,
            kind="domain",
            identity="example.org",
            roles=["staff", "ministry_leader"],
        ),
    )
    assert rules(store)["example.org"]["roles"] == ["ministry_leader", "staff"]
    # Shrinking to an explicit deny drops the removed roles' origins.
    applied(
        store,
        browser,
        proposal(store, kind="address", identity="new.staff@example.org"),
    )
    denied = rules(store)["new.staff@example.org"]
    assert denied["roles"] == [] and denied["grants"] == {}
    assert "Explicit deny" in row(page(browser), "new.staff@example.org")
    # Removal is one request too, and the row is gone afterwards.
    applied(
        store,
        browser,
        proposal(
            store, kind="address", identity="new.staff@example.org", operation="remove"
        ),
    )
    assert "new.staff@example.org" not in rules(store)
    assert "new.staff@example.org" not in page(browser)
    # The Administrator who made every change is still one.
    assert rules(store)["admin@example.org"]["roles"] == ["administrator"]


def test_refusals_explain_without_echoing_and_guards_hold(auth_service, google):
    """Closed refusals, a stale digest, the last Administrator, and denial."""
    store = auth_service.store
    add_rules(store, domain("example.org", roles=("staff",)))
    # Installing that rule made a request of its own; nothing below adds one.
    requests = ConfigurationChangeRequest.objects.count()
    browser, login = signed_in()
    assert login.status_code == 302
    refusals = (
        (dict(kind="domain", identity="gmail.com", roles=["staff"]), "consumer"),
        (dict(kind="domain", identity="not a domain", roles=["staff"]), "not valid"),
        (dict(kind="domain", identity="new.example", roles=[]), "at least one role"),
        (dict(kind="domain", identity="example.org", roles=["staff"]), "already"),
        (
            dict(kind="address", identity="x@example.org", operation="remove"),
            "no longer",
        ),
        # Removing the only Administrator's role leaves an invalid policy.
        (
            dict(kind="address", identity="admin@example.org", roles=["staff"]),
            "exact-address Administrator",
        ),
        (
            dict(kind="address", identity="admin@example.org", operation="remove"),
            "exact-address Administrator",
        ),
    )
    with web():
        for values, expected in refusals:
            response = post(browser, proposal(store, **values))
            assert response.status_code == 400, values
            body = response.content.decode()
            # The refusal keeps the Admin chrome, whose own text names the
            # parish's testing address; the refusal itself names no target.
            refusal = re.search(
                r'<section class="flow panel">.*?</section>', body, flags=re.S
            ).group(0)
            assert expected in refusal and "Return to Portal users" in refusal
            assert values["identity"] not in refusal and "gmail" not in refusal.lower()
            assert response["Cache-Control"] == "no-store"
        # A preview drawn from an older policy is stale, never applied to a newer one.
        stale = proposal(
            store, kind="domain", identity="other.example", roles=["staff"]
        )
        stale["base_digest"] = "0" * 64
        old = post(browser, stale)
        assert old.status_code == 409 and b"changed since" in old.content
        assert b"Return to Portal users" in old.content
        # A review signed against the applied policy is refused at confirmation
        # once another change has activated: the shared admission checks the
        # digest again under the work transaction, and nothing is requested.
        signed = token(
            post(
                browser,
                proposal(
                    store, kind="domain", identity="late.example", roles=["staff"]
                ),
            )
        )
    add_rules(store, domain("meanwhile.example", roles=("staff",)))
    requests += 1
    with web():
        late = post(browser, {"action": "confirm", "preview": signed})
        assert late.status_code == 409 and b"changed since" in late.content
        assert "late.example" not in rules(store)
        # Unknown fields, a query string and the wrong method are refused outright.
        extra = proposal(store, kind="domain", identity="a.example", roles=["staff"])
        assert post(browser, extra | {"extra": "x"}).status_code == 400
        queried = browser.post(
            URL + "?kind=domain",
            {"csrfmiddlewaretoken": browser.cookies["csrftoken"].value} | extra,
        )
        assert queried.status_code == 400
        assert browser.get(URL).status_code == 405
        assert browser.post(URL, {"action": "preview"}).status_code == 403  # No CSRF.
        # A tampered preview is refused, and nothing was requested by any of this.
        assert (
            post(browser, {"action": "confirm", "preview": "forged"}).status_code == 400
        )
        assert ConfigurationChangeRequest.objects.count() == requests
    # With a second Administrator in place, giving up one's own role is allowed
    # and said plainly; granting Administrator is called the expansion it is.
    add_rules(store, address("second@example.org"))
    with web():
        own = post(
            browser,
            proposal(
                store, kind="address", identity="admin@example.org", roles=["staff"]
            ),
        )
        assert own.status_code == 200
        assert b"removes your own Administrator role" in own.content
        assert b"High-impact expansion" not in own.content
        promotion = post(
            browser,
            proposal(
                store,
                kind="address",
                identity="new@example.org",
                roles=["administrator"],
            ),
        )
        assert b"High-impact expansion" in promotion.content
        assert b"grants Administrator to an exact address" in promotion.content
    # A Ministry leader never reaches the editor at all.
    add_rules(store, address("leader@example.org", roles=("ministry_leader",)))
    google[0].update(email="leader@example.org", sub="leader-subject")
    other, login = signed_in()
    assert login.status_code == 302
    with web():
        refused = other.post(
            URL,
            {"csrfmiddlewaretoken": other.cookies["csrftoken"].value} | extra,
        )
        assert refused.status_code == 403
    # The second Administrator's and the leader's rules were two more installed
    # requests; the previews, refusals and the leader's attempt added none.
    assert ConfigurationChangeRequest.objects.count() == requests + 2
    assert PortalUser.objects.filter(email="leader@example.org").exists()

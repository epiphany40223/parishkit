"""Manual Ministry assignments as previewed, confirmed, installed requests."""

import re
from html import unescape
from uuid import uuid4

import pytest

from parishkit.stewardship.accounts.configuration_installation import install_request
from parishkit.stewardship.accounts.configuration_service import (
    admit_configuration_database,
)
from parishkit.stewardship.accounts.policy import current_principal
from parishkit.stewardship.accounts.policy_models import MinistryAssignment
from parishkit.stewardship.accounts.request_models import ConfigurationChangeRequest
from parishkit.stewardship.deployment import ServiceRole

from ..policy_factory import address, assignment, domain
from ..test_source_corpus import source
from .auth_builders import signed_in
from .test_admin_navigation_postgresql import STEPS, flow_steps
from .test_background_grants_postgresql import task_login
from .test_chair_suggestions_postgresql import publish
from .test_configuration_service_postgresql import (
    as_config_installer,
    config_role,  # noqa: F401
)
from .test_policy_postgresql import user
from .test_source_families_postgresql import source_singletons  # noqa: F401
from .test_user_views_postgresql import URL as PAGE
from .test_user_views_postgresql import add_rules, row

pytestmark = pytest.mark.django_db(transaction=True)
# What every assignment review says since Ministry leaders come from
# ParishSoft roles (#922).
NOTICE = "Ministry assignments no longer grant anything"
URL = "/admin/users/assignments"
ACTIVITY = "/admin/parish/ministries/"


def web():
    """Every browser request runs under the real restricted web role."""
    return task_login(ServiceRole.WEB, exact=True, reconnect=True)


def post(browser, values, url=URL):
    """Use the genuine CSRF cookie."""
    return browser.post(
        url, values | {"csrfmiddlewaretoken": browser.cookies["pk_admin_csrf"].value}
    )


def token(response):
    """Confirm only server-rendered signed intent."""
    assert response.status_code == 200, response.content
    return unescape(
        re.search(r'name="preview" value="([^"]+)"', response.content.decode()).group(1)
    )


def proposal(store, **values):
    """A preview submission against the applied digest, as the page's forms send it."""
    return {
        "action": "preview",
        "base_digest": store.active().digest,
        "operation": "add",
        "identity": "leader@example.org",
        "ministry_duid": 4,
    } | values


def applied(store, browser, values, url=URL):
    """Preview and confirm as the web role; install under the restricted installer."""
    with web():
        review = post(browser, values, url)
        # The review step of a change started on Portal users (#196).
        assert flow_steps(review.content) == (STEPS, "Review")
        signed = token(review)
        response = post(browser, {"action": "confirm", "preview": signed}, url)
    assert response.status_code == 302, response.content
    request = ConfigurationChangeRequest.objects.get(
        pk=response["Location"].rstrip("/").rsplit("/", 1)[-1]
    )
    with as_config_installer():
        admit_configuration_database()
        receipt = install_request(store, request_id=request.pk, correlation_id=uuid4())
    assert receipt.state == "applied", receipt
    return request


def address_row(body, email):
    """The exact-address rule row, which precedes the suggestion tables."""
    end = body.find('id="chair-reviews"')
    if end < 0:
        end = body.find('id="chair-suggestions"')
    return row(body if end < 0 else body[:end], email)


@pytest.mark.usefixtures("source_singletons", "config_role")
def test_an_assignment_is_added_named_in_force_and_removed(auth_service, google):
    """The editor offers the catalog; the assignment binds and takes effect."""
    store = auth_service.store
    add_rules(store, address("leader@example.org", ("ministry_leader",)))
    publish(source())
    account = user("leader@example.org")
    assert current_principal(store, account.pk).ministries == frozenset()
    browser, login = signed_in()
    assert login.status_code == 302
    with web():
        body = browser.get(PAGE).content.decode()
    offered = address_row(body, "leader@example.org")
    assert '<option value="4">Choir (4)</option>' in offered
    # Since #922 no assignment grants anything, whatever rule names the
    # address, and the review says so.
    with web():
        for identity in (
            "leader@example.org",
            "admin@example.org",
            "nobody@elsewhere.example",
        ):
            text = post(browser, proposal(store, identity=identity)).content.decode()
            assert NOTICE in text
    request = applied(store, browser, proposal(store))
    # The POST-only review is named, never linked; Return goes to users.
    with web():
        status = browser.get(f"/admin/changes/{request.pk}/").content
    assert b"<li><span>Review Ministry assignment</span></li>" in status
    assert f'<a href="{PAGE}">Return to Portal users</a>'.encode() in status
    assignment = MinistryAssignment.objects.get(
        configuration_id=store.active().version_id, email="leader@example.org"
    )
    assert (assignment.ministry_duid, assignment.source) == (4, "manual")
    assert assignment.operation_id == request.pk
    assert current_principal(store, account.pk).ministries == frozenset()
    with web():
        body = browser.get(PAGE).content.decode()
    listed = address_row(body, "leader@example.org")
    assert "Choir (Ministry DUID 4) (Administrator entry; suspended)" in listed
    # The assignment's own removal form, not the rule editor's removal button.
    assert "Review assignment removal" in listed
    with web():
        again = post(browser, proposal(store))
        assert again.status_code == 400
        assert (
            "already has an assignment an Administrator added" in again.content.decode()
        )
        inactive = post(browser, proposal(store, ministry_duid=99))
        assert inactive.status_code == 400
        assert (
            "is not active in the latest ParishSoft data" in inactive.content.decode()
        )
        bad = post(browser, proposal(store, identity="not an address"))
        assert bad.status_code == 400
        removal = post(browser, proposal(store, operation="remove")).content.decode()
        assert "assignment to Choir (Ministry DUID 4) will be removed" in removal
        assert NOTICE in removal
    applied(store, browser, proposal(store, operation="remove"))
    assert not MinistryAssignment.objects.filter(
        configuration_id=store.active().version_id, email="leader@example.org"
    ).exists()
    assert current_principal(store, account.pk).ministries == frozenset()
    with web():
        missing = post(browser, proposal(store, operation="remove"))
        assert missing.status_code == 400
        assert "no longer exists" in missing.content.decode()


@pytest.mark.usefixtures("source_singletons", "config_role")
def test_an_address_without_a_rule_is_assigned_and_told_how_it_takes_effect(
    auth_service, google
):
    """The preview states the rule it depends on; the page lists it by domain."""
    store = auth_service.store
    add_rules(
        store,
        domain("example.org", roles=("staff",)),
        domain("other.example", roles=("ministry_leader",)),
        address("clerk@example.org", ("staff",)),
    )
    publish(source())
    browser, login = signed_in()
    assert login.status_code == 302
    with web():
        # Since #922 no assignment grants anything, whatever rule backs it.
        for identity in ("lead@other.example", "clerk@example.org"):
            text = post(browser, proposal(store, identity=identity)).content.decode()
            assert NOTICE in text
        preview = post(browser, proposal(store, identity="helper@example.org"))
        assert preview.status_code == 200
        assert NOTICE in preview.content.decode()
        signed = token(preview)
    # A promotion changes no digest but may change the catalog the addition
    # was judged against, so the preview is stale after one.
    publish(source())
    requests = ConfigurationChangeRequest.objects.count()
    with web():
        assert (
            post(browser, {"action": "confirm", "preview": signed}).status_code == 409
        )
        assert ConfigurationChangeRequest.objects.count() == requests
        signed = token(post(browser, proposal(store, identity="helper@example.org")))
        response = post(browser, {"action": "confirm", "preview": signed})
    assert response.status_code == 302
    request = ConfigurationChangeRequest.objects.get(
        pk=response["Location"].rstrip("/").rsplit("/", 1)[-1]
    )
    with as_config_installer():
        admit_configuration_database()
        assert (
            install_request(store, request_id=request.pk, correlation_id=uuid4()).state
            == "applied"
        )
    with web():
        body = browser.get(PAGE).content.decode()
    listed = row(body[body.index('id="domain-assignments"') :], "helper@example.org")
    assert "Choir (Ministry DUID 4) (Administrator entry; suspended)" in listed
    assert NOTICE in listed
    assert "Review assignment removal" in listed


@pytest.mark.usefixtures("source_singletons", "config_role")
def test_an_assignment_to_a_deactivated_ministry_is_still_removed(auth_service, google):
    """The catalog gates additions only; cleaning up is what a removal is for."""
    store = auth_service.store
    add_rules(
        store,
        address("leader@example.org", ("ministry_leader",)),
        assignment("leader@example.org", ministry=77),
    )
    publish(source())
    browser, login = signed_in()
    assert login.status_code == 302
    applied(store, browser, proposal(store))
    applied(
        store,
        browser,
        {"action": "preview", "ministry_duid": "4", "active": "no"},
        ACTIVITY,
    )
    with web():
        body = browser.get(PAGE).content.decode()
    listed = address_row(body, "leader@example.org")
    assert "Choir (Ministry DUID 4) (Administrator entry; suspended)" in listed
    assert listed.count("Review assignment removal") == 2
    assert '<option value="4">' not in listed
    with web():
        inactive = post(browser, proposal(store, identity="other@example.org"))
        assert inactive.status_code == 400
        assert (
            "is not active in the latest ParishSoft data" in inactive.content.decode()
        )
        removal = post(browser, proposal(store, operation="remove")).content.decode()
        assert "assignment to Choir (Ministry DUID 4) will be removed" in removal
        # A Ministry the catalog never had is removed by DUID alone.
        dropped = proposal(store, operation="remove", ministry_duid=77)
        assert "no longer names" in post(browser, dropped).content.decode()
    applied(store, browser, proposal(store, operation="remove"))
    applied(store, browser, dropped | {"base_digest": store.active().digest})
    assert not MinistryAssignment.objects.filter(
        configuration_id=store.active().version_id, email="leader@example.org"
    ).exists()


@pytest.mark.usefixtures("config_role")
def test_without_a_promoted_catalog_only_removals_are_possible(auth_service, google):
    """No catalog: the page offers no addition and the route refuses one, but an
    assignment left behind is still listed and removed."""
    store = auth_service.store
    add_rules(
        store,
        address("leader@example.org", ("ministry_leader",)),
        assignment("leader@example.org", ministry=77),
    )
    browser, login = signed_in()
    assert login.status_code == 302
    with web():
        body = browser.get(PAGE).content.decode()
        assert "Add a Ministry assignment" not in body
        listed = address_row(body, "leader@example.org")
        assert "Ministry DUID 77 (Administrator entry; suspended)" in listed
        assert "Review assignment removal" in listed
        assert "Assign to Ministry" not in listed
        assert post(browser, proposal(store)).status_code == 503
    applied(store, browser, proposal(store, operation="remove", ministry_duid=77))
    assert not MinistryAssignment.objects.filter(
        configuration_id=store.active().version_id, email="leader@example.org"
    ).exists()

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

from ..policy_factory import address, domain
from ..test_source_corpus import source
from .auth_builders import signed_in
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
URL = "/admin/users/assignments"
ACTIVITY = "/admin/configuration/ministries"


def web():
    """Every browser request runs under the real restricted web role."""
    return task_login(ServiceRole.WEB, exact=True, reconnect=True)


def post(browser, values, url=URL):
    """Use the genuine CSRF cookie."""
    return browser.post(
        url, values | {"csrfmiddlewaretoken": browser.cookies["csrftoken"].value}
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
        signed = token(post(browser, values, url))
        response = post(browser, {"action": "confirm", "preview": signed}, url)
    assert response.status_code == 302, response.content
    request = ConfigurationChangeRequest.objects.get(
        pk=response["Location"].rsplit("/", 1)[-1]
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
    # The effect is stated as the evaluator decides it: an exact leader rule
    # takes effect at once, an Administrator needs no scope, and an address
    # no rule names gets none until one does.
    with web():
        text = post(browser, proposal(store)).content.decode()
        assert "exact-address rule granting Ministry leader" in text
        admin = post(browser, proposal(store, identity="admin@example.org"))
        assert (
            "grants Administrator, who leads every Ministry" in admin.content.decode()
        )
        unruled = post(browser, proposal(store, identity="nobody@elsewhere.example"))
        assert "No login rule names this address" in unruled.content.decode()
    request = applied(store, browser, proposal(store))
    assignment = MinistryAssignment.objects.get(
        configuration_id=store.active().version_id, email="leader@example.org"
    )
    assert (assignment.ministry_duid, assignment.source) == (4, "manual")
    assert assignment.operation_id == request.pk
    assert current_principal(store, account.pk).ministries == frozenset({4})
    with web():
        body = browser.get(PAGE).content.decode()
    listed = address_row(body, "leader@example.org")
    assert "Choir (Ministry DUID 4) (Administrator entry)" in listed
    assert 'name="operation" value="remove"' in listed
    with web():
        again = post(browser, proposal(store))
        assert again.status_code == 400
        assert "already has an Administrator entry" in again.content.decode()
        inactive = post(browser, proposal(store, ministry_duid=99))
        assert inactive.status_code == 400
        assert "not an active Ministry" in inactive.content.decode()
        bad = post(browser, proposal(store, identity="not an address"))
        assert bad.status_code == 400
        removal = post(browser, proposal(store, operation="remove")).content.decode()
        assert "assignment to Choir (Ministry DUID 4) will be removed" in removal
        assert "Any scope this assignment gave ends" in removal
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
    add_rules(store, domain("example.org", roles=("staff",)))
    publish(source())
    browser, login = signed_in()
    assert login.status_code == 302
    with web():
        preview = post(browser, proposal(store, identity="helper@example.org"))
        assert preview.status_code == 200
        text = preview.content.decode()
        assert "hosted-domain rule does not grant Ministry leader" in text
        signed = token(preview)
        response = post(browser, {"action": "confirm", "preview": signed})
    assert response.status_code == 302
    request = ConfigurationChangeRequest.objects.get(
        pk=response["Location"].rsplit("/", 1)[-1]
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
    assert "Choir (Ministry DUID 4) (Administrator entry)" in listed
    assert "No login rule gives this person the Ministry leader role." in listed
    assert 'name="operation" value="remove"' in listed


@pytest.mark.usefixtures("source_singletons", "config_role")
def test_an_assignment_to_a_deactivated_ministry_is_still_removed(auth_service, google):
    """The catalog gates additions only; cleaning up is what a removal is for."""
    store = auth_service.store
    add_rules(store, address("leader@example.org", ("ministry_leader",)))
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
    assert "Choir (Ministry DUID 4) (Administrator entry)" in listed
    assert 'name="operation" value="remove"' in listed
    assert '<option value="4">' not in listed
    with web():
        inactive = post(browser, proposal(store, identity="other@example.org"))
        assert inactive.status_code == 400
        assert "not an active Ministry" in inactive.content.decode()
        removal = post(browser, proposal(store, operation="remove")).content.decode()
        assert "assignment to Choir (Ministry DUID 4) will be removed" in removal
    applied(store, browser, proposal(store, operation="remove"))
    assert not MinistryAssignment.objects.filter(
        configuration_id=store.active().version_id, email="leader@example.org"
    ).exists()


def test_without_a_promoted_catalog_the_editor_is_unavailable(auth_service, google):
    """No catalog, no assignments: the page offers no form and the route refuses."""
    store = auth_service.store
    browser, login = signed_in()
    assert login.status_code == 302
    with web():
        body = browser.get(PAGE).content.decode()
        assert "Add a Ministry assignment" not in body
        response = post(browser, proposal(store))
    assert response.status_code == 503

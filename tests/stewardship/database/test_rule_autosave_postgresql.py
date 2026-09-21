"""Autosaved role intents as durable requests under the real web role."""

from uuid import UUID, uuid4

import pytest

from parishkit.stewardship.accounts.configuration_installation import install_request
from parishkit.stewardship.accounts.configuration_service import (
    admit_configuration_database,
)
from parishkit.stewardship.accounts.policy import current_principal
from parishkit.stewardship.accounts.request_models import ConfigurationChangeRequest
from parishkit.stewardship.deployment import ServiceRole

from ..policy_factory import address, domain
from .auth_builders import signed_in
from .test_background_grants_postgresql import task_login
from .test_configuration_service_postgresql import (
    as_config_installer,
    config_role,  # noqa: F401
)
from .test_policy_postgresql import user
from .test_user_views_postgresql import URL as PAGE
from .test_user_views_postgresql import add_rules

pytestmark = pytest.mark.django_db(transaction=True)
APPLY = "/admin/users/rules/apply"
REQUESTS = "/admin/users/rules/requests/"
BASE = "/admin/users/rules/base"


def web():
    """Every browser request runs under the real restricted web role."""
    return task_login(ServiceRole.WEB, exact=True, reconnect=True)


def intent(store, **values):
    """One autosaved intent as the page's script sends it."""
    return {
        "request_key": str(uuid4()),
        "base_digest": store.active().digest,
        "kind": "address",
        "identity": "clerk@example.org",
        "role": "ministry_leader",
        "checked": "1",
    } | values


def apply(browser, values):
    """Send with the genuine CSRF token in the header, as the script does."""
    return browser.post(
        APPLY, values, headers={"X-CSRFToken": browser.cookies["csrftoken"].value}
    )


def installed(store, request_id):
    """Activate under the restricted installer; the web process never does."""
    with as_config_installer():
        admit_configuration_database()
        receipt = install_request(
            store, request_id=UUID(request_id), correlation_id=uuid4()
        )
    assert receipt.state == "applied", receipt
    return receipt


@pytest.mark.usefixtures("config_role")
def test_an_intent_is_recorded_once_and_reported_applied_with_its_digest(
    auth_service, google
):
    """Same key: one request, answered by its state whatever the rules are now."""
    store = auth_service.store
    add_rules(store, address("clerk@example.org", ("staff",)))
    account = user("clerk@example.org")
    browser, login = signed_in()
    assert login.status_code == 302
    values = intent(store)
    # The rules above were installed through a request of their own.
    before = ConfigurationChangeRequest.objects.count()
    with web():
        first = apply(browser, values)
        assert first.status_code == 202, first.content
        receipt = first.json()
        assert receipt["state"] == "staged" and receipt["applied_digest"] is None
        again = apply(browser, values)
        assert again.status_code == 202 and again.json() == receipt
        # The same key with another intent answers with the original request,
        # never a second one.
        rebound = apply(browser, values | {"role": "administrator"})
        assert rebound.status_code == 202 and rebound.json() == receipt
        assert ConfigurationChangeRequest.objects.count() == before + 1
        pending = browser.get(REQUESTS + receipt["request_id"])
        assert pending.status_code == 200 and pending.json()["state"] == "staged"
        assert browser.get(REQUESTS + str(uuid4())).status_code == 404
    installed(store, receipt["request_id"])
    with web():
        applied = browser.get(REQUESTS + receipt["request_id"]).json()
        # A lost answer recovered after activation reads the applied receipt,
        # although the digest the intent named is no longer the applied one.
        recovered = apply(browser, values)
        assert recovered.status_code == 202 and recovered.json() == applied
    assert applied["state"] == "applied"
    assert applied["applied_digest"] == store.active().digest
    assert "ministry_leader" in current_principal(store, account.pk).roles
    request = ConfigurationChangeRequest.objects.get(pk=receipt["request_id"])
    patched = request.patch[0]["values"]
    assert patched["grants"]["ministry_leader"] == {"manual": str(request.pk)}
    with web():
        stale = apply(browser, intent(store, base_digest=values["base_digest"]))
        assert stale.status_code == 409, stale.content
        assert stale.json()["errors"][0]["field_id"] == "base_digest"
        current = browser.get(BASE).json()
        assert current["digest"] == store.active().digest
        assert current["rules"]["address"]["clerk@example.org"] == [
            "ministry_leader",
            "staff",
        ]
    # Another Administrator cannot read this request, by its real id either.
    add_rules(store, address("second@example.org", ("administrator",)))
    google[0].update(email="second@example.org", sub="second-google-subject")
    other, login = signed_in()
    assert login.status_code == 302
    with web():
        assert other.get(REQUESTS + receipt["request_id"]).status_code == 404


@pytest.mark.usefixtures("config_role")
def test_refusals_are_closed_and_record_nothing(auth_service, google):
    """Policy fences, missing rules, bad fields and strangers all fail closed."""
    store = auth_service.store
    add_rules(store, domain("example.org", roles=("staff",)))
    browser, login = signed_in()
    assert login.status_code == 302
    before = ConfigurationChangeRequest.objects.count()
    with web():
        # The last Administrator cannot withdraw their own role.
        last = apply(
            browser,
            intent(
                store, identity="admin@example.org", role="administrator", checked="0"
            ),
        )
        assert last.status_code == 400
        assert last.json()["errors"][0]["field_id"] == "intent"
        # A rule the page does not show is a stale page, whichever way the
        # tick went: autosave never creates a rule.
        for checked in ("0", "1"):
            missing = apply(
                browser, intent(store, identity="nobody@example.org", checked=checked)
            )
            assert missing.status_code == 409
            assert missing.json()["errors"][0]["field_id"] == "intent"
        assert (
            apply(
                browser, intent(store, kind="domain", identity="new.example")
            ).status_code
            == 409
        )
        # A domain rule can never grant Administrator or lose its last role.
        assert (
            apply(
                browser,
                intent(
                    store, kind="domain", identity="example.org", role="administrator"
                ),
            ).status_code
            == 400
        )
        assert (
            apply(
                browser,
                intent(
                    store,
                    kind="domain",
                    identity="example.org",
                    role="staff",
                    checked="0",
                ),
            ).status_code
            == 400
        )
        assert (
            apply(browser, intent(store, request_key="not-a-uuid")).status_code == 400
        )
        assert apply(browser, intent(store, extra="1")).status_code == 400
        assert browser.get(APPLY).status_code == 405
        assert ConfigurationChangeRequest.objects.count() == before
        assert browser.post(APPLY, intent(store)).status_code == 403
    google[0].update(email="reader@example.org", sub="reader-google-subject")
    add_rules(store, address("reader@example.org", ("staff",)))
    before = ConfigurationChangeRequest.objects.count()
    stranger, login = signed_in()
    assert login.status_code == 302
    with web():
        assert apply(stranger, intent(store)).status_code == 403
        assert stranger.get(BASE).status_code == 403
    assert ConfigurationChangeRequest.objects.count() == before
    assert browser.get(PAGE).status_code in {200, 403}

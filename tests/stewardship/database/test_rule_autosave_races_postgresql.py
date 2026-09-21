"""Policy races and exact-once behavior of the login-rule autosave queue."""

from uuid import UUID, uuid4

import pytest
from django.db.models import F
from django.utils import timezone

from parishkit.stewardship.accounts.configuration_installation import install_request
from parishkit.stewardship.accounts.configuration_service import (
    admit_configuration_database,
)
from parishkit.stewardship.accounts.models import PortalSession
from parishkit.stewardship.accounts.policy_models import PolicySecurityEvent
from parishkit.stewardship.accounts.request_models import (
    ConfigurationChangeRequest,
    ConfigurationRequestCheckpoint,
)
from parishkit.stewardship.deployment import ServiceRole

from ..policy_factory import address
from .auth_builders import signed_in
from .test_background_grants_postgresql import task_login
from .test_configuration_service_postgresql import (
    as_config_installer,
    config_role,  # noqa: F401
)
from .test_rule_autosave_postgresql import APPLY, REQUESTS, apply, intent, state
from .test_user_views_postgresql import add_rules

pytestmark = pytest.mark.django_db(transaction=True)


def web():
    """Every browser request runs under the real restricted web role."""
    return task_login(ServiceRole.WEB, exact=True, reconnect=True)


def installed(store, request_id):
    """Activate under the restricted installer and return the receipt."""
    with as_config_installer():
        admit_configuration_database()
        return install_request(
            store, request_id=UUID(request_id), correlation_id=uuid4()
        )


@pytest.mark.usefixtures("config_role")
def test_an_autosaved_administrator_grant_is_exactly_once(auth_service, google):
    """One request, one checkpoint chain, one security event, however often resent."""
    store = auth_service.store
    add_rules(store, address("clerk@example.org", ("staff",)))
    browser, login = signed_in()
    assert login.status_code == 302
    values = intent(store, role="administrator")
    with web():
        receipt = state(apply(browser, values).json())
        for _ in range(2):
            assert state(apply(browser, values).json()) == receipt
    request_id = receipt["request_id"]
    checkpoints = ConfigurationRequestCheckpoint.objects.filter(
        request_id=request_id
    ).count()
    assert checkpoints == 1
    assert installed(store, request_id).state == "applied"
    assert (
        PolicySecurityEvent.objects.filter(activation__request_id=request_id).count()
        == 1
    )
    with web():
        for _ in range(2):
            assert apply(browser, values).json()["state"] == "applied"
    assert (
        PolicySecurityEvent.objects.filter(activation__request_id=request_id).count()
        == 1
    )
    assert ConfigurationChangeRequest.objects.filter(pk=request_id).count() == 1
    # Activation appended its checkpoints once; resubmission appended none.
    later = ConfigurationRequestCheckpoint.objects.filter(request_id=request_id).count()
    assert later > checkpoints
    with web():
        apply(browser, values)
    assert (
        ConfigurationRequestCheckpoint.objects.filter(request_id=request_id).count()
        == later
    )


@pytest.mark.usefixtures("config_role")
def test_another_administrators_activation_fails_the_second_intent_as_stale(
    auth_service, google
):
    """Two intents on one base: the second fails at activation with stale_base."""
    store = auth_service.store
    add_rules(
        store,
        address("clerk@example.org", ("staff",)),
        address("second@example.org", ("administrator",)),
    )
    first, login = signed_in()
    assert login.status_code == 302
    google[0].update(email="second@example.org", sub="second-google-subject")
    second, login = signed_in()
    assert login.status_code == 302
    with web():
        response = apply(first, intent(store))
        assert response.status_code == 202, response.content
        mine = state(response.json())
        response = apply(second, intent(store, role="administrator"))
        assert response.status_code == 202, response.content
        theirs = state(response.json())
    assert installed(store, mine["request_id"]).state == "applied"
    receipt = installed(store, theirs["request_id"])
    assert (receipt.state, receipt.failure_code) == ("failed", "stale_base")
    with web():
        reported = second.get(REQUESTS + theirs["request_id"]).json()
        assert (reported["state"], reported["failure_code"]) == ("failed", "stale_base")
        # The retry the conflict view makes: a new key against the new digest.
        again = state(apply(second, intent(store, role="administrator")).json())
    assert again["request_id"] != theirs["request_id"]
    assert installed(store, again["request_id"]).state == "applied"


@pytest.mark.usefixtures("config_role")
def test_a_revoked_session_stops_the_queue_and_records_nothing(auth_service, google):
    """Revocation mid-queue: the apply and status routes deny, nothing is recorded."""
    store = auth_service.store
    add_rules(store, address("clerk@example.org", ("staff",)))
    browser, login = signed_in()
    assert login.status_code == 302
    with web():
        receipt = state(apply(browser, intent(store)).json())
    row = PortalSession.objects.get(revoked_at__isnull=True)
    # Every update of a mutable record advances its version, as the owner does.
    PortalSession.objects.filter(pk=row.pk, version=row.version).update(
        revoked_at=timezone.now(), version=F("version") + 1
    )
    before = ConfigurationChangeRequest.objects.count()
    with web():
        assert apply(browser, intent(store, role="administrator")).status_code == 403
        assert browser.get(REQUESTS + receipt["request_id"]).status_code == 403
        assert browser.post(APPLY, intent(store)).status_code == 403
    assert ConfigurationChangeRequest.objects.count() == before

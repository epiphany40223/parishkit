"""Security events stay on every Administrator's dashboard until acknowledged."""

from uuid import uuid4

import pytest

from parishkit.stewardship.accounts.policy_models import (
    PolicySecurityAcknowledgement,
    PolicySecurityEvent,
    PortalUser,
)
from parishkit.stewardship.audit.models import AuditEvent

from ..policy_factory import address
from .auth_builders import signed_in
from .test_user_rule_views_postgresql import applied, proposal, web
from .test_user_views_postgresql import add_rules

pytestmark = pytest.mark.django_db(transaction=True)
HOME = "/admin/"
PANEL = 'id="security-events"'


def acknowledge(browser, event_id):
    """Post the dashboard's own form with the genuine CSRF cookie."""
    with web():
        return browser.post(
            f"/admin/security-events/{event_id}/acknowledge",
            {"csrfmiddlewaretoken": browser.cookies["csrftoken"].value},
        )


def home(browser):
    """The dashboard as this Administrator sees it now."""
    with web():
        response = browser.get(HOME)
    assert response.status_code == 200
    return response.content.decode()


def offered(page, event):
    """Whether the page offers this event's own acknowledgement form."""
    return f"/admin/security-events/{event.pk}/acknowledge" in page


def signed_in_as(google, email, subject):
    """Complete a real sign-in for another Google identity."""
    google[0].update(email=email, sub=subject)
    browser, login = signed_in()
    assert login.status_code == 302
    return browser


def test_an_expansion_waits_for_another_administrator(auth_service, google):
    """The granting actor's own acknowledgement clears it for that actor alone."""
    store = auth_service.store
    add_rules(store, address("second@example.org"))
    browser, login = signed_in()
    assert login.status_code == 302
    admin = PortalUser.objects.get(email="admin@example.org")
    applied(
        store,
        browser,
        proposal(
            store, kind="address", identity="new@example.org", roles=["administrator"]
        ),
    )
    event = PolicySecurityEvent.objects.get(target="new@example.org")
    assert event.kind == "administrator_granted"
    assert event.recipients == ["admin@example.org", "second@example.org"]
    assert event.actor_id == admin.pk
    page = home(browser)
    assert PANEL in page and "new@example.org" in page
    assert "Administrator added to an exact address" in page
    assert offered(page, event)
    # The actor acknowledges: recorded once, audited, and gone for the actor.
    response = acknowledge(browser, event.pk)
    assert response.status_code == 302 and response["Location"] == HOME
    acknowledgement = PolicySecurityAcknowledgement.objects.get(event=event)
    assert acknowledgement.email == "admin@example.org"
    assert acknowledgement.actor_id == admin.pk
    assert (
        AuditEvent.objects.filter(
            event_type="security_event_acknowledged", subject_id=event.pk
        ).count()
        == 1
    )
    # Other events the fixture's own rule installations recorded stay open,
    # so the panel itself remains; this event's row is what leaves.
    page = home(browser)
    assert not offered(page, event) and "new@example.org" not in page
    assert acknowledge(browser, event.pk).status_code == 302
    assert PolicySecurityAcknowledgement.objects.filter(event=event).count() == 1
    assert (
        AuditEvent.objects.filter(
            event_type="security_event_acknowledged", subject_id=event.pk
        ).count()
        == 1
    )
    # The other Administrator still sees it, without the actor's note.
    other = signed_in_as(google, "second@example.org", "second-subject")
    page = home(other)
    assert offered(page, event) and "You have acknowledged" not in page
    # Their acknowledgement settles it for everyone, the newcomer included.
    assert acknowledge(other, event.pk).status_code == 302
    assert not offered(home(other), event)
    assert not offered(home(browser), event)
    newcomer = signed_in_as(google, "new@example.org", "new-subject")
    page = home(newcomer)
    assert not offered(page, event) and "You have acknowledged" not in page
    assert PolicySecurityAcknowledgement.objects.filter(event=event).count() == 2


def test_the_only_administrator_settles_an_expansion_alone(auth_service, google):
    """With no other Administrator at activation, the actor's own word suffices."""
    store = auth_service.store
    browser, login = signed_in()
    assert login.status_code == 302
    applied(
        store,
        browser,
        proposal(store, kind="domain", identity="partner.example", roles=["staff"]),
    )
    event = PolicySecurityEvent.objects.get(target="partner.example")
    assert event.kind == "domain_created" and event.recipients == ["admin@example.org"]
    page = home(browser)
    assert PANEL in page and "Hosted-domain rule created" in page
    assert offered(page, event)
    assert acknowledge(browser, event.pk).status_code == 302
    page = home(browser)
    assert not offered(page, event) and "partner.example" not in page
    # A later Administrator inherits nothing of it to acknowledge.
    add_rules(store, address("later@example.org"))
    later = signed_in_as(google, "later@example.org", "later-subject")
    page = home(later)
    assert not offered(page, event) and "partner.example" not in page


def test_acknowledgement_needs_an_administrator_and_a_real_event(auth_service, google):
    """Staff are refused, an unknown event is not found, and only POST is served."""
    store = auth_service.store
    add_rules(store, address("staff@example.org", ("staff",)))
    browser, login = signed_in()
    assert login.status_code == 302
    applied(
        store,
        browser,
        proposal(
            store, kind="address", identity="new@example.org", roles=["administrator"]
        ),
    )
    event = PolicySecurityEvent.objects.get(target="new@example.org")
    assert acknowledge(browser, uuid4()).status_code == 404
    with web():
        assert (
            browser.get(f"/admin/security-events/{event.pk}/acknowledge").status_code
            == 405
        )
        assert (
            browser.post(f"/admin/security-events/{event.pk}/acknowledge").status_code
            == 403
        )
    staff = signed_in_as(google, "staff@example.org", "staff-subject")
    assert PANEL not in home(staff)
    assert acknowledge(staff, event.pk).status_code == 403
    assert not PolicySecurityAcknowledgement.objects.filter(event=event).exists()
    assert not AuditEvent.objects.filter(
        event_type="security_event_acknowledged"
    ).exists()

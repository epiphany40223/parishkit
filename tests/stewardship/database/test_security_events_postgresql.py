"""Security events stay on every Administrator's dashboard until acknowledged."""

from types import SimpleNamespace
from uuid import uuid4

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from parishkit.stewardship.accounts.policy_models import (
    PolicySecurityAcknowledgement,
    PolicySecurityEvent,
    PortalUser,
)
from parishkit.stewardship.accounts.security_events import open_events
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
            {"csrfmiddlewaretoken": browser.cookies["pk_admin_csrf"].value},
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
    # The panel costs the Admin page's fixed query budget exactly one query.
    principal = SimpleNamespace(identity=admin.pk)
    with CaptureQueriesContext(connection) as queries:
        rows = open_events(principal)
    assert len(queries) == 1
    assert [row["id"] for row in rows if row["id"] == event.pk] == [event.pk]
    assert next(row for row in rows if row["id"] == event.pk)["actor"] == (
        "admin@example.org"
    )
    # The actor acknowledges through a second Google identity at the same
    # address: the actor still, by address, so the row is the actor's own,
    # recorded once and audited, and the event is gone for the actor's
    # first identity as well but not settled.
    again = signed_in_as(google, "admin@example.org", "admin-second-subject")
    assert offered(home(again), event)
    response = acknowledge(again, event.pk)
    assert response.status_code == 302 and response["Location"] == HOME
    acknowledgement = PolicySecurityAcknowledgement.objects.get(event=event)
    assert acknowledgement.email == "admin@example.org" and acknowledgement.own
    assert acknowledgement.actor_id != admin.pk
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
    assert not offered(home(again), event)
    # The granted account cannot wave its own grant through: acknowledging
    # clears it for that account alone.
    newcomer = signed_in_as(google, "new@example.org", "new-subject")
    assert offered(home(newcomer), event)
    assert acknowledge(newcomer, event.pk).status_code == 302
    assert not offered(home(newcomer), event)
    assert not PolicySecurityAcknowledgement.objects.get(
        event=event, email="new@example.org"
    ).own
    # The other Administrator who existed at activation still sees it.
    other = signed_in_as(google, "second@example.org", "second-subject")
    assert offered(home(other), event)
    # Their acknowledgement settles it for everyone.
    assert acknowledge(other, event.pk).status_code == 302
    assert not offered(home(other), event)
    assert not offered(home(browser), event)
    assert not offered(home(newcomer), event)
    assert PolicySecurityAcknowledgement.objects.filter(event=event).count() == 3


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
    # The deployment's root activation recorded the first Administrator's own
    # grant with nobody to await; that event is settled by any acknowledgement.
    root = PolicySecurityEvent.objects.get(target="admin@example.org")
    assert root.recipients == [] and offered(page, root)
    assert acknowledge(browser, root.pk).status_code == 302
    assert not PolicySecurityAcknowledgement.objects.get(event=root).own
    assert not offered(home(browser), root)
    # A later Administrator inherits nothing of either to acknowledge.
    add_rules(store, address("later@example.org"))
    later = signed_in_as(google, "later@example.org", "later-subject")
    page = home(later)
    assert not offered(page, event) and "partner.example" not in page
    assert not offered(page, root)


def test_acknowledgement_needs_an_administrator_and_a_real_event(
    auth_service, google, monkeypatch
):
    """Staff are refused, an unknown event is not found, and only POST is served."""
    from parishkit.stewardship.accounts import security_event_views as views

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
    route = f"/admin/security-events/{event.pk}/acknowledge"
    csrf = {"csrfmiddlewaretoken": browser.cookies["pk_admin_csrf"].value}
    with web():
        assert browser.get(route).status_code == 405
        assert browser.post(route).status_code == 403
        assert browser.post(route + "?next=/", csrf).status_code == 400
    # A configuration under restore review takes no acknowledgement either.
    # Scoped, so the Google fixture's own patch stays in force for the
    # sign-ins below.
    with monkeypatch.context() as patched:
        patched.setattr(
            views,
            "coherent_configuration",
            lambda store: SimpleNamespace(restore_review_required=True),
        )
        assert acknowledge(browser, event.pk).status_code == 503
    staff = signed_in_as(google, "staff@example.org", "staff-subject")
    assert PANEL not in home(staff)
    assert acknowledge(staff, event.pk).status_code == 403
    assert not PolicySecurityAcknowledgement.objects.filter(event=event).exists()
    assert not AuditEvent.objects.filter(
        event_type="security_event_acknowledged"
    ).exists()

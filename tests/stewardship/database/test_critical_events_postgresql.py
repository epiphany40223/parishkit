"""The critical-events banner explains itself and an Administrator can clear it."""

from types import SimpleNamespace

import pytest
from django.db import transaction

from parishkit.stewardship.audit.models import (
    AuditEvent,
    CriticalEventAcknowledgement,
)
from parishkit.stewardship.audit.services import operational
from parishkit.stewardship.observability import Event

from ..policy_factory import address
from .auth_builders import signed_in
from .test_security_events_postgresql import home, signed_in_as
from .test_user_rule_views_postgresql import web
from .test_user_views_postgresql import add_rules

pytestmark = pytest.mark.django_db(transaction=True)
ROUTE = "/admin/critical-events/acknowledge"
BANNER = "Critical problems in the past 24 hours"


def critical(event):
    """Record one CRITICAL operational event the way producers do."""
    with transaction.atomic():
        operational(event, level="CRITICAL")


def acknowledge(browser):
    """Post the banner's own form with the genuine CSRF cookie."""
    with web():
        return browser.post(
            ROUTE, {"csrfmiddlewaretoken": browser.cookies["pk_admin_csrf"].value}
        )


def audits():
    """How many acknowledgements the audit log holds."""
    return AuditEvent.objects.filter(
        event_type="critical_events_acknowledged"
    ).count()


def test_banner_names_events_and_acknowledgement_clears_it_for_everyone(
    auth_service, google
):
    """Grouped plain-language labels, a shared acknowledgement, and a return."""
    add_rules(auth_service.store, address("second@example.org"))
    browser, login = signed_in()
    assert login.status_code == 302
    assert BANNER not in home(browser)
    critical(Event.SOURCE_INVALID)
    critical(Event.SOURCE_INVALID)
    critical(Event.MAIL_PROVIDER_FAILED)
    page = home(browser)
    assert BANNER in page
    assert "ParishSoft data refresh failed (2×)" in page
    assert "Email sending failed" in page
    assert ROUTE in page and 'name="critical" value="yes"' in page
    response = acknowledge(browser)
    assert response.status_code == 302 and response["Location"] == "/admin/"
    assert CriticalEventAcknowledgement.objects.count() == 1
    assert audits() == 1
    event = AuditEvent.objects.get(event_type="critical_events_acknowledged")
    assert event.auditcontext.context == {"outcome": "succeeded", "count": 3}
    # Hidden for this Administrator and for another one.
    assert BANNER not in home(browser)
    other = signed_in_as(google, "second@example.org", "second-subject")
    assert BANNER not in home(other)
    # Nothing new to acknowledge records nothing.
    assert acknowledge(other).status_code == 302
    assert CriticalEventAcknowledgement.objects.count() == 1 and audits() == 1
    # A newer CRITICAL event brings the banner back, naming only itself.
    critical(Event.TASK_FAILED)
    page = home(other)
    assert BANNER in page and "Background task failed" in page
    assert "ParishSoft data refresh failed" not in page


def test_acknowledgement_needs_an_administrator_and_post(
    auth_service, google, monkeypatch
):
    """Staff are refused, GET is not served, and restore review refuses."""
    from parishkit.stewardship.accounts import critical_event_views as views

    add_rules(auth_service.store, address("staff@example.org", ("staff",)))
    browser, login = signed_in()
    assert login.status_code == 302
    critical(Event.SOURCE_INVALID)
    with web():
        assert browser.get(ROUTE).status_code == 405
        assert browser.post(ROUTE).status_code == 403
    with monkeypatch.context() as patched:
        patched.setattr(
            views,
            "coherent_configuration",
            lambda store: SimpleNamespace(restore_review_required=True),
        )
        assert acknowledge(browser).status_code == 503
    staff = signed_in_as(google, "staff@example.org", "staff-subject")
    assert BANNER not in home(staff)
    assert acknowledge(staff).status_code == 403
    assert not CriticalEventAcknowledgement.objects.exists()
    assert audits() == 0
    assert BANNER in home(browser)

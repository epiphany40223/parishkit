"""The repeat rules' live date list on the restricted web login (#2092).

New scheduled email's and the date-change review's Sends Repeating post the
dates their rule gives to ``schedule_repeat_dates``, which answers the list
drawn by the shared schedule preview. It saves nothing, needs the pages'
Configure capability, and refuses fields the page script never posts.
"""

import pytest
from django.urls import reverse

from parishkit.stewardship.accounts.request_models import ConfigurationChangeRequest
from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.audit.models import AuditEvent
from parishkit.stewardship.deployment import ServiceRole

from .auth_builders import signed_in
from .report_builders import set_user, users_form
from .test_background_grants_postgresql import task_login

pytestmark = pytest.mark.django_db(transaction=True)

ZONE = "America/New_York"
FIELDS = {
    "zone": ZONE,
    "campaign_zone": ZONE,
    "time": "09:00",
    "date": ["2054-10-19", "2054-10-26"],
    "reason": ["", "taken"],
}


def post(browser, data, *, csrf=True):
    """POST ``data`` to the list's address, as the page script does
    (without its CSRF header when ``csrf`` is false)."""
    header = (
        {"HTTP_X_CSRFTOKEN": browser.cookies["pk_admin_csrf"].value} if csrf else {}
    )
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        return browser.post(reverse("admin:schedule_repeat_dates"), data, **header)


def recorded():
    """What the list must never change: the configuration, change requests
    and audit entries."""
    return (
        SystemConfiguration.objects.get().active_configuration.digest,
        ConfigurationChangeRequest.objects.count(),
        AuditEvent.objects.count(),
    )


def test_the_list_is_drawn_by_the_shared_preview(live_response_service, google):
    """An Administrator gets the fragment, uncached; nothing is saved or
    audited, a field or date form the page never posts is refused (400),
    and a post without the CSRF header is refused (403)."""
    browser, _ = signed_in()
    before = recorded()
    answer = post(browser, FIELDS)
    assert answer.status_code == 200, answer.content
    assert answer["Cache-Control"] == "no-store"
    text = answer.content.decode()
    assert text.startswith('<ul class="occurrence-list">')
    assert "Monday, October 19, 2054, <time" in text
    assert "not added: another email is already scheduled then" in text
    assert post(browser, FIELDS | {"other": "x"}).status_code == 400
    assert post(browser, FIELDS | {"reason": [""]}).status_code == 400
    unusual = FIELDS | {"date": ["20541019", "2054-10-26"]}
    assert post(browser, unusual).status_code == 400
    assert post(browser, FIELDS, csrf=False).status_code == 403
    assert recorded() == before


def test_staff_may_not_read_the_list(live_response_service, google):
    """Without Configure the list is refused (403)."""
    store = live_response_service.service.store
    users_form(store)
    set_user(store, "staff@example.org", "staff")
    google[0]["email"] = "staff@example.org"
    staff, _ = signed_in()
    assert post(staff, FIELDS).status_code == 403

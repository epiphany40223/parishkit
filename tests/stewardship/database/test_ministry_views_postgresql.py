"""Real signed Google sessions read the Ministries page, which changes nothing.

The local Ministry activity and its Review activation / Review inactivation
flow are gone (#1267): the page lists the ParishSoft catalog and whether
each Ministry is in the current campaign, and refuses every POST.
"""

import pytest
from django.test import Client

from parishkit.stewardship.accounts.request_models import ConfigurationChangeRequest
from parishkit.stewardship.deployment import ServiceRole

from ..policy_factory import address
from ..test_source_corpus import source
from .auth_builders import signed_in
from .campaign_builders import change
from .test_background_grants_postgresql import task_login
from .test_source_families_postgresql import (
    publish,
    source_singletons,  # noqa: F401
)

pytestmark = pytest.mark.django_db(transaction=True)
URL = "/admin/parish/ministries/"


def post(browser, values, query=""):
    """Post with the real CSRF cookie, as a form on the page once did."""
    return browser.post(
        URL + query,
        values | {"csrfmiddlewaretoken": browser.cookies["pk_admin_csrf"].value},
    )


def test_the_page_lists_the_catalog_and_changes_nothing(auth_service, google):
    """The catalog shows with no activity, selection, review or flow steps."""
    publish(source())
    browser, _ = signed_in()
    listing = browser.get(URL)
    assert listing.status_code == 200
    body = listing.content.decode()
    assert "Choir" in body and "In current campaign" in body
    for retired in (
        "Review activation",
        "Review inactivation",
        "data-select-row",
        "settings-review",
        "Inactive",
    ):
        assert retired not in body, retired
    assert 'class="flow-steps"' not in body
    assert not ConfigurationChangeRequest.objects.exists()


@pytest.mark.parametrize(
    "values",
    [
        {"action": "preview", "ministry_duid": "4", "active": "no"},
        {"action": "confirm", "preview": "anything"},
        {},
    ],
)
def test_every_post_is_refused(auth_service, google, values):
    """The retired review and apply posts have no handler: 405, no request."""
    publish(source())
    browser, _ = signed_in()
    assert post(browser, values).status_code == 405
    assert not ConfigurationChangeRequest.objects.exists()


@pytest.mark.parametrize("role", ["staff", "ministry_leader"])
def test_non_admin_cannot_read_the_page(auth_service, google, role):
    """The page stays an Administrator's, as Campaign settings is."""
    publish(source())
    row = address("reader@example.org", roles=(role,))
    store = auth_service.store
    change(
        store,
        store.active(),
        store.active().version_id,
        [{"operation": "add", "section": "login_rules", **row}],
    )
    google[0]["email"] = "reader@example.org"
    browser, _ = signed_in()
    assert browser.get(URL).status_code == 403


def test_anonymous_callers_are_refused(auth_service, google):
    """No session, no page."""
    publish(source())
    assert Client().get(URL).status_code == 403


def test_empty_search_is_clear(auth_service, google):
    """A search that finds nothing says so."""
    publish(source())
    browser, _ = signed_in()
    response = browser.get(URL + "?q=absent")
    assert response.status_code == 200 and b"No Ministries match" in response.content


@pytest.mark.parametrize(
    "query",
    [
        "?page=word",
        "?page=-1",
        "?size=7",
        # The retired activity filter and sort, and a change's status, are
        # refused like any other unknown parameter (no legacy URLs).
        "?state=active",
        "?state=inactive",
        "?sort=active",
        # A fixed id: a random one would change the test's collected name,
        # which the image's collection must match (test_compose).
        "?request=7b0f3a52-9c4e-4d1a-8f6b-2e5d9a1c4b37",
        "?q=a&q=b",
        "?extra=1",
        "?q=" + "x" * 201,
    ],
)
def test_invalid_filters_fail_closed(auth_service, google, query):
    """Malformed, out-of-range or retired filters are client errors."""
    publish(source())
    browser, _ = signed_in()
    assert browser.get(URL + query).status_code == 400


def test_real_web_role_can_read_the_catalog(auth_service, google):
    """Exercise the actual web SQL inventory, not the test database owner's rights."""
    publish(source())
    browser, _ = signed_in()
    with task_login(ServiceRole.WEB):
        assert browser.get(URL).status_code == 200


def two_ministries():
    """The standard source plus a second Ministry without members."""
    data = source()
    data.ministry_types[5] = {"id": 5, "name": "Lectors"}
    data.ministry_type_memberships[5] = {"membership": []}
    return data


def test_ministry_table_pages_and_keeps_filters(auth_service, google):
    """The shared navigator pages the table and a page past the end shows the last."""
    publish(two_ministries())
    browser, _ = signed_in()
    body = browser.get(URL + "?size=25&page=9").content.decode()
    assert "Showing 1–2 of 2" in body and 'name="page" value="1"' in body
    body = browser.get(URL + "?q=Lect&size=all").content.decode()
    assert "Lectors" in body and "Choir" not in body
    assert 'name="q" value="Lect"' in body
    # Search is by name only: a Ministry DUID finds nothing (#1123).
    body = browser.get(URL + "?q=5&size=all").content.decode()
    assert "No Ministries match these filters." in body and "Lectors" not in body

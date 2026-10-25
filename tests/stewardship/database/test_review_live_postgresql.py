"""Review regions that update themselves, over HTTP (#2174).

Each review region sends the items it drew (``live=key.token,...``) in one
passive GET of its page's own address. These tests read each page as its
reader does, take the pairs it drew, change an item through another
session, and check the region's read: nothing (and no audit, and no
sign-in renewal) while nothing changed, then only the changed item, drawn
with its new stage, whatever stage it moved to, recorded once as an
automatic view.
"""

import re
from dataclasses import asdict

import pytest
from django.urls import reverse

from parishkit.stewardship.accounts.models import PortalSession
from parishkit.stewardship.audit.models import AuditContext, AuditEvent
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.responses.models import AdditionalInformationItem
from parishkit.stewardship.web.review_live import item_key

from ..test_financial_answers import CHECK, OPTIONS, OTHER
from .auth_builders import signed_in
from .report_builders import revoke_admin
from .response_builders import activate_response_service
from .test_background_grants_postgresql import task_login
from .test_census_pipeline_review_postgresql import decide, prepared
from .test_csrf_namespaces_postgresql import page_token
from .test_financial_report_postgresql import pledge
from .test_financial_source_postgresql import financial_source
from .test_hub_actions_postgresql import dialog_fields, icon
from .test_information_followup_postgresql import search
from .test_limitations_report_postgresql import submit_live
from .test_ministry_exports_postgresql import leader
from .test_ministry_followup_postgresql import requests
from .test_ministry_reports_postgresql import LEADERS, prepare, setup
from .test_ministry_responses_postgresql import ministry_source, start
from .test_report_workspace_postgresql import read as get
from .test_response_http_postgresql import load_form
from .test_roster_entries_postgresql import resolved
from .test_runtime_auth_grants_postgresql import web_login
from .test_source_families_postgresql import promote
from .test_weekly_observation_postgresql import respond

pytestmark = pytest.mark.django_db(transaction=True)

CENSUS = "/admin/reports/census/"
DRAWN = re.compile(r'data-live-item="([^"]+)" data-live-token="([^"]+)"')
PASSIVE = {"HTTP_X_PARISHKIT_READ": "passive"}


def drawn(page):
    """The items a page drew, as ``{key: token}``."""
    return dict(DRAWN.findall(page))


def check(browser, address, items, headers=PASSIVE):
    """The region's read of ``address`` with ``items``: (status, body text).

    ``headers`` are the read's request headers: the passive one by default,
    as ui-v1.js sends it.
    """
    pairs = ",".join(f"{key}.{token}" for key, token in items.items())
    joiner = "&" if "?" in address else "?"
    response, body = get(browser, f"{address}{joiner}live={pairs}", **headers)
    return response.status_code, body.decode()


def views(event_type):
    """Each audited view's context of ``event_type``, oldest first."""
    return [
        AuditContext.objects.get(event=event).context
        for event in AuditEvent.objects.filter(event_type=event_type).order_by(
            "created_at"
        )
    ]


def last_activity():
    """Every sign-in's last activity: a renewal moves one."""
    return dict(PortalSession.objects.values_list("pk", "last_activity_at"))


def admin_browser():
    """The Administrator's signed-in browser."""
    browser, login = signed_in()
    assert login.status_code == 302
    return browser


def test_census_changes_check_brings_a_change_made_elsewhere(response_service, google):
    """Census changes' table (To review) checks its four rows: nothing while
    none changed, unaudited and passive; once another session accepts one,
    only that row, now Ready to update though the page lists To review, and
    one automatic view counting it."""
    harness, admin, _staff, rows = prepared(response_service)
    browser = admin_browser()
    response, body = get(browser, CENSUS)
    assert response.status_code == 200
    items = drawn(body.decode())
    assert len(items) == 4
    viewed = len(views("census_changes_viewed"))
    before = last_activity()
    assert check(browser, CENSUS, items) == (200, "")
    assert last_activity() == before
    assert len(views("census_changes_viewed")) == viewed
    first = rows["first_name"]
    decide(harness, admin, [first], "accept")
    status, page = check(browser, CENSUS, items)
    assert status == 200
    assert list(drawn(page)) == [item_key(first.pk)]
    row = page.split(f'id="census-row-{first.pk}"')[1].split("</tr>")[0]
    assert '<span class="status-word">Ready to update</span>' in row
    assert "Changed by someone else" in row
    assert last_activity() == before
    assert views("census_changes_viewed")[viewed:] == [
        {"outcome": "succeeded", "count": 1, "automatic": True}
    ]
    # The answer's row then matches: the next check is empty again.
    items[item_key(first.pk)] = drawn(page)[item_key(first.pk)]
    assert check(browser, CENSUS, items) == (200, "")


@pytest.mark.parametrize(
    "address",
    [
        # Only the drawn items: no filter or table choice beside them.
        f"{CENSUS}?stage=all&live=",
        # Pairs of the wrong shape.
        f"{CENSUS}?live=short.x",
    ],
)
def test_a_malformed_check_is_refused(response_service, google, address):
    """A read the region would never send is the page's 400."""
    prepared(response_service)
    browser = admin_browser()
    response, _ = get(browser, address, **PASSIVE)
    assert response.status_code == 400


def test_the_hub_tab_checks_its_rows(response_service, google):
    """The Family tab reads its own address with its drawn rows: nothing
    while they are unchanged; the page once another session accepted the
    home address, which it draws with its new stage."""
    harness, admin, _staff, rows = prepared(response_service)
    home = rows["home_address"]
    family_id = home.submission.family_id
    browser = admin_browser()
    address = f"{reverse('admin:timeline', args=[family_id])}?tab=family"
    response, body = get(browser, address)
    assert response.status_code == 200
    page = body.decode()
    assert f'data-live-items="{address}"' in page
    items = drawn(page)
    assert item_key(home.pk) in items
    viewed = len(views("family_timeline_viewed"))
    assert check(browser, address, items) == (200, "")
    assert len(views("family_timeline_viewed")) == viewed
    decide(harness, admin, [home], "accept")
    status, page = check(browser, address, items)
    assert status == 200
    assert drawn(page)[item_key(home.pk)] != items[item_key(home.pk)]
    row = page.split(f'id="hub-family-census-row-{home.pk}"')[1].split("</tr>")[0]
    assert "is-selected" in row
    assert views("family_timeline_viewed")[viewed:] == [
        {"outcome": "succeeded", "automatic": True}
    ]


def test_review_work_counts_check_their_cards(response_service, google):
    """Home's Review work cards check their counts: nothing while they hold;
    once a change is accepted elsewhere, the panels, whose To review card
    now counts one fewer, and one automatic dashboard view."""
    harness, admin, _staff, rows = prepared(response_service)
    browser = admin_browser()
    response, body = get(browser, "/admin/")
    assert response.status_code == 200
    page = body.decode()
    region = page.split('data-review-work data-live-items="/admin/"')[1]
    items = drawn(region)
    to_review = item_key("To review", "census")
    assert to_review in items
    viewed = len(views("dashboard_viewed"))
    assert check(browser, "/admin/", items) == (200, "")
    assert len(views("dashboard_viewed")) == viewed
    decide(harness, admin, [rows["first_name"]], "accept")
    status, page = check(browser, "/admin/", items)
    assert status == 200
    assert drawn(page)[to_review] != items[to_review]
    assert views("dashboard_viewed")[viewed:] == [
        {"outcome": "succeeded", "automatic": True}
    ]


def changed_through(browser, page, prefix, item_id, action, extra=None):
    """Post ``action`` on one row of ``page`` from ``browser``, as its icon
    and its stream page's decision dialog would."""
    row_id = f"{prefix}-row-{item_id}"
    route, fields = dialog_fields(page, f"{prefix}-decide")
    chosen = fields | icon(page, row_id, action) | {"note": ""} | (extra or {})
    response, body = search(browser, route, chosen)
    assert response.status_code == 200, body
    return route


def stream_check(browser, route, prefix, item_id, action):
    """Read ``route``, check its drawn rows, take ``action`` on ``item_id``
    and check again: the answer holds that row alone, as it now is."""
    response, body = get(browser, route)
    assert response.status_code == 200
    page = body.decode()
    items = drawn(page)
    assert item_key(item_id) in items
    assert check(browser, route, items) == (200, "")
    changed_through(browser, page, prefix, item_id, action)
    status, fresh = check(browser, route, items)
    assert status == 200
    assert list(drawn(fresh)) == [item_key(item_id)]
    return fresh.split(f'id="{prefix}-row-{item_id}"')[1].split("</tr>")[0]


def test_additional_information_checks_its_rows(live_response_service, google):
    """An Ignore is brought in, its row Ignored though the page lists To
    review; the view is recorded once more, as automatic."""
    harness = live_response_service
    item = AdditionalInformationItem.objects.get(
        submission=respond(harness, "Please call about the choir")
    )
    browser = admin_browser()
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        row = stream_check(
            browser,
            reverse("admin:information_queue"),
            "information",
            item.pk,
            "ignore",
        )
    assert '<span class="status-word">Ignored</span>' in row
    assert views("information_viewed")[-1] == {
        "outcome": "succeeded",
        "automatic": True,
    }


def test_limitations_check_their_rows(response_service, google):
    """A Member's cannot serve answer, ignored, is brought in Ignored."""
    harness = response_service
    start(harness)
    harness = activate_response_service(harness)
    submit_live(harness, cannot_serve=True)
    browser = admin_browser()
    route = reverse("admin:limitations_report")
    response, body = get(browser, route)
    assert response.status_code == 200
    item_id = re.search(r'<tr id="limitations-row-([^"]+)"', body.decode())[1]
    row = stream_check(browser, route, "limitations", item_id, "ignore")
    assert '<span class="status-word">Ignored</span>' in row
    assert views("talents_report_viewed")[-1]["automatic"] is True


def test_ministry_requests_check_their_rows(response_service, google):
    """Ministry requests' check reads its rows at every stage, in the
    reader's scope, with the remembered view's token in its address."""
    harness, _admin, _join, leave = resolved(response_service)
    route = reverse("admin:ministry_report")
    reader = admin_browser()
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        response, body = get(reader, route)
    assert response.status_code == 200
    page = body.decode()
    address = re.search(r'data-live-items="([^"]+)"', page)[1].replace("&amp;", "&")
    assert address.startswith(f"{route}?queue=")
    items = drawn(page)
    assert item_key(leave.pk) in items
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        assert check(reader, address, items) == (200, "")
        changed_through(reader, page, "requests", leave.pk, "accept")
        status, fresh = check(reader, address, items)
    assert status == 200
    assert list(drawn(fresh)) == [item_key(leave.pk)]
    row = fresh.split(f'id="requests-row-{leave.pk}"')[1].split("</tr>")[0]
    assert '<span class="status-word">To enter by hand</span>' in row
    # A redrawn row's links keep the reader's remembered view.
    assert address.split("?")[1] in row.replace("&amp;", "&")


def test_pledges_check_their_rows(response_service, google):
    """Pledges' check brings an Accept made elsewhere in, though the page
    lists To review."""
    harness = response_service
    financial_source(harness, modules=["financial"], options=map(asdict, OPTIONS))
    harness = activate_response_service(harness)
    with web_login():
        response = pledge(
            harness, load_form(harness), shares={CHECK: "", OTHER: "Stock gift"}
        )
    route = reverse("admin:pledges_to_enter")
    reader = admin_browser()
    result, body = get(reader, route)
    assert result.status_code == 200
    page = body.decode()
    items = drawn(page)
    assert list(items) == [item_key(response.pk)]
    assert check(reader, route, items) == (200, "")
    changed_through(reader, page, "pledges", response.pk, "accept")
    status, fresh = check(reader, route, items)
    assert status == 200
    assert list(drawn(fresh)) == [item_key(response.pk)]
    assert "Changed by someone else" in fresh


def test_a_check_without_the_passive_header_never_extends_the_sign_in(
    response_service, google
):
    """A request naming ``live`` is a check whatever its headers say: one
    sent without the passive header leaves the sign-in's last activity as
    it was, while a plain page load renews it."""
    harness, admin, _staff, rows = prepared(response_service)
    browser = admin_browser()
    response, body = get(browser, CENSUS)
    assert response.status_code == 200
    items = drawn(body.decode())
    before = last_activity()
    assert check(browser, CENSUS, items, headers={}) == (200, "")
    assert last_activity() == before
    decide(harness, admin, [rows["first_name"]], "accept")
    status, page = check(browser, CENSUS, items, headers={})
    assert status == 200 and list(drawn(page)) == [item_key(rows["first_name"].pk)]
    assert last_activity() == before
    # The page itself, loaded by the reader, renews it.
    response, _ = get(browser, CENSUS)
    assert response.status_code == 200
    assert last_activity() != before


def test_a_check_after_sign_out_is_refused(response_service, google):
    """Once the reader signed out, the region's check is refused, and its
    answer draws no row."""
    prepared(response_service)
    browser = admin_browser()
    response, body = get(browser, CENSUS)
    assert response.status_code == 200
    items = drawn(body.decode())
    token = page_token(browser, "/admin/")
    ended = browser.post("/admin/logout", {"csrfmiddlewaretoken": token})
    assert ended.status_code == 302
    status, page = check(browser, CENSUS, items)
    assert status == 403
    assert "data-live-item" not in page


def test_a_check_after_the_role_was_removed_is_refused(response_service, google):
    """Staff whose sign-in rule was removed since the page was drawn: the
    check is refused and draws no row."""
    harness, _admin, _staff, _rows = prepared(response_service)
    google[0]["email"] = "staff@example.org"
    google[0]["sub"] = "synthetic-staff"
    browser = admin_browser()
    response, body = get(browser, CENSUS)
    assert response.status_code == 200
    items = drawn(body.decode())
    assert items
    revoke_admin(harness, "staff@example.org")
    status, page = check(browser, CENSUS, items)
    assert status == 403
    assert "data-live-item" not in page


def test_a_leaders_check_keeps_to_their_ministries(response_service, google):
    """A leader of Ministry 9 checks Ministry requests: a check naming the
    Ministry 4 request too (outside their scope) brings only Ministry 9's.
    Once ParishSoft lists them leading Ministry 4 alone, a check of the
    Ministry 9 request brings nothing for it."""
    harness = setup(response_service)
    join, leave = requests()
    reader, _head, _rule, _ = leader(harness, google)
    route = reverse("admin:ministry_report")
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        response, body = get(reader, route)
    assert response.status_code == 200
    page = body.decode()
    address = re.search(r'data-live-items="([^"]+)"', page)[1].replace("&amp;", "&")
    items = drawn(page)
    assert item_key(join.pk) in items and item_key(leave.pk) not in items
    # Stale tokens for both: only the leader's own Ministry's row answers.
    stale = {item_key(join.pk): "aaaaaa", item_key(leave.pk): "aaaaaa"}
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        status, fresh = check(reader, address, stale)
    assert status == 200
    assert list(drawn(fresh)) == [item_key(join.pk)]
    # ParishSoft now lists the leader leading Ministry 4 only.
    snapshot, claim = prepare(ministry_source(), LEADERS | {"leader@example.org": [4]})
    promote(snapshot, claim, harness.campaign, harness.rings)
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        status, fresh = check(reader, address, {item_key(join.pk): "aaaaaa"})
    assert status == 200
    assert item_key(join.pk) not in drawn(fresh)

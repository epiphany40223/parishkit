"""Capability-filtered Admin navigation, operational banners and passive work views."""

import re
from uuid import uuid4

import pytest
from django.db import connection
from django.db.models import F
from django.test import Client

from parishkit.stewardship.accounts.models import PortalSession, PortalUser
from parishkit.stewardship.audit.models import AuditEvent
from parishkit.stewardship.audit.services import operational
from parishkit.stewardship.campaigns.models import Campaign
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.jobs.phases import TaskPhase
from parishkit.stewardship.observability import Event

from ..log_samples import sample as log_sample
from ..policy_factory import address, assignment
from . import test_parish_views_postgresql as parish
from .auth_builders import signed_in
from .campaign_builders import add_draft, change
from .test_background_grants_postgresql import task_login
from .test_taskrun_postgresql import act, new

pytestmark = pytest.mark.django_db(transaction=True)
STEPS = ["Make changes", "Review", "Apply"]
GO_LIVE = [
    "Check readiness",
    "Testing cleanup",
    "Family links",
    "Confirm Production",
    "Activation",
]


@pytest.mark.parametrize("role", ["administrator", "staff", "ministry_leader"])
def test_navigation_and_testing_banner_match_current_capabilities(
    auth_service, google, role
):
    """Ministry leaders never receive code links or Admin-only configuration details."""
    store = auth_service.store
    add_draft(store, store.active(), uuid4())
    if role != "administrator":
        change(
            store,
            store.active(),
            uuid4(),
            [
                {
                    "operation": "add",
                    "section": "login_rules",
                    **address("reader@example.org", roles=(role,)),
                }
            ],
        )
        google[0]["email"] = "reader@example.org"
    browser, _ = signed_in()
    response = browser.get("/admin/")
    assert response.status_code == 200
    body = response.content
    assert b"Testing mode" in body
    assert (b"test@example.org" in body) == (role == "administrator")
    assert (b'href="/admin/parish/ministries/"' in body) == (role == "administrator")
    # Who else holds access is offered to Administrators only.
    assert (b'href="/admin/users"' in body) == (role == "administrator")
    assert (b"Parish settings" in body) == (role == "administrator")
    # The combined logs are offered to Administrators only.
    assert (b'href="/admin/system/logs/"' in body) == (role == "administrator")
    assert (b"Background work" in body) == (role == "administrator")
    assert (b"Family directory" in body) == (role != "ministry_leader")
    # The Postal outreach page merged into the Family directory (#202).
    assert b"Postal outreach" not in body
    assert body.count(b'id="session-warning"') == 1
    assert body.count(b'id="session-expired"') == 1
    assert (b"Family participation" in body) == (role != "ministry_leader")
    # Pages and emails, and Dates and mail schedules, are first-class entries.
    assert (b"Pages and emails" in body) == (role == "administrator")
    assert (b"Dates and mail schedules" in body) == (role == "administrator")
    # The manual ParishSoft refresh has its own sidebar entry (#196).
    menu = body[body.index(b'aria-label="Administration"') :]
    menu = menu[: menu.index(b"</nav>")]
    assert (b'href="/admin/parish/parishsoft-refresh/"' in menu) == (
        role == "administrator"
    )
    assert body.count(b'aria-label="Administration"') == 1
    # Home is the current page; the home trail is just "Home", so no trail.
    assert b'aria-label="Breadcrumb"' not in body
    assert AuditEvent.objects.filter(event_type="dashboard_viewed").count() == 1


def test_every_admin_page_offers_sign_out_in_the_menu(auth_service, google):
    """The menu ends with a CSRF-protected Sign out form that ends the session."""
    store = auth_service.store
    _, owner, _ = add_draft(store, store.active(), uuid4())
    browser, _ = signed_in()
    campaign = owner["id"]
    for path in (
        "/admin/",
        f"/admin/campaign/{campaign}/content",
        "/admin/system/logs/",
    ):
        body = browser.get(path).content.decode()
        menu = body[body.index('aria-label="Administration"') :]
        menu = menu[: menu.index("</nav>")]
        assert '<form method="post" action="/admin/logout">' in menu
        assert 'name="csrfmiddlewaretoken"' in menu and "Sign out" in menu
        # No page carries the old product footer.
        assert "Stewardship and census" not in body
    row = PortalSession.objects.get()
    response = browser.post(
        "/admin/logout", {"csrfmiddlewaretoken": browser.cookies["pk_admin_csrf"].value}
    )
    assert response.status_code == 302
    row.refresh_from_db()
    assert row.revoked_at is not None


def test_campaign_pages_show_breadcrumbs_and_highlight_the_sidebar(
    auth_service, google
):
    """Campaign › Pages and emails › <email>, with the sidebar entry current."""
    store = auth_service.store
    result, owner, _ = add_draft(store, store.active(), uuid4())
    campaign = owner["id"]
    browser, _ = signed_in()
    catalog = f"/admin/campaign/{campaign}/content"
    body = browser.get(catalog).content
    assert b'aria-label="Breadcrumb"' in body
    assert b'<span aria-current="page">Pages and emails</span>' in body
    assert f'<a href="{catalog}" aria-current="page">'.encode() in body
    assert b"admin-section is-current" in body
    edit = browser.get(f"{catalog}/email/initial")
    assert edit.status_code == 200
    # The trail links back to the catalog and names the email being edited.
    assert f'<li><a href="{catalog}">Pages and emails</a></li>'.encode() in edit.content
    assert b'<span aria-current="page">Initial invitation</span>' in edit.content
    # On the child page the catalog entry marks the location, not the page.
    assert f'<a href="{catalog}" aria-current="true">'.encode() in edit.content


def flow_steps(body):
    """The step indicator's labels and the current step, from a rendered page."""
    body = body.decode()
    if 'class="flow-steps"' not in body:
        return None
    block = body[body.index('class="flow-steps"') :]
    block = block[: block.index("</ol>")]
    labels = re.findall(r'class="flow-step-label">([^<]+)<', block)
    current = re.search(
        r'aria-current="step">.*?class="flow-step-label">([^<]+)<', block
    )
    return labels, current.group(1)


def test_a_settings_change_shows_its_steps_and_leads_back_to_its_editor(
    auth_service, google
):
    """Edit, review and apply are shown, and the status page returns to Parish settings.

    The status page learns its origin from this sign-in's session, so another
    sign-in (or a bookmark after sign-out) still gets a trail, just not the
    editor's.
    """
    store = auth_service.store
    add_draft(store, store.active(), uuid4())
    browser, _ = signed_in()
    content = f"/admin/campaign/{Campaign.objects.get().pk}/content/email/initial"
    assert flow_steps(browser.get(content).content) == (STEPS, "Make changes")
    assert flow_steps(browser.get(parish.URL).content) == (STEPS, "Make changes")
    # Pages outside a flow show no indicator.
    assert flow_steps(browser.get("/admin/system/background/").content) is None
    # A review with nothing changed is refused back to the form, step 1.
    unchanged = parish.post(browser, parish.fields(store))
    assert unchanged.status_code == 400
    assert flow_steps(unchanged.content) == (STEPS, "Make changes")
    review = parish.post(browser, parish.fields(store, name="Renamed Parish"))
    assert flow_steps(review.content) == (STEPS, "Review")
    confirmed = parish.post(
        browser, {"action": "confirm", "preview": parish.token(review)}
    )
    assert confirmed.status_code == 302
    status = browser.get(confirmed["Location"])
    assert status.status_code == 200
    body = status.content
    assert flow_steps(body) == (STEPS, "Apply")
    assert f'<li><a href="{parish.URL}">Parish settings</a></li>'.encode() in body
    assert b'<span aria-current="page">Change status</span>' in body
    assert f'<a href="{parish.URL}">Return to Parish settings</a>'.encode() in body
    # The sidebar marks the editor the change came from.
    assert f'<a href="{parish.URL}" aria-current="true">'.encode() in body
    # A different sign-in does not know the origin: the trail is just Home.
    other, _ = signed_in()
    body = other.get(confirmed["Location"]).content
    assert b'<a href="/admin/">Return to Home</a>' in body
    trail = body[body.index(b'aria-label="Breadcrumb"') :]
    assert b"Parish settings" not in trail[: trail.index(b"</nav>")]


def test_anonymous_and_family_pages_do_not_gain_admin_chrome(auth_service, google):
    """Rendering public/login/error content never derives a menu from runtime alone."""
    for path in ("/", "/admin/login"):
        response = Client().get(path)
        assert b'href="/admin/parish/ministries/"' not in response.content
        assert b"test@example.org" not in response.content


def test_background_html_shows_exact_progress_and_does_not_renew_idle(
    auth_service, google
):
    """Status shares API authorization and uses exact numeric formatting."""
    task = act(new(), "claim")
    act(task, "progress", progress=(1000, 3000), phase=TaskPhase.FETCHING)
    browser, _ = signed_in()
    activity = PortalSession.objects.get().last_activity_at
    response = browser.get("/admin/system/background/")
    assert response.status_code == 200
    assert b"1,000 out of 3,000 (33.3%)" in response.content
    assert b"data-local-instant" in response.content
    assert PortalSession.objects.get().last_activity_at == activity
    assert response["Cache-Control"] == "no-store"


@pytest.mark.parametrize("role", ["staff", "ministry_leader"])
def test_background_html_is_not_accessible_through_direct_non_admin_url(
    auth_service, google, role
):
    """The visible menu is not the security boundary."""
    store = auth_service.store
    change(
        store,
        store.active(),
        uuid4(),
        [
            {
                "operation": "add",
                "section": "login_rules",
                **address("reader@example.org", roles=(role,)),
            }
        ],
    )
    google[0]["email"] = "reader@example.org"
    browser, _ = signed_in()
    assert browser.get("/admin/system/background/").status_code == 403


def test_background_pagination_preserves_filtered_scope(auth_service, google):
    """Following a page link must not unexpectedly broaden a completed-task view."""
    for _ in range(2):
        act(act(new(), "claim"), "complete")
    browser, _ = signed_in()
    response = browser.get("/admin/system/background/?state=succeeded&size=1")
    assert response.status_code == 200
    following = b"state=succeeded&amp;sort=-created&amp;size=1&amp;page=2"
    assert following in response.content
    assert b'value="succeeded" selected' in response.content
    # Shared navigator and table styling; a windowed page shows a bounded total.
    assert b'class="table-nav"' in response.content
    assert b'class="data-table"' in response.content
    assert b"Showing 1\xe2\x80\x931 of 2" in response.content
    assert b"Page 1 of 2" in response.content


def test_dashboard_failure_links_open_the_html_task_page(auth_service, google):
    """A dashboard navigation link must not strand the Admin on a JSON response."""
    task = act(act(new(), "claim"), "permanent_failure")
    browser, _ = signed_in()
    with task_login(ServiceRole.WEB):
        response = browser.get("/admin/")
        assert response.status_code == 200
        path = f"/admin/system/background/{task.run_id}/"
        assert f'href="{path}"'.encode() in response.content
        detail = browser.get(path)
        assert detail.status_code == 200
        assert detail["Content-Type"].startswith("text/html")


@pytest.mark.parametrize("revoke", [False, True])
def test_dashboard_render_releases_work_lock_and_rechecks_authority(
    auth_service, google, monkeypatch, revoke
):
    """Template work cannot serialize task claims or bypass a concurrent revocation."""
    from parishkit.stewardship.accounts import authentication

    browser, _ = signed_in()
    original = authentication.render
    observed = []

    def render(request, *args, **kwargs):
        """Probe the actual backend before rendering the captured observation."""
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT EXISTS(SELECT 1 FROM pg_locks WHERE pid=pg_backend_pid() "
                "AND locktype='advisory' AND classid=736220 AND objid=1 "
                "AND objsubid=2 AND granted)"
            )
            assert cursor.fetchone()[0] is False
        observed.append(True)
        result = original(request, *args, **kwargs)
        if revoke:
            PortalUser.objects.update(disabled=True, version=F("version") + 1)
        return result

    monkeypatch.setattr(authentication, "render", render)
    response = browser.get("/admin/")
    assert response.status_code == (403 if revoke else 200)
    assert observed == [True]
    assert AuditEvent.objects.filter(event_type="dashboard_viewed").count() == (
        0 if revoke else 1
    )
    if revoke:
        assert b"test@example.org" not in response.content


def test_restricted_web_role_can_read_dashboard_and_status_pages(auth_service, google):
    """Dashboard reads use the same real grants as container startup admission."""
    browser, _ = signed_in()
    with task_login(ServiceRole.WEB):
        assert browser.get("/admin/").status_code == 200
        assert browser.get("/admin/system/background/").status_code == 200


@pytest.mark.parametrize("error", [ValueError, TypeError, LookupError])
def test_unexpected_dashboard_failures_use_the_global_closed_error_boundary(
    auth_service, google, monkeypatch, error
):
    """Programming errors remain 500, but never expose private exception values."""
    from parishkit.stewardship.accounts import admin_dashboard

    browser, _ = signed_in()
    browser.raise_request_exception = False

    def failed(*args):
        """Simulate a defect, not known retryable configuration unavailability."""
        raise error("PRIVATE-DASHBOARD-FAILURE")

    monkeypatch.setattr(admin_dashboard, "summary", failed)
    response = browser.get("/admin/")
    assert response.status_code == 500
    assert response.content == b"The request could not be completed.\n"
    assert b"PRIVATE-DASHBOARD-FAILURE" not in response.content
    assert b"test@example.org" not in response.content


def test_critical_event_warning_is_persistent_and_admin_only(auth_service, google):
    """The warning reports recent critical events without exposing their context."""
    operational(Event.TASK_FAILED, level="CRITICAL", **log_sample(Event.TASK_FAILED))
    browser, _ = signed_in()
    for path in ("/admin/", "/admin/system/background/", "/admin/parish/settings/"):
        response = browser.get(path)
        assert b"Critical problems in the past 24 hours" in response.content
    store = auth_service.store
    change(
        store,
        store.active(),
        uuid4(),
        [
            {
                "operation": "add",
                "section": "login_rules",
                **address("reader@example.org", roles=("staff",)),
            }
        ],
    )
    google[0]["email"] = "reader@example.org"
    browser, _ = signed_in()
    assert b"Critical problems" not in browser.get("/admin/").content


# The Admin chrome's queries on one report page: (NAV-1, now). NAV-1 is this
# same test run on 8ad079ba, before the menu of Home and six groups
# (ADM-12.02 to .04). Building the menu reads only what the chrome already
# loads, so no role may pay more (admin-portal spec, "Stable menu shape").
# The Administrator's count with a campaign fell by one: the campaign and its
# configuration are now read together (admin_context._current_campaign).
CHROME_QUERIES = {
    ("administrator", False): (4, 4),
    ("administrator", True): (7, 6),
    ("staff", False): (2, 2),
    ("staff", True): (4, 4),
    ("ministry_leader", False): (2, 2),
    ("ministry_leader", True): (4, 4),
}


@pytest.mark.parametrize("draft", [False, True])
@pytest.mark.parametrize("role", ["administrator", "staff", "ministry_leader"])
def test_building_the_menu_adds_no_query(
    auth_service, google, monkeypatch, role, draft
):
    """The chrome's query count on a fresh report page is the same as NAV-1's.

    The real context processor is wrapped where the template engine keeps
    it, so the count is of the one call the page's own render makes, with
    nothing loaded beforehand by the test.
    """
    from django.template import engines
    from django.test.utils import CaptureQueriesContext

    from parishkit.stewardship.accounts import admin_context, family_maintenance

    # The Family portal switch is cached per process for a few seconds;
    # start cold, so the count does not depend on the tests run before.
    monkeypatch.setitem(family_maintenance._cache, "at", None)
    store = auth_service.store
    if draft:
        add_draft(store, store.active(), uuid4())
    if role != "administrator":
        records = [address("reader@example.org", roles=(role,))]
        if role == "ministry_leader":
            records.append(assignment("reader@example.org", ministry=9))
        change(
            store,
            store.active(),
            uuid4(),
            [{"operation": "add", "section": "login_rules", **r} for r in records],
        )
        google[0]["email"] = "reader@example.org"
    engine = engines["django"].engine
    counts = []

    def counted(request):
        """The real chrome, with the queries its one call runs counted."""
        with CaptureQueriesContext(connection) as chrome:
            result = admin_context.portal_chrome(request)
        if result:
            counts.append(len(chrome.captured_queries))
        return result

    processors = tuple(
        counted if processor is admin_context.portal_chrome else processor
        for processor in engine.template_context_processors
    )
    assert counted in processors
    monkeypatch.setitem(engine.__dict__, "template_context_processors", processors)
    browser, _ = signed_in()
    # A report page every role may open. The draft has no Ministry module,
    # so it renders its "no reports" page rather than redirecting.
    response = browser.get("/admin/ministry-reports/")
    assert response.status_code == 200
    assert b'aria-label="Administration"' in response.content
    before, now = CHROME_QUERIES[(role, draft)]
    assert counts == [now] and now <= before, counts

"""WCAG automated checks plus keyboard, mobile, timezone and activity behavior."""

from datetime import timedelta
from urllib.parse import parse_qs, urlsplit

import pytest

from parishkit.stewardship.accounts.templatetags.stewardship import parish_time
from parishkit.stewardship.web import dates

from .conftest import NOW, load_collections, no_script_context
from .waits import eventually, hidden, recorded, visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)


@pytest.mark.parametrize("width", [320, 1280])
@pytest.mark.parametrize("path", ["/schedule-preview", "/clone-preview"])
def test_schedule_preview_distinguishes_parish_intent_from_browser_time(
    page, component_origin, width, path
):
    """Keep parish civil time visible while converting the resolved UTC instant."""
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(component_origin + path)
    instant = page.locator("time[data-local-instant]").first
    assert instant.get_attribute("datetime") == "2054-10-01T13:00:00+00:00"
    assert "6:00" in instant.inner_text() and "PDT" in instant.inner_text()
    text = page.locator("main").inner_text()
    # The parish's intended time uses the parish style's clock (the default
    # style is 12-hour), computed from the same filter the template uses.
    with dates.using(dates.DEFAULT):
        parish_clock = parish_time("09:00:00")
    assert parish_clock == "9:00 AM"
    assert parish_clock in text and "America/New_York" in text
    assert "not a list of who will receive it" in text
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")


@pytest.mark.parametrize("clock_skew_hours", [-48, 0, 48])
def test_setup_progress_only_polls_visible_correlated_work_and_stops_at_deadline(
    page, component_origin, clock_skew_hours
):
    """Visible-page polling carries only CSRF and stops at local deadlines."""
    from playwright.sync_api import expect

    page.clock.install(time=NOW + timedelta(hours=clock_skew_hours))
    requests = []

    def observe(route):
        """The synthetic server response has no Family values or renewal promises."""
        requests.append(route.request)
        route.fulfill(
            json={
                "server_now": NOW.isoformat(),
                "task_id": urlsplit(route.request.url).path.split("/")[-1],
                "task_state": "running",
                "setup_state": "loading",
                "phase": "staging",
                "active": True,
                "current": 1234,
                "total": 5000,
                "collections": load_collections(7),
                "idle_at": (NOW + timedelta(minutes=30)).isoformat(),
                "watchdog_at": (NOW + timedelta(hours=2)).isoformat(),
                "absolute_at": (NOW + timedelta(hours=12)).isoformat(),
            }
        )

    page.route("**/admin/setup/source/*?format=json", observe)
    page.goto(component_origin + "/setup-source-progress")
    expect(page.locator("[data-task-counts]")).to_contain_text("1,234")
    assert page.locator("[data-task-counts]").inner_text() == "1,234 of 5,000 (25%)"
    visible(page.locator("[data-load-records]"))
    assert "Done" in page.locator('[data-load-phase="fetching"]').inner_text()
    assert "In progress" in page.locator('[data-load-phase="staging"]').inner_text()
    assert len(requests) == 1 and requests[0].method == "POST"
    assert requests[0].post_data.startswith("csrfmiddlewaretoken=")
    assert "&" not in requests[0].post_data
    page.clock.set_system_time(NOW + timedelta(days=7))
    page.evaluate(
        "Object.defineProperty(document, 'hidden', {configurable:true, get:()=>true})"
    )
    page.clock.fast_forward(60000)
    assert len(requests) == 1
    page.evaluate(
        "Object.defineProperty(document, 'hidden', {configurable:true, get:()=>false})"
    )
    with page.expect_response("**/admin/setup/source/*?format=json"):
        page.clock.fast_forward(15000)
    assert len(requests) == 2
    page.clock.fast_forward(13 * 60 * 60 * 1000)
    assert len(requests) == 2


def test_setup_progress_terminal_response_stops_automatic_posts(page, component_origin):
    """Expired setup does not look successful or keep sending renewal requests."""
    from playwright.sync_api import expect

    page.clock.install(time=NOW)
    requests = []

    def expired(route):
        """A terminal server verdict is final even before the local timer expires."""
        requests.append(route.request)
        route.fulfill(
            json={
                "server_now": NOW.isoformat(),
                "task_id": urlsplit(route.request.url).path.split("/")[-1],
                "task_state": "cancelled",
                "setup_state": "expired",
                "phase": "fetching",
                "active": False,
                "current": 0,
                "total": 0,
                "collections": load_collections(2),
                "idle_at": (NOW + timedelta(minutes=30)).isoformat(),
                "watchdog_at": (NOW + timedelta(hours=2)).isoformat(),
                "absolute_at": (NOW + timedelta(hours=12)).isoformat(),
            }
        )

    page.route("**/admin/setup/source/*?format=json", expired)
    page.goto(component_origin + "/setup-source-progress")
    expect(page.locator("[data-task-state]")).to_have_js_property(
        "textContent", "cancelled"
    )
    page.clock.fast_forward(60000)
    assert len(requests) == 1
    page.route(
        "**/setup-source-progress",
        lambda route: route.fulfill(content_type="text/html", body="Manual progress"),
    )
    with page.expect_request("**/setup-source-progress") as submitted:
        page.get_by_role("button", name="Check progress now").click()
    assert submitted.value.method == "POST"


def test_setup_progress_shows_each_collection_then_offers_continue(
    page, component_origin
):
    """The download lists finished collections; completion reveals Continue."""
    from playwright.sync_api import expect

    page.clock.install(time=NOW)
    state = {"done": False}

    def respond(route):
        """First a download midway through the rosters, then a finished load."""
        done = state["done"]
        route.fulfill(
            json={
                "server_now": NOW.isoformat(),
                "task_id": urlsplit(route.request.url).path.split("/")[-1],
                "task_state": "succeeded" if done else "running",
                "setup_state": "collecting" if done else "loading",
                "phase": "validating" if done else "fetching",
                "active": not done,
                "current": 9000 if done else 0,
                "total": 9000 if done else 0,
                "collections": load_collections(7)
                if done
                else load_collections(5, finished=57, expected=213),
                "started_at": (NOW - timedelta(seconds=75)).isoformat(),
                "heartbeat_at": (NOW - timedelta(seconds=3)).isoformat(),
                "idle_at": (NOW + timedelta(minutes=30)).isoformat(),
                "watchdog_at": (NOW + timedelta(hours=2)).isoformat(),
                "absolute_at": (NOW + timedelta(hours=12)).isoformat(),
            }
        )

    page.route("**/admin/setup/source/*?format=json", respond)
    page.goto(component_origin + "/setup-source-progress")
    expect(page.locator("[data-progress-elapsed]")).to_contain_text("1 min")
    assert "downloading" in page.locator("[data-progress-summary]").inner_text()
    assert "3 seconds ago" in page.locator("[data-progress-quiet]").inner_text()
    families = page.locator('[data-collection="families"]')
    assert "1,234 loaded" in families.inner_text()
    assert "setup-load-done" in families.get_attribute("class")
    rosters = page.locator('[data-collection="ministry_roster"]')
    assert "57 of 213 loaded" in rosters.inner_text()
    assert "setup-load-active" in rosters.get_attribute("class")
    assert "Waiting" in page.locator('[data-collection="funds"]').inner_text()
    # No meaningless "0 of 0" count while downloading.
    assert page.locator("[data-load-records]").is_hidden()
    assert page.locator("[data-progress-done]").is_hidden()
    state["done"] = True
    with page.expect_response("**/admin/setup/source/*?format=json"):
        page.clock.fast_forward(15000)
    expect(page.locator("[data-progress-done]")).to_have_js_property("hidden", False)
    assert (
        "Parish data is loaded" in page.locator("[data-progress-summary]").inner_text()
    )
    visible(page.get_by_role("link", name="Continue to the next step"))
    assert page.locator("[data-task-counts]").inner_text() == "9,000 of 9,000 (100%)"
    assert "Done" in page.locator('[data-load-phase="validating"]').inner_text()


def test_csp_permits_the_fixed_google_form_destination(page, component_origin):
    """Check the allowed destination with first-request interception only.

    Interception may not see later requests in a redirect chain. No fixture
    sends such a redirect; the OAuth HTTP suite verifies its destination.
    """
    page.route(
        "https://accounts.google.com/**",
        lambda route: route.fulfill(
            status=200,
            content_type="text/html",
            body="<h1>Synthetic Google sign-in</h1>",
        ),
    )
    page.goto(component_origin + "/login")
    page.locator("form").evaluate(
        "form => form.action = 'https://accounts.google.com/o/oauth2/v2/auth'"
    )
    page.get_by_role("button", name="Sign in with Google").click()
    page.wait_for_url("https://accounts.google.com/**")
    visible(page.get_by_role("heading", name="Synthetic Google sign-in"))


def test_csp_blocks_an_unrelated_form_destination(page, component_origin):
    """The permitted Google origin must not imply arbitrary cross-origin posting."""
    requests = []

    def foreign(route):
        requests.append(route.request.url)
        route.fulfill(status=200, body="unexpected navigation")

    page.route("https://unrelated.example/**", foreign)
    page.goto(component_origin + "/login")
    page.evaluate("""() => {
        window.formViolations = [];
        document.addEventListener('securitypolicyviolation', event => {
            window.formViolations.push(event.effectiveDirective);
        });
    }""")
    page.locator("form").evaluate(
        "form => form.action = 'https://unrelated.example/collect'"
    )
    # Chromium schedules a navigation before CSP cancels it; do not wait for
    # that nonexistent navigation, but do wait for the actual violation event.
    page.get_by_role("button", name="Sign in with Google").click(no_wait_after=True)
    eventually(page, "() => window.formViolations.includes('form-action')")
    assert requests == []
    assert page.url == component_origin + "/login"


@pytest.mark.parametrize(
    "path",
    [
        "/login",
        "/family-login",
        "/family",
        "/errors",
        "/home",
        "/codes",
        "/availability",
        "/denied",
        "/denied-code",
        "/denied-link",
        "/denied-unavailable",
        "/ministries",
        "/ministry-preview",
        "/parish-settings",
        "/parish-preview",
        "/configuration-request",
        "/go-live-steps",
        "/background",
        "/deliveries",
        "/delivery",
        "/delivery-refusals",
        "/delivery-refusal",
        "/delivery-error",
        "/campaign-settings",
        "/campaign-preview",
        "/share-settings",
        "/share-preview",
        "/presence",
        "/background-task",
        "/export-cleanup-task",
        "/export-cleanup-stale",
        "/export-cleanup-conflict",
        "/export-cleanup-invalid",
        "/content-settings",
        "/content-preview",
        "/content-history",
        "/campaign-mail",
        "/campaign-mail-unknown",
        "/campaign-mail-pending",
        "/schedule-settings",
        "/schedule-preview",
        "/clone-settings",
        "/clone-preview",
        "/integrations",
        "/integration-settings",
        "/integration-preview",
        "/backup-key",
        "/backup-key-proof",
        "/credential-status",
        "/credential-selection",
        "/branding-settings",
        "/branding-preview",
        "/hosted-files",
        "/hosted-file-unavailable",
        "/setup",
        "/setup-parish",
        "/setup-branding",
        "/setup-credential",
        "/setup-campaign",
        "/setup-content-edit",
        "/setup-shares",
        "/setup-schedules",
        "/setup-schedules-mail",
        "/setup-schedules-error",
        "/setup-preview",
        "/setup-confirmation",
        "/setup-confirmation-unready",
        "/setup-finalization",
        "/setup-installation",
        "/setup-mail-test",
        "/setup-mail-test-step",
        "/setup-mail-test-done",
        "/setup-slack-test",
        "/setup-access",
        "/setup-mail",
        "/setup-slack",
        "/setup-testing",
        "/setup-source-progress",
    ],
)
@pytest.mark.parametrize("width", [320, 1280])
def test_components_accessible_and_responsive(
    page, component_origin, axe_source, path, width
):
    """Automated checks supplement, not replace, human screen-reader review."""
    page.set_viewport_size({"width": width, "height": 900})
    failures = []
    page.on("pageerror", lambda error: failures.append(str(error)))
    page.goto(component_origin + path)
    if path in {"/branding-settings", "/branding-preview"}:
        assert page.locator(".branding-preview").evaluate_all(
            "images => images.length > 0 && images.every("
            "image => image.complete && image.naturalWidth > 0)"
        )
    assert not failures
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    page.evaluate(axe_source)
    violations = page.evaluate("""async () => (await axe.run(document, {
        runOnly: {type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21aa', 'wcag22aa']}
    })).violations.map(({id, impact, nodes}) => ({
        id, impact, targets: nodes.map(n => n.target)
    }))""")
    assert violations == []


def test_export_cleanup_keyboard_form_posts_only_csrf_and_replay_identity(
    page, component_origin
):
    """Recovery is an accessible explicit form action, never a GET or auto-poll."""
    page.route(
        "**/admin/background/tasks/*/retry-export-cleanup",
        lambda route: route.fulfill(content_type="text/html", body="Retry queued"),
    )
    page.goto(component_origin + "/export-cleanup-task")
    button = page.get_by_role("button", name="Retry export cleanup", exact=True)
    button.focus()
    with page.expect_request(
        "**/admin/background/tasks/*/retry-export-cleanup"
    ) as submitted:
        button.press("Enter")
    assert submitted.value.method == "POST"
    assert set(parse_qs(submitted.value.post_data)) == {
        "csrfmiddlewaretoken",
        "request_key",
    }


def test_setup_confirmation_requires_acknowledgement_and_stores_no_draft(
    page, component_origin
):
    """An explicit checkbox gates submission and no browser storage persists setup."""
    page.goto(component_origin + "/setup-confirmation")
    checkbox = page.get_by_role("checkbox")
    assert not checkbox.is_checked()
    assert not page.locator("form.panel").evaluate("form => form.checkValidity()")
    checkbox.check()
    assert page.locator("form.panel").evaluate("form => form.checkValidity()")
    assert page.evaluate("localStorage.length + sessionStorage.length") == 0
    page.goto(component_origin + "/setup-confirmation-unready")
    page.get_by_role("checkbox").check()
    assert page.get_by_role(
        "button", name="Check readiness and finish setup"
    ).is_disabled()


def test_campaign_mail_preview_is_passive_and_shows_uncertainty(page, component_origin):
    """Preview/reload never submits mail, and in-flight work disables another send."""
    sends = []
    page.on(
        "request",
        lambda request: (
            sends.append(request.url)
            if request.method == "POST" and "campaign-mail" in request.url
            else None
        ),
    )
    page.goto(component_origin + "/campaign-mail-unknown")
    assert "uncertain" in page.get_by_role("alert").inner_text()
    assert not page.get_by_role("checkbox").is_checked()
    assert "2026-09-10T12:00:00" not in page.locator("time").inner_text()
    page.reload()
    assert not page.get_by_role("checkbox").is_checked()
    page.goto(component_origin + "/campaign-mail-pending")
    assert page.get_by_role("button", name="Send this test email").is_disabled()
    assert not sends


def test_skip_link_and_error_summary_focus(page, component_origin):
    """Keyboard users can reach the main landmark and exact failing field."""
    page.goto(component_origin + "/login")
    page.keyboard.press("Tab")
    assert page.locator(":focus").inner_text() == "Skip to content"
    page.keyboard.press("Enter")
    assert page.locator(":focus").get_attribute("id") == "main"
    page.goto(component_origin + "/errors")
    assert page.locator(":focus").get_attribute("data-error-summary") == ""
    page.get_by_role("link", name="Check the Family code.").click()
    assert page.locator(":focus").get_attribute("id") == "family-code"


@pytest.mark.parametrize("path", ["/content-settings", "/setup-content-edit"])
def test_visual_content_editor_never_executes_source_or_pasted_markup(
    page, component_origin, path
):
    """Visual edits sync source; raw source waits for the server sanitizer."""
    page.goto(component_origin + path)
    editor = page.locator("[data-content-editor]")
    visible(editor)
    editor.fill("A visual edit")
    assert "A visual edit" in page.locator('textarea[name="html"]').input_value()
    editor.evaluate("""node => {
        const range = document.createRange();
        range.selectNodeContents(
            document.createTreeWalker(node, NodeFilter.SHOW_TEXT).nextNode()
        );
        const selection = window.getSelection();
        selection.removeAllRanges(); selection.addRange(range);
    }""")
    page.get_by_role("button", name="Bold", exact=True).click()
    assert (
        "<strong>A visual edit</strong>"
        in page.locator('textarea[name="html"]').input_value()
    )
    page.locator("[data-html-source] summary").click()
    page.locator('textarea[name="html"]').fill(
        '<img src=x onerror="window.unsafe=true">'
    )
    # The pane stays visible but read-only until the server's sanitized
    # preview answers (unanswered here); raw source never reaches it.
    visible(editor)
    assert editor.get_attribute("contenteditable") == "false"
    assert editor.locator("img").count() == 0
    assert page.evaluate("window.unsafe === undefined")
    page.reload()
    editor = page.locator("[data-content-editor]")
    editor.evaluate("""node => {
        node.focus();
        const range = document.createRange(); range.selectNodeContents(node);
        const selection = window.getSelection();
        selection.removeAllRanges(); selection.addRange(range);
        // Firefox intentionally strips synthetic ClipboardEvent data. Exercise
        // the application's paste handler with an explicit read-only fixture.
        const event = new Event('paste', {bubbles: true, cancelable: true});
        Object.defineProperty(event, 'clipboardData', {value: {
            getData: type => type === 'text/plain' ? '<b>plain only</b>' :
                '<img src=x onerror="window.unsafe=true">'
        }});
        node.dispatchEvent(event);
    }""")
    assert editor.inner_text() == "<b>plain only</b>"
    assert editor.locator("img, b").count() == 0


def test_timestamp_and_passive_presence_never_keep_session_alive(
    page, component_origin
):
    """Advance the browser clock without sleeping or synthesizing user activity."""
    page.clock.install(time=NOW)
    attempts = []
    page.route(
        "**/family/keepalive",
        lambda route: (attempts.append(route.request), route.abort()),
    )
    page.goto(component_origin + "/family")
    # The deadline itself is not shown (#207); only the warning and expiry are.
    assert page.get_by_text("Session deadline").count() == 0
    page.clock.fast_forward(56 * 60 * 1000)
    visible(page.locator("#session-warning"))
    assert attempts == []
    page.clock.fast_forward(5 * 60 * 1000)
    visible(page.locator("#session-expired"))
    hidden(page.locator("#session-warning"))
    assert attempts == []


def test_activity_keepalive_is_empty_csrf_protected_and_bounded(page, component_origin):
    """Editing claims no draft data; rejected keepalives do not renew the timer."""
    page.clock.install(time=NOW)
    attempts = []

    def reject(route):
        """Observe actual fetch bytes and deny the synthetic untrusted activity."""
        attempts.append(route.request)
        route.fulfill(status=403, body="Unavailable")

    page.route("**/family/keepalive", reject)
    page.goto(component_origin + "/family")
    page.keyboard.press("Tab")
    page.clock.fast_forward(6 * 60 * 1000)
    eventually(page, "() => document.readyState", "complete")
    recorded(page, attempts, 1)
    assert attempts[0].method == "POST" and not attempts[0].post_data
    assert attempts[0].headers["x-csrftoken"] == "a" * 64
    page.clock.fast_forward(60 * 60 * 1000)
    visible(page.locator("#session-expired"))
    assert len(attempts) == 1


@pytest.mark.parametrize("failure", ["http", "transport"])
def test_failed_keepalive_retains_activity_for_a_bounded_retry(
    page, component_origin, failure
):
    """No additional keystroke is needed after one failed activity transmission."""
    page.clock.install(time=NOW)
    attempts = []

    def reply(route):
        """The second empty claim succeeds with a synthetic future deadline."""
        attempts.append(route.request)
        if len(attempts) == 1:
            if failure == "transport":
                route.abort("failed")
            else:
                route.fulfill(status=503, body="Unavailable")
        else:
            route.fulfill(json={"idle_deadline": "2026-09-10T14:00:00Z"})

    page.route("**/family/keepalive", reply)
    page.goto(component_origin + "/family")
    page.keyboard.press("Tab")
    page.clock.fast_forward(6 * 60 * 1000)
    recorded(page, attempts, 1)
    page.clock.fast_forward(4 * 60 * 1000)
    page.wait_for_timeout(50)
    assert len(attempts) == 1  # Five minutes between attempts, even on failure.
    page.clock.fast_forward(2 * 60 * 1000)
    recorded(page, attempts, 2)
    assert all(not request.post_data for request in attempts)


def test_admin_activity_never_uses_family_keepalive(page, component_origin):
    """The Admin dialog warns and expires without renewing through Family endpoints."""
    from playwright.sync_api import expect

    page.clock.install(time=NOW)
    attempts = []
    page.route(
        "**/family/keepalive",
        lambda route: (attempts.append(route.request), route.abort()),
    )
    renewals = []
    page.route(
        "**/admin/session/renew",
        lambda route: (renewals.append(route.request), route.abort()),
    )
    page.goto(component_origin + "/home")
    page.keyboard.press("Tab")
    # Typing and passive status reads never renew; only "Stay signed in" does.
    page.clock.fast_forward(56 * 60 * 1000)
    expect(page.locator("#session-warning")).to_be_visible()
    expect(page.locator("[data-session-message]")).to_contain_text(
        "signed out in 4 minutes because of inactivity"
    )
    expect(page.locator("[data-session-stay]")).to_be_focused()
    # The spoken countdown lives inside the modal dialog, which makes the rest
    # of the page inert, so screen readers can announce it (#391 L7).
    announce = page.locator("dialog.session-dialog [data-session-announce]")
    expect(announce).to_have_attribute("aria-live", "polite")
    expect(announce).to_contain_text("signed out in 4 minutes")
    page.clock.fast_forward(5 * 60 * 1000)
    expect(page.locator("#session-expired")).to_be_visible()
    expect(page.locator("#session-warning")).to_be_hidden()
    assert attempts == [] and renewals == []


def test_admin_stay_signed_in_renews_and_closes_the_dialog(page, component_origin):
    """The explicit renewal posts CSRF and adopts the server's new idle deadline."""
    from playwright.sync_api import expect

    page.clock.install(time=NOW)
    renewals = []
    later = NOW + timedelta(hours=2)

    def renew(route):
        """Grant a renewed idle deadline, as the real endpoint would."""
        renewals.append(route.request)
        route.fulfill(
            json={
                "state": "active",
                "server_now": (NOW + timedelta(minutes=56)).isoformat(),
                "idle_deadline": later.isoformat(),
                "absolute_deadline": (NOW + timedelta(hours=12)).isoformat(),
            }
        )

    page.route("**/admin/session/renew", renew)
    page.goto(component_origin + "/home")
    page.clock.fast_forward(56 * 60 * 1000)
    dialog = page.locator("dialog.session-dialog")
    expect(dialog).to_be_visible()
    page.keyboard.press("Escape")  # Escape never silently dismisses the warning.
    expect(dialog).to_be_visible()
    page.locator("[data-session-stay]").click()
    expect(dialog).to_be_hidden()
    assert len(renewals) == 1
    assert renewals[0].method == "POST"
    assert renewals[0].headers["x-csrftoken"] == "a" * 64
    page.clock.fast_forward(10 * 60 * 1000)
    expect(dialog).to_be_hidden()


def test_admin_dialog_defers_to_activity_in_another_tab(page, component_origin):
    """A passive status read showing a later deadline suppresses the warning."""
    from playwright.sync_api import expect

    page.clock.install(time=NOW)
    statuses = []

    def status(route):
        """Another tab renewed the session; this read renews nothing itself."""
        statuses.append(route.request)
        route.fulfill(
            json={
                "state": "active",
                "server_now": (NOW + timedelta(minutes=55)).isoformat(),
                "idle_deadline": (NOW + timedelta(hours=1, minutes=50)).isoformat(),
                "absolute_deadline": (NOW + timedelta(hours=12)).isoformat(),
            }
        )

    page.route("**/admin/session/status", status)
    page.goto(component_origin + "/home")
    page.clock.fast_forward(56 * 60 * 1000)
    page.evaluate("() => true")  # one round trip after the clock jump
    expect(page.locator("dialog.session-dialog")).to_be_hidden()
    assert statuses and all(request.method == "GET" for request in statuses)


def test_admin_dialog_near_absolute_limit_offers_sign_in_only(page, component_origin):
    """Renewal cannot pass the absolute limit, so the dialog does not offer it."""
    from playwright.sync_api import expect

    page.clock.install(time=NOW)
    page.route(
        "**/admin/session/status",
        lambda route: route.fulfill(
            json={
                "state": "active",
                "server_now": NOW.isoformat(),
                "idle_deadline": (NOW + timedelta(minutes=4)).isoformat(),
                "absolute_deadline": (NOW + timedelta(minutes=4)).isoformat(),
            }
        ),
    )
    page.goto(component_origin + "/home")
    page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
    page.clock.fast_forward(2000)
    expect(page.locator("#session-warning")).to_be_visible()
    expect(page.locator("[data-session-message]")).to_contain_text("time limit")
    expect(page.locator("[data-session-stay]")).to_be_hidden()
    expect(page.locator("[data-session-signin]")).to_be_visible()


def test_javascript_disabled_retains_admin_form_and_family_explanation(
    browser_engine, component_origin
):
    """No silent failure: Admin core forms stay ordinary POST, Family explains JS."""
    context = no_script_context(browser_engine)
    try:
        page = context.new_page()
        page.goto(component_origin + "/login")
        visible(page.get_by_role("button", name="Sign in with Google"))
        assert page.locator("form").get_attribute("method") == "post"
        page.goto(component_origin + "/family")
        visible(page.locator("noscript"))
        assert "enable JavaScript" in page.locator("noscript").inner_text()
    finally:
        context.close()


def test_parish_editor_retains_native_form_validation_and_timezone_scope(
    page, component_origin
):
    """Profile forms retain native validation and prospective timezone guidance."""
    page.goto(component_origin + "/parish-settings")
    assert "Existing campaign timezones" in page.locator("main").inner_text()
    name = page.get_by_label("Parish name")
    name.fill("")
    assert not name.evaluate("field => field.checkValidity()")
    name.fill("A renamed parish")
    assert name.evaluate("field => field.checkValidity()")
    visible(page.get_by_role("button", name="Preview changes"))


def test_ministry_preview_preserves_operational_indicators(page, component_origin):
    """Configuration pages do not replace the persistent Admin navigation/header."""
    for path in ("/ministries", "/ministry-preview", "/configuration-request"):
        page.goto(component_origin + path)
        visible(page.locator("[data-background-indicator]"))
        visible(page.get_by_role("complementary", name="Testing mode"))


def test_shared_table_selection_enables_bulk_actions(page, component_origin):
    """Select all chooses every row on the page and enables the bulk buttons."""
    page.goto(component_origin + "/ministries")
    review = page.get_by_role("button", name="Review inactivation")
    assert review.is_disabled()
    page.get_by_role("button", name="Select all").click()
    rows = page.locator("input[data-select-row]")
    assert rows.evaluate_all("nodes => nodes.every(node => node.checked)")
    visible(page.get_by_text("2 selected"))
    assert review.is_enabled()
    page.get_by_role("button", name="Clear selection").click()
    assert review.is_disabled()
    rows.first.check()
    header = page.get_by_label("Select every Ministry on this page")
    assert header.evaluate("node => node.indeterminate")
    visible(page.get_by_text("Showing 1–2 of 2").first)


def test_sortable_headings_state_and_name_their_direction(page, component_origin):
    """Sort headings are plain links: aria-sort marks the sorted column and
    each link's accessible name says which direction it will choose."""
    page.goto(component_origin + "/background")
    created = page.get_by_role("columnheader", name="Created")
    assert created.get_attribute("aria-sort") == "descending"
    assert page.locator("th[aria-sort]").count() == 1
    link = page.get_by_role("link", name="Created (sort ascending)")
    assert "sort=created" in link.get_attribute("href")
    task = page.get_by_role("link", name="Task (sort ascending)")
    assert "sort=task" in task.get_attribute("href")
    visible(page.get_by_text("Page 1 of 2").first)


def test_campaign_modules_hide_and_disable_unselected_fields(page, component_origin):
    """Conditional groups cannot accidentally post data from a disabled module."""
    page.goto(component_origin + "/campaign-settings")
    ministry = page.get_by_role("group", name="Ministry selections")
    financial = page.get_by_role("group", name="Financial periods and funds")
    assert not ministry.is_visible() and not financial.is_visible()
    page.get_by_label("Ministry stewardship").check()
    visible(ministry)
    assert page.get_by_label("Included Ministries").input_value() == "4"
    page.get_by_label("Financial stewardship").check()
    visible(financial)
    page.get_by_label("Upcoming financial period start").fill("2027-01-01")
    page.get_by_label("Financial stewardship").uncheck()
    posted = page.locator("[data-campaign-form]").evaluate(
        "form => Array.from(new FormData(form).keys())"
    )
    assert "financial_start" not in posted and "fund_duids" not in posted
    assert "ministry_duids" in posted


def test_campaign_modules_remain_usable_without_javascript(
    browser_engine, component_origin
):
    """Server validation remains available when progressive enhancement is absent."""
    context = no_script_context(browser_engine)
    try:
        page = context.new_page()
        page.goto(component_origin + "/campaign-settings")
        visible(page.get_by_role("group", name="Financial periods and funds"))
        assert page.get_by_label("Upcoming financial period start").is_enabled()
        visible(page.get_by_role("button", name="Preview changes"))
    finally:
        context.close()


def test_family_presence_is_visible_only_bounded_and_carries_no_answers(
    page, component_origin
):
    """Presence posts neither answers nor activity claims and stops after expiry."""
    page.clock.install(time=NOW)
    requests = []

    def observe(route):
        """Record the exact wire contract and finish its bounded passive request."""
        requests.append(route.request)
        route.fulfill(
            status=200, content_type="application/json", body='{"recorded":true}'
        )

    page.route("**/family/presence", observe)
    page.goto(component_origin + "/family")
    page.wait_for_load_state("networkidle")
    assert len(requests) == 1 and requests[0].post_data == "section=welcome"
    page.clock.fast_forward(29000)
    assert len(requests) == 1
    page.evaluate(
        "Object.defineProperty(document, 'hidden', {configurable:true, get:()=>true})"
    )
    page.clock.fast_forward(31000)
    assert len(requests) == 1
    page.evaluate(
        "Object.defineProperty(document, 'hidden', {configurable:true, get:()=>false})"
    )
    with page.expect_response("**/family/presence"):
        page.clock.fast_forward(30000)
    page.wait_for_load_state("networkidle")
    assert len(requests) == 2
    page.clock.fast_forward(5 * 60 * 60 * 1000)
    assert len(requests) == 2


def test_admin_presence_poll_is_passive_and_shows_service_failure(
    page, component_origin
):
    """Header polling is read-only and a failure is not represented as zero presence."""
    from playwright.sync_api import expect

    page.clock.install(time=NOW)
    requests = []

    def observe(route):
        """First return a count, then a retriable failure without private error text."""
        requests.append(route.request)
        route.fulfill(
            status=200 if len(requests) == 1 else 503,
            content_type="application/json",
            body='{"count":1234}',
        )

    page.route("**/admin/presence?format=count", observe)
    with page.expect_response("**/admin/presence?format=count"):
        page.goto(component_origin + "/home")
    expect(page.locator("[data-presence-count]")).to_have_js_property(
        "textContent", "1,234"
    )
    assert requests[0].method == "GET" and not requests[0].post_data
    with page.expect_response("**/admin/presence?format=count"):
        page.clock.fast_forward(30000)
    expect(page.locator("[data-presence-unavailable]")).to_have_js_property(
        "hidden", False
    )
    assert page.locator("[data-presence-count]").inner_text() == "1,234"


def axe_violations(page, axe_source):
    """Run the pinned axe scanner against the page's current state."""
    page.evaluate(axe_source)
    return page.evaluate("""async () => (await axe.run(document, {
        runOnly: {type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21aa', 'wcag22aa']}
    })).violations.map(({id, impact, nodes}) => ({
        id, impact, targets: nodes.map(n => n.target)
    }))""")


@pytest.mark.parametrize("width", [320, 1280])
def test_setup_stepper_is_compact_and_its_full_list_stays_accessible(
    page, component_origin, axe_source, width
):
    """A short summary by default; the ordered list opens from the keyboard."""
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(component_origin + "/setup-testing")
    stepper = page.locator(".setup-stepper")
    assert "Step 4 of 15: Testing recipient" in stepper.inner_text()
    assert "1 of 15 steps completed" in stepper.inner_text()
    # Far shorter than fifteen stacked cards, even at phone width.
    assert stepper.bounding_box()["height"] < (260 if width == 320 else 160)
    assert page.locator(".setup-steps").is_hidden()
    page.locator(".setup-stepper-list summary").focus()
    page.keyboard.press("Enter")
    visible(page.locator(".setup-steps"))
    current = page.locator('.setup-steps [aria-current="step"]')
    assert "Current step" in current.inner_text()
    assert "Completed" in page.locator(".setup-step-done").first.inner_text()
    links = page.locator(".setup-steps a")
    assert links.count() >= 2
    assert all(
        box["height"] >= 44
        for box in links.evaluate_all(
            "nodes => nodes.map(node => node.getBoundingClientRect().toJSON())"
        )
    )
    page.keyboard.press("Tab")
    assert page.evaluate("document.activeElement.closest('.setup-steps') !== null")
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    assert axe_violations(page, axe_source) == []


@pytest.mark.parametrize("width", [320, 1280])
def test_setup_track_segments_name_their_step_on_hover(page, component_origin, width):
    """Each decorative segment's title names its step and status, as the list does."""
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(component_origin + "/setup-testing")
    track = page.locator(".setup-track")
    before = track.bounding_box()
    titles = track.locator("span").evaluate_all("nodes => nodes.map(n => n.title)")
    steps = page.locator(".setup-steps li").evaluate_all(
        "items => items.map(item => ["
        "item.querySelector('.setup-step-label').lastChild.textContent.trim(),"
        "item.querySelector('.setup-step-status').textContent.trim()])"
    )
    assert len(titles) == len(steps) == 15
    for number, (title, (label, status)) in enumerate(
        zip(titles, steps, strict=True), 1
    ):
        assert title == f"Step {number} of 15: {label} — {status}", title
    assert any(title.endswith(" — Completed") for title in titles)
    assert "— Current step — " in titles[3]
    # The larger hover target is invisible: the track keeps its size.
    segment = track.locator("span").nth(3)
    segment.hover()
    assert page.evaluate("document.querySelector('.setup-track span:hover') !== null")
    assert track.bounding_box() == before
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")


def test_admin_sidebar_and_breadcrumbs_mark_the_current_page(page, component_origin):
    """Desktop shows the open sidebar; narrow screens collapse it behind Menu."""
    from playwright.sync_api import expect

    page.set_viewport_size({"width": 1440, "height": 900})
    page.goto(component_origin + "/ministries")
    sidebar = page.get_by_role("navigation", name="Administration")
    current = sidebar.locator('a[aria-current="page"]')
    visible(current)
    assert current.inner_text() == "Ministry activity"
    expect(sidebar.get_by_text("Menu")).to_be_hidden()
    trail = page.get_by_role("navigation", name="Breadcrumb")
    visible(trail.get_by_role("link", name="Home"))
    assert trail.locator('[aria-current="page"]').inner_text() == "Ministry activity"
    # The sidebar sits beside the content, not above it, on a wide screen.
    side = sidebar.bounding_box()
    main = page.locator("main").bounding_box()
    assert side["x"] + side["width"] <= main["x"] + 1
    page.set_viewport_size({"width": 390, "height": 844})
    page.reload()
    menu = sidebar.get_by_text("Menu")
    visible(menu)
    # The script collapses the menu on narrow screens once it has run.
    expect(current).to_be_hidden()
    menu.click()
    visible(current)


@pytest.mark.parametrize("width", [320, 1280])
def test_admin_sidebar_groups_are_labelled_headings_set_apart_from_links(
    page, component_origin, axe_source, width
):
    """Section headings name their link groups and look unlike the links."""
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(component_origin + "/admin-navigation")
    sidebar = page.get_by_role("navigation", name="Administration")
    menu = sidebar.locator("details")
    if not menu.evaluate("details => details.open"):
        sidebar.get_by_text("Menu").click()
    headings = sidebar.get_by_role("heading", level=2)
    assert headings.all_inner_texts() == [
        "CAMPAIGN",
        "PARISH AND INTEGRATIONS",
        "SYSTEM",
    ]
    for name, links in (
        ("Campaign", ["Campaign settings", "Mail schedules"]),
        ("Parish and integrations", ["Parish settings", "Ministry activity"]),
        ("System", ["Background work"]),
    ):
        group = sidebar.get_by_role("list", name=name, exact=True)
        assert group.get_by_role("link").all_inner_texts() == links

    def style(locator, *names):
        """The named computed style values of the first match."""
        return locator.first.evaluate(
            "(node, names) => names.map(name => getComputedStyle(node)[name])",
            list(names),
        )

    # Headings are smaller, uppercase, bold and not pointer-styled; links are
    # normal weight, and the current page is bolder still.
    link = sidebar.get_by_role("link", name="Campaign settings")
    heading = style(headings, "fontSize", "textTransform", "fontWeight", "cursor")
    link_size, link_weight = style(link, "fontSize", "fontWeight")
    assert heading[1:] == ["uppercase", "700", "default"]
    assert float(heading[0][:-2]) < float(link_size[:-2])
    assert link_weight == "400"
    current = sidebar.locator('a[aria-current="page"]')
    assert style(current, "fontWeight") == ["650"]
    # Every group after the first has a divider above it.
    sections = sidebar.locator(".admin-section")
    widths = sections.evaluate_all(
        "nodes => nodes.map(node => getComputedStyle(node).borderTopWidth)"
    )
    assert widths == ["0px", "1px", "1px"]
    # Links sit indented under their heading.
    assert link.bounding_box()["x"] > headings.first.bounding_box()["x"]
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    assert axe_violations(page, axe_source) == []


@pytest.mark.parametrize("width", [390, 1280])
def test_flow_steps_and_return_link_orient_a_configuration_change(
    page, component_origin, axe_source, width
):
    """The change status shows its steps and leads back to its editor (#196)."""
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(component_origin + "/configuration-request")
    steps = page.get_by_role("list", name="Steps")
    visible(steps)
    assert steps.locator("li").count() == 3
    current = steps.locator('[aria-current="step"]')
    assert current.count() == 1
    assert "Apply" in current.inner_text()
    # Completed steps say so in text, not only by color.
    assert steps.locator("li").first.text_content().endswith("(done)")
    trail = page.get_by_role("navigation", name="Breadcrumb")
    visible(trail.get_by_role("link", name="Parish settings"))
    visible(page.get_by_role("link", name="Return to Parish settings"))
    # The steps sit between the trail and the heading and never overflow.
    top = page.get_by_role("heading", level=1).bounding_box()["y"]
    assert trail.bounding_box()["y"] < steps.bounding_box()["y"] < top
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    assert axe_violations(page, axe_source) == []


@pytest.mark.parametrize("width", [390, 1280])
def test_go_live_steps_wrap_without_overflow(page, component_origin, axe_source, width):
    """All five go-live steps fit a phone by wrapping, never scrolling (#196)."""
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(component_origin + "/go-live-steps")
    steps = page.get_by_role("list", name="Steps")
    visible(steps)
    assert steps.locator("li").count() == 5
    assert steps.get_by_role("link").count() == 0
    current = steps.locator('[aria-current="step"]')
    assert current.count() == 1 and "Activation" in current.inner_text()
    for item in steps.locator("li").all():
        visible(item)
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    assert axe_violations(page, axe_source) == []


@pytest.mark.parametrize("width", [390, 1280])
def test_key_status_returns_to_its_integration(page, component_origin, width):
    """A key's status names its integration in the trail and leads back (#196)."""
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(component_origin + "/credential-status")
    trail = page.get_by_role("navigation", name="Breadcrumb")
    visible(trail.get_by_role("link", name="ParishSoft"))
    visible(page.get_by_role("link", name="Return to ParishSoft"))
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")


@pytest.mark.parametrize("width", [390, 1280])
def test_menu_sign_out_is_a_keyboard_reachable_csrf_post(page, component_origin, width):
    """Sign out ends the menu; on phones it sits behind the Menu disclosure."""
    from playwright.sync_api import expect

    page.route(
        "**/admin/logout",
        lambda route: route.fulfill(content_type="text/html", body="Signed out"),
    )
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(component_origin + "/home")
    menu = page.get_by_role("navigation", name="Administration")
    button = menu.get_by_role("button", name="Sign out", exact=True)
    if width < 900:
        # Narrow screens collapse the menu until the Admin opens it.
        expect(page.locator("[data-admin-menu]")).to_have_js_property("open", False)
        menu.locator("summary").click()
    visible(button)
    assert page.get_by_role("button", name="Sign out", exact=True).count() == 1
    assert page.get_by_text("Stewardship and census").count() == 0
    button.focus()
    with page.expect_request("**/admin/logout") as submitted:
        button.press("Enter")
    assert submitted.value.method == "POST"
    assert set(parse_qs(submitted.value.post_data)) == {"csrfmiddlewaretoken"}


def test_web_only_page_editor_has_no_plain_text_panel(page, component_origin):
    """Web-only pages (#259) show no plain-text controls; the editor still works."""
    page.goto(component_origin + "/setup-content-edit")
    editor = page.locator("[data-content-editor]")
    visible(editor)
    assert page.locator("[data-plain-text]").count() == 0
    assert page.locator('textarea[name="text"]').count() == 0
    assert page.locator('input[name="generate_text"]').count() == 0
    editor.fill("Welcome edit")
    assert "Welcome edit" in page.locator('textarea[name="html"]').input_value()


def test_refresh_task_page_says_what_it_is_doing(page, component_origin):
    """A refresh run names its kind, its step, its count and why it restarted."""
    page.goto(component_origin + "/background-task")
    visible(page.get_by_text("Full refresh", exact=True))
    visible(page.get_by_text("Downloading from ParishSoft…"))
    visible(page.get_by_text("1,000 of 4,000 records checked"))
    visible(page.get_by_text("server restarted", exact=False))
    assert page.get_by_text("Waiting for progress details").count() == 0

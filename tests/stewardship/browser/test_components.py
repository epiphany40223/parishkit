"""WCAG automated checks plus keyboard, mobile, timezone and activity behavior."""

from datetime import timedelta
from urllib.parse import parse_qs, urlsplit

import pytest

from .conftest import NOW

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
    assert instant.get_attribute("datetime") == "2026-10-01T13:00:00+00:00"
    assert "6:00" in instant.inner_text() and "PDT" in instant.inner_text()
    text = page.locator("main").inner_text()
    assert "09:00:00" in text and "America/New_York" in text
    assert "not recipient eligibility or permission to send" in text
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")


@pytest.mark.parametrize("clock_skew_hours", [-48, 0, 48])
def test_setup_progress_only_polls_visible_correlated_work_and_stops_at_deadline(
    page, component_origin, clock_skew_hours
):
    """Visible-page polling carries only CSRF and stops at local deadlines."""
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
                "phase": "fetching",
                "active": True,
                "current": 1234,
                "total": 5000,
                "idle_at": (NOW + timedelta(minutes=30)).isoformat(),
                "watchdog_at": (NOW + timedelta(hours=2)).isoformat(),
                "absolute_at": (NOW + timedelta(hours=12)).isoformat(),
            }
        )

    page.route("**/admin/setup/source/*?format=json", observe)
    page.goto(component_origin + "/setup-source-progress")
    page.wait_for_function(
        "() => document.querySelector('[data-task-counts]')"
        ".textContent.includes('1,234')"
    )
    assert page.locator("[data-task-counts]").inner_text() == "1,234 out of 5,000 (25%)"
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
                "idle_at": (NOW + timedelta(minutes=30)).isoformat(),
                "watchdog_at": (NOW + timedelta(hours=2)).isoformat(),
                "absolute_at": (NOW + timedelta(hours=12)).isoformat(),
            }
        )

    page.route("**/admin/setup/source/*?format=json", expired)
    page.goto(component_origin + "/setup-source-progress")
    page.wait_for_function(
        "() => document.querySelector('[data-task-state]').textContent === 'cancelled'"
    )
    page.clock.fast_forward(60000)
    assert len(requests) == 1
    page.route(
        "**/setup-source-progress",
        lambda route: route.fulfill(content_type="text/html", body="Manual progress"),
    )
    with page.expect_request("**/setup-source-progress") as submitted:
        page.get_by_role("button", name="Check source progress").click()
    assert submitted.value.method == "POST"


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
    assert page.get_by_role("heading", name="Synthetic Google sign-in").is_visible()


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
    page.wait_for_function("() => window.formViolations.includes('form-action')")
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
        "/credential-replace",
        "/credential-status",
        "/credential-selection",
        "/branding-settings",
        "/branding-preview",
        "/setup",
        "/setup-parish",
        "/setup-branding",
        "/setup-credential",
        "/setup-campaign",
        "/setup-content-edit",
        "/setup-shares",
        "/setup-schedules",
        "/setup-preview",
        "/setup-confirmation",
        "/setup-confirmation-unready",
        "/setup-finalization",
        "/setup-installation",
        "/setup-mail-test",
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
    assert page.get_by_role("button", name="Confirm and finish setup").is_disabled()


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
    assert editor.is_visible()
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
    assert not editor.is_visible()
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
    assert "6:00 AM PDT" in page.locator("time").inner_text()
    page.clock.fast_forward(56 * 60 * 1000)
    assert page.locator("#session-warning").is_visible()
    assert attempts == []
    page.clock.fast_forward(5 * 60 * 1000)
    assert page.locator("#session-expired").is_visible()
    assert not page.locator("#session-warning").is_visible()
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
    page.wait_for_function("document.readyState === 'complete'")
    assert len(attempts) == 1
    assert attempts[0].method == "POST" and not attempts[0].post_data
    assert attempts[0].headers["x-csrftoken"] == "a" * 64
    page.clock.fast_forward(60 * 60 * 1000)
    assert page.locator("#session-expired").is_visible()
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
    page.wait_for_timeout(50)
    assert len(attempts) == 1
    page.clock.fast_forward(4 * 60 * 1000)
    page.wait_for_timeout(50)
    assert len(attempts) == 1  # Five minutes between attempts, even on failure.
    page.clock.fast_forward(2 * 60 * 1000)
    page.wait_for_timeout(50)
    assert len(attempts) == 2
    assert all(not request.post_data for request in attempts)


def test_stale_csrf_keepalive_and_presence_recover_with_one_retry(
    page, component_origin
):
    """A csrf_failed reply installs its fresh token and retries without expiry."""
    page.clock.install(time=NOW)
    claims, beats = [], []

    def stale(token):
        """The server's CSRF failure body carrying a fresh page token."""
        return {"error": "csrf_failed", "csrf_token": token * 64}

    def keepalive(route):
        """Reject the stale page token once, then accept the refreshed one."""
        claims.append(route.request.headers["x-csrftoken"])
        if len(claims) == 1:
            route.fulfill(status=403, json=stale("b"))
        else:
            route.fulfill(json={"idle_deadline": "2026-09-10T14:00:00Z"})

    def presence(route):
        """Presence answers the same way when its token is stale."""
        beats.append(route.request.headers["x-csrftoken"])
        if beats[-1] == "a" * 64:
            route.fulfill(status=403, json=stale("c"))
        else:
            route.fulfill(json={"recorded": True})

    page.route("**/family/keepalive", keepalive)
    page.route("**/family/presence", presence)
    page.goto(component_origin + "/family")
    page.wait_for_function(
        "document.querySelector('[name=csrfmiddlewaretoken]').value === 'c'.repeat(64)"
    )
    assert beats == ["a" * 64, "c" * 64]
    page.keyboard.press("Tab")
    page.clock.fast_forward(6 * 60 * 1000)
    page.wait_for_function("document.readyState === 'complete'")
    page.wait_for_timeout(50)
    assert claims == ["c" * 64, "b" * 64]
    token = page.locator("#family-cancel [name=csrfmiddlewaretoken]")
    assert token.input_value() == "b" * 64
    assert page.locator("#session-expired").is_hidden()
    assert page.locator("#family-cancel").is_visible()


def test_admin_activity_never_uses_family_keepalive(page, component_origin):
    """The Admin clock warns and expires without renewing through Family endpoints."""
    page.clock.install(time=NOW)
    attempts = []
    page.route(
        "**/family/keepalive",
        lambda route: (attempts.append(route.request), route.abort()),
    )
    page.goto(component_origin + "/home")
    page.keyboard.press("Tab")
    page.clock.fast_forward(56 * 60 * 1000)
    assert page.locator("#session-warning").is_visible()
    page.clock.fast_forward(5 * 60 * 1000)
    assert page.locator("#session-expired").is_visible()
    assert attempts == []


def test_javascript_disabled_retains_admin_form_and_family_explanation(
    browser_engine, component_origin
):
    """No silent failure: Admin core forms stay ordinary POST, Family explains JS."""
    context = browser_engine.new_context(java_script_enabled=False)
    try:
        page = context.new_page()
        page.goto(component_origin + "/login")
        assert page.get_by_role("button", name="Sign in with Google").is_visible()
        assert page.locator("form").get_attribute("method") == "post"
        page.goto(component_origin + "/family")
        assert page.locator("noscript").is_visible()
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
    assert page.get_by_role("button", name="Preview changes").is_visible()


def test_ministry_preview_preserves_operational_indicators(page, component_origin):
    """Configuration pages do not replace the persistent Admin navigation/header."""
    for path in ("/ministries", "/ministry-preview", "/configuration-request"):
        page.goto(component_origin + path)
        assert page.locator("[data-background-indicator]").is_visible()
        assert page.get_by_role("complementary", name="Testing mode").is_visible()


def test_campaign_modules_hide_and_disable_unselected_fields(page, component_origin):
    """Conditional groups cannot accidentally post data from a disabled module."""
    page.goto(component_origin + "/campaign-settings")
    ministry = page.get_by_role("group", name="Ministry selections")
    financial = page.get_by_role("group", name="Financial periods and funds")
    assert not ministry.is_visible() and not financial.is_visible()
    page.get_by_label("Ministry stewardship").check()
    assert ministry.is_visible()
    assert page.get_by_label("Included Ministries").input_value() == "4"
    page.get_by_label("Financial stewardship").check()
    assert financial.is_visible()
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
    context = browser_engine.new_context(java_script_enabled=False)
    try:
        page = context.new_page()
        page.goto(component_origin + "/campaign-settings")
        assert page.get_by_role(
            "group", name="Financial periods and funds"
        ).is_visible()
        assert page.get_by_label("Upcoming financial period start").is_enabled()
        assert page.get_by_role("button", name="Preview changes").is_visible()
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
    page.wait_for_function(
        "() => document.querySelector('[data-presence-count]').textContent === '1,234'"
    )
    assert requests[0].method == "GET" and not requests[0].post_data
    with page.expect_response("**/admin/presence?format=count"):
        page.clock.fast_forward(30000)
    page.wait_for_function(
        "() => !document.querySelector('[data-presence-unavailable]').hidden"
    )
    assert page.locator("[data-presence-count]").inner_text() == "1,234"

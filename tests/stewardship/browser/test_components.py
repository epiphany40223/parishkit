"""WCAG automated checks plus keyboard, mobile, timezone and activity behavior."""

from datetime import timedelta
from urllib.parse import parse_qs, urlsplit

import pytest

from parishkit.stewardship.accounts.templatetags.stewardship import parish_time
from parishkit.stewardship.web import dates

from .conftest import NOW, load_collections
from .keyboard import tab_to_next
from .waits import eventually, hidden, recorded, visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)


@pytest.mark.parametrize("width", [320, 1280])
@pytest.mark.parametrize("path", ["/clone-preview"])
def test_schedule_preview_shows_send_times_only_in_browser_time(
    page, component_origin, width, path
):
    """Show each send as the resolved UTC instant in the browser's zone (#558).

    The campaign-local date and time a schedule saves, with the campaign's
    zone, are not shown beside it: every Admin time is browser-local.
    """
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(component_origin + path)
    instant = page.locator("time[data-local-instant]").first
    assert instant.get_attribute("datetime") == "2054-10-01T13:00:00+00:00"
    assert "6:00" in instant.inner_text() and "PDT" in instant.inner_text()
    text = page.locator("main").inner_text()
    # The schedule's campaign-local intent (9:00 AM New York, in the parish
    # style's clock) is no longer printed.
    with dates.using(dates.DEFAULT):
        parish_clock = parish_time("09:00:00")
    assert parish_clock == "9:00 AM"
    assert parish_clock not in text
    # The shared schedule preview (#1129) draws the zone-less review's
    # sends; the always-on sentences are gone.
    assert "When it sends (shown in your computer's time zone):" in text
    assert "not a list of who will receive it" not in text
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")


def renewal(route, **values):
    """Answer one source-load renewal POST with a synthetic progress reading."""
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
        | values
    )


@pytest.mark.parametrize("clock_skew_hours", [-48, 0, 48])
def test_setup_progress_only_renews_visible_correlated_work_and_stops_at_deadline(
    page, component_origin, clock_skew_hours
):
    """Visible-page renewal carries only CSRF and stops at local deadlines."""
    page.clock.install(time=NOW + timedelta(hours=clock_skew_hours))
    requests = []

    def observe(route):
        """The synthetic server response has no Family values or renewal promises."""
        requests.append(route.request)
        renewal(route)

    page.route("**/admin/setup/source/*?format=json", observe)
    page.goto(component_origin + "/setup-source-progress")
    recorded(page, requests, 1)
    assert requests[0].method == "POST"
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


def test_setup_progress_terminal_renewal_stops_automatic_posts(page, component_origin):
    """A renewal that says the load ended stops renewing, and nothing else posts."""
    page.clock.install(time=NOW)
    requests = []

    def expired(route):
        """A terminal server verdict is final even before the local timer expires."""
        requests.append(route.request)
        renewal(route, task_state="cancelled", setup_state="expired", active=False)

    page.route("**/admin/setup/source/*?format=json", expired)
    page.goto(component_origin + "/setup-source-progress")
    recorded(page, requests, 1)
    page.clock.fast_forward(60000)
    page.wait_for_timeout(200)
    assert len(requests) == 1
    assert page.get_by_role("button", name="Check progress now").count() == 0


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
        "/home-key-change",
        "/availability",
        "/denied",
        "/denied-admin",
        "/denied-code",
        "/denied-link",
        "/denied-unavailable",
        "/family-ended",
        "/ministries",
        "/parish-settings",
        "/configuration-request",
        "/go-live-steps",
        "/background",
        "/deliveries",
        "/delivery",
        "/delivery-note-only",
        "/delivery-refusals",
        "/delivery-refusal",
        "/delivery-error",
        "/campaign-settings",
        "/campaign-settings-live",
        "/campaign-settings-locked",
        "/share-settings",
        "/presence",
        "/background-task",
        "/export-cleanup-task",
        "/export-cleanup-stale",
        "/export-cleanup-conflict",
        "/export-cleanup-invalid",
        "/content-settings",
        "/content-history",
        "/campaign-mail",
        "/campaign-mail-unknown",
        "/campaign-mail-pending",
        "/schedule-settings",
        "/schedule-list",
        "/schedule-new",
        "/schedule-edit",
        "/schedule-edit-digest",
        "/clone-settings",
        "/clone-preview",
        "/integrations",
        "/integration-settings",
        "/backup-key",
        "/backup-key-proof",
        "/branding-settings",
        "/hosted-files",
        "/hosted-file-unavailable",
        "/setup",
        "/setup-parish",
        "/setup-branding",
        "/setup-credential",
        "/campaign-create-long",
        "/content-settings-page",
        "/schedule-editor",
        "/schedule-editor-mail",
        "/schedule-editor-error",
        "/setup-preview",
        "/setup-confirmation",
        "/setup-confirmation-unready",
        "/setup-finalization",
        "/setup-installation",
        "/setup-credential-rejected",
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
    if path == "/branding-settings":
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


def test_key_change_banner_and_rejected_setup_credential_say_what_to_do(
    page, component_origin
):
    """Both #456 messages reach the Administrator in plain words.

    The banner names each integration whose key change holds settings
    changes, linked to its page; a rejected setup credential names what to
    check and that setup must start again.
    """
    page.goto(component_origin + "/home-key-change")
    banner = page.locator("[data-key-changes]")
    visible(banner.get_by_text("Other settings changes wait until it finishes"))
    assert banner.get_by_role("link", name="ParishSoft").get_attribute("href") == (
        "/integration-settings"
    )
    page.goto(component_origin + "/setup-credential-rejected")
    step = page.locator('[data-finishing-step="parishsoft"]')
    visible(step.get_by_text("Check the key and the organization ID"))
    visible(step.get_by_text("cancel this setup and start a new one"))


def test_export_cleanup_keyboard_form_posts_only_csrf_and_replay_identity(
    page, component_origin
):
    """Recovery is an accessible explicit form action, never a GET or auto-poll."""
    page.route(
        "**/admin/system/background/*/export-cleanup-retry/",
        lambda route: route.fulfill(content_type="text/html", body="Retry queued"),
    )
    page.goto(component_origin + "/export-cleanup-task")
    button = page.get_by_role("button", name="Retry export cleanup", exact=True)
    button.focus()
    with page.expect_request(
        "**/admin/system/background/*/export-cleanup-retry/"
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
    assert (
        "not sure the previous test arrived" in page.get_by_role("alert").inner_text()
    )
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


@pytest.mark.parametrize("path", ["/content-settings", "/content-settings-page"])
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
    # The deadline passed, so the dialog names inactivity as the cause.
    expect(page.locator('[data-session-ended="idle"]')).to_be_visible()
    expect(page.locator('[data-session-ended="other"]')).to_be_hidden()
    assert attempts == [] and renewals == []


def test_admin_dialog_treats_signed_out_status_as_ended(page, component_origin):
    """A 401 status read ends the session at once, without blaming inactivity.

    Signing out in another tab (or losing access) makes the passive
    status read answer 401 long before the countdown ends (#457 M4).
    """
    from playwright.sync_api import expect

    page.clock.install(time=NOW)
    page.route(
        "**/admin/session/status",
        lambda route: route.fulfill(status=401, json={"state": "signed_out"}),
    )
    page.goto(component_origin + "/home")
    page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
    page.clock.fast_forward(2000)
    expect(page.locator("#session-expired")).to_be_visible()
    expect(page.locator("#session-warning")).to_be_hidden()
    expect(page.locator('[data-session-ended="other"]')).to_contain_text(
        "signed out in another tab"
    )
    expect(page.locator('[data-session-ended="idle"]')).to_be_hidden()
    # The dialog is described by the cause it shows.
    expect(page.locator("dialog.session-dialog")).to_have_attribute(
        "aria-describedby", "session-ended-other"
    )
    # The countdown stops: later ticks never reopen the warning.
    page.clock.fast_forward(60 * 60 * 1000)
    expect(page.locator("#session-warning")).to_be_hidden()
    expect(page.locator("#session-expired")).to_be_visible()


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


HEADING_SIZES = """() => {
    const main = document.querySelector('main');
    const probe = document.createElement('div');
    probe.innerHTML = '<h2>a</h2><h3>b</h3><fieldset class="panel"><legend>c'
        + '</legend></fieldset><fieldset><legend>d</legend></fieldset>'
        + '<div class="notice"><h2>e</h2></div><table><caption>f</caption>'
        + '</table>';
    main.append(probe);
    const size = (selector) => parseFloat(
        getComputedStyle(probe.querySelector(selector)).fontSize);
    const sizes = {
        h2: size(':scope > h2'), h3: size(':scope > h3'),
        panelLegend: size('fieldset.panel > legend'),
        legend: size('fieldset:not(.panel) > legend'),
        noticeHeading: size('.notice h2'), caption: size('caption'),
        root: parseFloat(getComputedStyle(document.documentElement).fontSize),
    };
    probe.remove();
    return sizes;
}"""


def test_section_and_card_headings_take_the_heading_tokens(page, component_origin):
    """Every section and card heading is drawn at its heading token (#732)."""
    from parishkit.stewardship.web.design_tokens import HEADINGS

    page.goto(component_origin + "/home")
    sizes = page.evaluate(HEADING_SIZES)
    rem = sizes.pop("root")
    expected = {
        "h2": HEADINGS["heading-2"],
        "panelLegend": HEADINGS["heading-2"],
        "h3": HEADINGS["heading-3"],
        "noticeHeading": HEADINGS["heading-3"],
        "legend": HEADINGS["card-heading"],
        "caption": HEADINGS["card-heading"],
    }
    for name, token in expected.items():
        assert abs(sizes[name] - float(token[:-3]) * rem) <= 0.5, (name, sizes)


def test_session_dialog_is_the_shared_dialog_card(page, component_origin):
    """The inactivity warning has the panel corners and a section heading."""
    from playwright.sync_api import expect

    from parishkit.stewardship.web.design_tokens import HEADINGS, RADII

    page.clock.install(time=NOW)
    page.goto(component_origin + "/home")
    page.clock.fast_forward(56 * 60 * 1000)
    dialog = page.locator("dialog.session-dialog")
    expect(dialog).to_be_visible()
    rem = page.evaluate(
        "parseFloat(getComputedStyle(document.documentElement).fontSize)"
    )
    radius = dialog.evaluate("(el) => parseFloat(getComputedStyle(el).borderRadius)")
    heading = dialog.locator("h2:visible").first.evaluate(
        "(el) => parseFloat(getComputedStyle(el).fontSize)"
    )
    assert abs(radius - float(RADII["radius"][:-3]) * rem) <= 0.5
    assert abs(heading - float(HEADINGS["heading-2"][:-3]) * rem) <= 0.5


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


@pytest.mark.parametrize("failure", ["http", "transport"])
def test_admin_dialog_ignores_a_failed_status_read(page, component_origin, failure):
    """A 5xx or a network failure is not a sign-out: no dialog opens (#755 L3)."""
    from playwright.sync_api import expect

    page.clock.install(time=NOW)
    statuses = []

    def status(route):
        """The status endpoint is unavailable or unreachable."""
        statuses.append(route.request)
        if failure == "transport":
            route.abort("failed")
        else:
            route.fulfill(status=503, json={"state": "unavailable"})

    page.route("**/admin/session/status", status)
    page.goto(component_origin + "/home")
    page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
    page.clock.fast_forward(2000)
    page.evaluate("() => true")  # one round trip after the clock jump
    assert statuses
    expect(page.locator("dialog.session-dialog")).to_be_hidden()


def test_admin_dialog_names_the_time_limit_when_it_ends(page, component_origin):
    """Past the absolute limit the ended dialog names the limit, not inactivity."""
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
    # Later reads find no answer (the component server has no status), so
    # the countdown runs out on the deadlines already adopted.
    page.unroute("**/admin/session/status")
    page.clock.fast_forward(5 * 60 * 1000)
    expect(page.locator("#session-expired")).to_be_visible()
    expect(page.locator('[data-session-ended="limit"]')).to_be_visible()
    expect(page.locator('[data-session-ended="idle"]')).to_be_hidden()
    expect(page.locator('[data-session-ended="other"]')).to_be_hidden()
    expect(page.locator("dialog.session-dialog")).to_have_attribute(
        "aria-describedby", "session-ended-limit"
    )


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


def test_sign_in_form_posts(page, component_origin):
    """The Admin sign-in starts with an ordinary POST form."""
    page.goto(component_origin + "/login")
    visible(page.get_by_role("button", name="Sign in with Google"))
    assert page.locator("main form").get_attribute("method") == "post"


def test_family_page_without_javascript_explains_it(browser_engine, component_origin):
    """No silent failure: the ungated Family page explains what JavaScript adds.

    The Family portal is not gated (#565), so a plain script-off context
    loads it as a parishioner's browser would.
    """
    context = browser_engine.new_context(java_script_enabled=False)
    try:
        page = context.new_page()
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
    # The form's Review is labelled Save, as a compact review's (#1356).
    visible(page.get_by_role("button", name="Save", exact=True))


def test_ministry_preview_preserves_operational_indicators(page, component_origin):
    """Configuration pages do not replace the persistent Admin navigation/header."""
    for path in ("/ministries", "/configuration-request"):
        page.goto(component_origin + path)
        visible(page.locator("[data-background-indicator]"))
        visible(page.get_by_role("complementary", name="Testing mode"))


def test_shared_table_selection_enables_bulk_actions(page, component_origin):
    """Select all chooses every row on the page and enables the bulk buttons.

    Mail schedules' table is the example since Ministries lost its row
    selection (#1267)."""
    page.goto(component_origin + "/schedule-list")
    delete = page.get_by_role("button", name="Delete selected", exact=True)
    assert delete.is_disabled()
    rows = page.locator("input[data-select-row]")
    assert rows.count() >= 2
    page.get_by_role("button", name="Select all").click()
    assert rows.evaluate_all("nodes => nodes.every(node => node.checked)")
    visible(page.get_by_text(f"{rows.count()} selected"))
    assert delete.is_enabled()
    page.get_by_role("button", name="Clear selection").click()
    assert delete.is_disabled()
    rows.first.check()
    header = page.get_by_label("Select every scheduled email that can be deleted")
    assert header.evaluate("node => node.indeterminate")


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
    # Campaign settings edit an existing campaign (New campaign is retired),
    # so checking the module selects nothing by itself.
    included = page.get_by_label("Included Ministries")
    assert included.evaluate("select => select.selectedOptions.length") == 0
    included.select_option("4")
    page.get_by_label("Financial stewardship").check()
    visible(financial)
    page.get_by_label("Upcoming financial period start").fill("2027-01-01")
    page.get_by_label("Financial stewardship").uncheck()
    posted = page.locator("[data-campaign-form]").evaluate(
        "form => Array.from(new FormData(form).keys())"
    )
    assert "financial_start" not in posted and "fund_duids" not in posted
    assert "ministry_duids" in posted


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
    # The failure sentence takes the pill's place (#1643), but the last good
    # count stays in the page; it is never overwritten with zero.
    assert page.locator("[data-presence-count]").text_content() == "1,234"


def axe_violations(page, axe_source):
    """Run the pinned axe scanner against the page's current state."""
    page.evaluate(axe_source)
    return page.evaluate("""async () => (await axe.run(document, {
        runOnly: {type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21aa', 'wcag22aa']}
    })).violations.map(({id, impact, nodes}) => ({
        id, impact, targets: nodes.map(n => n.target)
    }))""")


def shown_step_list(page, stepper, width):
    """The stepper's one shown step list, opened from the keyboard if narrow.

    At 64rem and wider the open copy is shown and the disclosure is not
    (#1404); narrower, the disclosure is shown collapsed and opens with its
    summary. Exactly one copy is ever shown, so it is met once.
    """
    wide = stepper.locator(".setup-stepper-wide .setup-steps")
    narrow = stepper.locator(".setup-stepper-list .setup-steps")
    if width >= 1024:
        visible(wide)
        assert stepper.locator(".setup-stepper-list").is_hidden()
        return wide
    assert wide.is_hidden() and narrow.is_hidden()
    stepper.locator(".setup-stepper-list summary").focus()
    page.keyboard.press("Enter")
    visible(narrow)
    return narrow


@pytest.mark.parametrize("width", [320, 1280])
def test_setup_stepper_is_compact_and_its_full_list_stays_accessible(
    page, component_origin, axe_source, width
):
    """A short summary; the ordered list is open on a wide screen (#1404) and
    opens from the keyboard on a narrow one."""
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(component_origin + "/setup-testing")
    stepper = page.locator(".setup-stepper")
    assert "Step 3 of 12: Testing recipient" in stepper.inner_text()
    assert "1 of 12 steps completed" in stepper.inner_text()
    # Far shorter than fifteen stacked cards, even at phone width (where the
    # list is still collapsed), and with the list open in two columns. At
    # 320px the step bar's twelve 44px segments wrap into two rows of eight
    # (#1406, ui-conventions "Step bars"), so the summary measures 278-302px
    # in CI's Linux engines.
    assert stepper.bounding_box()["height"] < (320 if width == 320 else 480)
    steps = shown_step_list(page, stepper, width)
    current = steps.locator('[aria-current="step"]')
    assert "Current step" in current.inner_text()
    assert "Completed" in steps.locator(".setup-step-done").first.inner_text()
    links = steps.locator("a")
    assert links.count() >= 2
    assert all(
        box["height"] >= 44
        for box in links.evaluate_all(
            "nodes => nodes.map(node => node.getBoundingClientRect().toJSON())"
        )
    )
    # The list holds only links, which Tab skips in macOS WebKit (#1043).
    if width >= 1024:
        links.first.focus()
    else:
        tab_to_next(page)
    assert page.evaluate("document.activeElement.closest('.setup-steps') !== null")
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    assert axe_violations(page, axe_source) == []


def tip_shown(locator):
    """Whether a step bar segment's floating tooltip is drawn."""
    return locator.evaluate("node => getComputedStyle(node, '::after').display") == (
        "block"
    )


@pytest.mark.parametrize("width", [320, 1280])
def test_setup_step_bar_links_open_steps_and_explains_the_rest(
    page, component_origin, width
):
    """Each open step's segment is a link; a blocked one only shows why (#1402)."""
    page.set_viewport_size({"width": width, "height": 900})
    failures = []
    page.on("pageerror", lambda error: failures.append(str(error)))
    opened = []
    # The component server has no wizard pages: answer any setup step page.
    page.route(
        "**/admin/setup/**",
        lambda route: (
            opened.append(route.request.url),
            route.fulfill(content_type="text/html", body="<title>Step</title>"),
        ),
    )
    page.goto(component_origin + "/setup-testing")
    track = page.get_by_role("list", name="Setup steps")
    segments = track.locator("a")
    steps = page.locator(".setup-stepper-list .setup-steps li").evaluate_all(
        "items => items.map(item => ["
        "item.querySelector('.setup-step-label').lastChild.textContent.trim(),"
        "item.querySelector('.setup-step-status').textContent.trim()])"
    )
    assert segments.count() == len(steps) == 12
    names = segments.evaluate_all("nodes => nodes.map(n => n.textContent.trim())")
    tips = segments.evaluate_all("nodes => nodes.map(n => n.dataset.tip)")
    assert names == tips
    for number, (name, (label, status)) in enumerate(zip(names, steps, strict=True), 1):
        assert name.startswith(f"Step {number} of 12: {label} — "), name
        assert name.endswith(status), name
    assert "— Current step — " in names[2]
    assert segments.nth(2).get_attribute("aria-current") == "step"
    # A 44px target each, as on the Family bar, with no horizontal scroll.
    assert all(
        box["height"] >= 44
        for box in segments.evaluate_all(
            "nodes => nodes.map(node => node.getBoundingClientRect().toJSON())"
        )
    )
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    # A step that cannot open yet is not a link: focusable, disabled, and a
    # click only shows its tooltip, moving nothing and opening nothing.
    blocked = segments.nth(5)
    assert blocked.get_attribute("href") is None
    assert blocked.get_attribute("aria-disabled") == "true"
    assert "Not available yet: " in blocked.get_attribute("data-tip")
    before = page.locator(".setup-stepper").bounding_box()
    address = page.url
    # Playwright waits for an aria-disabled control to enable; force sends
    # the real mouse click a reader would.
    blocked.click(force=True)
    assert page.evaluate("document.activeElement.dataset.stepUnavailable === ''")
    assert tip_shown(blocked)
    assert page.locator(".setup-stepper").bounding_box() == before
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    page.wait_for_timeout(200)
    assert page.url == address and opened == []
    # Keyboard focus shows a link's tooltip too; following it only opens its
    # page (a GET, with nothing submitted).
    done = segments.nth(1)
    done.focus()
    assert tip_shown(done)
    address = done.get_attribute("href")
    assert address == "/admin/setup/mail"
    with page.expect_request("**/admin/setup/**") as request:
        done.click()
    assert request.value.method == "GET" and request.value.post_data is None
    assert request.value.url == component_origin + address
    assert not failures


def test_admin_sidebar_and_breadcrumbs_mark_the_current_page(page, component_origin):
    """Desktop shows the open sidebar; narrow screens collapse it behind Menu."""
    from playwright.sync_api import expect

    page.set_viewport_size({"width": 1440, "height": 900})
    page.goto(component_origin + "/ministries")
    sidebar = page.get_by_role("navigation", name="Administration")
    current = sidebar.locator('a[aria-current="page"]')
    visible(current)
    assert current.inner_text() == "Ministries"
    expect(sidebar.get_by_text("Menu")).to_be_hidden()
    trail = page.get_by_role("navigation", name="Breadcrumb")
    visible(trail.get_by_role("link", name="Home"))
    assert trail.locator('[aria-current="page"]').inner_text() == "Ministries"
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
def test_admin_sidebar_groups_are_labelled_disclosures_set_apart_from_links(
    page, component_origin, axe_source, width
):
    """Group titles are disclosure summaries that name their link groups."""
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(component_origin + "/admin-navigation")
    sidebar = page.get_by_role("navigation", name="Administration")
    menu = sidebar.locator("details[data-admin-menu]")
    if not menu.evaluate("details => details.open"):
        sidebar.get_by_text("Menu", exact=True).click()
    # The title is the summary itself, not a heading inside it (some screen
    # readers then drop the summary's disclosure role).
    titles = sidebar.locator("details[data-menu-group] > summary")
    assert titles.all_inner_texts() == [
        "CAMPAIGN SETUP",
        "PARISH DATA",
        "SYSTEM",
    ]
    assert sidebar.get_by_role("heading").count() == 0
    for name, links in (
        ("Campaign setup", ["Campaign settings", "Dates and mail schedules"]),
        ("Parish data", ["Parish settings", "Ministries"]),
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

    # Titles are smaller, uppercase and bold; links are normal weight, and
    # the current page is bolder still.
    link = sidebar.get_by_role("link", name="Campaign settings")
    title = style(titles, "fontSize", "textTransform", "fontWeight")
    link_size, link_weight = style(link, "fontSize", "fontWeight")
    assert title[1:] == ["uppercase", "700"]
    assert float(title[0][:-2]) < float(link_size[:-2])
    assert link_weight == "400"
    current = sidebar.locator('a[aria-current="page"]')
    assert style(current, "fontWeight") == ["650"]
    # Every group after the first has a divider above it.
    sections = sidebar.locator(".admin-section")
    widths = sections.evaluate_all(
        "nodes => nodes.map(node => getComputedStyle(node).borderTopWidth)"
    )
    assert widths == ["0px", "1px", "1px"]
    # Links sit indented under their group's title.
    assert link.bounding_box()["x"] > titles.first.bounding_box()["x"]
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
    """All four Go live steps fit a phone, never scrolling (#196): Production
    activation's step bar while initial mail is prepared (#1585 slice 5)."""
    from playwright.sync_api import expect

    page.set_viewport_size({"width": width, "height": 900})
    page.goto(component_origin + "/go-live-steps")
    steps = page.get_by_role("list", name="Go live steps")
    visible(steps)
    assert steps.locator("li").count() == 4
    current = steps.locator('[aria-current="step"]')
    assert current.count() == 1
    expect(page.locator(".setup-stepper-current")).to_have_text(
        "Step 4 of 4: Activation"
    )
    expect(page.locator(".setup-stepper-count")).to_have_text("3 of 4 steps completed")
    # No numbered flow list beside it: one step indicator per page.
    assert page.locator(".flow-steps").count() == 0
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    assert axe_violations(page, axe_source) == []


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
        menu.locator("details[data-admin-menu] > summary").click()
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
    page.goto(component_origin + "/content-settings-page")
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
    # Its step is the shared stage boxes (#1579), not a "Step:" line.
    stages = page.get_by_role("list", name="Parts of the refresh")
    visible(
        stages.locator('[data-refresh-stage="fetching"][data-stage-state="active"]')
    )
    assert page.get_by_text("Step:", exact=False).count() == 0
    visible(page.get_by_text("server restarted", exact=False))
    assert page.get_by_text("Waiting for progress details").count() == 0

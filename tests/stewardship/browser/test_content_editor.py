"""Visual editor structure survives save, sanitizing and rendering again."""

from urllib.parse import parse_qs
from uuid import uuid4

import pytest
from django.template.loader import render_to_string

from parishkit.stewardship.accounts.content_forms import ContentForm
from parishkit.stewardship.web.content import sanitize_html
from parishkit.stewardship.web.security import CSP

from .waits import hidden, visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)

PATH = "/editor-roundtrip"
START = '<p><a href="https://example.org/give">Begin your renewal</a></p>'


def editor_page(html):
    """The Admin content editor as the server renders stored, sanitized HTML."""
    visual = sanitize_html(html)
    return render_to_string(
        "stewardship/content-settings.html",
        {
            "csrf_token": "a" * 64,
            "campaign": {"pk": uuid4(), "active_configuration": {"name": "Sample"}},
            "label": "Confirmation email",
            "visual": visual,
            "placeholders": ["family_name"],
            # An email, so the editor renders the plain-text panel (#259)
            # these tests exercise.
            "form": ContentForm(
                kind="email",
                slot="confirmation",
                initial={
                    "base_digest": "a" * 64,
                    "subject": "Received",
                    "html": visual,
                    "generate_text": True,
                },
            ),
        },
    )


def select_text(page, text, *, caret=False):
    """Select one text node's words in the editor (or put the caret after them)."""
    page.locator("[data-content-editor]").evaluate(
        """(node, [text, caret]) => {
            const walker = document.createTreeWalker(node, NodeFilter.SHOW_TEXT);
            let current;
            while ((current = walker.nextNode()) && !current.data.includes(text)) {}
            const range = document.createRange();
            const start = current.data.indexOf(text);
            range.setStart(current, start);
            range.setEnd(current, start + text.length);
            if (caret) range.collapse(false);
            node.focus();
            const selection = window.getSelection();
            selection.removeAllRanges(); selection.addRange(range);
        }""",
        [text, caret],
    )


def caret_to_end(page):
    """Put the caret after the editor's last character."""
    page.locator("[data-content-editor]").evaluate(
        """node => {
            node.focus();
            const range = document.createRange();
            range.selectNodeContents(node); range.collapse(false);
            const selection = window.getSelection();
            selection.removeAllRanges(); selection.addRange(range);
        }"""
    )


def submit(page):
    """Save the form and wait until the rendered response page has loaded.

    The page posts back to its own URL, so waiting for the POST response and
    then ``wait_for_load_state()`` is not enough: when Playwright has not yet
    seen the new document commit, the old page's already-fired ``load``
    satisfies the wait and the next query races the swap to the new page
    (#544, "assert 0 == 3" on loaded CI runners). ``expect_navigation`` waits
    for the new document itself to commit and finish loading.
    """
    with page.expect_navigation():
        page.get_by_role("button", name="Preview changes").click()


def test_typed_paragraphs_breaks_bold_lists_and_links_round_trip(
    page, component_origin
):
    """Enter, Shift+Enter, Bold, a list and a link keep their structure."""
    saved = []

    def serve(route):
        """Sanitize a posted form like the server does, then render it again."""
        request = route.request
        html = START
        if request.method == "POST":
            posted = {
                name: values[0]
                for name, values in parse_qs(
                    request.post_data, keep_blank_values=True
                ).items()
            }
            form = ContentForm(posted, kind="page")
            assert form.is_valid(), form.errors
            html = form.cleaned_data["prepared"].html
            saved.append(form.cleaned_data["prepared"])
        route.fulfill(
            status=200,
            content_type="text/html; charset=utf-8",
            headers={"Content-Security-Policy": CSP},
            body=editor_page(html),
        )

    page.route(component_origin + PATH, serve)
    failures = []
    page.on("pageerror", lambda error: failures.append(str(error)))
    page.goto(component_origin + PATH)
    caret_to_end(page)
    keyboard = page.keyboard
    keyboard.press("Enter")
    keyboard.type("Dear Alex and Sam:")
    keyboard.press("Enter")
    keyboard.type("Stewardship is a way of life.")
    # On macOS, Playwright's WebKit (and presumably Safari) turns Shift+Enter
    # into the same "insert newline" as Enter; the editor's own handler makes
    # it a <br>.
    keyboard.press("Shift+Enter")
    keyboard.type("It is gratitude.")
    keyboard.press("Enter")
    keyboard.type("Worship together")
    select_text(page, "a way of life")
    page.get_by_role("button", name="Bold", exact=True).click()
    select_text(page, "Worship together")
    page.get_by_role("button", name="Bulleted list", exact=True).click()
    select_text(page, "Worship together", caret=True)
    keyboard.press("Enter")
    keyboard.type("Serve others")
    submit(page)
    assert len(saved) == 1 and not failures
    stored = saved[0]
    # Each paragraph is its own <p>; Shift+Enter is a <br> inside one.
    assert stored.html.count("<p>") == 3, stored.html
    assert '<a href="https://example.org/give"' in stored.html
    assert "<p>Dear Alex and Sam:</p>" in stored.html
    assert (
        "<p>Stewardship is <strong>a way of life</strong>.<br>It is gratitude.</p>"
        in stored.html
    )
    assert "<ul><li>Worship together</li><li>Serve others</li></ul>" in stored.html
    assert stored.text == (
        "Begin your renewal: https://example.org/give\n\nDear Alex and Sam:\n\n"
        "Stewardship is a way of life.\nIt is gratitude.\n\n"
        "- Worship together\n- Serve others"
    )
    # Rendering the stored HTML again shows, and resubmits, the same structure.
    from playwright.sync_api import expect

    editor = page.locator("[data-content-editor]")
    expect(editor.locator("p")).to_have_count(3)
    expect(editor.locator("li")).to_have_count(2)
    expect(editor.locator("strong")).to_have_text("a way of life")
    keyboard.press("Tab")  # No edit: the source still holds the stored HTML.
    submit(page)
    assert saved[1].html == stored.html


def test_shift_enter_is_a_line_break_whatever_the_platform_key_binding(
    page, component_origin
):
    """The editor itself turns Shift+Enter into a <br>, not a new paragraph.

    Linux WebKit, Chromium and Firefox already do this natively, so a real
    key press cannot show the handler at work there; a dispatched keydown
    reaches only the editor's handler (synthetic events have no native
    editing default) and so proves it runs on every engine, including the
    macOS WebKit (and presumably Safari) case where Shift+Return would start
    a paragraph.
    """
    page.goto(component_origin + "/content-settings")
    caret_to_end(page)
    page.keyboard.type("First line")
    assert shift_enter(page)
    page.keyboard.type("Second line")
    source = page.locator('textarea[name="html"]').input_value()
    assert "First line<br>Second line" in source, source


def shift_enter(page, **extra):
    """Dispatch a synthetic Shift+Enter keydown; return whether it was handled.

    ``extra`` adds KeyboardEvent options such as ``ctrlKey`` or
    ``isComposing``. ``defaultPrevented`` is true only when the editor's own
    handler inserted the line break.
    """
    return page.locator("[data-content-editor]").evaluate(
        """(node, extra) => {
            const event = new KeyboardEvent("keydown", {
                key: "Enter", shiftKey: true, bubbles: true, cancelable: true,
                ...extra
            });
            node.dispatchEvent(event);
            return event.defaultPrevented;
        }""",
        extra,
    )


@pytest.mark.parametrize(
    "extra",
    [{"ctrlKey": True}, {"altKey": True}, {"metaKey": True}, {"isComposing": True}],
)
def test_modified_or_composing_shift_enter_is_left_to_the_browser(
    page, component_origin, extra
):
    """Ctrl, Alt or Meta with Shift+Enter, or an IME commit, is not taken over."""
    page.goto(component_origin + "/content-settings")
    caret_to_end(page)
    page.keyboard.type("First line")
    before = page.locator("[data-content-editor]").inner_html()
    assert not shift_enter(page, **extra)
    assert page.locator("[data-content-editor]").inner_html() == before


def test_pasted_lines_become_paragraphs(page, component_origin):
    """Plain-text paste keeps line breaks, and blank lines split paragraphs."""
    page.goto(component_origin + "/content-settings")
    editor = page.locator("[data-content-editor]")
    editor.evaluate("""node => {
        node.focus();
        const range = document.createRange(); range.selectNodeContents(node);
        const selection = window.getSelection();
        selection.removeAllRanges(); selection.addRange(range);
        const event = new Event('paste', {bubbles: true, cancelable: true});
        Object.defineProperty(event, 'clipboardData', {value: {
            getData: () => 'Dear Alex:\\r\\n\\r\\nFirst line\\nsecond line\\n\\nThanks'
        }});
        node.dispatchEvent(event);
    }""")
    source = page.locator('textarea[name="html"]').input_value()
    assert sanitize_html(source) == (
        "<p>Dear Alex:</p><p>First line<br>second line</p><p>Thanks</p>"
    )


def test_generated_plain_text_is_shown_read_only_and_never_silently_dropped(
    page, component_origin
):
    """Checked: read-only server preview. Edit: unchecks and keeps the text."""
    from playwright.sync_api import expect

    from parishkit.stewardship.web.content import prepare_content

    posted = []

    def generate(route):
        """Answer like the server's plain-text preview endpoint."""
        html = parse_qs(route.request.post_data)["html"][0]
        route.fulfill(json={"text": prepare_content(html).text})

    page.route("**/admin/content/plain-text", generate)
    page.goto(component_origin + "/content-settings")
    box = page.locator('input[name="generate_text"]')
    text = page.locator('textarea[name="text"]')
    assert box.is_checked()
    expect(text).to_have_value("Hello Sample Family")
    assert not text.is_editable()
    visible(page.get_by_text("Generated from the HTML version"))
    # Editing the visual content refreshes the generated preview.
    caret_to_end(page)
    page.keyboard.press("Enter")
    page.keyboard.type("Second paragraph")
    expect(text).to_have_value("Hello Sample Family\n\nSecond paragraph")
    # "Edit plain text" unchecks generation and keeps the generated text.
    page.get_by_role("button", name="Edit plain text").click()
    assert not box.is_checked() and text.is_editable()
    assert text.input_value() == "Hello Sample Family\n\nSecond paragraph"
    text.press("End")
    text.type(" (edited)")

    def capture(route):
        """Record the posted form instead of saving it."""
        if route.request.method == "POST":
            posted.append(parse_qs(route.request.post_data, keep_blank_values=True))
            route.fulfill(status=200, content_type="text/html", body="<p>Saved</p>")
        else:
            route.continue_()

    page.route(component_origin + "/content-settings", capture)
    with page.expect_response(lambda response: response.request.method == "POST"):
        page.get_by_role("button", name="Preview changes").click()
    assert "generate_text" not in posted[0]
    # Browsers submit a textarea's line breaks as CRLF.
    assert posted[0]["text"][0].replace("\r\n", "\n") == (
        "Hello Sample Family\n\nSecond paragraph (edited)"
    )


def test_typing_into_generated_text_switches_to_editing(page, component_origin):
    """A keystroke in the read-only preview unchecks generation, keeping text."""
    from playwright.sync_api import expect

    page.route(
        "**/admin/content/plain-text",
        lambda route: route.fulfill(json={"text": "Hello Sample Family"}),
    )
    page.goto(component_origin + "/content-settings")
    text = page.locator('textarea[name="text"]')
    expect(text).not_to_have_value("")
    text.click()
    text.press("End")
    page.keyboard.press("x")
    assert not page.locator('input[name="generate_text"]').is_checked()
    assert text.is_editable()
    assert text.input_value().startswith("Hello Sample Family")


def serve_preview(route):
    """Answer like the server's content preview: sanitized HTML, text, removals."""
    from parishkit.stewardship.web.content import prepare_content, removed_markup

    html = parse_qs(route.request.post_data, keep_blank_values=True)["html"][0]
    prepared = prepare_content(html)
    route.fulfill(
        json={
            "html": prepared.html,
            "text": prepared.text,
            "removed": removed_markup(html),
        }
    )


def test_source_edits_redraw_the_visual_pane_from_the_sanitizer(page, component_origin):
    """The pane stays visible, redraws from sanitized HTML and never runs script."""
    from playwright.sync_api import expect

    requests = []

    def preview(route):
        """Count requests: one shared request serves both live previews."""
        requests.append(route.request.post_data)
        serve_preview(route)

    page.route("**/admin/content/plain-text", preview)
    page.goto(component_origin + "/content-settings")
    expect(page.locator('textarea[name="text"]')).not_to_have_value("")
    requests.clear()
    visual = page.locator("[data-visual-content]")
    editor = page.locator("[data-content-editor]")
    page.get_by_text("HTML source", exact=True).click()
    page.locator('textarea[name="html"]').fill(
        "<script>window.__pwned = true</script>"
        '<p onclick="window.__pwned = true">Hello <b>Alex</b></p>'
        '<img src="x" onerror="window.__pwned = true">'
    )
    # While the server re-sanitizes, the pane dims but stays visible.
    visible(visual)
    assert visual.get_attribute("aria-busy") == "true"
    assert editor.get_attribute("contenteditable") == "false"
    expect(page.locator("[data-visual-content]")).to_have_attribute(
        "aria-busy", "false"
    )
    visible(visual)
    assert editor.get_attribute("contenteditable") == "true"
    assert editor.inner_html() == "<p>Hello <strong>Alex</strong></p>"
    assert editor.locator("script, img, [onclick]").count() == 0
    assert page.evaluate("() => window.__pwned") is None
    notice = page.locator("[data-visual-removed]")
    visible(notice)
    for removed in (
        "image not from the hosted file library",
        "<script> element and its content",
        "onclick attribute",
    ):
        assert removed in notice.inner_text()
    # A removed element's own attributes are not listed separately.
    assert "onerror" not in notice.inner_text()
    # The same single response also refreshed the generated plain text.
    assert page.locator('textarea[name="text"]').input_value() == "Hello Alex"
    assert len(requests) == 1
    # Clean source clears the removal notice.
    page.locator('textarea[name="html"]').fill("<p>Hello again</p>")
    expect(page.locator("[data-content-editor]")).to_have_js_property(
        "textContent", "Hello again"
    )
    hidden(notice)


def test_unavailable_source_preview_keeps_the_pane_visible_and_read_only(
    page, component_origin
):
    """A failed preview explains itself; the stale pane cannot be edited."""
    page.route("**/admin/content/plain-text", lambda route: route.fulfill(status=503))
    page.goto(component_origin + "/content-settings")
    page.get_by_text("HTML source", exact=True).click()
    page.locator('textarea[name="html"]').fill("<p>Changed</p>")
    unavailable = page.locator("[data-visual-unavailable]")
    unavailable.wait_for()
    visible(page.locator("[data-visual-content]"))
    editor = page.locator("[data-content-editor]")
    assert editor.get_attribute("contenteditable") == "false"
    assert editor.inner_text() == "Hello Sample Family"
    hidden(page.locator("[data-visual-updating]"))


def test_a_late_answer_for_older_source_never_unlocks_the_pane(page, component_origin):
    """Typing after a request was sent invalidates it; only current answers apply."""
    from playwright.sync_api import expect

    held = []

    def hold_first(route):
        """Keep the first source request unanswered; answer the rest normally."""
        html = parse_qs(route.request.post_data, keep_blank_values=True)["html"][0]
        if html == "<p>First</p>" and not held:
            held.append(route)
        else:
            serve_preview(route)

    page.route("**/admin/content/plain-text", hold_first)
    page.goto(component_origin + "/content-settings")
    expect(page.locator('textarea[name="text"]')).not_to_have_value("")
    page.get_by_text("HTML source", exact=True).click()
    source = page.locator('textarea[name="html"]')
    source.fill("<p>First</p>")
    page.wait_for_timeout(1000)  # let the 800 ms debounce send the request
    assert held, "the first source request should be pending"
    # Newer source arrives while the first request is still unanswered.
    source.fill("<p>Second</p>")
    editor = page.locator("[data-content-editor]")
    bold = page.get_by_role("button", name="Bold", exact=True)
    assert bold.is_disabled()
    held[0].fulfill(json={"html": "<p>First</p>", "text": "First", "removed": []})
    page.wait_for_timeout(200)
    # The stale answer was ignored: the pane stayed locked and unchanged.
    assert editor.get_attribute("contenteditable") == "false"
    assert "First" not in editor.inner_text()
    expect(page.locator("[data-content-editor]")).to_have_js_property(
        "textContent", "Second"
    )
    assert editor.get_attribute("contenteditable") == "true"
    assert bold.is_enabled()
    assert source.input_value() == "<p>Second</p>"
    assert page.locator('textarea[name="text"]').input_value() == "Second"

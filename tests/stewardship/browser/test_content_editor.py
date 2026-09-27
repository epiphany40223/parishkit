"""Visual editor structure survives save, sanitizing and rendering again."""

from urllib.parse import parse_qs
from uuid import uuid4

import pytest
from django.template.loader import render_to_string

from parishkit.stewardship.accounts.content_forms import ContentForm
from parishkit.stewardship.web.content import sanitize_html
from parishkit.stewardship.web.security import CSP

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
            "label": "Family welcome",
            "visual": visual,
            "placeholders": ["family_name"],
            "form": ContentForm(
                kind="page",
                initial={
                    "base_digest": "a" * 64,
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
    """Save the form and wait until the rendered response page has loaded."""
    with page.expect_response(lambda response: response.request.method == "POST"):
        page.get_by_role("button", name="Preview changes").click()
    page.wait_for_load_state()


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
    editor = page.locator("[data-content-editor]")
    assert editor.locator("p").count() == 3
    assert editor.locator("li").count() == 2
    assert editor.locator("strong").inner_text() == "a way of life"
    keyboard.press("Tab")  # No edit: the source still holds the stored HTML.
    submit(page)
    assert saved[1].html == stored.html


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

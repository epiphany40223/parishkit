"""A report send waiting on an earlier report email, and settling it (#1872).

Sent mail draws a note, with the page, naming each earlier report email a
later send of its report waits on, by when it was created (browser-local),
each linking its Mail message page. That page, for an email once unknown
and then cancelled, says a later send waits on it and offers Count as
delivered and Count as not delivered in the usual resolution form: each
waits for a note that is more than spaces, and posts the closed form.
"""

from urllib.parse import parse_qs

import pytest

from .delivery_components import REPORT_EMAIL, REPORT_WAITING_LIST

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "webkit"], indirect=True
)

PHONE = {"width": 375, "height": 740}
# The other answer of the question, by its label and by its choice.
OTHER = {
    "Yes, it was sent": "No, or I can't tell",
    "No, or I can't tell": "Yes, it was sent",
}
OTHER_CHOICE = {"reached": "not_reached", "not_reached": "reached"}


def test_sent_mail_names_the_waiting_email_and_links_it(page, component_origin):
    """The note sits above the list, its time browser-local, its link the email's."""
    page.goto(component_origin + REPORT_WAITING_LIST)
    note = page.locator("#delivery-report-waiting")
    assert note.get_by_role("heading").inner_text() == "A report email is waiting"
    link = note.get_by_role("link")
    assert link.get_attribute("href") == REPORT_EMAIL
    assert link.inner_text().startswith("Report email created ")
    # The time is drawn in the browser's zone, not left as the ISO instant.
    time = link.locator("time")
    assert time.inner_text() != time.get_attribute("datetime")
    assert "(2 sends wait on it)" in note.inner_text()
    # Above the list, never inside its in-place region.
    table = page.locator("table.data-table")
    assert note.bounding_box()["y"] < table.bounding_box()["y"]
    assert not note.evaluate("node => Boolean(node.closest('[data-in-place-region]'))")


def test_the_waiting_note_fits_a_phone(page, component_origin):
    """No sideways page scroll at phone width."""
    page.set_viewport_size(PHONE)
    page.goto(component_origin + REPORT_WAITING_LIST)
    assert page.locator("#delivery-report-waiting").is_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")


def test_the_email_page_asks_whether_it_reached_its_recipient(page, component_origin):
    """The cancelled panel asks one question; the waiting notice ends the panel."""
    page.goto(component_origin + REPORT_EMAIL)
    panel = page.locator("#delivery-resolve section.outcome-panel")
    assert panel.locator("#outcome-heading").inner_text() == "Not sent (cancelled)"
    assert panel.locator("legend").inner_text() == "Did it reach its recipient?"
    assert panel.locator("form").count() == 2
    notice = panel.locator("#delivery-report-waiting")
    assert "A later send of this report is waiting" in notice.inner_text()
    assert "Answer the question above." in notice.inner_text()
    question = panel.locator("fieldset.choice-question")
    assert question.bounding_box()["y"] < notice.bounding_box()["y"]


@pytest.mark.parametrize(
    "answer,choice,action",
    [
        ("Yes, it was sent", "reached", "count_delivered"),
        ("No, or I can't tell", "not_reached", "count_unsent"),
    ],
)
def test_an_answer_waits_for_its_note_then_posts_the_closed_form(
    page, component_origin, answer, choice, action
):
    """Choosing never moves the button; it waits for a real note, then posts."""
    page.goto(component_origin + REPORT_EMAIL)
    page.get_by_role("radio", name=answer, exact=True).check()
    form = page.locator(f"#answer-{choice}")
    button = form.get_by_role("button")
    assert form.is_visible() and button.is_disabled()
    form.locator("textarea").fill("   ")
    assert button.is_disabled()
    form.locator("textarea").fill("Its recipient confirmed it arrived.")
    assert button.is_enabled()
    # The other answer's button sits at the same place (one reserved slot),
    # so changing the answer never moves it.
    before = button.bounding_box()["y"]
    other = page.get_by_role("radio", name=OTHER[answer], exact=True)
    other.check()
    assert page.locator(f"#answer-{OTHER_CHOICE[choice]} button").bounding_box()[
        "y"
    ] == pytest.approx(before, abs=1)
    page.get_by_role("radio", name=answer, exact=True).check()
    page.route(
        "**/admin/mail/sent/*/resolution/",
        lambda route: route.fulfill(status=200, body="Recorded"),
    )
    with page.expect_request("**/admin/mail/sent/*/resolution/") as sent:
        button.click()
    assert sent.value.method == "POST"
    fields = parse_qs(sent.value.post_data)
    assert set(fields) == {
        "csrfmiddlewaretoken",
        "command_id",
        "expected_version",
        "action",
        "note",
    }
    assert fields["action"] == [action]


def test_the_email_page_fits_a_phone(page, component_origin):
    """No sideways page scroll at phone width with the notice and decisions."""
    page.set_viewport_size(PHONE)
    page.goto(component_origin + REPORT_EMAIL)
    assert page.locator("#delivery-report-waiting").is_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")


@pytest.mark.parametrize("path", [REPORT_WAITING_LIST, REPORT_EMAIL])
def test_both_pages_pass_the_automated_accessibility_checks(
    page, component_origin, axe_source, path
):
    """axe finds no WCAG 2.2 AA violation, and no script error is raised."""
    failures = []
    page.on("pageerror", lambda error: failures.append(str(error)))
    page.goto(component_origin + path)
    page.evaluate(axe_source)
    violations = page.evaluate("""async () => (await axe.run(document, {
        runOnly: {type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21aa', 'wcag22aa']}
    })).violations.map(({id, impact, nodes}) => ({
        id, impact, targets: nodes.map(n => n.target)
    }))""")
    assert violations == [] and failures == []

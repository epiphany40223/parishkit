"""Data to fix in ParishSoft (#1011) in Chromium and WebKit.

Each role sees only its sections, in the decided order, and the Parish data
menu group ends with this page; nothing on the page moves once it has
loaded, and showing a greyed-out link's reason moves nothing either. A
minute on, the counts update by themselves from the passive status
fragment, in place (#1859).
"""

import pytest

from .conftest import NOW
from .data_to_fix_components import PATH, STAFF_PATH, later_status
from .test_in_place import MARK, MARKED

# Autouse: answer the header's Background work poll, which the component
# server does not serve, so the minute the clock moves draws nothing else.
from .test_system_health import background_counts  # noqa: F401
from .waits import has_text, painted, visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "webkit"], indirect=True
)

SECTIONS = "main section.data-to-fix-section h2"


def _parish_group(page):
    """The labels of the Parish data menu group's entries, in order."""
    group = page.locator('details[data-menu-group="parish"] ul li')
    return [text.strip() for text in group.all_inner_texts()]


def _boxes(page):
    """Where each section block and the data line sit on the page (focusing
    a control may scroll the window, which moves nothing on the page)."""
    return page.locator(
        "#data-to-fix-data, main section.data-to-fix-section"
    ).evaluate_all(
        "nodes => nodes.map((node) => {"
        " const box = node.getBoundingClientRect();"
        " return [box.x + scrollX, box.y + scrollY, box.width, box.height]; })"
    )


def test_an_administrator_sees_every_section_in_order(page, component_origin):
    """Five sections, most blocking first; the page is the group's last entry,
    marked current."""
    page.goto(component_origin + PATH)
    assert page.locator(SECTIONS).all_inner_texts() == [
        "Families the form cannot open",
        "Refused email addresses",
        "Families no mail can reach",
        "Census changes to enter",
        "Roster changes to enter",
    ]
    assert _parish_group(page)[-1] == "Data to fix in ParishSoft"
    current = page.locator('details[data-menu-group="parish"] [aria-current="page"]')
    assert current.inner_text().strip() == "Data to fix in ParishSoft"
    main = page.locator("main")
    assert main.get_by_role("link", name="Open Refused addresses").is_visible()


def test_staff_see_only_their_sections(page, component_origin):
    """Staff: Families no mail can reach, census and roster changes, and a Parish
    data group holding only this page; never a refused-address count."""
    page.goto(component_origin + STAFF_PATH)
    assert page.locator(SECTIONS).all_inner_texts() == [
        "Families no mail can reach",
        "Census changes to enter",
        "Roster changes to enter",
    ]
    assert _parish_group(page) == ["Data to fix in ParishSoft"]
    assert page.locator("main").get_by_text("refused", exact=False).count() == 0


def test_nothing_moves_after_the_page_loads(page, component_origin):
    """No poll and no late content: the blocks stay put after load, and
    showing the greyed-out census link's reason moves none of them."""
    page.goto(component_origin + PATH)
    page.wait_for_load_state("load")
    painted(page)
    loaded = _boxes(page)
    page.wait_for_timeout(1500)
    assert _boxes(page) == loaded
    greyed = page.locator("main").get_by_role("link", name="Open Census changes")
    assert greyed.get_attribute("aria-disabled") == "true"
    greyed.focus()
    visible(page.locator("#" + greyed.get_attribute("aria-describedby")))
    painted(page)
    assert _boxes(page) == loaded


def test_the_counts_update_by_themselves(page, component_origin):
    """A minute on, the status fragment's newer counts replace the old ones
    in place: no reload, no reload instruction, and the change announced
    once in one sentence."""
    reads = []

    def answer(route):
        """The status fragment, a minute on."""
        reads.append(route.request.url)
        route.fulfill(content_type="text/html", body=later_status())

    page.route("**/admin/parish/data-to-fix/status/", answer)
    page.clock.install(time=NOW)
    page.goto(component_origin + PATH)
    page.evaluate(MARK)
    main = page.locator("main")
    assert main.get_by_text("Reload", exact=False).count() == 0
    refused = page.locator('[data-section="refused_addresses"] [data-count]')
    has_text(refused, "3 refused email addresses are not cleared yet, from 2 Families.")
    page.clock.fast_forward(61 * 1000)
    has_text(refused, "1 refused email address is not cleared yet, from 1 Family.")
    has_text(
        page.locator('[data-section="form_cannot_open"] [data-count]'),
        "1 Family cannot open the Family form because a Member's ParishSoft data "
        "has a value the form cannot accept.",
    )
    assert page.evaluate(MARKED) == "kept"
    assert page.url == component_origin + PATH
    assert len(reads) == 1
    has_text(
        page.locator('[data-live-announcer="data-to-fix"]'),
        "2 problems to fix in ParishSoft.",
    )

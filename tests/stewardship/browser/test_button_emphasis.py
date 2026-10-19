"""At most one green (primary) button shows per control group (#1713).

The template guard (test_button_emphasis_guard.py) reads template text, so it
cannot follow an {% include %} or see a button a script builds. This test
opens representative pages and counts, in each rendered group, the visible
buttons (and the setup wizard's Continue links) drawn in the accent fill:
the colour, not the markup, is what the reader sees. A group is a form, a
dialog, an element with one of the group classes or ``data-control-group``,
or a row of sibling forms; a button with a
``form`` attribute belongs to that form. Selected-state controls (tabs,
segmented pairs, pressed icons) are separate from the rule and are skipped.
ui-conventions "Button emphasis" is the rule.
"""

import pytest

from ..test_button_emphasis_guard import GROUP_CLASSES
from .automation_components import ACCESS, HOME
from .census_components import PATH as CENSUS
from .system_health_components import PAGE as SYSTEM_HEALTH

pytestmark = pytest.mark.parametrize("browser_engine", ["chromium"], indirect=True)

# The template guard's groups, as one CSS selector.
GROUP_SELECTOR = ", ".join(
    ["form", "dialog", "[data-control-group]"]
    + [f".{name}" for name in sorted(GROUP_CLASSES)]
)

# Each crowded group: its tag and classes, and the labels of its green
# buttons. The accent is read from a probe so the token stays the one
# source of the colour.
CROWDED_GROUPS = """(groupSelector) => {
  const probe = document.createElement("span");
  probe.style.background = "var(--accent)";
  document.body.append(probe);
  const accent = getComputedStyle(probe).backgroundColor;
  probe.remove();
  const selected = "[aria-pressed=true], [aria-current], [aria-selected=true], "
    + "[role=tab], .segmented-choice *";
  const groups = new Map();
  const add = (group, button) => {
    if (!groups.has(group)) groups.set(group, []);
    groups.get(group).push(button.textContent.trim().replace(/\\s+/g, " "));
  };
  // A form whose previous element sibling is a form joins that form's row.
  const row = (form) => {
    while (form.previousElementSibling?.tagName === "FORM") {
      form = form.previousElementSibling;
    }
    return form;
  };
  for (const button of document.querySelectorAll(
      "button, a.button, a.setup-continue, input[type=submit]")) {
    if (!button.checkVisibility({visibilityProperty: true})
        || button.matches(selected)) continue;
    if (getComputedStyle(button).backgroundColor !== accent) continue;
    const owner = button.getAttribute("form")
      && document.getElementById(button.getAttribute("form"));
    if (owner) { add(row(owner), button); continue; }
    for (let group = button.closest(groupSelector); group;
         group = group.parentElement?.closest(groupSelector)) {
      add(group.tagName === "FORM" ? row(group) : group, button);
    }
  }
  return [...groups].filter(([, labels]) => labels.length > 1).map(
    ([group, labels]) => `${group.tagName}.${group.className}: ${labels.join(" | ")}`);
}"""

PAGES = [
    HOME,
    ACCESS,
    SYSTEM_HEALTH,
    CENSUS,
    "/campaign-settings",
    "/setup",
    "/setup-parish",
    "/setup-mail",
    "/logs",
    "/limitations-report",
    "/hosted-files",
    "/background",
    "/delivery-refusals",
    "/ministries",
]


@pytest.mark.parametrize("path", PAGES)
def test_one_green_button_per_group(page, component_origin, path):
    """No rendered group shows two green buttons at once."""
    page.goto(component_origin + path)
    assert page.evaluate(CROWDED_GROUPS, GROUP_SELECTOR) == []


def test_the_sweep_finds_what_it_describes(page, component_origin):
    """Two green buttons in one form, or in a row of sibling forms, are
    found; a secondary button, a pressed control and a lone form are not."""
    page.goto(component_origin + HOME)
    page.evaluate(
        """() => document.querySelector("main").insertAdjacentHTML("beforeend",
        '<form id="sweep-a"><button type="button">A</button>'
        + '<button type="button">B</button></form>'
        + '<div><form><button type="button">C</button></form>'
        + '<form><button type="button">D</button></form></div>'
        + '<form><button type="button">E</button>'
        + '<button type="button" class="button-secondary">F</button>'
        + '<button type="button" aria-pressed="true">G</button></form>')"""
    )
    assert page.evaluate(CROWDED_GROUPS, GROUP_SELECTOR) == [
        "FORM.: A | B",
        "FORM.: C | D",
    ]

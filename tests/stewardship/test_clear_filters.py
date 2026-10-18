"""Clear filters beside every Admin filter form's Apply filters (#1651).

The ui-conventions spec, "Clear filters": every Admin filter form
(``form#table-filters``) ends with Apply filters and Clear filters, drawn
by components/filter-actions.html. Clear sends the form's hidden fields
only, except those marked ``data-filter`` (ui-v1.js), so every hidden field
a filter form draws must be kept state or marked as a filter. These tests
render the component and guard the templates.
"""

import re
from pathlib import Path

from django.template.loader import render_to_string

TEMPLATES = (
    Path(__file__).parents[2]
    / "src/parishkit/stewardship/accounts/templates/stewardship"
)
COMPONENT = "stewardship/components/filter-actions.html"
INCLUDE = '{% include "stewardship/components/filter-actions.html"'
# The Admin filter forms, by template (#1651's inventory). A new filter form
# is added here, so the list in the spec's review stays complete.
FILTER_FORMS = {
    "background.html",
    "census-changes.html",
    "deliveries.html",
    "delivery-refusals.html",
    "directory.html",
    "financial-report.html",
    "information.html",
    "limitations-report.html",
    "logs.html",
    "ministries.html",
    "ministry-followup.html",
    "ministry-roster.html",
}
# Hidden fields a filter form keeps when its filters are cleared: the
# table's sort and size (a TablePage's view_fields, drawn by a loop), the
# browser's time zone, and a segmented pair's view choice carried in the
# form (Sent mail's Production or Testing, #1562; Roster changes' Show,
# #1535), which Clear filters keeps by rule.
KEPT_NAMES = {"{{ name }}", "zone", "mode", "show"}
FORM = re.compile(r'<form id="table-filters".*?</form>', re.S)
HIDDEN = re.compile(r'<input type="hidden"[^>]*>')
NAME = re.compile(r'\bname="([^"]*)"')
# A submit button: the shared tag with type="submit", or a raw one.
SUBMIT = re.compile(r'\{%\s*button\b[^%]*type="submit"|<button\b|type="submit"')


def filter_forms():
    """Each template's filter form source, by template name."""
    forms = {}
    for path in sorted(TEMPLATES.glob("*.html")):
        match = FORM.search(path.read_text())
        if match:
            forms[path.name] = match.group(0)
    return forms


def test_the_inventory_names_every_filter_form():
    """Every form#table-filters is known, so none is left without Clear."""
    assert set(filter_forms()) == FILTER_FORMS


def test_every_filter_form_draws_its_buttons_through_the_component():
    """One include per form and no other submit button in it."""
    for name, form in filter_forms().items():
        assert form.count(INCLUDE) == 1, name
        assert not SUBMIT.search(form), name


def test_no_admin_template_draws_apply_filters_on_its_own():
    """ "Apply filters" is written only by the component."""
    for path in sorted(TEMPLATES.rglob("*.html")):
        if path.name == "filter-actions.html":
            continue
        assert 'translate "Apply filters" %}{% endbutton' not in path.read_text(), (
            path.name
        )


def test_every_hidden_field_of_a_filter_form_is_kept_or_a_filter():
    """Clear filters keeps every hidden field not marked data-filter, so a
    hidden field that carries a filter must say so."""
    for name, form in filter_forms().items():
        for field in HIDDEN.findall(form):
            if "data-filter" in field:
                continue
            assert NAME.search(field).group(1) in KEPT_NAMES, (name, field)


def test_component_draws_apply_then_clear_with_its_tip():
    """Apply filters first (Enter applies), then a secondary formnovalidate
    Clear filters and its floating tip, which names nothing until ui-v1.js
    marks the button unavailable."""
    html = render_to_string(COMPONENT, {"apply_hint": "log-filter-hint"})
    buttons = re.findall(r"<button\b[^>]*>[^<]*</button>", html)
    assert len(buttons) == 2
    apply, clear = buttons
    assert apply == (
        '<button type="submit" aria-describedby="log-filter-hint">'
        "Apply filters</button>"
    )
    assert clear == (
        '<button type="submit" class="button-secondary" formnovalidate'
        " data-clear-filters data-menu-tip>Clear filters</button>"
    )
    assert (
        '<span class="admin-tip" id="table-filters-clear-tip" role="tooltip">'
        "Every filter already shows its default.</span>"
    ) in html


def test_component_without_a_hint_names_none():
    """A form with no reserved hint line gets no aria-describedby."""
    html = render_to_string(COMPONENT, {})
    assert '<button type="submit">Apply filters</button>' in html

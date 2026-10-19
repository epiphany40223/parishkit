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
    "components/review-worklist.html",
    "deliveries.html",
    "delivery-refusals.html",
    "directory.html",
    "financial-report.html",
    "information.html",
    "limitations-report.html",
    "logs.html",
    "ministries.html",
    "ministry-roster.html",
    "timeline.html",
}
# Hidden fields a filter form keeps when its filters are cleared: the
# table's sort and size (a TablePage's view_fields, drawn by a loop), the
# browser's time zone, and a segmented pair's view choice carried in the
# form (Sent mail's Production or Testing, #1562; Roster changes' Show,
# #1535), which Clear filters keeps by rule.
KEPT_NAMES = {"{{ name }}", "zone", "mode", "show"}
# Visible fields that switch the data shown between Production and Testing:
# a view, not a filter, so Clear filters keeps them (data-view).
VIEW_NAMES = {"mode"}
# Tags are matched whatever their attribute order, so a form or field
# written another way never escapes the guard.
FORM = re.compile(r'<form\b[^>]*\bid="table-filters"[^>]*>.*?</form>', re.S)
FIELD = re.compile(r"<(?:input|select|textarea)\b[^>]*>")
HIDDEN = re.compile(r'\btype="hidden"')
NAME = re.compile(r'\bname="([^"]*)"')
# A submit button: the shared tag with type="submit", or a raw one.
SUBMIT = re.compile(r'\{%\s*button\b[^%]*type="submit"|<button\b|type="submit"')


def filter_forms(root=TEMPLATES):
    """Each template's filter form source, by its path under ``root``,
    searching every subdirectory."""
    forms = {}
    for path in sorted(root.rglob("*.html")):
        match = FORM.search(path.read_text())
        if match:
            forms[path.relative_to(root).as_posix()] = match.group(0)
    return forms


def unmarked_fields(form):
    """A filter form's fields that break the Clear filters rule: a hidden
    field neither kept state nor marked data-filter, or a visible
    Production or Testing choice not marked data-view."""
    bad = []
    for field in FIELD.findall(form):
        name = NAME.search(field)
        name = name.group(1) if name else ""
        if HIDDEN.search(field):
            if "data-filter" not in field and name not in KEPT_NAMES:
                bad.append(field)
        elif name in VIEW_NAMES and "data-view" not in field:
            bad.append(field)
    return bad


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
    hidden field that carries a filter must say so; and it resets every
    visible field not marked data-view, so a Production or Testing choice
    must say so."""
    for name, form in filter_forms().items():
        assert unmarked_fields(form) == [], name


def test_the_directory_keeps_its_responses_shown_choice():
    """The directory's Production or Testing choice is a view: its segmented
    pair's choice travels in the filter form as a kept hidden field, never
    marked data-filter."""
    form = filter_forms()["directory.html"]
    field = re.search(r'<input type="hidden" name="mode"[^>]*>', form)
    assert field and "data-filter" not in field.group(0)


def test_the_guard_finds_forms_and_fields_in_any_attribute_order(tmp_path):
    """A filter form in a subdirectory, with its id after other attributes,
    is found, and so are its hidden filter field and unmarked view choice
    whatever their attribute order; marked ones pass."""
    nested = tmp_path / "stewardship" / "reports"
    nested.mkdir(parents=True)
    (nested / "bad.html").write_text(
        '<form method="get" id="table-filters" data-in-place>'
        '<input name="secret" value="x" type="hidden">'
        '<input value="1" type="hidden" name="secret2" data-filter>'
        '<select aria-label="Mode" name="mode"><option>p</option></select>'
        '<input value="{{ v }}" type="hidden" name="zone">'
        "</form>"
    )
    (tmp_path / "plain.html").write_text('<form id="other"></form>')
    forms = filter_forms(tmp_path)
    assert set(forms) == {"stewardship/reports/bad.html"}
    bad = unmarked_fields(forms["stewardship/reports/bad.html"])
    assert bad == [
        '<input name="secret" value="x" type="hidden">',
        '<select aria-label="Mode" name="mode">',
    ]
    marked = '<form id="table-filters"><select data-view name="mode"></select></form>'
    assert unmarked_fields(marked) == []


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

"""Report filters refused at their field, not on an error page (#1332)."""

import re
from pathlib import Path

import pytest
from django.http import HttpResponse
from django.template import Context, Template

from parishkit.stewardship.reports.financial import FIELD_MESSAGES, FinancialQuery
from parishkit.stewardship.reports.financial_views import FIELD_IDS, FIELD_LINES
from parishkit.stewardship.web.filter_errors import (
    FilterError,
    error_context,
    line_sizes,
    refused,
)

TEMPLATE = (
    Path(__file__).parents[2]
    / "src/parishkit/stewardship/accounts/templates/stewardship/financial-report.html"
).read_text()


def render(source, **context):
    """Render a template that loads the stewardship tag library."""
    return Template("{% load stewardship %}" + source).render(Context(context))


@pytest.mark.parametrize(
    ("values", "fields"),
    [
        ({"first_start": "2026-02-30"}, ("first_start",)),
        ({"first_end": "soon"}, ("first_end",)),
        ({"latest_start": "2026-1-5"}, ("latest_start",)),
        ({"latest_end": "2026-13-01"}, ("latest_end",)),
        (
            {"first_start": "2026-09-02", "first_end": "2026-09-01"},
            ("first_end", "first_start"),
        ),
        (
            {"latest_start": "2026-09-02", "latest_end": "2026-09-01"},
            ("latest_end", "latest_start"),
        ),
        ({"pledge_min": "1,234"}, ("pledge_min",)),
        ({"pledge_max": "12.5"}, ("pledge_max",)),
        ({"pledge_min": "9", "pledge_max": "1"}, ("pledge_max", "pledge_min")),
    ],
)
def test_financial_field_rules_name_their_fields(values, fields):
    """Each rule names its field (a range's order both ends), words and query."""
    with pytest.raises(FilterError) as caught:
        FinancialQuery.parse(values | {"search": "Example", "sort": "pledge_desc"})
    error = caught.value
    assert error.fields == fields and error.field == fields[0]
    assert error.message == FIELD_MESSAGES[error.field]
    # The query as sent, for the form; its closed choices were all accepted.
    assert error.query.search == "Example" and error.query.sort == "pledge_desc"
    assert getattr(error.query, error.field) == values[error.field]
    # Still a ValueError: exports and pk-admin refuse it as before.
    assert isinstance(error, ValueError)


@pytest.mark.parametrize(
    "values",
    [
        {"sort": "random"},
        {"amount": "all"},
        {"share": "no-such-option"},
        {"extra": "x"},
        {"search": "x" * 201},
        {"page": "0"},
    ],
)
def test_financial_structural_refusals_keep_the_error_page(values):
    """A refusal no field can show stays a plain ValueError."""
    with pytest.raises(ValueError) as caught:
        FinancialQuery.parse(values)
    assert not isinstance(caught.value, FilterError)


def test_financial_messages_are_the_page_hints_and_fields_exist():
    """The server's words are the hints the browser shows first, at real ids.

    Every refusable field has a hint (data-missing-hint) that says the same
    as the server, so the two never disagree, its element carries the mark
    tag, and its range's reserved line is drawn once.
    """
    assert set(FIELD_IDS) == set(FIELD_MESSAGES) == set(FIELD_LINES)
    for field, element in FIELD_IDS.items():
        tag = re.search(rf'<input id="{element}" [^>]*>', TEMPLATE).group(0)
        assert f'name="{field}"' in tag
        assert f'{{% filter_mark filter_error "{field}" %}}' in tag
        hint = re.search(r"data-missing-hint=\"{% translate '([^']*)' %}\"", tag)
        assert hint.group(1) == str(FIELD_MESSAGES[field])
    for line in set(FIELD_LINES.values()):
        assert TEMPLATE.count(f'{{% filter_line filter_error "{line}" ') == 1


def test_mark_merges_with_the_field_s_own_descriptions():
    """One aria-describedby: the field's own ids, then its message line."""
    error = FilterError("first_end", "Fix it.", FinancialQuery(), pair="first_start")
    context = error_context(error, FIELD_IDS, FIELD_LINES)
    source = (
        '<input id="first-start"{% filter_mark filter_error "first_start" %}>'
        '<input id="first-end"'
        '{% filter_mark filter_error "first_end" "zone-help other-help" %}>'
        '<input id="pledge-min"{% filter_mark filter_error "pledge_min" "help" %}>'
        '<input id="pledge-max"{% filter_mark filter_error "pledge_max" %}>'
    )
    assert render(source, **context) == (
        '<input id="first-start" aria-invalid="true"'
        ' aria-describedby="first-response-dates_error">'
        '<input id="first-end" aria-invalid="true"'
        ' aria-describedby="zone-help other-help first-response-dates_error">'
        '<input id="pledge-min" aria-describedby="help">'
        '<input id="pledge-max">'
    )
    # No refusal: only the field's own ids.
    assert render(source) == (
        '<input id="first-start">'
        '<input id="first-end" aria-describedby="zone-help other-help">'
        '<input id="pledge-min" aria-describedby="help">'
        '<input id="pledge-max">'
    )
    # The base template's summary links to the field refused, the end.
    assert context["errors"] == [{"field_id": "first-end", "message": "Fix it."}]


def test_line_is_reserved_and_shows_only_its_own_refusal():
    """The line is always drawn at its longest message's size, hidden when idle."""
    sizes = line_sizes(FIELD_MESSAGES, FIELD_LINES)
    assert sizes["pledge-range"] == str(FIELD_MESSAGES["pledge_min"])
    error = FilterError("pledge_max", "Enter <b>less</b>.", FinancialQuery())
    context = error_context(error, FIELD_IDS, FIELD_LINES) | {"sizes": sizes}
    source = '{% filter_line filter_error "pledge-range" sizes %}'
    size = (
        '<div class="field-error-line"><span class="field-error-size"'
        ' aria-hidden="true">' + sizes["pledge-range"] + "</span>"
    )
    assert render(source, **context) == (
        size + '<ul class="errorlist" id="pledge-range_error">'
        "<li>Enter &lt;b&gt;less&lt;/b&gt;.</li></ul></div>"
    )
    idle = (
        size
        + '<ul class="errorlist" id="pledge-range_error" hidden><li></li></ul></div>'
    )
    assert render(source, sizes=sizes) == idle
    # Another range's refusal leaves this line idle.
    other = FilterError("first_end", "Fix it.", FinancialQuery())
    assert (
        render(source, sizes=sizes, **error_context(other, FIELD_IDS, FIELD_LINES))
        == idle
    )


def test_panel_opens_for_a_refused_filter_with_none_applied():
    """An error inside the Filters panel is never hidden behind it."""
    source = "{% filterpanel query %}<p>x</p>{% endfilterpanel %}"
    assert '<details class="filter-panel" data-filter-panel>' in render(
        source, query=FinancialQuery()
    )
    error = FilterError("first_end", "Enter a date.", FinancialQuery())
    html = render(
        source, query=FinancialQuery(), **error_context(error, FIELD_IDS, FIELD_LINES)
    )
    assert '<details class="filter-panel" data-filter-panel open>' in html
    # The count is of the filters the results use: none.
    assert "none applied" in html


def test_refused_answers_400_kept_and_logged_as_invalid():
    """The page drawn again is the view's own 400, with its refusal code."""
    response = refused(HttpResponse("page"))
    assert response.status_code == 400 and response.stewardship_safe_error is True
    assert response.stewardship_codes == ("invalid",)
    assert not hasattr(response, "stewardship_errors")

"""The shared Admin table pager: sizes, page clamping and navigator links."""

from urllib.parse import parse_qs

import pytest

from parishkit.stewardship.web.tables import paginate, table_parameters

ROWS = list(range(1, 131))


def test_default_size_and_first_page():
    """Fifty rows per page unless the caller chooses another default."""
    table = paginate(ROWS, {})
    assert table.rows == ROWS[:50] and (table.number, table.pages) == (1, 3)
    assert (table.first_index, table.last_index, table.count) == (1, 50, 130)
    assert table.previous_query is None
    assert parse_qs(table.next_query) == {"size": ["50"], "page": ["2"]}


def test_a_page_past_the_end_shows_the_last_page():
    """A typed or bookmarked page number never lands on an empty error."""
    table = paginate(ROWS, {"page": "99", "size": "25"})
    assert table.number == 6 and table.rows == ROWS[125:]
    assert (table.first_index, table.last_index) == (126, 130)
    assert table.next_query is None


def test_all_rows_on_one_page_and_empty_tables():
    """The All size shows every row; an empty table still has one page."""
    table = paginate(ROWS, {"size": "all"})
    assert table.rows == ROWS and table.pages == 1 and table.size_value == "all"
    empty = paginate([], {})
    assert (empty.pages, empty.first_index, empty.last_index) == (1, 0, 0)


def test_prefixes_and_carried_filters():
    """Prefixed tables page independently and links keep non-empty filters."""
    table = paginate(
        ROWS,
        {"b_page": "2", "b_size": "100"},
        prefix="b_",
        carry=(("q", "choir"), ("state", "")),
    )
    assert table.number == 2 and table.page_name == "b_page"
    assert parse_qs(table.previous_query) == {
        "q": ["choir"],
        "b_size": ["100"],
        "b_page": ["1"],
    }
    assert table_parameters("b_") == {"b_page", "b_size"}
    assert [value for value, _, chosen in table.size_choices if chosen] == ["100"]


@pytest.mark.parametrize(
    "values", [{"size": "7"}, {"size": "x"}, {"page": "-1"}, {"page": "1" * 10}]
)
def test_malformed_values_are_refused(values):
    """Unknown sizes and malformed page numbers are client errors."""
    with pytest.raises(ValueError):
        paginate(ROWS, values)


def test_window_table_links_without_a_known_total():
    """A PageWindow page shows neighbours and sizes but never invents a total."""
    from parishkit.stewardship.web.contracts import PageWindow
    from parishkit.stewardship.web.tables import WINDOW_SIZES, window_table

    table = window_table(
        PageWindow(2, 25), list(range(25)), True, carry=[("state", "all")]
    )
    assert (table.count, table.pages) == (None, None)
    assert (table.first_index, table.last_index) == (26, 50)
    assert table.previous_query == "state=all&size=25&page=1"
    assert table.next_query == "state=all&size=25&page=3"
    assert [value for value, _, _ in table.size_choices] == [
        str(size) for size in WINDOW_SIZES
    ]
    last = window_table(PageWindow(3, 25), [1, 2], False)
    assert last.next_query is None and last.last_index == 52


def test_window_table_lists_a_nonstandard_accepted_size():
    """An older URL's size stays visible instead of silently changing."""
    from parishkit.stewardship.web.contracts import PageWindow
    from parishkit.stewardship.web.tables import window_table

    table = window_table(PageWindow(1, 7), [], False)
    assert ("7", "7", True) in table.size_choices
    assert table.first_index == 0 and table.next_query is None


def test_window_navigator_omits_total_and_page_count():
    """The shared navigator must not print "of N" for windowed tables."""
    from django.template.loader import render_to_string

    from parishkit.stewardship.web.contracts import PageWindow
    from parishkit.stewardship.web.tables import window_table

    html = render_to_string(
        "stewardship/table-navigator.html",
        {"table": window_table(PageWindow(1, 25), [1, 2], True), "label": "Pages"},
    )
    assert "Showing 1–2" in html and " of " not in html
    assert 'rel="next"' in html and "max=" not in html

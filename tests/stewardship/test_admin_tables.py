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

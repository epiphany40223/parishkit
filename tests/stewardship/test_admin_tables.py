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
    assert table_parameters("b_") == {"b_page", "b_size", "b_sort"}
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


NAMES = [
    {"id": 1, "name": "carol", "when": 3},
    {"id": 2, "name": "alice", "when": None},
    {"id": 3, "name": "bob", "when": 3},
    {"id": 4, "name": "alice", "when": 1},
]


def _sorting():
    """Two in-memory columns; times sort newest first on their first click."""
    from parishkit.stewardship.web.tables import Sorting

    return Sorting.by_column(
        {"name": lambda row: row["name"], "when": lambda row: row["when"]},
        default="name",
        descending_first={"when"},
    )


def test_sorting_tokens_are_a_closed_whitelist():
    """Only declared tokens parse; the first-click token leads each column."""
    sorting = _sorting()
    assert list(sorting.tokens) == ["name", "-name", "-when", "when"]
    assert sorting.parse({}) == "name" and sorting.parse({"x_sort": "-when"}, "x_")
    for token in ("created_at", "name; drop table", "--name", ""):
        with pytest.raises(ValueError):
            sorting.parse({"sort": token})
    with pytest.raises(ValueError):
        paginate(NAMES, {"sort": "id"}, sorting=sorting)


def test_in_memory_sort_is_stable_and_puts_missing_values_last():
    """Equal values keep source order in both directions, and a missing value
    sorts last in both, so "newest first" never opens on empty rows."""
    sorting = _sorting()
    ids = [row["id"] for row in paginate(NAMES, {}, sorting=sorting).rows]
    assert ids == [2, 4, 3, 1]
    down = paginate(NAMES, {"sort": "-name"}, sorting=sorting).rows
    assert [row["id"] for row in down] == [1, 3, 2, 4]
    newest = paginate(NAMES, {"sort": "-when"}, sorting=sorting).rows
    assert [row["id"] for row in newest] == [1, 3, 4, 2]
    oldest = paginate(NAMES, {"sort": "when"}, sorting=sorting).rows
    assert [row["id"] for row in oldest] == [4, 1, 3, 2]


def test_sorted_links_keep_the_sort_and_headings_restart_at_page_one():
    """Navigator links carry the sort; a heading keeps filters and size,
    toggles the sorted column and starts other columns at their first click."""
    table = paginate(
        ROWS,
        {"sort": "-when", "page": "2", "size": "25"},
        carry=(("q", "x"),),
        sorting=_sorting_for_numbers(),
    )
    assert parse_qs(table.next_query) == {
        "q": ["x"],
        "sort": ["-when"],
        "size": ["25"],
        "page": ["3"],
    }
    assert table.aria_sort("when") == "descending" and table.aria_sort("name") is None
    assert parse_qs(table.heading_query("when")) == {
        "q": ["x"],
        "size": ["25"],
        "sort": ["when"],
    }
    assert table.sort_target("name") == "name" and not table.sort_descends("name")
    assert table.view_fields == [("size", "25"), ("sort", "-when")]
    with pytest.raises(ValueError):
        table.aria_sort("id")


def _sorting_for_numbers():
    """A two-column sort over the plain integers in ROWS."""
    from parishkit.stewardship.web.tables import Sorting

    return Sorting.by_column(
        {"name": str, "when": lambda value: value},
        default="name",
        descending_first={"when"},
    )


def test_database_order_appends_a_unique_tiebreak_in_the_same_direction():
    """Querysets order by server-owned terms, NULLs last, then the unique key."""
    from django.db.models import F
    from django.db.models.functions import Lower

    from parishkit.stewardship.web.tables import Sorting

    class Query:
        """Records order_by terms like a queryset would receive them."""

        def order_by(self, *terms):
            self.terms = terms
            return self

    sorting = Sorting.by_column(
        {"name": (Lower("name"),), "created": ("created_at",)},
        default="-created",
        descending_first={"created"},
        tiebreak=("id",),
    )
    newest = sorting.order(Query(), "-created").terms
    assert str(newest[0]) == str(F("created_at").desc(nulls_last=True))
    assert newest[1] == "-id"
    assert sorting.order(Query(), "created").terms == ("created_at", "id")
    terms = sorting.order(Query(), "-name").terms
    assert terms[0].descending and terms[0].nulls_last and terms[1] == "-id"
    # A pre-negated term flips too, and keeps NULLs last when it descends.
    flipped = Sorting.by_column({"age": ("-age",)}, default="age", tiebreak=())
    assert str(flipped.order(Query(), "age").terms[0]) == str(
        F("age").desc(nulls_last=True)
    )
    assert flipped.order(Query(), "-age").terms == ("age",)
    with pytest.raises(ValueError):
        Sorting({"a": ("a", False)}, "b")


def test_bounded_count_stops_at_its_limit():
    """A windowed table never counts past the limit."""
    from parishkit.stewardship.web.tables import bounded_count

    class Query:
        """Counts a slice the way a queryset's LIMITed subquery would."""

        def __init__(self, total):
            self.total, self.stop = total, None

        def order_by(self):
            return self

        def __getitem__(self, window):
            self.stop = window.stop
            return self

        def count(self):
            return min(self.total, self.stop)

    assert bounded_count(Query(7), limit=10) == (7, False)
    assert bounded_count(Query(10), limit=10) == (10, False)
    assert bounded_count(Query(10_000_000), limit=10) == (10, True)


def test_counted_window_shows_page_n_of_m_and_capped_counts_say_more():
    """A counted windowed table knows its pages; a capped count does not."""
    from django.template.loader import render_to_string

    from parishkit.stewardship.web.contracts import PageWindow
    from parishkit.stewardship.web.tables import window_table

    table = window_table(PageWindow(2, 25), list(range(25)), True, total=(60, False))
    assert (table.pages, table.page_label) == (3, (2, 3))
    html = render_to_string(
        "stewardship/table-navigator.html", {"table": table, "label": "Pages"}
    )
    assert "Showing 26–50 of 60" in html and "Page 2 of 3" in html
    assert 'max="3"' in html
    capped = window_table(PageWindow(1, 25), list(range(25)), True, total=(10, True))
    assert capped.next_query and capped.page_label == (1, None)
    html = render_to_string(
        "stewardship/table-navigator.html", {"table": capped, "label": "Pages"}
    )
    assert "of more than 10" in html and "max=" not in html


def test_post_tables_keep_private_filters_out_of_urls():
    """A report table's navigator and headings are CSRF POST forms."""
    from django.template import Context, Template

    from parishkit.stewardship.web.tables import Sorting, report_table

    sorting = Sorting(
        {"name": ("family", False), "name_desc": ("family", True)}, "name"
    )
    table = report_table(
        [1, 2],
        number=2,
        size=50,
        total=120,
        carry=(("search", "private name"),),
        sorting=sorting,
        sort="name",
        action="/admin/report/",
    )
    assert table.pages == 3 and table.previous_query and table.next_fields
    html = Template(
        "{% load stewardship %}"
        '{% include "stewardship/table-navigator.html" with label="P" %}'
        '{% sort_heading table "family" "Family" %}'
    ).render(Context({"table": table, "csrf_token": "t0ken"}))
    assert "href=" not in html and html.count('method="post"') == 4
    assert html.count('value="private name"') == 4
    assert 'aria-sort="ascending"' in html
    assert '<input type="hidden" name="sort" value="name_desc">' in html
    assert "(sort descending)" in html and "t0ken" in html


def test_get_heading_is_a_labelled_link():
    """A GET table's heading links to page one of the other direction."""
    from django.template import Context, Template

    table = paginate(NAMES, {"sort": "-name"}, sorting=_sorting())
    html = Template(
        '{% load stewardship %}{% sort_heading table "name" "Name" "numeric" %}'
        '{% sort_heading table "when" "When" %}'
    ).render(Context({"table": table}))
    assert (
        '<th scope="col" class="numeric" aria-sort="descending" '
        'data-sort-column="name">' in html
    )
    assert 'href="?size=50&amp;sort=name#table"' in html
    assert 'href="?size=50&amp;sort=-when#table"' in html
    assert html.count("aria-sort") == 1 and "(sort ascending)" in html


def test_table_anchors_are_unique_per_prefix():
    """Each table's region id comes from its prefix, so two tables on one page
    never share an id and a prefix-less table keeps the short "table"."""
    assert paginate(ROWS, {}).anchor == "table"
    assert paginate(ROWS, {}, prefix="members_").anchor == "members-table"
    assert paginate(ROWS, {}, prefix="b-").anchor == "b-table"


def test_every_control_ends_in_the_table_anchor():
    """Navigator links and forms, like headings, name the table's region, so a
    full page load (no script, or a failed in-place fetch) lands on the table;
    the controls ui-v1.js refocuses after an in-place swap are marked (#478)."""
    from dataclasses import replace

    from django.template.loader import render_to_string

    table = paginate(ROWS, {"b_page": "2", "b_size": "25"}, prefix="b_")
    html = render_to_string(
        "stewardship/table-navigator.html", {"table": table, "label": "Pages"}
    )
    previous = 'href="?b_size=25&amp;b_page=1#b-table" rel="prev" data-table-previous'
    assert previous in html
    assert 'href="?b_size=25&amp;b_page=3#b-table" rel="next" data-table-next' in html
    assert '<form method="get" action="#b-table" class="table-nav-form"' in html
    post = render_to_string(
        "stewardship/table-navigator.html",
        {
            "table": replace(table, method="post", action="/admin/report/"),
            "label": "Pages",
            "csrf_token": "t0ken",
        },
    )
    assert post.count('action="/admin/report/#b-table"') == 3
    assert "data-table-previous>" in post and "data-table-next>" in post
    assert "href=" not in post


def test_never_signed_in_users_sort_after_the_latest_sign_in():
    """Portal users' "Last successful sign-in" opens on real sign-ins."""
    from datetime import UTC, datetime

    from parishkit.stewardship.accounts.user_views import ADDRESS_SORTING, ADDRESSES

    rows = [
        {"email": "never@example.org", "last_login": None},
        {"email": "old@example.org", "last_login": datetime(2026, 1, 1, tzinfo=UTC)},
        {"email": "new@example.org", "last_login": datetime(2026, 9, 1, tzinfo=UTC)},
    ]
    for token, expected in (
        ("-last_login", ["new", "old"]),
        ("last_login", ["old", "new"]),
    ):
        table = paginate(
            rows, {f"{ADDRESSES}sort": token}, prefix=ADDRESSES, sorting=ADDRESS_SORTING
        )
        emails = [row["email"].split("@")[0] for row in table.rows]
        assert emails == [*expected, "never"]


def test_a_window_past_the_end_reads_the_last_page():
    """Windowed reads clamp to the last page of a known, uncapped count."""
    from parishkit.stewardship.web.contracts import PageWindow
    from parishkit.stewardship.web.tables import clamp_window, read_window

    assert clamp_window(PageWindow(9, 25), (60, False)).page == 3
    assert clamp_window(PageWindow(9, 25), (0, False)).page == 1
    assert clamp_window(PageWindow(2, 25), (60, False)).page == 2
    # A capped count is only a lower bound, so the requested page stands.
    assert clamp_window(PageWindow(900, 25), (10, True)).page == 900
    window, rows, has_next = read_window(PageWindow(5, 2), list(range(5)), (5, False))
    assert (window.page, rows, has_next) == (3, [4], False)


def test_report_pages_past_the_end_move_back_even_for_empty_results():
    """A POST report asked for a page past its result reads the last page,
    which is page 1 when a filter change emptied the result."""
    from dataclasses import dataclass

    from parishkit.stewardship.reports.report_paging import clamp_query

    @dataclass(frozen=True)
    class Query:
        """Just the page field every report query has."""

        page: int

    assert clamp_query(Query(5), 0, 50) == Query(1)
    assert clamp_query(Query(5), 120, 50) == Query(3)
    assert clamp_query(Query(3), 120, 50) is None

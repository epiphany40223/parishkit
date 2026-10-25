"""Review these one at a time on the review tables (#2123 slice 4).

Review pipeline spec, "Wizard entry points": what each table's button posts
(its view, narrowed to the wizard start's fields, and its sort mapped to the
wizard's), and how it is drawn: unavailable with its reason on an empty
table, and never asking about unfinished runs (R52 Part W).
"""

from dataclasses import dataclass, field
from types import SimpleNamespace

import pytest
from django.template.loader import render_to_string

from parishkit.stewardship.pipeline import Stream
from parishkit.stewardship.reports.review_streams import BY_KEY
from parishkit.stewardship.reports.review_wizard_tables import (
    EMPTY,
    TableEntry,
    table_entry,
    table_fields,
    table_sort,
)
from parishkit.stewardship.web.tables import Sorting

SORTING = Sorting.by_column(
    {"family": ("family",), "submitted": ("submitted",)},
    default="-submitted",
    descending_first={"submitted"},
)


@dataclass
class FakeWorklist:
    """The parts of ``review_worklist.Worklist`` the entry reads."""

    stream: Stream
    fields: dict
    prefix: str = "census"


@dataclass
class FakeTable:
    """The parts of ``tables.TablePage`` the entry reads."""

    sort: str | None = None
    sorting: Sorting | None = SORTING
    rows: list = field(default_factory=lambda: [{"id": 1}])


@pytest.mark.parametrize(
    ("sort", "expected"),
    [
        ("submitted", "oldest"),
        ("-submitted", "newest"),
        ("family", "name"),
        ("-family", "name"),
        (None, "name"),
    ],
)
def test_the_table_sort_maps_to_the_wizard_sort(sort, expected):
    """Submitted ascending and descending are Oldest and Newest submitted
    first; any other column is Last name (A–Z)."""
    assert table_sort(FakeTable(sort=sort)) == expected


def test_a_table_with_no_sorting_maps_to_name_order():
    """A table drawn without a sort choice starts in Last name (A–Z)."""
    assert table_sort(FakeTable(sort="anything", sorting=None)) == "name"


def test_the_fields_are_the_tables_view_narrowed_to_the_start():
    """Stage, search and the stream's closed filters; never page or dates."""
    worklist = FakeWorklist(
        Stream.CENSUS,
        {
            "stage": "conflict",
            "search": "Smith",
            "route": "manual",
            "kind": "any",
            "page": "3",
            "start": "",
            "sort": "family",
        },
    )
    fields = table_fields(BY_KEY["census"], worklist, FakeTable(sort="family"))
    assert fields == [
        ("stream", "census"),
        ("mode", "advanced"),
        ("sort", "name"),
        ("table", "1"),
        ("stage", "conflict"),
        ("search", "Smith"),
        ("route", "manual"),
        ("kind", "any"),
    ]


def test_a_ministry_tables_empty_filters_are_left_out():
    """An empty Ministry (every Ministry) is the default, so it is not posted."""
    worklist = FakeWorklist(
        Stream.MINISTRY,
        {"stage": "to_review", "search": "", "ministry": "", "kind": "join"},
        prefix="followup",
    )
    names = [
        name for name, _value in table_fields(BY_KEY["ministry"], worklist, FakeTable())
    ]
    assert names == ["stream", "mode", "sort", "table", "stage", "kind"]


@pytest.mark.parametrize(
    ("stream", "key"),
    [
        (Stream.CENSUS, "census"),
        (Stream.MINISTRY, "ministry"),
        (Stream.FINANCIAL, "pledges"),
        (Stream.ADDITIONAL_INFORMATION, "information"),
        (Stream.CANNOT_PARTICIPATE, "limitations"),
    ],
)
def test_every_review_table_starts_its_own_stream(stream, key):
    """Each of the five tables posts its own wizard stream."""
    entry = table_entry(FakeWorklist(stream, {"stage": "all"}), FakeTable())
    assert ("stream", key) in entry.fields
    assert entry.reason is None


def test_an_empty_table_is_unavailable():
    """Nothing listed: the button names its reason."""
    entry = table_entry(
        FakeWorklist(Stream.CENSUS, {"stage": "all"}), FakeTable(rows=[])
    )
    assert entry.reason == str(EMPTY)


def render(entry):
    """The table's button as the selection bar draws it."""
    return render_to_string(
        "stewardship/components/review-wizard-entry.html", {"entry": entry}
    )


def test_an_unavailable_button_names_its_reason():
    """Disabled, described by its visible reason beside it."""
    html = render(TableEntry("census", [("stream", "census")], reason=str(EMPTY)))
    assert "disabled" in html
    assert 'aria-describedby="census-wizard-reason"' in html
    assert f'id="census-wizard-reason">{EMPTY}</span>' in html
    assert "<dialog" not in html


@pytest.mark.parametrize(
    ("module", "name"),
    [
        ("census_change_views", "SORTING"),
        ("ministry_worklist", "WORKLIST_SORTING"),
        ("pledge_review_views", "SORTING"),
        ("information_worklist", "SORTING"),
        ("limitation_worklist", "SORTING"),
    ],
)
def test_every_tables_own_sort_maps(module, name):
    """Each real table's tokens: its Submitted ones map to Oldest and Newest,
    every other one to Last name (A–Z)."""
    from importlib import import_module

    sorting = getattr(import_module(f"parishkit.stewardship.reports.{module}"), name)
    expected = {False: "oldest", True: "newest"}
    for token, (column, descending) in sorting.tokens.items():
        want = expected[descending] if column == "submitted" else "name"
        assert table_sort(FakeTable(sort=token, sorting=sorting)) == want, token
    assert any(column == "submitted" for column, _ in sorting.tokens.values())


def test_the_button_starts_at_once():
    """The button submits its form, with no dialog; the search travels only
    in the form's body, never in an address."""
    html = render(TableEntry("census", [("stream", "census"), ("search", "Smith")]))
    assert '<input type="hidden" name="search" value="Smith">' in html
    assert "?search" not in html
    assert 'form="census-wizard"' in html
    assert "data-wizard-ask" not in html
    assert "<dialog" not in html
    assert "disabled" not in html


def test_the_tag_draws_nothing_without_a_signed_in_reader():
    """A render with no principal (a unit render of the table) has no button."""
    from parishkit.stewardship.accounts.templatetags.review_work import (
        review_wizard_entry,
    )

    context = SimpleNamespace(request=SimpleNamespace())
    worklist = FakeWorklist(Stream.CENSUS, {"stage": "all"})
    assert review_wizard_entry(context, worklist, FakeTable()) is None

"""The review work tables' default name order (#2124), without a database.

UX conventions, "Default row order": every review work table lists its rows
by the shown name (casefolded), then that name's DUID (a Family's or
Member's rows stay together), the finer parts and the row's own id, so
equal names always come out in the same order, on every page and refresh,
and a column the reader sorts keeps that order for its ties. Each stream
orders rows it has already read whole, in memory, through its ``SORTING``
(``WORKLIST_SORTING`` for Ministry requests); these tests
feed each one several same-name rows in scrambled orders.
"""

import random
from datetime import UTC, datetime

import pytest

from parishkit.stewardship.reports import (
    census_change_views,
    information_worklist,
    limitation_worklist,
    ministry_worklist,
    pledge_review_views,
)
from parishkit.stewardship.web.tables import Sorting, name_order, paginate

# One submission time for every row, so a Submitted sort leaves them all
# tied and must fall back to the default order.
SAME_TIME = datetime(2054, 10, 5, 12, tzinfo=UTC)


def scrambled(rows, seed):
    """A shuffled copy of ``rows``: the order a read may hand them over in."""
    copy = [dict(row) for row in rows]
    random.Random(seed).shuffle(copy)
    return copy


def ids(rows):
    """The rows' ids, in order."""
    return [row["id"] for row in rows]


def family_rows(**extra):
    """Same-name Families (and a differently cased one) in the documented order.

    The expected order: "Smith, Ann" before "smith, Bob" (casefolded), and
    among the three "Smith, Ann" Families the lower DUID first, then for
    equal DUIDs (two requests of one Family) the lower id.
    """
    return [
        {"id": "a1", "family_name": "Adams, Kim", "family_duid": 90} | extra,
        {"id": "b1", "family_name": "Smith, Ann", "family_duid": 12} | extra,
        {"id": "b2", "family_name": "SMITH, ANN", "family_duid": 30} | extra,
        {"id": "b3", "family_name": "Smith, Ann", "family_duid": 30} | extra,
        {"id": "b4", "family_name": "Smith, Ann", "family_duid": 41} | extra,
        {"id": "c1", "family_name": "smith, Bob", "family_duid": 5} | extra,
        # A Family the current data no longer names sorts last.
        {"id": "a0", "family_name": None, "family_duid": 70} | extra,
    ]


FAMILY_ORDER = ["a1", "b1", "b2", "b3", "b4", "c1", "a0"]


def assert_stable(sorting, rows, expected, sort=None):
    """Every scrambled read of ``rows`` lists ``expected`` (``sort`` or default)."""
    token = {} if sort is None else {"sort": sort}
    for seed in range(8):
        whole = paginate(
            scrambled(rows, seed), token | {"size": "all"}, sorting=sorting
        )
        assert ids(whole.rows) == expected, seed


def assert_paged(sorting, rows, expected, size=2):
    """Pages of ``size`` rows, each from its own scrambled read, cover the order.

    Each page is cut from a fresh read, as a reader's next page is, so no
    row repeats or goes missing between pages.
    """
    shown = []
    for number in range(1, -(-len(rows) // size) + 1):
        read = scrambled(rows, number)
        page = sorting.sort_rows(read, sorting.default)
        shown += ids(page[(number - 1) * size : number * size])
    assert shown == expected


def test_name_order_casefolds_and_puts_a_missing_duid_last():
    """The shared key: names casefolded, a row without a DUID after those with one."""
    assert name_order("SMITH, Ann", 3, "x") == name_order("smith, ann", 3, "x")
    assert name_order("Smith", None, "a") > name_order("Smith", 9, "z")
    # The DUID comes before the finer parts, so two same-named Families'
    # rows never interleave.
    assert name_order("Smith", 1, "z") < name_order("Smith", 2, "a")


def test_name_order_puts_a_row_with_no_name_last():
    """A row with no name follows every named row, as a missing DUID does."""
    assert name_order("Zed", 1, "1") < name_order(None, 1, "1")
    assert name_order("Zed", 1, "1") < name_order("", None, "1")


def test_name_order_sorts_an_accented_name_with_its_base_letter():
    """ "Étienne" sorts beside "Etienne", not after Z; the finer parts fold too."""
    names = ["Zed", "Étienne", "Eve", "Adam"]
    ordered = sorted(names, key=lambda name: name_order(name, 1, "1"))
    assert ordered == ["Adam", "Étienne", "Eve", "Zed"]
    assert name_order("Étienne", 1, "1") == name_order("etienne", 1, "1")
    assert name_order("A", 1, "Éa", "1") < name_order("A", 1, "Eb", "1")


def test_name_order_compares_ids_numerically_and_deterministically():
    """Row ids that are numbers (or digit strings) sort by value, not text."""
    keys = [name_order("A", 1, row_id) for row_id in ("10", "9", 2, "added-1")]
    assert sorted(keys) == [
        name_order("A", 1, 2),
        name_order("A", 1, "9"),
        name_order("A", 1, "10"),
        name_order("A", 1, "added-1"),
    ]


def test_sorting_then_keeps_the_default_order_for_ties_in_either_direction():
    """A chosen column sorts first; its ties keep ``then``'s ascending order."""
    sorting = Sorting.by_column(
        {"name": lambda row: row["name"], "size": lambda row: row["size"]},
        default="name",
        descending_first={"size"},
        then=lambda row: row["id"],
    )
    rows = [
        {"id": 3, "name": "b", "size": 1},
        {"id": 1, "name": "a", "size": 1},
        {"id": 2, "name": "a", "size": 2},
    ]
    assert ids(sorting.sort_rows(rows, "name")) == [1, 2, 3]
    assert ids(sorting.sort_rows(rows, "-name")) == [3, 1, 2]
    # Either direction, the two size-1 rows keep the default (id) order.
    assert ids(sorting.sort_rows(rows, "size")) == [1, 3, 2]
    assert ids(sorting.sort_rows(rows, "-size")) == [2, 1, 3]


def test_census_changes_order_by_family_who_duid_what_and_id():
    """Family name, Family DUID, Who, What changed, then the change's id."""
    rows = [
        row | {"who": "Ann", "label": "Email", "member_duid": None}
        for row in family_rows(status="to_review", submitted_at=SAME_TIME)
    ]
    # One Family's several changes: Who first ("Family" before a Member's
    # name), then What changed, then the id.
    rows += [
        {
            "id": f"d{key}",
            "family_name": "Smith, Ann",
            "family_duid": 12,
            "who": who,
            "label": label,
            "member_duid": None,
            "submitted_at": SAME_TIME,
        }
        for key, who, label in (
            (1, "Smith, Zoe", "Email"),
            (2, "Family", "Home phone"),
            (3, "Smith, Zoe", "Cell phone"),
            (4, "Family", "Home phone"),
        )
    ]
    # The DUID comes before Who, so the Family 12's rows ("Ann", then
    # "Family", then "Smith, Zoe") stay together ahead of the Family 30's.
    expected = ["a1", "b1", "d2", "d4", "d3", "d1", "b2", "b3", "b4", "c1", "a0"]
    sorting = census_change_views.SORTING
    assert sorting.default == "family"
    assert_stable(sorting, rows, expected)
    assert_paged(sorting, rows, expected)
    # Submitted ties every row, so either direction lists the default order.
    assert_stable(sorting, rows, expected, sort="-submitted")
    assert_stable(sorting, rows, expected, sort="submitted")


def test_census_changes_reversed_family_keeps_ties_in_the_default_order():
    """Z to A by Family; within one name the rows stay DUID, Who and id order."""
    rows = [row | {"who": "Ann", "label": "Email"} for row in family_rows()]
    expected = ["c1", "b1", "b2", "b3", "b4", "a1", "a0"]
    assert_stable(census_change_views.SORTING, rows, expected, sort="-family")


def test_limitations_order_by_family_member_duid_and_id():
    """Family name, DUID, Member name (the Family's own answer first), id."""
    rows = [row | {"submitted_at": SAME_TIME} for row in family_rows()]
    rows += [
        {
            "id": f"m{key}",
            "family_name": "Smith, Ann",
            "family_duid": 12,
            "member_name": member,
            "submitted_at": SAME_TIME,
        }
        for key, member in ((1, "Smith, Zoe"), (2, "smith, al"), (3, "Smith, Zoe"))
    ]
    expected = ["a1", "b1", "m2", "m1", "m3", "b2", "b3", "b4", "c1", "a0"]
    sorting = limitation_worklist.SORTING
    assert sorting.default == "family"
    assert_stable(sorting, rows, expected)
    assert_paged(sorting, rows, expected)
    assert_stable(sorting, rows, expected, sort="-submitted")


@pytest.mark.parametrize(
    "sorting",
    [information_worklist.SORTING, pledge_review_views.SORTING],
    ids=["additional information", "pledges"],
)
def test_family_streams_order_by_family_duid_and_id(sorting):
    """Additional information and Pledges: Family name, Family DUID, then id."""
    rows = [row | {"submitted_at": SAME_TIME} for row in family_rows()]
    assert sorting.default == "family"
    assert_stable(sorting, rows, FAMILY_ORDER)
    assert_paged(sorting, rows, FAMILY_ORDER)
    assert_stable(sorting, rows, FAMILY_ORDER, sort="-submitted")
    # By DUID: equal DUIDs (one Family's two requests) keep name, then id.
    by_duid = ["c1", "b1", "b2", "b3", "b4", "a0", "a1"]
    assert_stable(sorting, rows, by_duid, sort="duid")


def ministry_rows():
    """Same-name Members' requests across Ministries, as the selection reads them."""
    return [
        {
            "id": key,
            "member_label": member,
            "ministry_name": ministry,
            "member_duid": duid,
            "submitted_at": SAME_TIME,
        }
        for key, member, ministry, duid in (
            ("r1", "Smith, Ann", "Choir", 30),
            ("r2", "smith, ann", "Choir", 12),
            ("r3", "Smith, Ann", "altar servers", 30),
            ("r4", "Smith, Ann", "Choir", None),
            ("r5", "Adams, Kim", "Lectors", 8),
            ("r6", "Smith, Ann", "Choir", 12),
        )
    ]


def test_ministry_requests_order_by_member_duid_ministry_and_id():
    """Member name, Member DUID (a proposed Member last), Ministry name, id."""
    sorting = ministry_worklist.WORKLIST_SORTING
    assert sorting.default == "name"
    assert ministry_worklist.WorklistQuery().sort == "name"
    expected = ["r5", "r2", "r6", "r3", "r1", "r4"]
    assert_stable(sorting, ministry_rows(), expected)
    assert_paged(sorting, ministry_rows(), expected)
    # Newest first ties every row here, so it keeps the default order.
    assert_stable(sorting, ministry_rows(), expected, sort="newest")
    # By Ministry, then the default order within each Ministry.
    assert_stable(
        sorting, ministry_rows(), ["r3", "r2", "r6", "r1", "r4", "r5"], sort="ministry"
    )


def test_ministry_reads_come_back_in_the_view_sort():
    """``read_rows``' result is ordered once, for the table, pages and download."""
    result = {"rows": scrambled(ministry_rows(), 3)}
    assert ids(ministry_worklist._ordered(result, "name")["rows"]) == [
        "r5",
        "r2",
        "r6",
        "r3",
        "r1",
        "r4",
    ]


def test_the_hub_tabs_list_their_rows_in_the_default_order(monkeypatch):
    """The Family hub's Census and Additional information tabs sort as the pages do."""
    from types import SimpleNamespace

    from parishkit.stewardship.reports import hub_actions

    def row(row_id, name):
        return {
            "id": row_id,
            "family_name": name,
            "family_duid": 1,
            "who": "",
            "label": "",
        }

    rows = [row("10", "Smith"), row("9", "Smith"), row("2", "Étienne"), row("1", "Zed")]
    seen = []

    def page(*args, **kwargs):
        seen.append(args[2].rows)
        return SimpleNamespace(), None

    monkeypatch.setattr(hub_actions, "allows", lambda *args: True)
    monkeypatch.setattr(hub_actions, "census_changes", lambda *a, **k: {"rows": rows})
    monkeypatch.setattr(hub_actions, "worklist_page", page)
    hub_actions.census_worklist(1, 1, None, prefix="p", writable=False)
    monkeypatch.setattr(
        hub_actions, "information_requests", lambda *a: (None, rows, None)
    )
    monkeypatch.setattr(hub_actions, "information_page", page)
    for item in ("10", "9", "2", "1"):
        hub_actions.information_worklist(1, 1, item, writable=False)
    assert ids(seen[0]) == ["2", "9", "10", "1"]
    assert [ids(shown) for shown in seen[1:]] == [["10"], ["9"], ["2"], ["1"]]

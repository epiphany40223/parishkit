"""The ParishSoft leaders import's pure decisions (#952 slice 6).

``plan_import`` and the row shaping decide, without I/O, which chosen
leaders become Ministry leader users and which are skipped; the views and
``pk-admin users import-leaders`` share them.
"""

import pytest
from django.http import QueryDict

from parishkit.stewardship.accounts import leader_import, leader_import_views
from parishkit.stewardship.accounts.leader_import import (
    Leaders,
    _row,
    imported_ids,
    ministries_label,
    plan_import,
)
from parishkit.stewardship.accounts.user_changes import ENTRY_LIMIT
from parishkit.stewardship.web.refusals import UserFacingError


def record(email, role="administrator", name="Someone"):
    """One saved user record of a ``login_rules`` list."""
    return {
        "id": f"id-{email}",
        "values": {"kind": "user", "email": email, "name": name, "role": role},
    }


def row(email, name, duid=1, ministries=(4,)):
    """One leaders-table row as ``read_leaders`` shapes it."""
    return {
        "email": email,
        "name": name,
        "member_duid": duid,
        "ministries": list(ministries),
    }


RECORDS = [record("admin@example.org"), record("staff@example.org", "staff")]
LEADERS = Leaders(
    rows=[
        row("bob@example.org", "Smith, Bob", 2),
        row("ann@example.org", "Jones, Ann", 1),
        row("staff@example.org", "Staff, Sam", 3),
    ],
    unreachable=0,
)


def test_import_all_adds_every_listed_leader_as_a_ministry_leader():
    """Each new user takes the row's Name and only the Ministry leader role."""
    plan = plan_import(RECORDS, LEADERS)
    assert [item["email"] for item in plan.added] == [
        "ann@example.org",
        "bob@example.org",
    ]
    assert [(change.email, change.after) for change in plan.changes] == [
        ("ann@example.org", {"name": "Jones, Ann", "role": "ministry_leader"}),
        ("bob@example.org", {"name": "Smith, Bob", "role": "ministry_leader"}),
    ]
    assert all(operation["operation"] == "add" for operation in plan.patch)
    assert imported_ids(plan.patch) == [change.id for change in plan.changes]
    # Someone listed a moment ago who is a user now is skipped, never changed.
    assert plan.skipped == [{"email": "staff@example.org", "reason": "user"}]
    assert plan.remaining == 0
    assert not any(change.grants_administrator for change in plan.changes)


def test_import_selected_skips_users_and_unlisted_addresses():
    """Only chosen leaders; a repeated choice counts once."""
    plan = plan_import(
        RECORDS,
        LEADERS,
        [
            "bob@example.org",
            "admin@example.org",
            "nobody@example.org",
            "bob@example.org",
        ],
    )
    assert [item["email"] for item in plan.added] == ["bob@example.org"]
    assert plan.skipped == [
        {"email": "admin@example.org", "reason": "user"},
        {"email": "nobody@example.org", "reason": "not_listed"},
    ]


def test_nothing_left_to_import_plans_no_change():
    """No patch at all, so the review offers no Apply."""
    plan = plan_import(RECORDS, LEADERS, ["staff@example.org"])
    assert (plan.changes, plan.patch, plan.added, plan.remaining) == ([], [], [], 0)


def test_one_change_adds_at_most_the_entry_limit():
    """More leaders than one request may add: the rest wait, counted."""
    many = Leaders(
        rows=[
            row(f"leader{number:03}@example.org", f"Leader, {number:03}", number)
            for number in range(ENTRY_LIMIT + 5)
        ],
        unreachable=0,
    )
    plan = plan_import(RECORDS, many)
    assert len(plan.patch) == ENTRY_LIMIT and plan.remaining == 5
    assert plan.added[0]["email"] == "leader000@example.org"


def test_rows_the_users_schema_refuses_are_shaped_or_dropped():
    """A bad address is dropped; a bad Name falls back to the local part."""
    assert _row({"email": "not an address", "name": "X"}) is None
    assert _row({"name": "X"}) is None
    shaped = _row(
        {
            "email": " Ann@Example.org ",
            "name": "",
            "member_duid": 7,
            "ministries": [4, 9],
        }
    )
    assert shaped == {
        "email": "ann@example.org",
        "name": "ann",
        "member_duid": 7,
        "ministries": [4, 9],
    }
    assert _row({"email": "a@example.org", "name": "x" * 201})["name"] == "a"


@pytest.mark.parametrize(
    ("names", "label"),
    [
        ([], ""),
        (["Choir"], "Choir"),
        (["Choir", "Ushers"], "Choir, Ushers"),
        (["Choir", "Ushers", "Lectors"], "Choir, Ushers and 1 more"),
        (["Choir", "Ushers", "A", "B", "C"], "Choir, Ushers and 3 more"),
    ],
)
def test_ministries_are_a_short_list(names, label):
    """The spec's "Choir, Ushers and 3 more"."""
    assert ministries_label(names) == label


def test_the_table_sorts_by_name_by_default():
    """Name, Member DUID and Email sort; Name is the default."""
    sorting = leader_import.LEADER_SORTING
    assert sorting.default == "name"
    assert sorting.columns() == {"name", "member_duid", "email"}
    rows = sorting.sort_rows(LEADERS.rows, "name")
    assert [item["name"] for item in rows] == ["Jones, Ann", "Smith, Bob", "Staff, Sam"]


def choice(text):
    """``_choice`` of a posted form body."""
    return leader_import_views._choice(QueryDict(text))


def test_the_import_form_names_a_scope_and_addresses():
    """All ignores ticked rows; selected needs valid addresses; nothing else."""
    assert choice("csrfmiddlewaretoken=t&scope=all&email=a%40example.org") is None
    assert choice(
        "csrfmiddlewaretoken=t&scope=selected&email=A%40Example.org&email=b%40example.org"
    ) == ["a@example.org", "b@example.org"]
    with pytest.raises(UserFacingError, match="No leader was chosen"):
        choice("csrfmiddlewaretoken=t&scope=selected")
    for refused in (
        "csrfmiddlewaretoken=t&scope=some",
        "csrfmiddlewaretoken=t&scope=all&scope=selected",
        "csrfmiddlewaretoken=t&scope=all&name=x",
        "csrfmiddlewaretoken=t&scope=selected&email=not+an+address",
    ):
        with pytest.raises(UserFacingError, match="did not expect"):
            choice(refused)

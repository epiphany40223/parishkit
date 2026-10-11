"""The Users page's change builder (#952 slice 5), decided by pure code.

``plan_changes`` turns the page's (and ``users preview``'s) entries into one
``login_rules`` patch with each user's Name and role before and after, or
refuses with a field and a plain message: a valid, new address, a usable
Name, one of the three roles, a real change and the last-Administrator
guard.
"""

import pytest

from parishkit.stewardship.accounts.user_changes import (
    DEFAULT_ROLE,
    UserRefused,
    only_administrator,
    plan_changes,
    saved_users,
)
from parishkit.stewardship.accounts.user_rows import ROLE_HELP, ROLE_LABELS, role_labels

from .policy_factory import user

ADMIN = user("admin@example.org", "administrator", "Admin, Ada")
STAFF = user("staff@example.org", "staff", "Staff, Sam")
RECORDS = [ADMIN, STAFF]


def refused(entries, records=RECORDS, **options):
    """The refusal ``plan_changes`` gives, as (code, field, index)."""
    with pytest.raises(UserRefused) as caught:
        plan_changes(records, entries, **options)
    return caught.value.code, caught.value.field, caught.value.index


def test_a_new_user_is_normalized_and_defaults_to_ministry_leader():
    """Trimmed, lowercased address; trimmed Name; Ministry leader by default."""
    (change,), patch = plan_changes(
        RECORDS, [{"email": "  New@Example.ORG ", "name": "  Person, New "}]
    )
    assert DEFAULT_ROLE == "ministry_leader"
    assert change.operation == "add" and change.before is None
    assert change.after == {"name": "Person, New", "role": "ministry_leader"}
    assert not change.grants_administrator
    (operation,) = patch
    assert operation["operation"] == "add" and operation["section"] == "login_rules"
    assert operation["id"] == change.id
    assert operation["values"] == {
        "kind": "user",
        "email": "new@example.org",
        "name": "Person, New",
        "role": "ministry_leader",
    }


@pytest.mark.parametrize(
    ("entry", "expected"),
    [
        ({"email": "", "name": "X"}, ("email_required", "email")),
        ({"email": "a@b@c", "name": "X"}, ("email_invalid", "email")),
        ({"email": "STAFF@example.org", "name": "X"}, ("email_taken", "email")),
        ({"email": "n@example.org"}, ("name_required", "name")),
        ({"email": "n@example.org", "name": "x" * 201}, ("name_invalid", "name")),
        ({"email": "n@example.org", "name": "a\nb"}, ("name_invalid", "name")),
        (
            {"email": "n@example.org", "name": "X", "role": "owner"},
            ("role_invalid", "role"),
        ),
        ({"email": "n@example.org", "name": "X", "grants": {}}, ("invalid", None)),
    ],
)
def test_a_new_user_is_refused_at_its_field(entry, expected):
    """Each problem names its field and a closed code, never the value."""
    code, field, _ = refused([entry])
    assert (code, field) == expected
    assert str(UserRefused(code).message)


def test_an_edit_changes_only_what_differs():
    """Name and role change by ID; the address never does; no-op is refused."""
    (change,), patch = plan_changes(
        RECORDS, [{"id": STAFF["id"], "role": "administrator"}]
    )
    assert change.changed == ["role"] and change.grants_administrator
    assert patch == [
        {
            "operation": "update",
            "section": "login_rules",
            "id": STAFF["id"],
            "values": {"role": "administrator"},
        }
    ]
    assert refused([{"id": STAFF["id"], "name": " Staff, Sam "}])[0] == "unchanged"
    assert refused([{"id": STAFF["id"], "email": "x@example.org"}])[0] == "invalid"
    assert refused([{"id": "not-a-uuid", "role": "staff"}])[0] == "missing"
    assert refused([{"id": user()["id"], "role": "staff"}])[0] == "missing"
    repeated = [{"id": STAFF["id"], "name": "A"}, {"id": STAFF["id"], "name": "B"}]
    assert refused(repeated) == ("repeated", None, 1)


def test_the_last_administrator_stays():
    """No change may leave no Administrator; another one makes it possible."""
    assert refused([{"id": ADMIN["id"], "role": "staff"}])[0] == "last_administrator"
    assert refused([{"id": ADMIN["id"], "delete": True}])[0] == "last_administrator"
    # Promoting someone else in the same change keeps an Administrator.
    changes, _ = plan_changes(
        RECORDS,
        [
            {"id": ADMIN["id"], "delete": True},
            {"id": STAFF["id"], "role": "administrator"},
        ],
    )
    assert [change.operation for change in changes] == ["remove", "update"]
    assert changes[0].removes_administrator
    users = saved_users(RECORDS)
    assert only_administrator(users, ADMIN["id"])
    assert not only_administrator(users, STAFF["id"])
    second = user("second@example.org")
    assert not only_administrator(saved_users([*RECORDS, second]), ADMIN["id"])
    (change,), _ = plan_changes(
        [*RECORDS, second], [{"id": ADMIN["id"], "delete": True}]
    )
    assert change.operation == "remove" and change.after is None


def test_a_default_name_is_used_only_when_none_is_given():
    """``users preview`` names a new user from ParishSoft; a given Name wins."""
    asked = []

    def default(email):
        """The first ParishSoft name, as ``member_names`` would give it."""
        asked.append(email)
        return "Smith, Ann"

    (change,), _ = plan_changes(
        RECORDS, [{"email": "ann@example.org"}], default_name=default
    )
    assert change.after["name"] == "Smith, Ann" and asked == ["ann@example.org"]
    (change,), _ = plan_changes(
        RECORDS, [{"email": "bob@example.org", "name": "Bob"}], default_name=default
    )
    assert change.after["name"] == "Bob" and asked == ["ann@example.org"]
    # No ParishSoft name and none given: the Name is required, as on the page.
    assert refused([{"email": "cy@example.org"}], default_name=lambda email: None)[
        :2
    ] == ("name_required", "name")


def test_documents_are_bounded():
    """An empty, oversized or malformed change document is refused."""
    assert refused([])[0] == "invalid"
    assert refused(["x"])[0] == "invalid"
    many = [{"email": f"u{index}@example.org", "name": "U"} for index in range(101)]
    assert refused(many)[0] == "invalid"
    assert refused(
        [
            {"email": "dup@example.org", "name": "A"},
            {"email": "DUP@example.org", "name": "B"},
        ]
    ) == ("email_repeated", "email", 1)


def test_role_words_are_listed_highest_first_with_help():
    """Every role has a label and a plain-language line, highest first."""
    assert list(ROLE_LABELS) == ["administrator", "staff", "ministry_leader"]
    assert set(ROLE_HELP) == set(ROLE_LABELS)
    assert [
        str(label) for label in role_labels(["ministry_leader", "administrator"])
    ] == [
        "Administrator",
        "Ministry leader",
    ]

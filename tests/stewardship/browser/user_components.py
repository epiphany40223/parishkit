"""The Users page (#952 slice 5), rendered from the real templates and helpers.

The browser checks need no database:

- ``LIST``: four users, read by admin@example.org (``READER``), whose own
  row has no Delete. The Administrator signed in with one recorded
  identity; a Staff user has a long address and Name (cut short with an
  ellipsis); a Ministry leader has two identities; another Administrator
  never signed in. It is sorted by Email, and by Role descending at its
  Role heading's address. Its Delete posts to ``DELETE``, which answers as
  the server does: a redirect to the change's status page, here already
  Applied, after which the dialog redraws the table from ``LIST``'s refresh
  address, without the Ministry leader.
- ``SINGLE``: only the Administrator, who has no Delete, and no ParishSoft
  Ministry leader waiting for a user, so both Import buttons are
  unavailable with the reason.
- ``PENDING``: a change's status page still applying, for the follow-up's
  refused status read; ``LIST_PENDING`` is ``LIST`` whose Delete posts to
  ``DELETE_PENDING``, which redirects there.
- ``LIST``'s second table lists ``LEADERS``, three ParishSoft Ministry
  leaders without a user (#952 slice 6), and counts two with no email. Its
  Import selected and Import all post to ``IMPORT``, which answers with
  ``LEADER_REVIEW``, the Import Ministry leaders review of two of them with
  one skipped.
- ``NEW``: New user. Its Email lookup posts to ``NAMES``, which answers two
  ParishSoft names; ``NEW_TAKEN``'s posts to ``NAMES_TAKEN``, which answers
  that the address is already a user. Review posts to ``REVIEW_POST``, which
  answers with ``REVIEW``.
- ``EDIT_ONLY``: Edit user for the only Administrator: the lower roles are
  unavailable with the reason; ``EDIT_OWN``: the reader's own Edit user
  while another Administrator remains, the same; ``EDIT``: Edit user for
  the Ministry leader, with two identities, one disabled.

``POSTS`` lists the fixture server's answers to the pages' POSTs; it is
filled in by ``components``, before the server starts.
"""

import json
from datetime import UTC, datetime
from uuid import NAMESPACE_URL, uuid4, uuid5

from django.template.loader import render_to_string

from parishkit.stewardship.accounts.leader_import import (
    LEADER_SORTING,
    SKIP_REASONS,
    ministries_label,
)
from parishkit.stewardship.accounts.leader_import_views import PREFIX as LEADER_PREFIX
from parishkit.stewardship.accounts.user_changes import plan_changes, saved_users
from parishkit.stewardship.accounts.user_reads import USER_SORTING, user_list
from parishkit.stewardship.accounts.user_views import (
    OWN_DELETE,
    PREFIX,
    UserForm,
    _entry_context,
    table_rows,
)
from parishkit.stewardship.web.tables import paginate

from ..policy_factory import user

LIST = "/users"
SINGLE = "/users-single"
DELETED = "/users-one-deleted"
DELETE = "/users/deletion"
APPLIED = "/users-change-applied"
PENDING = "/users-change-pending"
LIST_PENDING = "/users-delete-pending"
DELETE_PENDING = "/users/deletion-pending"
NEW = "/user-new"
NEW_TAKEN = "/user-new-taken"
NAMES = "/user-names"
NAMES_TAKEN = "/user-names-taken"
REVIEW_POST = "/user-new/review"
REVIEW = "/user-review"
EDIT = "/user-edit"
EDIT_ONLY = "/user-edit-only"
EDIT_OWN = "/user-edit-own"
# The signed-in Administrator the pages are drawn for.
READER = "admin@example.org"
IMPORT = "/users/leaders/import"
LEADER_REVIEW = "/user-leader-review"
LONG_EMAIL = "a-very-long-address-for-the-parish-office-staff@stewardship.example.org"
LONG_NAME = "Montgomery-Fitzwilliam, Bartholomew Alexander Christopher"
SUGGESTED = ["Smith, Ann", "Smith, Bob"]
POSTS = {}

ADMIN = user("admin@example.org", "administrator", "Admin, Ada")
SECOND = user("second@example.org", "administrator", "Second, Sal")
STAFF = user(LONG_EMAIL, "staff", LONG_NAME)
LEADER = user("leader@example.org", "ministry_leader", "Leader, Lee")
MOMENT = datetime(2026, 9, 19, 15, 4, tzinfo=UTC)


def leader_row(email, name, duid, ministries):
    """One leaders-table row as ``leader_import.table_rows`` shapes it."""
    return {
        "email": email,
        "name": name,
        "member_duid": duid,
        "ministries": list(range(1, len(ministries) + 1)),
        "ministry_names": ministries,
        "ministries_label": ministries_label(ministries),
    }


LEADERS = [
    leader_row("ann.jones@example.org", "Jones, Ann", 901, ["Choir"]),
    leader_row(
        "bob.smith@example.org",
        "Smith, Bob",
        902,
        ["Choir", "Food pantry", "Lectors", "Ushers", "Youth ministry"],
    ),
    leader_row("cy.young@example.org", "Young, Cy", 903, ["Ushers"]),
]


def identity(email, number=0, *, login=None, disabled=False):
    """One recorded identity as ``policy_identities`` reads it."""
    return {
        "id": uuid5(NAMESPACE_URL, f"{email}/{number}"),
        "email": email,
        "hosted_domain": None,
        "disabled": disabled,
        "last_login": login,
    }


IDENTITIES = [
    identity("admin@example.org", login=MOMENT),
    identity(LONG_EMAIL, login=MOMENT),
    identity("leader@example.org", 0),
    identity("leader@example.org", 1, disabled=True),
]


class Request:
    """Just what ``_entry_context`` reads from a request: its path."""

    def __init__(self, path):
        """Keep the page's own address."""
        self.path = path


def _list(
    values, records, *, sort="email", refresh=LIST, delete=DELETE, leaders=LEADERS
):
    """The Users page for ``records``, sorted by ``sort``, with ``leaders``."""
    rows = user_list(records, IDENTITIES)
    leader_table = paginate(
        leaders,
        {f"{LEADER_PREFIX}sort": "name"},
        prefix=LEADER_PREFIX,
        sorting=LEADER_SORTING,
    )
    table = paginate(
        table_rows(rows, saved_users(records), READER),
        {f"{PREFIX}sort": sort},
        prefix=PREFIX,
        sorting=USER_SORTING,
    )
    return (
        "text/html",
        render_to_string(
            "stewardship/users.html",
            values
            | {
                "table": table,
                "base_digest": "a" * 64,
                "refresh_url": refresh,
                "new_url": NEW,
                "delete_url": delete,
                "delete_reason": OWN_DELETE,
                "leaders": leader_table,
                "leaders_unreachable": 2 if leaders else 0,
                "import_url": IMPORT,
            },
        ),
    )


def _entry(values, records, *, record=None, names_url=NAMES, post=REVIEW_POST):
    """New user, or Edit user for ``record``."""
    users = saved_users(records)
    current = None if record is None else users[record["id"]]
    initial = (
        {"role": "ministry_leader"}
        if record is None
        else {"name": current["name"], "role": current["role"]}
    )
    # The page reads the identities from PortalUser; here, from the sample.
    known = [
        {"id": str(item["id"]), "disabled": item["disabled"]}
        for item in IDENTITIES
        if current is not None and item["email"] == current["email"]
    ]
    context = _entry_context(
        Request(post),
        UserForm(initial=initial, new=record is None),
        user=current,
        user_id=None if record is None else record["id"],
        users=users,
        digest="a" * 64,
        identities=known,
        own_email=READER,
    )
    context |= {"post_url": post, "list_url": LIST, "names_url": names_url}
    return (
        "text/html",
        render_to_string("stewardship/user-entry.html", values | context),
    )


def components(context, admin):
    """Every page above, and the POST answers (see the module docstring)."""
    values = context | {"admin_chrome": admin}
    records = [ADMIN, SECOND, STAFF, LEADER]
    (change,), _patch = plan_changes(
        records,
        [{"email": "new@example.org", "name": "Smith, Ann", "role": "staff"}],
    )
    review = render_to_string(
        "stewardship/user-review.html",
        values
        | {
            "change": change,
            "before_role": None,
            "after_role": "Staff",
            "preview": "synthetic-preview",
            "post_url": LIST,
            "list_url": LIST,
        },
    )
    leader_review = render_to_string(
        "stewardship/user-leader-import.html",
        values
        | {
            "added": LEADERS[:2],
            "skipped": [
                {
                    "email": "cy.young@example.org",
                    "reason": "user",
                    "reason_label": SKIP_REASONS["user"],
                }
            ],
            "remaining": 0,
            "limit": 100,
            "preview": "synthetic-preview",
            "post_url": LIST,
            "list_url": LIST,
        },
    )
    status = render_to_string(
        "stewardship/configuration-request.html",
        values | {"receipt": {"request_id": uuid4(), "state": "applied"}},
    )
    pending = render_to_string(
        "stewardship/configuration-request.html",
        values | {"receipt": {"request_id": uuid4(), "state": "validating"}},
    )
    responses = {
        LIST: _list(values, records, refresh=DELETED),
        # A heading keeps the table's page size and starts at page 1.
        f"{LIST}?users_size=50&users_sort=-role": _list(values, records, sort="-role"),
        f"{LIST}?users_size=50&users_sort=email": _list(values, records),
        DELETED: _list(values, [ADMIN, SECOND, STAFF], refresh=DELETED),
        SINGLE: _list(values, [ADMIN], refresh=SINGLE, leaders=[]),
        LEADER_REVIEW: ("text/html", leader_review),
        NEW: _entry(values, records),
        NEW_TAKEN: _entry(values, records, names_url=NAMES_TAKEN),
        EDIT: _entry(values, records, record=LEADER, post=EDIT),
        EDIT_ONLY: _entry(values, [ADMIN, STAFF], record=ADMIN, post=EDIT_ONLY),
        EDIT_OWN: _entry(values, records, record=ADMIN, post=EDIT_OWN),
        REVIEW: ("text/html", review),
        APPLIED: ("text/html", status),
        PENDING: ("text/html", pending),
        LIST_PENDING: _list(values, records, delete=DELETE_PENDING),
    }
    POSTS.update(
        {
            DELETE: (303, APPLIED, ""),
            DELETE_PENDING: (303, PENDING, ""),
            NAMES: (200, None, json.dumps({"error": None, "names": SUGGESTED})),
            NAMES_TAKEN: (
                200,
                None,
                json.dumps(
                    {
                        "error": "This address is already a user. Choose Edit on "
                        "that user's row instead.",
                        "names": [],
                    }
                ),
            ),
            REVIEW_POST: (200, None, review),
            IMPORT: (200, None, leader_review),
        }
    )
    return responses

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
  Import selected and Import all post to ``IMPORT``, which answers with the
  Import Ministry leaders review fragment of two of them with one skipped,
  for the review dialog (#750), with one more hidden field (``extra``)
  besides its preview. Its Apply posts to ``APPLY``, which
  redirects to the Applied status page, after which both tables are
  redrawn from ``LIST``'s refresh address. ``LIST_CONFIRM`` is the same
  page whose import posts to ``IMPORT_CONFIRM``, which answers as Import
  selected does when every ticked leader is imported: a confirmation
  (#1356) for one leader.
- ``LIST``'s third table, Email Ministry leaders (#1592), lists
  ``EMAIL_ROWS``: one leader never emailed, one emailed and delivered. Its
  Email selected posts to ``EMAILS``, which answers with the
  review of two leaders' emails in Testing with one skipped; the dialog's
  Apply posts there too. ``LIST_EMAILS`` is ``LIST`` whose regions redraw
  from ``EMAILED``, where the never-emailed leader's email is queued;
  ``EMAILS_UNAVAILABLE`` has no current campaign with Ministries, so the
  section says so in place of the table.
- ``NEW``: New user. Its Email lookup posts to ``NAMES``, which answers two
  ParishSoft names; ``NEW_TAKEN``'s posts to ``NAMES_TAKEN``, which answers
  that the address is already a user. Review posts to ``REVIEW_POST``, which
  answers with the New user review fragment; the dialog's Apply posts to
  ``APPLY`` (Applied at once), then returns to ``LIST``.
- ``NEW_PENDING``: New user whose Apply posts to ``APPLY_PENDING``, whose
  change stays Applying; ``NEW_INVALID``: Review posts to
  ``REVIEW_INVALID``, which answers 400 with the page drawn again with the
  Name's error and the summary; ``NEW_STALE``: Review posts to
  ``REVIEW_STALE``, which answers 409 with a refusal in the server's words;
  ``NEW_REFUSED``: Apply posts to ``APPLY_REFUSED``, which answers 400 with
  the request's own refusal (``APPLY_REFUSAL``), as a self-demotion is.
- ``EDIT_ONLY``: Edit user for the only Administrator: the lower roles are
  unavailable with the reason; ``EDIT_OWN``: the reader's own Edit user
  while another Administrator remains, the same; ``EDIT``: Edit user for
  the Ministry leader, with two identities, one disabled, whose Review posts
  to ``EDIT_REVIEW``, which answers with the change of that user's role.

``POSTS`` lists the fixture server's answers to the pages' POSTs; it is
filled in by ``components``, before the server starts.
"""

import json
from datetime import UTC, datetime
from uuid import NAMESPACE_URL, uuid4, uuid5

from django import forms
from django.template.loader import render_to_string

from parishkit.stewardship.accounts.leader_email_views import NO_CAMPAIGN
from parishkit.stewardship.accounts.leader_email_views import PREFIX as EMAIL_PREFIX
from parishkit.stewardship.accounts.leader_emails import (
    EMAIL_SORTING,
    LeaderFacts,
)
from parishkit.stewardship.accounts.leader_emails import (
    SKIP_REASONS as EMAIL_SKIP_REASONS,
)
from parishkit.stewardship.accounts.leader_import import (
    LEADER_SORTING,
    SKIP_REASONS,
    ministries_label,
)
from parishkit.stewardship.accounts.leader_import_views import LEADERS as UNREAD
from parishkit.stewardship.accounts.leader_import_views import PREFIX as LEADER_PREFIX
from parishkit.stewardship.accounts.user_changes import plan_changes, saved_users
from parishkit.stewardship.accounts.user_reads import USER_SORTING, user_list
from parishkit.stewardship.accounts.user_views import (
    OWN_DELETE,
    PREFIX,
    UserForm,
    _entry_context,
    review_lines,
    table_rows,
)
from parishkit.stewardship.jobs.leader_email_content import LeaderMinistry
from parishkit.stewardship.web.part_status import part_status
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
REVIEW_INVALID = "/user-new/review-invalid"
REVIEW_STALE = "/user-new/review-stale"
EDIT_REVIEW = "/user-edit/review"
NEW_PENDING = "/user-new-pending"
NEW_INVALID = "/user-new-invalid"
NEW_STALE = "/user-new-stale"
NEW_REFUSED = "/user-new-refused"
APPLY_REFUSED = "/users/apply-refused"
APPLY_REFUSAL = "You can't remove your own Administrator role."
APPLY = "/users/apply"
APPLY_PENDING = "/users/apply-pending"
STALE_REFUSAL = "Another Administrator changed the users meanwhile."
EDIT = "/user-edit"
EDIT_ONLY = "/user-edit-only"
EDIT_OWN = "/user-edit-own"
# The signed-in Administrator the pages are drawn for.
READER = "admin@example.org"
IMPORT = "/users/leaders/import"
# The Users page whose leaders could not be read (#1863), and its
# notice's status answers: ready after the reader's own view, and still
# waiting for newer ParishSoft data.
LEADERS_UNREAD = "/users-leaders-unread"
LEADERS_READY = "/users-leaders-ready"
LEADERS_WAITING = "/users-leaders-waiting"
# Users whose import answers a confirmation (#1356): every ticked leader is
# imported, nobody skipped.
LIST_CONFIRM = "/users-import-confirm"
IMPORT_CONFIRM = "/users/leaders/import-confirm"
# Email Ministry leaders (#1592).
EMAILS = "/users/leader-emails"
LIST_EMAILS = "/users-emails"
EMAILED = "/users-emailed"
EMAILS_UNAVAILABLE = "/users-emails-unavailable"
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


def email_row(email, name, *, login=None, emailed=None, label="Never"):
    """One Email Ministry leaders row as ``leader_emails.leader_rows`` shapes it."""
    return {
        "email": email,
        "name": name,
        "last_login": login,
        "last_emailed": emailed,
        "emailed_label": label,
    }


EMAIL_ROWS = [
    email_row("leader@example.org", "Leader, Lee"),
    email_row(
        "cy.young@example.org",
        "Young, Cy",
        login=MOMENT,
        emailed=MOMENT,
        label="Delivered",
    ),
]
# EMAIL_ROWS once the never-emailed leader's email is queued.
QUEUED_ROWS = [
    email_row(
        "leader@example.org",
        "Leader, Lee",
        emailed=MOMENT,
        label="Still sending (queued)",
    ),
    EMAIL_ROWS[1],
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
    values,
    records,
    *,
    sort="email",
    refresh=LIST,
    delete=DELETE,
    leaders=LEADERS,
    import_url=IMPORT,
    emails=EMAIL_ROWS,
    emails_unavailable=None,
    leaders_status=None,
):
    """The Users page for ``records``, sorted by ``sort``, with ``leaders`` and
    the Email Ministry leaders rows ``emails``; ``leaders_status`` instead
    says the leaders could not be read (#1863)."""
    rows = user_list(records, IDENTITIES)
    email_table = paginate(
        emails,
        {f"{EMAIL_PREFIX}sort": "name"},
        prefix=EMAIL_PREFIX,
        sorting=EMAIL_SORTING,
    )
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
                "import_url": import_url,
                "apply_url": APPLY,
                "emails": email_table,
                "emails_unavailable": emails_unavailable,
                "emails_url": EMAILS,
            }
            | (
                {
                    "leaders": None,
                    "leaders_unavailable": True,
                    "leaders_status": leaders_status,
                }
                if leaders_status
                else {}
            ),
        ),
    )


def _entry(
    values,
    records,
    *,
    record=None,
    names_url=NAMES,
    post=REVIEW_POST,
    apply=APPLY,
    form=None,
    errors=None,
):
    """New user, or Edit user for ``record``; ``form`` and ``errors`` draw a
    refused Review as ``user_views._entry_page`` does."""
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
        form or UserForm(initial=initial, new=record is None),
        user=current,
        user_id=None if record is None else record["id"],
        users=users,
        digest="a" * 64,
        identities=known,
        own_email=READER,
    )
    context |= {
        "post_url": post,
        "list_url": LIST,
        "names_url": names_url,
        "apply_url": apply,
    }
    if errors:
        context["errors"] = errors
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
        {"change": change, "lines": [], "preview": "synthetic-preview"},
    )
    (edited,), _patch = plan_changes(
        records, [{"id": LEADER["id"], "name": "Leader, Lee", "role": "staff"}]
    )
    edit_review = render_to_string(
        "stewardship/user-review.html",
        {
            "change": edited,
            "lines": review_lines(edited),
            "preview": "synthetic-preview",
        },
    )
    # New user refused for its Name, drawn again as the server draws it.
    invalid = UserForm({"email": "new@example.org", "name": " ", "role": "staff"})
    assert invalid.is_valid()
    invalid.add_error(
        "name", forms.ValidationError("Enter a name.", code="name_required")
    )
    leader_review = render_to_string(
        "stewardship/user-leader-import.html",
        {
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
        },
    )
    # Another hidden field, as a later editor's version would be: Apply
    # posts every hidden field of the review, not only its preview.
    leader_review = leader_review.replace(
        "data-review-preview>",
        'data-review-preview><input type="hidden" name="extra" value="kept">',
    )
    leader_confirm = render_to_string(
        "stewardship/user-leader-import.html",
        {
            "added": LEADERS[:1],
            "skipped": [],
            "remaining": 0,
            "limit": 100,
            "preview": "synthetic-preview",
            "confirmation": True,
        },
    )
    email_review = render_to_string(
        "stewardship/user-leader-emails.html",
        {
            "emailed": [
                {
                    "email": "leader@example.org",
                    "name": "Leader, Lee",
                    "facts": LeaderFacts(
                        "leader@example.org",
                        (
                            LeaderMinistry(4, "Choir", 2, 1),
                            LeaderMinistry(9, "Food pantry", 0, 0),
                        ),
                    ),
                },
                {
                    "email": "cy.young@example.org",
                    "name": "Young, Cy",
                    "facts": LeaderFacts(
                        "cy.young@example.org", (LeaderMinistry(5, "Ushers", 0, 3),)
                    ),
                },
            ],
            "skipped": [
                {
                    "email": "ann.jones@example.org",
                    "reason": "no_ministry",
                    "reason_label": EMAIL_SKIP_REASONS["no_ministry"],
                }
            ],
            "remaining": 0,
            "testing_recipient": "test@example.org",
            "preview": "synthetic-preview",
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
        SINGLE: _list(values, [ADMIN], refresh=SINGLE, leaders=[], emails=[]),
        LIST_EMAILS: _list(values, records, refresh=EMAILED),
        EMAILED: _list(values, records, refresh=EMAILED, emails=QUEUED_ROWS),
        EMAILS_UNAVAILABLE: _list(values, records, emails_unavailable=NO_CAMPAIGN),
        NEW: _entry(values, records),
        NEW_TAKEN: _entry(values, records, names_url=NAMES_TAKEN),
        EDIT: _entry(values, records, record=LEADER, post=EDIT_REVIEW),
        NEW_PENDING: _entry(values, records, apply=APPLY_PENDING),
        NEW_INVALID: _entry(values, records, post=REVIEW_INVALID),
        NEW_STALE: _entry(values, records, post=REVIEW_STALE),
        NEW_REFUSED: _entry(values, records, apply=APPLY_REFUSED),
        EDIT_ONLY: _entry(values, [ADMIN, STAFF], record=ADMIN, post=EDIT_ONLY),
        EDIT_OWN: _entry(values, records, record=ADMIN, post=EDIT_OWN),
        APPLIED: ("text/html", status),
        PENDING: ("text/html", pending),
        LIST_PENDING: _list(values, records, delete=DELETE_PENDING),
        LIST_CONFIRM: _list(values, records, import_url=IMPORT_CONFIRM),
        LEADERS_UNREAD: _list(
            values,
            records,
            refresh=LEADERS_UNREAD,
            leaders_status=part_status(UNREAD, shown=3, retry=True),
        ),
        LEADERS_READY: (
            "text/html",
            render_to_string(
                "stewardship/components/part-status.html",
                {"part_status": part_status(UNREAD, shown=3, ready=True)},
            ),
        ),
        LEADERS_WAITING: (
            "text/html",
            render_to_string(
                "stewardship/components/part-status.html",
                {"part_status": part_status(UNREAD, shown=3, retry=True)},
            ),
        ),
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
            EDIT_REVIEW: (200, None, edit_review),
            REVIEW_INVALID: (
                400,
                None,
                _entry(
                    values,
                    records,
                    post=REVIEW_INVALID,
                    form=invalid,
                    errors=[{"message": "Enter a name.", "field_id": "id_name"}],
                )[1],
            ),
            REVIEW_STALE: (
                409,
                None,
                json.dumps(
                    {
                        "errors": [{"code": "stale"}],
                        "refusal": {
                            "message": STALE_REFUSAL,
                            "fix": "Reload the page to start again.",
                        },
                    }
                ),
            ),
            APPLY: (303, APPLIED, ""),
            APPLY_PENDING: (303, PENDING, ""),
            APPLY_REFUSED: (
                400,
                None,
                json.dumps(
                    {
                        "errors": [{"code": "invalid"}],
                        "refusal": {
                            "message": APPLY_REFUSAL,
                            "fix": "Nothing was changed.",
                        },
                    }
                ),
            ),
            IMPORT: (200, None, leader_review),
            IMPORT_CONFIRM: (200, None, leader_confirm),
            EMAILS: (200, None, email_review),
        }
    )
    return responses

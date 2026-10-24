"""The shared review worklist (#1681), served on a fixture page only.

No stream uses the component yet (#1683 and #1684 move Census changes and
Ministry requests to it), so these pages host
``components/review-worklist.html`` in a minimal Admin page, built by the
real ``web/review_worklist.build`` from rows at every stage a Staff reader
and a Ministry leader can meet. Each test answers the page's POST with the
fixture page after the decision (``fulfil_post_with``), as the stream's
view will answer with its page redrawn.
"""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID

from django.template import engines

from parishkit.stewardship.accounts.policy import Principal
from parishkit.stewardship.pipeline import Decision, Placement, Stage
from parishkit.stewardship.web.review_worklist import (
    STALE,
    Column,
    build,
    page_address,
)
from parishkit.stewardship.web.tables import paginate

PATH = "/review-worklist"
LEADER = "/review-worklist-leader"
# The page after each decision the tests take, at an address of its own.
ACCEPTED = "/review-worklist-accepted"
BULKED = "/review-worklist-bulked"
REOPENED = "/review-worklist-reopened"
# The page after row 6's pressed Entered by hand was chosen again (#2131).
UNDONE = "/review-worklist-undone"
# A decision another reader overtook, with a long item name: the longest
# notice the line must hold (R38 finding 10).
STALED = "/review-worklist-staled"
# The answer to Apply with Stage Ignored, and the address it names, which a
# reload then loads (R38 finding 6).
FILTERED = "/review-worklist-filtered"
FILTERED_ADDRESS = PATH + "?stage=ignored"
ACCEPTED_NOTICE = "Mobile phone for Member 00 accepted."
BULK_NOTICE = "2 changes accepted."
REOPENED_NOTICE = "Mobile phone for Member 04 sent back to review."
UNDONE_NOTICE = "Mobile phone for Member 06 sent back to review."
STALE_NOTICE = STALE.format(name="Home address for Martin, Luke and Emily")

# The host page: the stream's heading, then the shared component.
HOST = engines["django"].from_string(
    "{% extends 'stewardship/admin-base.html' %}"
    "{% block title %}Review worklist{% endblock %}"
    "{% block content %}<h1>Review worklist</h1>"
    '{% include "stewardship/components/review-worklist.html" %}'
    "{% endblock %}"
)
# A stream's own filter after Stage, and its Add editor, as each stream
# will give them.
FILTERS = engines["django"].from_string(
    '<div><label for="worklist-search">Search name</label>'
    '<input id="worklist-search" name="search" maxlength="200" value=""></div>'
)
ADD = engines["django"].from_string(
    '<div class="dialog-fields" data-missing-hint="Name the change to add.">'
    '<label for="{{ dialog_id }}-value">Change to add</label>'
    '<input id="{{ dialog_id }}-value" name="value" maxlength="200" value=""'
    " required></div>"
    '{% include "stewardship/components/review-worklist-note.html" %}'
)
# Who is a Member's name, opening the Family hub for viewers who may open
# it; What changed opens the item's own page for every viewer (R38 finding
# 13).
COLUMNS = (
    Column("who", "Who", family=True),
    Column("what", "What changed", link="what_url"),
)
FAMILY = UUID(int=81)
HUB = f"/admin/reports/families/{FAMILY}/"
ITEM = "/review-item/{index}"

WHEN = datetime(2054, 10, 5, 12, tzinfo=UTC)

# (stage, decision, by_hand, not_seen, held decision) of each Staff row: every
# stage a census change shows a Staff reader.
STAFF_ROWS = (
    (Stage.TO_REVIEW, None, False, False, None),
    (Stage.TO_REVIEW, None, True, False, None),
    (Stage.READY_TO_UPDATE, Decision.ACCEPTED, False, False, None),
    (Stage.TO_ENTER_BY_HAND, Decision.ACCEPTED_EDITED, True, False, None),
    (Stage.IGNORED, Decision.IGNORED, False, False, None),
    (Stage.ENTERED_INCORRECTLY, Decision.ACCEPTED, True, False, None),
    (Stage.ENTERED_BY_HAND, Decision.ACCEPTED, True, True, None),
    (Stage.CONFLICT, None, False, False, Decision.ACCEPTED),
)


def _row(index, stage, decision=None, by_hand=False, not_seen=False, held=None):
    """One census change for Member ``index`` at ``stage``."""
    edited = decision is Decision.ACCEPTED_EDITED
    return {
        "id": str(index),
        "version": 1,
        "name": f"Mobile phone for Member {index:02d}",
        "placement": Placement(stage, decision, held_decision=held),
        "by_hand": by_hand,
        "not_seen": not_seen,
        "who": f"Member {index:02d}",
        "family_id": FAMILY,
        "what": "Mobile phone",
        "what_url": ITEM.format(index=index),
        "baseline": "555-0100",
        "family_answer": f"555-{index:04d}",
        "reviewer_value": "555-0199" if edited else "",
        "parishsoft_now": "555-0177",
        "submitted_at": WHEN + timedelta(minutes=index),
        "decided_by": "staff@example.org" if decision else "",
        "decided_at": WHEN + timedelta(hours=1) if decision else None,
        "note": "Checked with the Family" if decision else "",
    }


def _page(
    context,
    admin,
    rows,
    roles,
    stream="census",
    notice=None,
    path=PATH,
    stage="all",
):
    """Render the host page with ``rows`` for a reader holding ``roles``,
    at Stage ``stage``, naming its own address as a stream's view does."""
    table = paginate(rows, {"size": "25"}, carry=[("stage", stage)])
    table = replace(table, method="post", action=path)
    counts = {}
    for row in rows:
        at = row["placement"].stage.value
        counts[at] = counts.get(at, 0) + 1
    counts.setdefault("not_seen", sum(row.get("not_seen", False) for row in rows))
    worklist = build(
        stream,
        table.rows,
        roles,
        prefix="worklist",
        action=path,
        columns=COLUMNS,
        stage=stage,
        counts=counts,
        stages=[Stage.TO_REVIEW, Stage.CONFLICT, Stage.IGNORED, Stage.ENTERED_BY_HAND],
        items="changes",
        caption="Changes to review",
        filters_template=FILTERS,
        add_template=ADD,
        address=page_address(path, [("stage", "" if stage == "all" else stage)]),
    )
    return (
        "text/html",
        HOST.render(
            context
            | {"admin_chrome": admin, "worklist": worklist, "table": table}
            # The page's viewer, whose role decides the Family hub link.
            | {
                "request": SimpleNamespace(
                    principal=Principal(UUID(int=1), frozenset(roles))
                )
            }
            | {"notice": notice}
        ),
    )


def staff_rows(changed=None):
    """The Staff rows, with ``changed`` (index → stage, decision) applied."""
    changed = changed or {}
    rows = []
    for index, (stage, decision, by_hand, not_seen, held) in enumerate(STAFF_ROWS):
        if index in changed:
            stage, decision = changed[index]
            held = None
        rows.append(_row(index, stage, decision, by_hand, not_seen, held))
    return rows


def leader_rows():
    """A leader's Ministry requests: two on a Ministry they lead, one not."""
    rows = [
        _row(0, Stage.TO_REVIEW),
        _row(1, Stage.TO_REVIEW),
        _row(2, Stage.TO_ENTER_BY_HAND, Decision.ACCEPTED),
    ]
    for row, leads in zip(rows, (True, False, True), strict=True):
        row["leads"] = leads
        row["what"] = "Join Food pantry"
    return rows


def components(context, admin):
    """The Staff and leader pages, and the Staff page after each decision."""
    accepted = {0: (Stage.READY_TO_UPDATE, Decision.ACCEPTED)}
    both = accepted | {1: (Stage.TO_ENTER_BY_HAND, Decision.ACCEPTED)}
    reopened = {4: (Stage.TO_REVIEW, None)}
    undone = {6: (Stage.TO_ENTER_BY_HAND, Decision.ACCEPTED)}
    staff = {"staff"}
    ignored = [row for row in staff_rows() if row["placement"].stage is Stage.IGNORED]
    return {
        PATH: _page(context, admin, staff_rows(), staff),
        ACCEPTED: _page(
            context, admin, staff_rows(accepted), staff, notice=ACCEPTED_NOTICE
        ),
        BULKED: _page(context, admin, staff_rows(both), staff, notice=BULK_NOTICE),
        REOPENED: _page(
            context, admin, staff_rows(reopened), staff, notice=REOPENED_NOTICE
        ),
        UNDONE: _page(context, admin, staff_rows(undone), staff, notice=UNDONE_NOTICE),
        STALED: _page(context, admin, staff_rows(), staff, notice=STALE_NOTICE),
        FILTERED: _page(context, admin, ignored, staff, stage="ignored"),
        FILTERED_ADDRESS: _page(context, admin, ignored, staff, stage="ignored"),
        LEADER: _page(
            context,
            admin,
            leader_rows(),
            {"ministry_leader"},
            stream="ministry",
            path=LEADER,
        ),
    }

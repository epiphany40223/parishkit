"""Limitations (#1685): the review worklist, as the view renders it.

Each fixture path renders the real page through the view's own sorting,
``limitation_worklist.worklist_page`` and the real templates, from placed
answers, so the headings', navigator's and dialogs' POST forms carry exactly
what the production page would. Each test answers a POST with the fixture
page after it, as the view answers with its page redrawn.
"""

from datetime import UTC, datetime
from uuid import UUID

from django.template.loader import render_to_string
from django.urls import reverse

from parishkit.stewardship.pipeline import (
    cannot_participate_placement,
    financial_placement,
)
from parishkit.stewardship.reports.limitation_worklist import (
    SORTING,
    WorklistQuery,
    answer_rows,
    at_stage,
    worklist_page,
)
from parishkit.stewardship.reports.limitations import LimitationsQuery
from parishkit.stewardship.web.tables import paginate

CAMPAIGN = UUID(int=90)
# The page's own path: what every heading, navigator, filter and dialog
# posts back to, and what the tests route.
PATH = reverse("admin:limitations_report")
# The answers the tests receive after a decision, and their notices.
ACCEPTED = "/limitations-report-accepted"
ACCEPTED_NOTICE = "The answer of Family DUID 2 accepted."
# A Family's Cannot contribute financially answer, decided on Pledges.
CONTRIBUTION = "/limitations-report-cannot-contribute"
PLEDGES_LINK = reverse("admin:pledges_to_enter") + "?stage=to_review"


def answer(kind, family, duid, *, member=None, key="", decision=None, when=None):
    """One answer as ``limitations_report`` returns it, placed in its stage."""
    row = {
        "kind": kind,
        "member_key": key,
        "family_name": family,
        "family_duid": duid,
        "family_id": None,
        "submitted_at": when,
        "version": 1 if decision else 0,
        "decision": decision,
        "in_force": bool(decision),
        "decided_by": "staff@example.org" if decision else None,
        "decided_at": when if decision else None,
        "note": "Called <them>" if decision else None,
    }
    if kind != "family":
        row |= {
            "member_name": member,
            "member_duid": int(key) if kind == "member" else None,
            "proposed": kind == "proposed_member",
            "cannot_serve": True,
        }
    row["placement"] = cannot_participate_placement(
        decision=decision, in_force=bool(decision)
    )
    return row


def components(context, admin):
    """The worklist under each choice the tests step through."""
    when = datetime(2026, 9, 20, 12, tzinfo=UTC)
    proposed = str(UUID(int=91))

    def result(accepted=False):
        """The report's answers: two Members and two Families (Baker's
        accepted when ``accepted``)."""
        return {
            "metadata": {"name": "Sample campaign"},
            "members": [
                answer("member", "Baker", 2, member="Zed Able", key="7", when=when),
                answer(
                    "proposed_member",
                    "Carter",
                    1,
                    member="Amy Young",
                    key=proposed,
                    when=when,
                ),
            ],
            "families": [
                answer(
                    "family",
                    "Baker",
                    2,
                    decision="accepted" if accepted else None,
                    when=when,
                ),
                answer("family", "Carter", 1, when=when),
            ],
        }

    def page(paging, notice=None, *, data=None, chosen=None, stage="all"):
        """Render the worklist with the table sorted as ``paging`` says, from
        ``data`` under the filters ``chosen`` at ``stage``."""
        query = WorklistQuery(stage=stage, limitations=chosen or LimitationsQuery())
        shown, counts = at_stage(answer_rows(data or result()), stage)
        table = paginate(
            shown, paging, carry=list(query.form_values().items()), sorting=SORTING
        )
        worklist, table = worklist_page(counts, query, table, PATH, mutable=True)
        return (
            "text/html",
            render_to_string(
                "stewardship/limitations-report.html",
                context
                | {"admin_chrome": admin}
                | {
                    "worklist": worklist,
                    "table": table,
                    "campaign_id": CAMPAIGN,
                    "query": query,
                    "query_fields": query.form_values(),
                    "notice": notice,
                    "mutable": True,
                },
            ),
        )

    nobody = {"metadata": {"name": "Sample campaign"}, "members": [], "families": []}
    # Dalton cannot contribute financially: a Limitations row at its Pledges
    # stage, with a link to Pledges and no action (R39 finding 11).
    contribution = nobody | {
        "contributions": [
            {
                "kind": "cannot_give",
                "member_key": "",
                "family_name": "Dalton",
                "family_duid": 3,
                "family_id": None,
                "submitted_at": when,
                "pledge_item": str(UUID(int=92)),
                "version": 0,
                "placement": financial_placement(),
                "in_force": False,
                "decided_by": "",
                "decided_at": None,
                "note": "",
                "pledges_url": PLEDGES_LINK,
            }
        ]
    }
    narrowed = result()
    narrowed = narrowed | {
        "members": narrowed["members"][1:],
        "families": narrowed["families"][1:],
    }
    return {
        "/limitations-report": page({}),
        PATH: page({}),
        "/limitations-report-families-desc": page({"sort": "-family"}),
        "/limitations-report-member": page({"sort": "member"}),
        ACCEPTED: page({}, ACCEPTED_NOTICE, data=result(accepted=True)),
        CONTRIBUTION: page({}, data=contribution, stage="to_review"),
        # Nobody has a limitation yet, and a Stage with nothing at it: the
        # two empty states differ (#227).
        "/limitations-report-none": page({}, data=nobody),
        "/limitations-report-none-searched": page(
            {}, data=nobody, chosen=LimitationsQuery(search="Nobody"), stage="ignored"
        ),
        # What Show "Cannot participate in ministries" returns (#484), here
        # narrowed as a search would be: one Member and one Family.
        "/limitations-report-cannot-serve": page(
            {}, data=narrowed, chosen=LimitationsQuery(show="cannot_serve")
        ),
    }

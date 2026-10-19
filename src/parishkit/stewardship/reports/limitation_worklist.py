"""Limitations on the shared review worklist (#1685).

``limitations.limitations_report`` reads the current answers (each with its
latest review decision); this module puts them in one table, filters and
counts them by stage, turns one page of them into the review worklist's rows
(``web/review_worklist.build``) and records the worklist's decisions through
``responses.limitation_review`` (``LimitationDecisions``, the stream's
``DecisionService``). The stages, words and role actions are
``parishkit.stewardship.pipeline``'s; the service and its SQL guard
(migration 0126) refuse what a reader may not do.
"""

from dataclasses import dataclass, replace

from django.db import IntegrityError
from django.utils.datastructures import MultiValueDict
from django.utils.html import format_html

from parishkit.stewardship.observability import Event, emit_failure
from parishkit.stewardship.pipeline import Stage, Stream
from parishkit.stewardship.responses.information import StaleItem
from parishkit.stewardship.responses.limitation_review import (
    answer_id,
    decide_limitations,
    parse_answer,
)
from parishkit.stewardship.source.source_status import source_status_label
from parishkit.stewardship.web.contracts import filters
from parishkit.stewardship.web.presentation import duid
from parishkit.stewardship.web.review_worklist import (
    EVERY_STAGE,
    WORDS,
    Column,
    StaleDecision,
    build,
)
from parishkit.stewardship.web.tables import Sorting

from .limitations import ANSWERS, STAGES, LimitationsQuery

STAGE_CHOICES = frozenset({*(stage.value for stage in STAGES), EVERY_STAGE})
# The identity columns, in the shared column order (UX conventions, "Table
# column order"): the Family, then the Member, each with its DUID.
COLUMNS = (
    Column("family", "Family", sort="family"),
    Column("family_duid", "Family DUID", sort="duid", numeric=True),
    Column("member", "Member", sort="member"),
    Column("member_duid_cell", "Member DUID", sort="member_duid", numeric=True),
)
# PostgreSQL's SQLSTATE for a unique violation: a racing second decision of
# the same version, which reads as the stale notice.
UNIQUE_VIOLATION = "23505"


def _text(value):
    """Case-insensitive text sort key; a missing value sorts as empty."""
    return (value or "").casefold()


# The list is complete in memory, so every column sorts here; the selection's
# order (Family, then its Members, then its own answer) stays the tiebreak.
SORTING = Sorting.by_column(
    {
        "family": lambda row: (_text(row["family_name"]), row["family_duid"]),
        "duid": lambda row: row["family_duid"],
        "member": lambda row: _text(row.get("member_name")),
        # Members by DUID; proposed Members and the Family's own answer,
        # which have none, follow them.
        "member_duid": lambda row: (
            row.get("member_duid") is None,
            row.get("member_duid") or 0,
        ),
        "submitted": lambda row: row["submitted_at"],
    },
    default="family",
    descending_first={"submitted"},
)


@dataclass(frozen=True, repr=False)
class WorklistQuery:
    """The worklist's filters: the Stage, then the report's own (search, Show)."""

    stage: str = Stage.TO_REVIEW.value
    limitations: LimitationsQuery = LimitationsQuery()

    @classmethod
    def parse(cls, parameters):
        """Single bounded values: a known Stage, then the report's filters."""
        if not hasattr(parameters, "getlist"):
            parameters = MultiValueDict(
                {key: [value] for key, value in parameters.items()}
            )
        values = filters(parameters, allowed={"stage", "search", "show"})
        stage = values.pop("stage", Stage.TO_REVIEW.value)
        if stage not in STAGE_CHOICES:
            raise ValueError("Invalid Limitations stage.")
        return cls(stage=stage, limitations=LimitationsQuery.parse(values))

    def form_values(self):
        """The fields every form posts so its answer shows the same view."""
        return {"stage": self.stage, **self.limitations.form_values()}


def answer_rows(result):
    """One row per answer, Members' first, each with its worklist item id.

    The Families' Cannot contribute financially answers (R39 finding 11)
    follow, each with an id of its own that no Limitations decision takes
    (its Pledges item), since it is decided on Pledges.
    """
    rows = []
    for row in result["members"] + result["families"]:
        rows.append(
            row
            | {
                "id": answer_id(row["family_duid"], row["kind"], row["member_key"]),
            }
        )
    for row in result.get("contributions", []):
        rows.append(row | {"id": f"pledge-{row['pledge_item']}"})
    return rows


def at_stage(rows, stage):
    """The rows the Stage choice lists, and each stage's count over all rows."""
    counts = {}
    for row in rows:
        value = row["placement"].stage.value
        counts[value] = counts.get(value, 0) + 1
    shown = [
        row
        for row in rows
        if stage == EVERY_STAGE or row["placement"].stage.value == stage
    ]
    return shown, counts


def answer_name(row):
    """How the icons, dialogs and the table name one answer."""
    if row["kind"] in ("family", "cannot_give"):
        who = row["family_name"] or f"Family DUID {row['family_duid']}"
        return f"the answer of {who}"
    return f"the answer of {row['member_name']}"


def notice_name(answer):
    """A decided answer as a notice names it: by DUIDs, as census changes do."""
    if answer.kind == "family":
        return f"The answer of Family DUID {answer.family_duid}"
    if answer.kind == "proposed_member":
        return f"The answer of a New Member in Family DUID {answer.family_duid}"
    return (
        f"The answer of Member DUID {answer.member_key} "
        f"in Family DUID {answer.family_duid}"
    )


def _family(row):
    """The Family cell: the name, or the shared Not in ParishSoft badge."""
    if row["family_name"]:
        return row["family_name"]
    return format_html(
        '<span class="status-badge" data-source-status="missing">{}</span>',
        source_status_label("missing"),
    )


def _member_duid(row):
    """The Member DUID cell: the DUID, "New Member", or blank for the Family."""
    if row["kind"] == "proposed_member":
        return "New Member"
    return duid(row["member_duid"]) if row.get("member_duid") is not None else ""


def worklist_rows(rows):
    """The review worklist's rows for one page of placed answers.

    Each gains what ``review_worklist.build`` reads: the name, the identity
    cells, the Family's answer in words and, while it is in force, the
    answer's latest decision (who, when, the note). A decision a later
    response from the Family broke is no longer the row's.
    """
    found = []
    for row in rows:
        current = row["in_force"]
        found.append(
            row
            | {
                "name": answer_name(row),
                "family": _family(row),
                "family_duid": duid(row["family_duid"]),
                "member": row.get("member_name", ""),
                "member_duid_cell": _member_duid(row),
                "family_answer": ANSWERS[row["kind"]],
                "decided_by": (row["decided_by"] or "") if current else "",
                "decided_at": row["decided_at"] if current else None,
                "note": (row["note"] or "") if current else "",
                # A Cannot contribute financially row is decided on Pledges:
                # no box and no icons, only a link there (R39 finding 11).
                "elsewhere": (
                    {
                        "label": "Open in Pledges",
                        "aria_label": f"Open in Pledges: {answer_name(row)}",
                        "url": row["pledges_url"],
                    }
                    if row["kind"] == "cannot_give"
                    else None
                ),
            }
        )
    return found


def worklist_page(counts, query, table, action, *, mutable):
    """The page's ``worklist`` and its table, from the placed answers.

    ``table`` is the placed rows' page (``paginate``); its rows become the
    worklist's rows. ``mutable`` is false while other campaign work holds
    the campaign: the reader is then offered no action at all, as the
    service would refuse them.
    """
    rows = worklist_rows(table.rows)
    table = replace(table, rows=rows, method="post", action=action)
    worklist = build(
        Stream.CANNOT_PARTICIPATE,
        rows,
        frozenset({"staff"}) if mutable else frozenset(),
        prefix="limitations",
        action=action,
        columns=COLUMNS,
        stage=query.stage,
        # Every count, so Every stage also counts a Cannot contribute row
        # at a Pledges-only stage (To enter by hand, Entered by hand), which
        # no Limitations Stage choice names (R39 finding 11).
        counts=dict(counts),
        stages=STAGES,
        items="answers",
        caption="Limitations",
        baseline=False,
        reviewer=False,
        fields=query.limitations.form_values(),
        filters_template="stewardship/limitations-worklist-filters.html",
        export_template="stewardship/limitations-worklist-export.html",
    )
    return worklist, table


class LimitationDecisions:
    """The stream's worklist decisions in one campaign (``DecisionService``).

    ``decide`` records the decision through ``decide_limitations``, all or
    none, and returns the notice the page shows. An answer someone else (or
    the Family answering again) changed first is ``StaleDecision``.
    """

    def __init__(self, store, campaign_id):
        self.store = store
        self.campaign_id = campaign_id

    def decide(self, principal, decision):
        """Record ``decision`` for ``principal``; the notice the page shows."""
        if decision.fields:
            raise ValueError("Limitations decisions take no value.")
        answers = {item: parse_answer(item) for item in decision.versions}
        try:
            recorded = decide_limitations(
                self.store,
                principal.identity,
                self.campaign_id,
                decision.action,
                dict(decision.versions),
                note=decision.note,
                request_key=decision.key,
                bulk=decision.bulk,
            )
        except StaleItem as error:
            raise StaleDecision(notice_name(answers[error.item_id])) from None
        except IntegrityError as error:
            # Only the expected race (a second decision of the same version)
            # becomes the stale notice; any other refusal is logged and fails.
            if getattr(error.__cause__, "sqlstate", None) == UNIQUE_VIOLATION:
                raise StaleDecision() from None
            emit_failure(error, event=Event.TASK_FAILED)
            raise
        words = WORDS[decision.action].done
        if len(recorded) > 1:
            return f"{len(recorded):,} answers {words}"
        (answer,) = answers.values()
        return f"{notice_name(answer)} {words}"

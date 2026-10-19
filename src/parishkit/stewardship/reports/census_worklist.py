"""Census changes on the shared review worklist (#1683).

``census_changes`` reads and words the rows; this module turns one page of
them into the review worklist's rows (``web/review_worklist.build``) and
records the worklist's decisions through the census services
(``CensusDecisions``, the stream's ``DecisionService``). The stages, words
and role actions are ``parishkit.stewardship.pipeline``'s; nothing here
restates them, and nothing here decides what is allowed: the services and
their SQL guards (migrations 0060, 0096, 0107, 0108 and 0128) refuse
anything a reader may not do.
"""

from dataclasses import replace
from uuid import UUID

from django.db import IntegrityError
from django.utils.html import format_html

from parishkit.stewardship.accounts.policy import Capability, allows
from parishkit.stewardship.observability import Event, emit_failure
from parishkit.stewardship.pipeline import (
    Action,
    Stage,
    Stream,
    allowed_actions,
    census_by_hand,
    census_placement,
)
from parishkit.stewardship.responses.census_review import (
    BulkStale,
    decide_census_changes,
)
from parishkit.stewardship.responses.comparison import ADDRESS_COMPONENTS
from parishkit.stewardship.responses.models import ProposedChange
from parishkit.stewardship.responses.refresh_checks import (
    FINAL_EXECUTIONS,
    check_states,
)
from parishkit.stewardship.source.source_status import source_status_label
from parishkit.stewardship.web.presentation import duid
from parishkit.stewardship.web.review_worklist import (
    NOT_SEEN,
    WORDS,
    Column,
    StaleDecision,
    build,
    page_address,
)

from .census_changes import LABELS, REOPENED, STAGES

# The census stream's identity columns, in the shared column order (UX
# conventions, "Table column order"), each sorted by ``census_change_views``'s
# whitelisted sort tokens. The Stage is chosen with the Stage filter. How
# it reaches ParishSoft is left out of the table, which it made too wide,
# and kept in the downloads and as a filter (R38 finding 16).
COLUMNS = (
    Column("family", "Family", sort="family"),
    Column("family_duid", "Family DUID", sort="duid", numeric=True),
    Column("who", "Who", sort="who"),
    Column("member_duid", "Member DUID", sort="member_duid", numeric=True),
    Column("what", "What changed", sort="what"),
)
# PostgreSQL's SQLSTATE for a unique violation: a racing second decision of
# the same version, which reads as the stale notice.
UNIQUE_VIOLATION = "23505"
EDIT_MATCHED = "accepted: the value matched the Family's answer."


def _family(row):
    """The Family cell: the name, or the shared Not in ParishSoft badge."""
    if row["family_name"]:
        return row["family_name"]
    return format_html(
        '<span class="status-badge" data-source-status="missing">{}</span>',
        source_status_label("missing"),
    )


def _member_duid(row):
    """The Member DUID cell: the DUID, "New Member" or blank (a household change)."""
    if row["member_duid"] is not None:
        return duid(row["member_duid"])
    return "New Member" if row["entity_kind"] == "proposed_member" else ""


def _withheld(row):
    """The actions this change cannot take whatever its stage.

    Edit for a field no value can be typed for (moved, deceased, the email
    opt-out, a New Member), and Accept and Edit on a Conflict whose
    ParishSoft value is unknown, which waits for a refresh: Accept on a
    Conflict is decided against ParishSoft's current value. Entered
    incorrectly's two fixes come with #1737; until then a refresh confirms
    the change once ParishSoft holds the reviewer's value.
    """
    withheld = set()
    if row["editor"] is None:
        withheld.add(Action.EDIT)
    if row["placement"].stage is Stage.CONFLICT and not row["resolvable"]:
        withheld |= {Action.ACCEPT, Action.EDIT}
    if row["placement"].stage is Stage.ENTERED_INCORRECTLY:
        withheld |= {Action.BACK_TO_REVIEW, Action.ENTERED_BY_HAND}
    return withheld


def _last_decision(row):
    """(who, when, note) of the row's latest review or mark.

    A change the Family's same answer reopened over an accepted ParishSoft
    value has none of its own: its history line says why it is back, with
    the Family as actor, as the pipeline's audit rule words a resubmission.
    """
    if row.get("resolution_action"):
        return row["resolution_by"], row["resolution_at"], row["resolution_note"]
    if row.get("reopened") and row["decision"] == "unreviewed":
        return "Family", row["submitted_at"], REOPENED
    return "", None, ""


def worklist_rows(rows):
    """The review worklist's rows for one page of shaped census rows.

    Each keeps the shaped row's keys (the editor template reads ``editor``)
    and gains what ``review_worklist.build`` reads: the id, version and name,
    the placement and By hand flag, the actions it withholds, its identity
    cells and the shared value and decision columns.
    """
    found = []
    for row in rows:
        decided_by, decided_at, note = _last_decision(row)
        found.append(
            row
            | {
                "id": str(row["id"]),
                "name": f"{row['label']} for {row['who']}",
                "by_hand": not row["automatic"],
                "withheld": _withheld(row),
                "family": _family(row),
                "family_duid": duid(row["family_duid"]),
                "member_duid": _member_duid(row),
                "what": row["label"],
                "reviewer_value": row["edited"],
                "decided_by": decided_by or "",
                "decided_at": decided_at,
                "note": note or "",
            }
        )
    return found


def stage_counts(counts):
    """The Stage filter's counts without History, which Every stage leaves out.

    Not seen in ParishSoft (#1689) is a choice only while some change has
    the flag.
    """
    return {
        value: counts.get(value, 0)
        for value in (*(stage.value for stage in STAGES), NOT_SEEN)
        if counts.get(value)
    }


def history_choice(counts):
    """The Stage filter's History choice, listed before Every stage."""
    return (Stage.HISTORY.value, f"History ({counts.get(Stage.HISTORY.value, 0):,})")


# The filters a link may carry (R38 finding 6), with the value each takes
# when not chosen: never the name search, which stays in POST bodies.
LINK_DEFAULTS = {
    "stage": Stage.TO_REVIEW.value,
    "route": "any",
    "kind": "any",
    "start": "",
    "end": "",
    "zone": "",
}


def link_pairs(query, paging=None):
    """The view's choices a link may carry, as ``(name, value)`` pairs.

    The Stage and the closed filters other than their defaults, the zone
    only while a day needs it, then the table's ``paging`` (its page, size
    and sort as the form sent them). ``page_address`` drops empty values.
    """
    days = bool(query.start or query.end)
    pairs = [
        (name, "" if value == LINK_DEFAULTS[name] else value)
        for name, value in ((name, getattr(query, name)) for name in LINK_DEFAULTS)
        if name != "zone" or days
    ]
    return pairs + sorted((paging or {}).items())


def worklist_page(result, query, table, action, *, mutable, paging=None):
    """The page's ``worklist`` and its table, from ``census_changes``'s result.

    ``table`` is the shaped rows' page (``paginate``); its rows become the
    worklist's rows. ``mutable`` is false while other campaign work holds
    the campaign: the reader is then offered no action at all, as the
    services would refuse them. History (superseded and cancelled rows) is
    a Stage choice of its own, which Every stage does not count. ``paging``
    is the table's choices as sent, for the view's own address.
    """
    rows = worklist_rows(table.rows)
    table = replace(table, rows=rows, method="post", action=action)
    counts = result["stage_counts"]
    worklist = build(
        Stream.CENSUS,
        rows,
        result["roles"] if mutable else frozenset(),
        prefix="census",
        action=action,
        columns=COLUMNS,
        stage=query.stage,
        counts=stage_counts(counts),
        stages=STAGES,
        items="changes",
        caption="Census changes",
        fields={
            name: value
            for name, value in query.form_values().items()
            if name not in {"status", "history"}
        },
        filters_template="stewardship/census-worklist-filters.html",
        editor_template="stewardship/census-worklist-value.html",
        export_template="stewardship/census-worklist-export.html",
        address=page_address(action, link_pairs(query, paging)),
    )
    worklist.stage_choices.insert(-1, history_choice(counts))
    return worklist, table


def notice_name(proposal):
    """The changed row as a notice names it: what changed, and whose.

    DUIDs, not names, so the notice reads the same whatever the snapshot
    holds; the row itself shows the names.
    """
    who = (
        "the Family"
        if proposal.entity_kind == "family"
        else "a New Member"
        if proposal.entity_kind == "proposed_member"
        else f"Member DUID {proposal.entity_key}"
    )
    label = LABELS.get(proposal.field, proposal.field)
    return f"{label} for {who} in Family DUID {proposal.submission.family.family_duid}"


def _value(fields, field):
    """Edit's raw value from the editor's posted ``fields`` for ``field``.

    One ``value`` for a Member field; for a Family address, each part as
    ``value_<part>``. Anything else is a malformed form (a ValueError).
    """
    if field in ("home_address", "mailing_address"):
        expected = {f"value_{part}" for part in ADDRESS_COMPONENTS}
        if set(fields) != expected:
            raise ValueError("An address edit sends each of its parts once.")
        parts = {}
        for part in ADDRESS_COMPONENTS:
            (parts[part],) = fields[f"value_{part}"]
        return parts
    if set(fields) != {"value"} or len(fields["value"]) != 1:
        raise ValueError("An edit sends its value once.")
    return fields["value"][0]


class CensusDecisions:
    """The census stream's worklist decisions, one campaign's (``DecisionService``).

    ``decide`` checks that every chosen change is one of this campaign's live
    changes (otherwise it is denied, as an unknown one is), records the
    decision through ``census_review.decide_census_changes`` all or none,
    and returns the notice the page shows. A change someone else changed
    first is ``StaleDecision``, named when the service knows which.
    """

    def __init__(self, store, campaign_id):
        self.store = store
        self.campaign_id = campaign_id

    def _proposals(self, decision):
        """The chosen changes, by id, each this campaign's live change."""
        ids = {UUID(key): version for key, version in decision.versions.items()}
        found = {
            proposal.pk: proposal
            for proposal in ProposedChange.objects.select_related("submission__family")
            .filter(pk__in=list(ids), submission__campaign_id=self.campaign_id)
            .filter(submission__mode="live")
            .exclude(handling="report-only")
        }
        if len(found) != len(ids):
            raise PermissionError("A census change is unavailable.")
        return ids, found

    @staticmethod
    def _offered(principal, action, versions, proposals):
        """Refuse an action a chosen change's stage does not offer this reader.

        The services and their guards allow some decisions the worklist does
        not offer (Entered by hand on a Conflict, which is accepted first;
        Accept on an Ignored change, which goes back to review first), so the
        page's own offer (``pipeline.allowed_actions``) is checked here, as
        the ``DecisionService`` contract asks. A change whose version moved
        since the page was drawn is left to the service, which answers with
        the stale notice rather than a refusal.
        """
        roles = frozenset(
            role
            for role, capability in (
                ("staff", Capability.MANUAL_CENSUS),
                ("administrator", Capability.PUBLISH_CENSUS),
            )
            if allows(principal, capability)
        )
        # A final change is placed by its latest refresh check (#1689), as
        # the page placed it.
        checks = check_states(
            proposals=[
                pk
                for pk, proposal in proposals.items()
                if proposal.execution in FINAL_EXECUTIONS
            ]
        )
        for pk, proposal in proposals.items():
            if proposal.version != versions[pk]:
                continue
            check = checks.get(str(pk))
            stage = census_placement(
                decision=proposal.decision,
                execution=proposal.execution,
                handling=proposal.handling,
                entity_kind=proposal.entity_kind,
                admin_value_set=proposal.admin_value_set,
                check=check.outcome if check else None,
            ).stage
            offered = allowed_actions(
                Stream.CENSUS,
                stage,
                roles,
                by_hand=census_by_hand(proposal.handling, proposal.entity_kind),
            )
            if action not in offered:
                raise ValueError("This census change does not offer that action now.")

    def decide(self, principal, decision):
        """Record ``decision`` for ``principal``; the notice the page shows."""
        if decision.action is Action.ADD:
            # Census Add is its own slice (#1710); the page offers no Add.
            raise ValueError("Census changes take no Add yet.")
        versions, proposals = self._proposals(decision)
        self._offered(principal, decision.action, versions, proposals)
        value = None
        if decision.action is Action.EDIT:
            (proposal,) = proposals.values()
            value = _value(decision.fields, proposal.field)
        elif decision.fields:
            raise ValueError("Only Edit sends a value.")
        try:
            recorded = decide_census_changes(
                self.store,
                principal.identity,
                decision.action.value,
                versions,
                note=decision.note,
                request_key=decision.key,
                bulk=decision.bulk,
                value=value,
            )
        except BulkStale as error:
            raise StaleDecision(notice_name(proposals[error.proposal_id])) from None
        except IntegrityError as error:
            # A racing write normally surfaces as a stale version, under the
            # row lock. Only the expected race (a second decision of the same
            # version: a unique violation) becomes the stale notice. Any other
            # refusal (a guard or audit mismatch) is logged and fails the
            # request, so a future bug is never hidden behind the race wording.
            if getattr(error.__cause__, "sqlstate", None) == UNIQUE_VIOLATION:
                raise StaleDecision() from None
            emit_failure(error, event=Event.TASK_FAILED)
            raise
        words = WORDS[decision.action].done
        if len(recorded) > 1:
            return f"{len(recorded):,} changes {words}"
        (proposal,) = proposals.values()
        if decision.action is Action.EDIT and not recorded[0].admin_value_set:
            return f"{notice_name(proposal)} {EDIT_MATCHED}"
        return f"{notice_name(proposal)} {words}"

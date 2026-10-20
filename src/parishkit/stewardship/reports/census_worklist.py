"""Census changes on the shared review worklist (#1683).

``census_changes`` reads and words the rows; this module turns one page of
them into the review worklist's rows (``web/review_worklist.build``) and
records the worklist's decisions through the census services
(``CensusDecisions``, the stream's ``DecisionService``). The stages, words
and role actions are ``parishkit.stewardship.pipeline``'s; nothing here
restates them, and nothing here decides what is allowed: the services and
their SQL guards (migrations 0060, 0096, 0107, 0108 and 0131) refuse
anything a reader may not do.
"""

from dataclasses import replace
from uuid import UUID

from django.db import IntegrityError
from django.utils.html import format_html

from parishkit.stewardship.accounts.policy import Capability, allows
from parishkit.stewardship.accounts.templatetags.stewardship import source_status
from parishkit.stewardship.observability import Event, emit_failure
from parishkit.stewardship.pipeline import (
    Action,
    Stage,
    Stream,
    allowed_actions,
    census_by_hand,
    census_placement,
    role_actions,
)
from parishkit.stewardship.responses.census_resolution import member_correctable
from parishkit.stewardship.responses.census_review import (
    BulkStale,
    decide_census_changes,
)
from parishkit.stewardship.responses.comparison import ADDRESS_COMPONENTS
from parishkit.stewardship.responses.models import ProposalResolution, ProposedChange
from parishkit.stewardship.responses.refresh_checks import (
    FINAL_EXECUTIONS,
    check_states,
)
from parishkit.stewardship.source.source_status import source_status_label
from parishkit.stewardship.web.presentation import duid
from parishkit.stewardship.web.review_worklist import (
    INCORRECT_WORDS,
    NOT_SEEN,
    WORDS,
    Column,
    Remark,
    StaleDecision,
    build,
)
from parishkit.stewardship.workflows.models import RefreshCheck

from .census_changes import LABELS, REOPENED, STAGES

# The census stream's identity columns, in the shared column order (UX
# conventions, "Table column order"), each sorted by ``census_change_views``'s
# whitelisted sort tokens. The Stage is chosen with the Stage filter.
COLUMNS = (
    Column("family", "Family", sort="family"),
    Column("family_duid", "Family DUID", sort="duid", numeric=True),
    Column("who", "Who", sort="who"),
    Column("member_duid", "Member DUID", sort="member_duid", numeric=True),
    Column("what", "What changed", sort="what"),
    Column("route", "How it reaches ParishSoft", sort="route"),
)
# PostgreSQL's SQLSTATE for a unique violation: a racing second decision of
# the same version, which reads as the stale notice.
UNIQUE_VIOLATION = "23505"
EDIT_MATCHED = "accepted: the value matched the Family's answer."
# The ParishSoft Member ID field of a New Member's Entered by hand dialog
# (#1749), named as the worklist's editor fields are (``read_decision``).
MEMBER_FIELD = "value_member"
MEMBER_TEMPLATE = "stewardship/census-worklist-member.html"
# The line under a row's stage (#1749): why a refresh put an Updated in
# ParishSoft change back in Conflict, and the Member ID a New Member's
# mark named.
REFRESH_CONFLICT = "ParishSoft no longer holds the update."
MEMBER_ID = "ParishSoft Member ID {}."
# A New Member Entered by hand and not Confirmed may have its ParishSoft
# Member ID corrected, or given (#1773): the mark made again, one row at a
# time, in a dialog of its own asking for the ID.
CORRECT_TEMPLATE = "stewardship/census-worklist-member-correct.html"
CORRECT_MEMBER_ID = Remark(
    icon="edit",
    label="Correct the ParishSoft Member ID of {name}",
    title_one="Correct the ParishSoft Member ID of {name}",
    done="ParishSoft Member ID corrected.",
    body=(
        "Record the ID of the Member you added. Check the Member in "
        "ParishSoft first: the next refresh checks the entry again."
    ),
)


def _family(row):
    """The Family cell: the name, or the shared Not in ParishSoft badge."""
    if row["family_name"]:
        return row["family_name"]
    return format_html(
        '<span class="status-badge" data-source-status="missing">{}</span>',
        source_status_label("missing"),
    )


def _who(row):
    """The Who cell: the name, with the shared ParishSoft status badge beside it.

    A Member change whose Member the current data no longer lists, active
    and living, in the Family keeps its stage (rule 4, #1738), so the badge
    (Not in ParishSoft, Inactive or Deceased in ParishSoft) says why.
    """
    badge = source_status(row.get("member_status"))
    return format_html("{} {}", row["who"], badge) if badge else row["who"]


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
    Conflict is decided against ParishSoft's current value.
    """
    withheld = set()
    if row["editor"] is None:
        withheld.add(Action.EDIT)
    if row["placement"].stage is Stage.CONFLICT and not row["resolvable"]:
        withheld |= {Action.ACCEPT, Action.EDIT}
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


def _stage_note(row):
    """The census line under a row's stage (``REFRESH_CONFLICT``,
    ``MEMBER_ID``), or empty."""
    if row.get("refresh_conflict"):
        return REFRESH_CONFLICT
    if row.get("resolution_member") and row.get("resolution_action") == "entered":
        return MEMBER_ID.format(duid(row["resolution_member"]))
    return ""


def _new_member(row):
    """Whether ``row`` is a New Member, whose Entered by hand asks for the
    Member's ParishSoft Member ID (#1749)."""
    return row["entity_kind"] == "proposed_member" and row["field"] == "new_member"


def _correctable(row):
    """Whether a New Member row may have its ParishSoft Member ID corrected.

    Entered by hand (no refresh confirmed it at its current version) and no
    later response replaced it; the page's half of
    ``census_resolution.member_correctable``, from the shaped row.
    """
    return (
        _new_member(row)
        and row["placement"].stage is Stage.ENTERED_BY_HAND
        and not row.get("replaced")
    )


def _entered_template(row):
    """The row's own Entered by hand fields: a New Member's Member ID for a
    first mark (#1749), or for its correction (#1773); None for any other."""
    if not _new_member(row):
        return None
    return CORRECT_TEMPLATE if _correctable(row) else MEMBER_TEMPLATE


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
                "who": _who(row),
                "member_duid": _member_duid(row),
                "what": row["label"],
                "route": row["route_label"],
                "reviewer_value": row["edited"],
                "decided_by": decided_by or "",
                "decided_at": decided_at,
                "note": note or "",
                "stage_note": _stage_note(row),
                "entered_template": _entered_template(row),
                "remark": CORRECT_MEMBER_ID if _correctable(row) else None,
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


def worklist_page(result, query, table, action, *, mutable):
    """The page's ``worklist`` and its table, from ``census_changes``'s result.

    ``table`` is the shaped rows' page (``paginate``); its rows become the
    worklist's rows. ``mutable`` is false while other campaign work holds
    the campaign: the reader is then offered no action at all, as the
    services would refuse them. History (superseded and cancelled rows) is
    a Stage choice of its own, which Every stage does not count.
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


def _made_again(resolution):
    """Whether ``resolution`` marked a change Entered by hand again (#1737).

    Refresh checks exist only at a final change's versions, so a mark made
    at a version some check was made at was made over Entered incorrectly.
    Read from the recorded row, so a replayed first mark (its change is
    entered by now) is not worded "again".
    """
    return RefreshCheck.objects.filter(
        proposal_id=resolution.proposal_id, item_version=resolution.expected_version
    ).exists()


def _corrected(resolution):
    """Whether ``resolution`` corrected a New Member's Member ID (#1773).

    A first mark never follows another: Entered by hand is final, so only a
    mark made again has an ``entered`` resolution at the version before its
    own. A New Member is never Entered incorrectly, so for one that is the
    correction. Read from the recorded row, so a replay is worded alike.
    """
    return (
        resolution.member_duid is not None
        and ProposalResolution.objects.filter(
            proposal_id=resolution.proposal_id,
            expected_version=resolution.expected_version - 1,
            action="entered",
            proposal__entity_kind="proposed_member",
            proposal__field="new_member",
        ).exists()
    )


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
    def _offered(principal, action, versions, proposals, *, bulk):
        """Refuse an action a chosen change's stage does not offer this reader.

        The services and their guards allow some decisions the worklist does
        not offer (Entered by hand on a Conflict, which is accepted first;
        Accept on an Ignored change, which goes back to review first), so the
        page's own offer (``pipeline.allowed_actions``) is checked here, as
        the ``DecisionService`` contract asks. A change whose version moved
        since the page was drawn is left to the service, which answers with
        the stale notice rather than a refusal. A New Member Entered by hand
        also offers Entered by hand again, one row at a time, to correct its
        ParishSoft Member ID (``census_resolution.member_correctable``,
        #1773).
        """
        roles = frozenset(
            role
            for role, capability in (
                ("staff", Capability.MANUAL_CENSUS),
                ("administrator", Capability.PUBLISH_CENSUS),
            )
            if allows(principal, capability)
        )
        # A final change is placed by its latest refresh check at its
        # current version (#1689, #1737). A change a later response replaced
        # is placed so too here; its fixes are refused by the service
        # (``refresh_checks.replaced``), as the page offers none.
        final = {
            pk: proposal
            for pk, proposal in proposals.items()
            if proposal.execution in FINAL_EXECUTIONS
        }
        checks = check_states(
            proposals=list(final),
            versions={str(pk): proposal.version for pk, proposal in final.items()},
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
            correction = (
                action is Action.ENTERED_BY_HAND
                and Action.ENTERED_BY_HAND in role_actions(Stream.CENSUS, roles)
                and stage is Stage.ENTERED_BY_HAND
                and not bulk
                and member_correctable(proposal)
            )
            if action not in offered and not correction:
                raise ValueError("This census change does not offer that action now.")

    def decide(self, principal, decision):
        """Record ``decision`` for ``principal``; the notice the page shows."""
        if decision.action is Action.ADD:
            # Census Add is its own slice (#1710); the page offers no Add.
            raise ValueError("Census changes take no Add yet.")
        versions, proposals = self._proposals(decision)
        self._offered(
            principal, decision.action, versions, proposals, bulk=decision.bulk
        )
        value = member = None
        if decision.action is Action.EDIT:
            (proposal,) = proposals.values()
            value = _value(decision.fields, proposal.field)
        elif decision.action is Action.ENTERED_BY_HAND and decision.fields:
            # A New Member's own Entered by hand dialog sends the ParishSoft
            # Member ID, blank or not (#1749); the service checks it.
            if (
                set(decision.fields) != {MEMBER_FIELD}
                or len(decision.fields[MEMBER_FIELD]) != 1
            ):
                raise ValueError("A Member ID is sent once.")
            (member,) = decision.fields[MEMBER_FIELD]
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
                member=member,
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
        if decision.action is Action.ENTERED_BY_HAND and _corrected(recorded[0]):
            # A New Member's Member ID corrected (#1773), also on a replay.
            return f"{notice_name(proposal)} {CORRECT_MEMBER_ID.done}"
        if decision.action in INCORRECT_WORDS and _made_again(recorded[0]):
            # Entered incorrectly's mark made again (#1737), worded as its
            # icon was, also when the answer is a replay.
            words = INCORRECT_WORDS[decision.action].done
        if decision.action is Action.EDIT and not recorded[0].admin_value_set:
            return f"{notice_name(proposal)} {EDIT_MATCHED}"
        return f"{notice_name(proposal)} {words}"

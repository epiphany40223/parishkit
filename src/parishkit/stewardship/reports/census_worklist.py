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
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.observability import Event, emit_failure
from parishkit.stewardship.pipeline import (
    Action,
    Stage,
    Stream,
    allowed_actions,
    census_addition_placement,
    census_by_hand,
    census_placement,
    may_add,
)
from parishkit.stewardship.responses.capabilities import capability
from parishkit.stewardship.responses.census import (
    ADDRESS_LIMITS,
    blank_address,
    country_choices,
)
from parishkit.stewardship.responses.census_additions import (
    ADD_FIELDS,
    ADDED_PREFIX,
    FAMILY_ADD_FIELDS,
    AddRefusal,
    add_census_change,
    parse_add,
    resolve_census_additions,
)
from parishkit.stewardship.responses.census_resolution import ReopenCovered
from parishkit.stewardship.responses.census_review import (
    FIELDS,
    BulkStale,
    decide_census_changes,
    edit_limit,
)
from parishkit.stewardship.responses.comparison import ADDRESS_COMPONENTS, ValueKind
from parishkit.stewardship.responses.models import (
    CensusAddition,
    CensusAdditionResolution,
    ProposedChange,
)
from parishkit.stewardship.source.source_status import source_status_label
from parishkit.stewardship.web.presentation import duid
from parishkit.stewardship.web.review_worklist import (
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
    Conflict is decided against ParishSoft's current value.
    """
    withheld = set()
    if row["editor"] is None:
        withheld.add(Action.EDIT)
    if row["placement"].stage is Stage.CONFLICT and not row["resolvable"]:
        withheld |= {Action.ACCEPT, Action.EDIT}
    if row.get("covered"):
        # An open addition of the field stands in for it (#1710).
        withheld.add(Action.BACK_TO_REVIEW)
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
    """The Stage filter's counts without History, which Every stage leaves out."""
    return {
        stage.value: counts.get(stage.value, 0)
        for stage in STAGES
        if counts.get(stage.value)
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
        add_template="stewardship/census-worklist-add.html",
        export_template="stewardship/census-worklist-export.html",
        address=page_address(action, link_pairs(query, paging)),
    )
    worklist.stage_choices.insert(-1, history_choice(counts))
    replacements = result.get("replacements", {})
    for row in worklist.rows:
        _added_words(row, replacements)
    if any(row.get("added") for row in worklist.rows):
        # A ticked added change's Back to review withdraws it.
        for choice in worklist.bulk:
            if choice["value"] == Action.BACK_TO_REVIEW.value:
                choice["body"] = f"{choice['body']} {BULK_WITHDRAW}"
    return worklist, table


# An added change's Back to review withdraws it (spec, "Reviewed stage").
WITHDRAW_TITLE = "Withdraw {name}?"
BULK_WITHDRAW = "A ticked change the parish added is withdrawn instead."
OVERLAP = (
    "The Family has an open change of this field too: decide that one, and "
    "withdraw this one if it is no longer needed."
)
WITHDRAW_BODY = (
    "The added change is withdrawn: nothing is entered in ParishSoft, and it "
    "moves to History."
)


def _added_words(row, replacements):
    """Word a parish-added row's Back to review, and every row's Add history.

    An added change's Back to review withdraws it, so its dialog says so;
    an ignored change the parish added another in place of names that one
    under its stage ("Ignored. Added in its place: ..."), and an addition
    made in place of one says so (#1710), as does one whose field the
    Family has an open change of too.
    """
    parts = [row["decision_label"]] if row["decision_label"] else []
    if row.get("added"):
        for icon in row["icons"]:
            if icon["action"] == Action.BACK_TO_REVIEW.value and not icon["selected"]:
                icon["title_one"] = WITHDRAW_TITLE
                icon["body"] = WITHDRAW_BODY
        if row.get("in_place_of"):
            parts.append("Added in place of an ignored change.")
        if row.get("overlap"):
            parts.append(OVERLAP)
    elif row["id"] in replacements:
        parts.append(f"Added in its place: {'; '.join(replacements[row['id']])}")
    row["decision_label"] = " ".join(parts)


def add_fields():
    """What the Add dialog draws (#1710; ``census-worklist-add.html``).

    ``fields`` is each field an Add may be for, in ``ADD_FIELDS`` order,
    with its label and its value control: a date, its census choices,
    text no longer than the field takes (as Edit limits it, by the handling
    registry's handling), or a Family address's parts. ``address`` is the
    shared address editor's parts, limits and countries, and
    ``address_fields`` the choice value that shows it (data-show-when).
    """
    fields = []
    for name in ADD_FIELDS:
        if name in FAMILY_ADD_FIELDS:
            fields.append({"name": name, "label": LABELS[name], "kind": "address"})
            continue
        definition = FIELDS[name]
        handling = capability("member", name).handling.value
        fields.append(
            {
                "name": name,
                "label": LABELS[name],
                "kind": "date" if definition.kind is ValueKind.DATE else "text",
                "choices": [choice for choice in definition.choices if choice],
                "maxlength": edit_limit(name, handling),
            }
        )
    return {
        "fields": fields,
        "address_fields": "|".join(FAMILY_ADD_FIELDS),
        "address": {
            "parts": blank_address(),
            "limits": dict(ADDRESS_LIMITS),
            "countries": country_choices(),
        },
    }


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


def addition_name(addition):
    """A parish-added change as a notice names it, as ``notice_name`` does."""
    who = (
        "the Family"
        if addition.entity_kind == "family"
        else f"Member DUID {addition.entity_key}"
    )
    label = LABELS.get(addition.field, addition.field)
    return f"{label} for {who} in Family DUID {addition.family.family_duid}"


# The worklist decisions an added change takes: its own later steps.
ADDITION_STEPS = {
    Action.ENTERED_BY_HAND: "entered",
    Action.BACK_TO_REVIEW: "withdrawn",
}


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
    changes or parish-added ones (otherwise it is denied, as an unknown one
    is), records the decision through ``census_review.decide_census_changes``
    and ``census_additions`` all or none, and returns the notice the page
    shows. Add records a new addition (``census_additions.
    add_census_change``). A change someone else changed first is
    ``StaleDecision``, named when the service knows which.
    """

    def __init__(self, store, campaign_id):
        self.store = store
        self.campaign_id = campaign_id

    @staticmethod
    def _split(versions):
        """The chosen items as (proposals, additions), each id to version."""
        proposals, additions = {}, {}
        for key, version in versions.items():
            if key.startswith(ADDED_PREFIX):
                additions[UUID(key.removeprefix(ADDED_PREFIX))] = version
            else:
                proposals[UUID(key)] = version
        return proposals, additions

    def _additions(self, ids):
        """The chosen additions, by id, each this campaign's; and the resolved ones."""
        found = {
            addition.pk: addition
            for addition in CensusAddition.objects.select_related("family").filter(
                pk__in=list(ids), family__campaign_id=self.campaign_id
            )
        }
        if len(found) != len(ids):
            raise PermissionError("A census change is unavailable.")
        resolved = set(
            CensusAdditionResolution.objects.filter(
                addition_id__in=list(ids)
            ).values_list("addition_id", flat=True)
        )
        return found, resolved

    def _proposals(self, ids):
        """The chosen changes, by id, each this campaign's live change."""
        found = {
            proposal.pk: proposal
            for proposal in ProposedChange.objects.select_related("submission__family")
            .filter(pk__in=list(ids), submission__campaign_id=self.campaign_id)
            .filter(submission__mode="live")
            .exclude(handling="report-only")
        }
        if len(found) != len(ids):
            raise PermissionError("A census change is unavailable.")
        return found

    @staticmethod
    def _roles(principal):
        """The reader's census roles: Staff, and an Administrator's too."""
        return frozenset(
            role
            for role, capability in (
                ("staff", Capability.MANUAL_CENSUS),
                ("administrator", Capability.PUBLISH_CENSUS),
            )
            if allows(principal, capability)
        )

    def _offered(self, principal, action, versions, proposals, added=()):
        """Refuse an action a chosen change's stage does not offer this reader.

        The services and their guards allow some decisions the worklist does
        not offer (Entered by hand on a Conflict, which is accepted first;
        Accept on an Ignored change, which goes back to review first), so the
        page's own offer (``pipeline.allowed_actions``) is checked here, as
        the ``DecisionService`` contract asks. A change whose version moved
        since the page was drawn is left to the service, which answers with
        the stale notice rather than a refusal. ``added`` are the open
        additions' placements, which offer only their own later steps.
        """
        roles = self._roles(principal)
        stages = [
            (
                census_placement(
                    decision=proposal.decision,
                    execution=proposal.execution,
                    handling=proposal.handling,
                    entity_kind=proposal.entity_kind,
                    admin_value_set=proposal.admin_value_set,
                ).stage,
                census_by_hand(proposal.handling, proposal.entity_kind),
            )
            for pk, proposal in proposals.items()
            if proposal.version == versions[pk]
        ] + [(placement.stage, True) for placement in added]
        for stage, by_hand in stages:
            offered = allowed_actions(Stream.CENSUS, stage, roles, by_hand=by_hand)
            if action not in offered:
                raise ValueError("This census change does not offer that action now.")

    def _add(self, principal, decision):
        """Record a parish-added change (#1710); the notice the page shows."""
        if decision.versions:
            raise ValueError("Add names no item.")
        if not may_add(Stream.CENSUS, self._roles(principal)):
            raise PermissionError("Census Add is unavailable.")
        add = parse_add(decision.fields)
        addition, created = add_census_change(
            self.store,
            principal.identity,
            self.campaign_id,
            add,
            note=decision.note,
            request_key=decision.key,
        )
        name = addition_name(addition)
        return f"{name} added." if created else f"{name} was already added."

    def decide(self, principal, decision):
        """Record ``decision`` for ``principal``; the notice the page shows.

        Add records a new addition. Any other decision may cover proposals
        and additions together (a bulk Entered by hand or Back to review):
        each is recorded by its own service in one work transaction, all or
        none. An addition takes only Entered by hand and Back to review,
        which withdraws it.
        """
        if decision.action is Action.ADD:
            return self._add(principal, decision)
        proposal_ids, addition_ids = self._split(decision.versions)
        proposals = self._proposals(proposal_ids)
        additions, resolved = self._additions(addition_ids)
        self._offered(
            principal,
            decision.action,
            proposal_ids,
            proposals,
            added=[
                census_addition_placement("open")
                for pk in additions
                if pk not in resolved and addition_ids[pk] == 1
            ],
        )
        value = None
        if decision.action is Action.EDIT:
            if additions or len(proposals) != 1:
                raise ValueError("An edit names one Family's change.")
            (proposal,) = proposals.values()
            value = _value(decision.fields, proposal.field)
        elif decision.fields:
            raise ValueError("Only Edit sends a value.")
        if additions and decision.action not in ADDITION_STEPS:
            raise ValueError("An added change takes no such decision.")
        try:
            with work_transaction():
                recorded = []
                if proposals:
                    recorded += decide_census_changes(
                        self.store,
                        principal.identity,
                        decision.action.value,
                        proposal_ids,
                        note=decision.note,
                        request_key=decision.key,
                        bulk=decision.bulk,
                        value=value,
                    )
                if additions:
                    recorded += resolve_census_additions(
                        self.store,
                        principal.identity,
                        self.campaign_id,
                        addition_ids,
                        ADDITION_STEPS[decision.action],
                        note=decision.note,
                        request_key=decision.key,
                        bulk=decision.bulk,
                    )
        except ReopenCovered:
            # Checked under the work order, so no Add slips in between; the
            # page withholds the icon, but a page drawn before the Add, or
            # the bulk bar, still posts it (#1710).
            raise AddRefusal(
                "reopen_added",
                "A change the parish added for this field is still open, so this "
                "ignored change cannot go back to review.",
                field_ids=("bulk_note" if decision.bulk else "note",),
                fix="Withdraw the added change first, then send this one back.",
            ) from None
        except BulkStale as error:
            if error.proposal_id in additions:
                raise StaleDecision(
                    addition_name(additions[error.proposal_id])
                ) from None
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
        if additions:
            (addition,) = additions.values()
            if decision.action is Action.BACK_TO_REVIEW:
                return f"{addition_name(addition)} withdrawn."
            return f"{addition_name(addition)} {words}"
        (proposal,) = proposals.values()
        if decision.action is Action.EDIT and not recorded[0].admin_value_set:
            return f"{notice_name(proposal)} {EDIT_MATCHED}"
        return f"{notice_name(proposal)} {words}"

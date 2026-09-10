"""Pure lifecycle decisions, not authorization or proof of external readiness.

Owning transactions must lock and reload these facts and obtain the named
guards from trusted services. A True decision is never an activation permit:
token preparation, work drainage and current Google authorization have separate
owners. No caller may treat a serialized collection of booleans as evidence.
"""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from types import MappingProxyType

from .domain import CampaignState as State
from .domain import SystemMode as Mode
from .domain import UTCInterval


class Action(StrEnum):
    """Explicit commands; mode-only operations are not fake campaign transitions."""

    ACTIVATE = "activate"
    START = "start"
    CLOSE = "close"
    WITHDRAW = "withdraw"
    REOPEN = "reopen"
    ARCHIVE = "archive"
    UNARCHIVE = "unarchive"
    RETURN_TESTING = "return_testing"
    PURGE = "purge"
    PURGE_ABORT = "purge_abort"
    PURGE_COMPLETE = "purge_complete"
    PURGE_CLEANUP_FAILED = "purge_cleanup_failed"


class CampaignWorkKind(StrEnum):
    """Immutable routing classifications supplied by the owning durable work record.

    These are policy inputs, not caller-selectable authorization exemptions.
    Post-close reporting, operational mail and restore maintenance have separate
    owners and are deliberately not represented as ordinary campaign work.
    """

    LIVE_PREPARATION = "live_preparation"
    LIVE_DELIVERY = "live_delivery"
    REHEARSAL = "rehearsal"
    PREVIEW = "preview"
    READINESS_TEST = "readiness_test"


@dataclass(frozen=True)
class Transition:
    """Reviewable registry entry with immutable guard and attribution contracts."""

    sources: frozenset[State]
    targets: frozenset[State]
    actor: str
    guards: frozenset[str]
    modes: frozenset[Mode]
    reauthentication: bool = False
    confirmation: bool = False


def _transition(
    sources, targets, actor, *guards, modes=(Mode.TESTING, Mode.PRODUCTION)
):
    """Construct internal registry entries without mutable collections."""
    admin = actor == "administrator"
    if actor == "purge_worker":
        guards += ("no_current_campaign", "fenced_owner")
    return Transition(
        frozenset(sources),
        frozenset(targets),
        actor,
        frozenset(guards),
        frozenset(modes),
        admin,
        admin,
    )


TRANSITIONS = MappingProxyType(
    {
        Action.ACTIVATE: _transition(
            [State.DRAFT],
            [State.SCHEDULED, State.ACTIVE],
            "administrator",
            "current",
            "readiness",
            "configuration_coherent",
            "cleanup_complete",
            "token_generation",
            "catch_up_demand",
            "no_restore",
            "no_purge",
            modes=(Mode.TESTING,),
        ),
        Action.START: _transition(
            [State.SCHEDULED],
            [State.ACTIVE],
            "boundary_worker",
            "current",
            "boundary_current",
            "no_restore",
            "no_purge",
            "fenced_owner",
            modes=(Mode.PRODUCTION,),
        ),
        Action.CLOSE: _transition(
            [State.ACTIVE],
            [State.CLOSED],
            "boundary_worker",
            "current",
            "boundary_current",
            "invalidate_family_access",
            "no_restore",
            "no_purge",
            "fenced_owner",
            modes=(Mode.PRODUCTION,),
        ),
        Action.WITHDRAW: _transition(
            [State.SCHEDULED],
            [State.DRAFT],
            "administrator",
            "current",
            "quiescent",
            "cancel_future_work",
            "invalidate_readiness",
            "no_restore",
            "no_purge",
            "reason",
            modes=(Mode.PRODUCTION,),
        ),
        Action.REOPEN: _transition(
            [State.CLOSED],
            [State.ACTIVE],
            "administrator",
            "current",
            "readiness",
            "configuration_coherent",
            "token_generation",
            "extended_end",
            "no_restore",
            "no_purge",
            "quiescent",
        ),
        Action.ARCHIVE: _transition(
            [State.CLOSED],
            [State.ARCHIVED],
            "administrator",
            "current",
            "quiescent",
            "post_close_resolved",
            "no_restore",
            "no_purge",
        ),
        Action.UNARCHIVE: _transition(
            [State.ARCHIVED],
            [State.CLOSED],
            "administrator",
            "current",
            "no_other_current",
            "quiescent",
            "no_restore",
            "no_purge",
        ),
        Action.RETURN_TESTING: _transition(
            [State.ARCHIVED],
            [State.ARCHIVED],
            "administrator",
            "current",
            "quiescent",
            "post_close_resolved",
            "no_restore",
            "no_purge",
            "clear_current_pointer",
        ),
        Action.PURGE: _transition(
            [State.ARCHIVED],
            [State.PURGING],
            "purge_worker",
            "purge_request",
            "purge_window",
            "no_current_campaign",
            "fresh_backup",
            "quiescent",
            modes=(Mode.TESTING,),
        ),
        Action.PURGE_ABORT: _transition(
            [State.PURGING],
            [State.ARCHIVED],
            "purge_worker",
            "no_deletion_committed",
            modes=(Mode.TESTING,),
        ),
        Action.PURGE_COMPLETE: _transition(
            [State.PURGING, State.PURGE_CLEANUP_FAILED],
            [State.PURGED],
            "purge_worker",
            "database_deleted",
            "files_deleted",
            "fenced_owner",
            modes=(Mode.TESTING,),
        ),
        Action.PURGE_CLEANUP_FAILED: _transition(
            [State.PURGING],
            [State.PURGE_CLEANUP_FAILED],
            "purge_worker",
            "database_deleted",
            "fenced_owner",
            modes=(Mode.TESTING,),
        ),
    }
)


@dataclass(frozen=True)
class CampaignFacts:
    """A point-in-time view; constructor rejects noncanonical identity and mode."""

    state: State
    mode: Mode
    interval: UTCInterval
    current: bool
    restore_required: bool = False
    delivery_paused: bool = False
    catch_up_pending: bool = False

    def __post_init__(self):
        """Reject strings/bools that could silently masquerade as trusted facts."""
        if not isinstance(self.state, State) or not isinstance(self.mode, Mode):
            raise ValueError("Canonical lifecycle and mode are required.")
        if not isinstance(self.interval, UTCInterval):
            raise ValueError("A resolved campaign interval is required.")
        for name in (
            "current",
            "restore_required",
            "delivery_paused",
            "catch_up_pending",
        ):
            if type(getattr(self, name)) is not bool:
                raise ValueError("Campaign flags must be booleans.")


def portal_admitted(facts: CampaignFacts, now: datetime) -> bool:
    """Gate exactly by dates even when persisted start/close processing lags."""
    within = facts.interval.contains(now)
    if not facts.current or facts.restore_required or not within:
        return False
    if facts.mode is Mode.TESTING:
        return facts.state is State.DRAFT
    return facts.state in {State.SCHEDULED, State.ACTIVE}


def campaign_work_admitted(facts: CampaignFacts, now: datetime, kind: CampaignWorkKind):
    """Decide ordinary/live versus explicit-test applicability, not authorization.

    The owner must reload immutable routing under the work/admission locks;
    selecting an enum in a web request must never grant an exemption.
    """
    facts.interval.contains(now)  # Validate the instant even for an explicit preview.
    if not isinstance(kind, CampaignWorkKind):
        raise ValueError("A canonical campaign work kind is required.")
    if not facts.current or facts.restore_required:
        return False
    if kind in {CampaignWorkKind.PREVIEW, CampaignWorkKind.READINESS_TEST}:
        return facts.state in {
            State.DRAFT,
            State.SCHEDULED,
            State.ACTIVE,
            State.CLOSED,
            State.ARCHIVED,
        }
    if not portal_admitted(facts, now):
        return False
    if kind is CampaignWorkKind.REHEARSAL:
        return facts.mode is Mode.TESTING
    if facts.mode is not Mode.PRODUCTION or facts.catch_up_pending:
        return False
    return kind is not CampaignWorkKind.LIVE_DELIVERY or not facts.delivery_paused


def transition_target(
    action: Action,
    facts: CampaignFacts,
    now: datetime,
    *,
    proposed_interval: UTCInterval | None = None,
) -> State | None:
    """Return a date/state-eligible target, never proof that registry guards passed."""
    # Validate now even for an operation with no date predicate.
    within = facts.interval.contains(now)
    if not isinstance(action, Action):
        raise ValueError("A canonical lifecycle action is required.")
    if proposed_interval is not None and not isinstance(proposed_interval, UTCInterval):
        raise ValueError("A resolved proposed campaign interval is required.")
    rule = TRANSITIONS[action]
    if facts.state not in rule.sources or facts.mode not in rule.modes:
        return None
    if "current" in rule.guards and not facts.current:
        return None
    if "no_restore" in rule.guards and facts.restore_required:
        return None
    if "no_current_campaign" in rule.guards and facts.current:
        return None
    if action is Action.ACTIVATE:
        if now >= facts.interval.end:
            return None
        return State.ACTIVE if within else State.SCHEDULED
    if action in {Action.START, Action.CLOSE, Action.WITHDRAW, Action.REOPEN}:
        if action is Action.START and now < facts.interval.start:
            return None
        if action is Action.CLOSE and now < facts.interval.end:
            return None
        if action is Action.WITHDRAW and now >= facts.interval.start:
            return None
        if action is Action.REOPEN and (
            proposed_interval is None
            or proposed_interval.start != facts.interval.start
            or proposed_interval.end <= facts.interval.end
            or not proposed_interval.contains(now)
        ):
            return None
    return next(iter(rule.targets))


def draft_creation_admitted(*, mode, current_id, states, nonterminal_purge):
    """Require the global purge-request fact, not only visible purge lifecycle
    states.
    """
    if not isinstance(mode, Mode) or type(nonterminal_purge) is not bool:
        raise ValueError("Canonical creation facts are required.")
    states = tuple(states)
    if any(not isinstance(state, State) for state in states):
        raise ValueError("Canonical campaign states are required.")
    return (
        mode is Mode.TESTING
        and current_id is None
        and not nonterminal_purge
        and all(state in {State.ARCHIVED, State.PURGED} for state in states)
    )


def structural_edit_admitted(state: State, *, ever_active: bool, locked: bool):
    """Only withdrawal can unlock a never-active draft; Testing alone is not enough."""
    if not isinstance(state, State) or any(
        type(v) is not bool for v in (ever_active, locked)
    ):
        raise ValueError("Canonical structural-lock facts are required.")
    return state is State.DRAFT and not ever_active and not locked


def transition_audit_event(action: Action) -> str:
    """Derive a stable event name; owning workflows emit its validated payload."""
    if not isinstance(action, Action):
        raise ValueError("A canonical lifecycle action is required.")
    return "campaign_" + action.value

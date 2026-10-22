"""Authorized Staff follow-up changes, never edits to a Family's submitted text.

Two writers share one revision history (``AdditionalInformationRevision``):

- ``decide_information`` records the Additional information worklist's
  decisions (#1685): Accept, Ignore, Back to review and Handled, on one
  request or in bulk, all or none.
- ``update_information`` is ``pk-admin followup information-update``'s Save
  of the follow-up flags and notes, whose revision's review decision follows
  the flags (``flag_decision``).

Every revision states its review decision; the stage is derived from it and
the flags (``information_stage``), never stored. SQL checks the same
authority, version and pairing again (schema/information_followup.sql and
migration 0126).
"""

from uuid import UUID

from django.db.models import F

from parishkit.stewardship.accounts.policy import Capability, allows, current_principal
from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.audit.schemas import Action, ActorKind, Outcome
from parishkit.stewardship.audit.services import record_action
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.pipeline import Action as Decide
from parishkit.stewardship.pipeline import (
    NoteRequired,
    Stage,
    Stream,
    information_placement,
    undo_needs_note,
    undo_stage,
)
from parishkit.stewardship.reports.export_services import admit_campaign
from parishkit.stewardship.storage import StaleRecordError
from parishkit.stewardship.web.content import bounded_text
from parishkit.stewardship.web.contracts import check_version

from .census_review import KeyRebound, bulk_key_for
from .models import AdditionalInformationItem, AdditionalInformationRevision

# The worklist's decisions on a request: what each records (follow-up
# needed, followed up, review decision) and the stages it is offered at
# (review pipeline spec, "Role-specific actions"). Back to review's record
# depends on the stage it leaves (``recorded``).
DECISIONS = {
    Decide.ACCEPT: ((True, False, "accepted"), {Stage.TO_REVIEW}),
    Decide.IGNORE: ((False, False, "ignored"), {Stage.TO_REVIEW}),
    Decide.BACK_TO_REVIEW: (
        (False, False, "none"),
        {Stage.TO_HANDLE, Stage.IGNORED, Stage.HANDLED},
    ),
    Decide.HANDLED: ((True, True, "accepted"), {Stage.TO_HANDLE}),
}
# What Back to review records for the stage it returns a request to
# (``pipeline.undo_stage``): a decision's undo goes to To review; Handled's
# undo (#1840) removes only the follow-up's completion, so the request is
# To handle again, still accepted, follow-up still needed.
UNDO_RECORDS = {
    Stage.TO_REVIEW: (False, False, "none"),
    Stage.TO_HANDLE: (True, False, "accepted"),
}
MAX_NOTE = 2000
MAX_BULK = 100


class ClearUnconfirmed(ValueError):
    """Clearing a completed follow-up without ticking its confirmation.

    The item page shows this refusal at the confirmation box (#1332); it
    is a ``ValueError``, so any other caller still refuses it as before.
    It is raised only after the version check, under the item's lock, so
    the submitted version was current when it was refused.
    """


class UndoNeedsNote(NoteRequired):
    """A Handled request (or answer) left Handled with no note; nothing was saved.

    Undoing a terminal step needs a reason (review pipeline spec, "Undoing
    a terminal stage", #1840), on the worklist and through ``pk-admin
    followup information-update`` alike; the revision guard refuses it too.
    Reopening an Ignored item takes an optional note (#1889), so this is
    the only missing note refused.
    """


class StaleItem(StaleRecordError):
    """A chosen item changed since the page was drawn; nothing was saved.

    ``item_id`` names it, so the page's notice can.
    """

    def __init__(self, item_id):
        super().__init__("A chosen item was changed by someone else first.")
        self.item_id = item_id


def staff_ignored(latest):
    """Whether a request's latest revision leaves it Ignored.

    ``latest`` is that revision (None when no Staff member has saved the
    request). A revision since migration 0126 says so itself; an older one
    has no decision, and one that left follow-up not needed is a Staff
    choice of Ignore (review pipeline spec, "Existing state to stage").
    The caller only asks while the request is neither needed nor completed.
    """
    if latest is None:
        return False
    return latest.review_decision in (None, "ignored")


def information_stage(item, latest):
    """One request's pipeline placement from its stored state and latest revision."""
    return information_placement(
        disposition=item.disposition,
        follow_up_needed=item.follow_up_needed,
        followed_up=item.followed_up_at is not None,
        staff_ignored=staff_ignored(latest),
    )


def flag_decision(item, latest, *, follow_up_needed, followed_up):
    """The review decision a follow-up flags Save records (``update_information``).

    Needed or completed is Accepted. Neither keeps a To review or Ignored
    request as it was, so a notes-only Save never decides it; clearing a To
    handle or Handled request sends it back to review, so the command line
    never ignores a request on its own.
    """
    if follow_up_needed or followed_up:
        return "accepted"
    if item.follow_up_needed or item.followed_up_at is not None:
        return "none"
    return "ignored" if staff_ignored(latest) else "none"


def new_note(notes, latest):
    """Whether ``notes`` say something new: not blank, nor the earlier notes.

    A revision carries the request's Staff notes forward, so undoing a
    Handled request needs notes that differ from ``latest``'s (its newest
    revision, or None), as the revision guard checks.
    """
    return bool(notes.strip()) and (latest is None or notes != latest.notes)


def recorded(action, placement):
    """What ``action`` records on a request at ``placement``: (needed, done, decision).

    Back to review records the stage ``pipeline.undo_stage`` returns the
    request to (``UNDO_RECORDS``); every other action records one fixed
    triple (``DECISIONS``).
    """
    if action is Decide.BACK_TO_REVIEW:
        return UNDO_RECORDS[undo_stage(Stream.ADDITIONAL_INFORMATION, placement)]
    return DECISIONS[action][0]


def records(action):
    """Every revision ``action`` can store, as the fields a repeat must match.

    A replayed request key is checked against these, since the request has
    moved on since its first post: Back to review stored either of its two
    records, and only the undo of Handled confirms clearing the completion.
    """
    triples = (
        UNDO_RECORDS.values()
        if action is Decide.BACK_TO_REVIEW
        else [DECISIONS[action][0]]
    )
    return [
        {
            "follow_up_needed": needed,
            "followed_up": done,
            "review_decision": decision,
            "confirm_clear": action is Decide.BACK_TO_REVIEW and needed,
        }
        for needed, done, decision in triples
    ]


def latest_revision(item_id):
    """A request's newest Staff revision, or None."""
    return (
        AdditionalInformationRevision.objects.filter(item_id=item_id)
        .order_by("-expected_version")
        .first()
    )


def authorize_information(store, actor_id):
    """Reload coherent policy; old sessions and a known item UUID are not grants."""
    principal = current_principal(store, actor_id)
    if not allows(principal, Capability.ADDITIONAL_FOLLOWUP):
        raise PermissionError("Additional-information access is unavailable.")
    return principal


def update_information(
    store,
    actor_id,
    item_id,
    *,
    expected_version,
    request_key,
    follow_up_needed,
    followed_up,
    confirm_clear,
    notes,
):
    """Append one replay-safe revision and projection under the shared work order.

    SQL independently checks ownership, replay uniqueness and the paired history/
    projection/audit. It stamps completion time and retains its original actor
    when a later Staff member only changes notes or the needed checkbox.
    """
    if (
        any(not isinstance(value, UUID) for value in (actor_id, item_id, request_key))
        or type(expected_version) is not int
        or not 1 <= expected_version < 2**63 - 1
        or any(
            type(value) is not bool
            for value in (follow_up_needed, followed_up, confirm_clear)
        )
        or not isinstance(notes, str)
        or len(notes) > 5000
    ):
        raise ValueError("Invalid additional-information change.")
    bounded_text(notes)
    intent = dict(
        item_id=item_id,
        expected_version=expected_version,
        follow_up_needed=follow_up_needed,
        followed_up=followed_up,
        confirm_clear=confirm_clear,
        notes=notes,
    )
    with work_transaction():
        authorize_information(store, actor_id)
        item = (
            AdditionalInformationItem.objects.select_for_update(of=("self",))
            .select_related("submission")
            .get(pk=item_id)
        )
        admit_campaign(item.submission.campaign_id, mutating=True)
        previous = AdditionalInformationRevision.objects.filter(
            actor_id=actor_id, request_key=request_key
        ).first()
        if previous is not None:
            if any(
                getattr(previous, field) != value for field, value in intent.items()
            ):
                raise ValueError("This request key is already bound.")
            return previous
        check_version(item, expected_version)
        if item.followed_up_at is not None and not followed_up and not confirm_clear:
            raise ClearUnconfirmed("Confirm clearing the completed follow-up.")
        latest = latest_revision(item_id)
        if (
            item.followed_up_at is not None
            and not followed_up
            and not new_note(notes, latest)
        ):
            # Clearing a completed follow-up undoes Handled, which needs a
            # reason however it is recorded (#1840).
            raise UndoNeedsNote("Undoing a handled request needs a note.")
        # An Ignored request leaves Ignored with an optional note (#1889).
        decision = flag_decision(
            item, latest, follow_up_needed=follow_up_needed, followed_up=followed_up
        )
        return _write(item, actor_id, request_key, intent, decision)


def _write(item, actor_id, request_key, intent, decision):
    """Append one locked request's revision, its projection and its audit event.

    The caller holds the work order and the request's row lock and has
    checked its version; ``intent`` is the revision's fields and
    ``decision`` its review decision.
    """
    expected_version = intent["expected_version"]
    revision = AdditionalInformationRevision.objects.create(
        actor_id=actor_id, request_key=request_key, review_decision=decision, **intent
    )
    revision.refresh_from_db()
    AdditionalInformationItem.objects.filter(pk=item.pk).update(
        follow_up_needed=intent["follow_up_needed"],
        followed_up_at=revision.followed_up_at,
        version=F("version") + 1,
    )
    system = SystemConfiguration.objects.select_related(
        "active_configuration__parish"
    ).get()
    record_action(
        Action.INFORMATION_UPDATED,
        actor_kind=ActorKind.PORTAL_USER,
        actor_id=actor_id,
        subject_id=revision.pk,
        parish_id=system.active_configuration.parish.pk,
        campaign_id=item.submission.campaign_id,
        context={
            "outcome": Outcome.CHANGED,
            "before_version": expected_version,
            "after_version": expected_version + 1,
        },
    )
    return revision


def _decide_one(item, actor_id, action, version, key, note):
    """Record one worklist decision on one locked request.

    A repeated post (its key already recorded) returns the first revision
    when it recorded this same decision, and is ``KeyRebound`` otherwise.
    A request whose version moved, or whose stage no longer offers the
    action (someone decided it first), is ``StaleItem``.
    """
    previous = AdditionalInformationRevision.objects.filter(
        actor_id=actor_id, request_key=key
    ).first()
    if previous is not None:
        if (
            previous.item_id != item.pk
            or previous.expected_version != version
            or not any(
                all(getattr(previous, name) == value for name, value in fields.items())
                for fields in records(action)
            )
        ):
            raise KeyRebound("This request key is already bound.")
        return previous
    if item.version != version:
        raise StaleItem(item.pk)
    latest = latest_revision(item.pk)
    placement = information_stage(item, latest)
    stage = placement.stage
    if stage not in DECISIONS[action][1]:
        raise StaleItem(item.pk)
    if (
        action is Decide.BACK_TO_REVIEW
        and undo_needs_note(stage)
        and not new_note(note, latest)
    ):
        # Undoing Handled needs a reason (review pipeline spec, "Undoing a
        # terminal stage"); the dialog asks for it first. Reopening an
        # Ignored request takes an optional note (#1889).
        raise UndoNeedsNote("Undoing a handled request needs a note.")
    needed, completed, decision = recorded(action, placement)
    # A revision's notes are the request's Staff notes, which the export
    # and the search read from the latest revision: a decision's note
    # replaces them, and a decision with none keeps them as they were.
    intent = dict(
        item_id=item.pk,
        expected_version=version,
        follow_up_needed=needed,
        followed_up=completed,
        # Only Handled's undo clears a completion, which it confirms.
        confirm_clear=stage is Stage.HANDLED,
        notes=note if note.strip() else (latest.notes if latest else ""),
    )
    return _write(item, actor_id, key, intent, decision)


def decide_information(
    store, actor_id, campaign_id, action, versions, *, note, request_key, bulk=False
):
    """Record one worklist decision on one or several requests, all or none.

    ``versions`` maps each request's id to the version its row showed;
    ``request_key`` is the row's key, or for ``bulk`` the page's bulk key,
    from which each request's own key is derived, so a repeated post
    records nothing more. Every request must be a live one of
    ``campaign_id``; they are locked in primary-key order in one work
    transaction and each is recorded as its own icon would record it. A
    request someone changed first raises ``StaleItem``, a rebound key
    ``KeyRebound``; nothing is saved then. Returns the revisions.
    """
    if (
        not isinstance(actor_id, UUID)
        or not isinstance(request_key, UUID)
        or action not in DECISIONS
        or not isinstance(versions, dict)
        or not 1 <= len(versions) <= MAX_BULK
        or (not bulk and len(versions) != 1)
        or any(
            not isinstance(key, UUID)
            or type(version) is not int
            or not 1 <= version < 2**63 - 1
            for key, version in versions.items()
        )
        or not isinstance(note, str)
        or len(note) > MAX_NOTE
    ):
        raise ValueError("Invalid information decision.")
    bounded_text(note)
    with work_transaction():
        authorize_information(store, actor_id)
        items = list(
            AdditionalInformationItem.objects.select_for_update(of=("self",))
            .select_related("submission")
            .filter(
                pk__in=list(versions),
                submission__campaign_id=campaign_id,
                submission__mode="live",
            )
            .order_by("pk")
        )
        if len(items) != len(versions):
            raise PermissionError("A chosen information request is unavailable.")
        admit_campaign(campaign_id, mutating=True)
        return [
            _decide_one(
                item,
                actor_id,
                action,
                versions[item.pk],
                bulk_key_for(request_key, item.pk) if bulk else request_key,
                note,
            )
            for item in items
        ]

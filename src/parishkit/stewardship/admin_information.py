"""``followup information-show`` and ``information-update`` (ADM-11 PR 11b).

Additional information (``admin:information_queue``,
``reports.information_views``) lists what Families asked the parish to know,
which Staff follow up. ``report information`` counts the queue; these are
one item's page and its Save, through the page's own pieces so the two
cannot drift:

- ``followup information-show`` reads the item page through the page's
  admission (``information_views._principal``, ``ADDITIONAL_FOLLOWUP``), the
  shared report read (current campaign, ``admit_report_read``, the campaign
  read guard) and the page's ``information_page`` and
  ``information_history``, recording the page's ``information_viewed``
  (started, then succeeded or failed).
- ``followup information-update`` sets the item's follow-up flags and notes
  through the shared service (``responses.information.update_information``),
  in the page's command scope, recording the service's
  ``information_updated`` and the command's
  ``admin_cmd_followup_information_update`` for a new revision. The item page
  had the same Save until the review worklist took its decisions (#1685);
  the revision's review decision follows the flags
  (``responses.information.flag_decision``).

The item is a Family's own words, so the documents print ids, versions,
states, flags, account ids and instants only: never the Family's name or
DUID, the text it submitted, or the Staff notes. The submission's content
stays ``export information``'s. The host-only command line adds no rate
limit or hold beyond the page's.
"""

from dataclasses import dataclass
from uuid import UUID

from .admin_followup import InvalidFollowup, _record_command, problem
from .admin_reads import NotAvailable, ReadModel
from .admin_roster_tick import admit_with, scoped_write

# The longest Staff notes the page's form takes (its textarea's maxlength,
# which ``update_information`` enforces again).
MAX_NOTES = 5000
# Clearing a completed follow-up undoes Handled, which needs a reason too
# (#1840), as Back to review on the worklist does.
UNDO_NOTE = "This request is Handled; give new --notes saying why it is undone."
# The page's confirmation box, on the command line.
CONFIRM_CLEAR = (
    "This follow-up is completed; give --confirm-clear to reopen it. The "
    "previous completion stays in history."
)


def _principal():
    """The page's own admission, imported once Django is set up."""
    from .reports.information_views import _principal as admission

    return admission


def _admit(caller, store, *, final=False):
    """The item page's admission for its Save (Staff and Administrators)."""
    return admit_with(_principal(), caller, store, final=final)


def _item(row):
    """One item as the documents print it: no Family, text or notes.

    ``disposition`` is the page's (``current_actionable``, or why the item
    is no longer actionable); ``completed`` and ``followed_up_by`` say who
    completed the follow-up, by account id, and when (``followed_up_at``).
    ``replacement_id`` names a later request that replaced this one.
    """
    return {
        "id": row["id"],
        "version": row["version"],
        "disposition": row["disposition"],
        "submitted_at": row["submitted_at"],
        "follow_up_needed": row["follow_up_needed"],
        "completed": row["followed_up_at"] is not None,
        "followed_up_at": row["followed_up_at"],
        "followed_up_by": row["followed_up_by_id"],
        "replacement_id": row.get("replacement_id"),
        "previously_reported": bool(row.get("previously_reported")),
        "correction_resolved": bool(row.get("correction_resolved")),
    }


def _revision(revision):
    """One history revision: its flags, actors and instants, never its notes."""
    return {
        "id": revision.pk,
        "expected_version": revision.expected_version,
        "follow_up_needed": revision.follow_up_needed,
        "followed_up": revision.followed_up,
        "confirm_clear": revision.confirm_clear,
        "followed_up_at": revision.followed_up_at,
        "followed_up_by": revision.followed_up_by_id,
        "actor_id": revision.actor_id,
        "created_at": revision.created_at,
    }


@dataclass(frozen=True)
class InformationShow(ReadModel):
    """One Additional information item's page, without its Family or words.

    ``item`` is the item as ``_item`` prints it; ``history`` one page of its
    Staff revisions, newest first, as the page shows it (each as
    ``_revision`` prints it); ``more_history`` whether another page follows.
    """

    campaign_id: UUID
    item: dict
    history_page: int
    history: list
    more_history: bool


def read_information_show(caller, service, item_id, *, history_page=1):
    """``followup information-show``: one item's page, as the page reads it.

    An item that is not one of the current campaign's is ``not_available``,
    as the page shows no such item. Records the page's ``information_viewed``,
    started and then succeeded or failed.
    """
    from .admin_report_reads import _report
    from .reports.information import (
        InformationQuery,
        information_history,
        information_page,
    )
    from .reports.information_views import _audit

    if type(history_page) is not int or history_page < 1:
        raise ValueError("A history page is a positive number.")

    def read(guard, actor, campaign_id):
        """The page's one-item selection and its history page."""
        result = information_page(
            campaign_id, InformationQuery(disposition="all"), item_id=item_id
        )
        row = result["rows"][0]
        history, more = information_history(
            item_id, history_page, version=row["version"]
        )
        model = InformationShow(
            campaign_id=campaign_id,
            item=_item(row),
            history_page=history_page,
            history=[_revision(revision) for revision in history],
            more_history=more,
        )
        return model, None

    def audit(actor, campaign_id, outcome, values):
        """The item page's view event."""
        _audit(actor, campaign_id, outcome)

    return _report(
        caller,
        service,
        principal=_principal(),
        read=read,
        audit=audit,
        started=True,
    )


@dataclass(frozen=True)
class InformationUpdate(ReadModel):
    """The revision a ``followup information-update`` recorded, or found.

    ``revision`` is the immutable record as ``followup information-show``
    prints a history row (never its notes); ``item`` the item's ``id``,
    ``version``, ``follow_up_needed`` and ``followed_up_at`` as the
    command's transaction left it.
    """

    created: bool
    request_key: UUID
    revision: dict
    item: dict


def _canonical(notes):
    """The page's notes form: line breaks as LF, as the browser counts them."""
    return notes.replace("\r\n", "\n").replace("\r", "\n")


def _kept(item, previous, *, needed, completed, notes):
    """The change's values, with each left-out option kept as it is.

    The page's form opens with the item's checkboxes and notes filled in,
    and a Save sends all of them; a left-out option keeps its current value
    in the same way. A repeat of a recorded key fills them from its first
    revision instead, so it replays whatever changed since.
    """
    from .responses.models import AdditionalInformationRevision

    if previous is not None:
        current_needed = previous.follow_up_needed
        current_completed = previous.followed_up
        current_notes = previous.notes
    else:
        latest = (
            AdditionalInformationRevision.objects.filter(item_id=item.pk)
            .order_by("-expected_version")
            .values_list("notes", flat=True)
            .first()
        )
        current_needed = item.follow_up_needed
        current_completed = item.followed_up_at is not None
        current_notes = latest or ""
    return dict(
        follow_up_needed=current_needed if needed is None else needed,
        followed_up=current_completed if completed is None else completed,
        notes=current_notes if notes is None else _canonical(notes),
    )


def update_information_item(
    caller,
    service,
    item_id,
    *,
    expected_version,
    request_key,
    needed,
    completed,
    confirm_clear,
    notes,
    context,
):
    """``followup information-update``: an item page's Save.

    ``needed``, ``completed`` and ``notes`` None keep the current values
    (``_kept``). The item must be a Live item of the current campaign (else
    ``not_available``); then the page's service checks the version (else
    ``stale_version``), the clearing confirmation (``invalid`` at
    ``confirm_clear``) and, for clearing a completed follow-up, new notes
    (``invalid`` at ``notes``; reopening an Ignored request needs none,
    #1889), and records
    ``information_updated``. The key binds the revision: a repeat with the
    same change returns it and records nothing, and the key with another
    change is ``invalid``. A new revision records
    ``admin_cmd_followup_information_update``.
    """
    from .admin_report_reads import _current
    from .audit.schemas import Action
    from .campaigns.work_locks import require_work_order
    from .responses.information import (
        ClearUnconfirmed,
        UndoNeedsNote,
        update_information,
    )
    from .responses.models import (
        AdditionalInformationItem,
        AdditionalInformationRevision,
    )

    if notes is not None and len(_canonical(notes)) > MAX_NOTES:
        raise InvalidFollowup(
            [problem("notes", f"The notes are longer than {MAX_NOTES:,} characters.")]
        )
    actor = _admit(caller, service.store)
    context["request_id"] = str(request_key)

    def step(actor, written):
        """The page's Save, with the command's event for a new revision."""
        item = AdditionalInformationItem.objects.filter(
            pk=item_id, submission__campaign_id=_current(), submission__mode="live"
        ).first()
        if item is None:
            raise NotAvailable("No such information item.")
        # The scope's work order serializes repeats of one key (#1222).
        require_work_order()
        previous = AdditionalInformationRevision.objects.filter(
            actor_id=actor.identity, request_key=request_key
        ).first()
        values = _kept(item, previous, needed=needed, completed=completed, notes=notes)
        try:
            revision = update_information(
                service.store,
                actor.identity,
                item_id,
                expected_version=expected_version,
                request_key=request_key,
                confirm_clear=confirm_clear,
                **values,
            )
        except ClearUnconfirmed:
            raise InvalidFollowup([problem("confirm_clear", CONFIRM_CLEAR)]) from None
        except UndoNeedsNote:
            raise InvalidFollowup([problem("notes", UNDO_NOTE)]) from None
        if previous is None:
            _record_command(
                Action.ADMIN_CMD_FOLLOWUP_INFORMATION_UPDATE, actor, caller, written
            )
        now = AdditionalInformationItem.objects.values(
            "id", "version", "follow_up_needed", "followed_up_at"
        ).get(pk=item_id)
        return InformationUpdate(
            created=previous is None,
            request_key=request_key,
            revision=_revision(revision),
            item=now,
        )

    return scoped_write(caller, service, _admit, actor, context, step)

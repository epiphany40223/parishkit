"""Start, list, settle and release a restore review (#537).

A restore puts the database back to a backup's moment. Emails that went out
after the backup are not recorded in it, so the site must not simply carry on:
the ordinary planner would send them again. The review is the pause between:

1. ``begin_review`` (the operator's ``restore-begin`` command, after the
   restore and before web starts) closes the site: Family access, Family mail
   and ordinary background work stop (the existing restore gate).
2. ``list_held_emails`` holds every Production invitation and reminder that was
   due at the restore and may have gone out after the backup
   (``stewardship_restore_hold_candidates_v1``). A hold only suppresses.
3. ``settle_held_email`` records an Administrator's decision on one hold:
   assume it was sent, or send it again.
4. ``release_review`` reopens the site. Its SQL refuses while any email still
   needs a hold, so every hold can be decided during the review; one left
   undecided stays in force until an Administrator decides it after release
   (``settle_held_email`` again, #757).

Every rule lives in the SQL guards (migration 0017, and the after-release
decision guard of #757), except that a resend of a reminder a later reminder
superseded is refused here; these functions otherwise only order the locks
and supply exact inputs. No Family code or link is created,
replaced or cancelled anywhere here: the backup's codes and links keep working.
Release and every decision need an Administrator who signed in with Google in
the last five minutes; the session and sign-in are recorded with the decision.
"""

from contextlib import contextmanager
from datetime import datetime
from typing import NamedTuple
from uuid import UUID, uuid4

from django.db import connection, transaction

from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.observability import correlation
from parishkit.stewardship.storage import StaleRecordError

from .models import (
    RestoreDeliveryHold,
    RestoreHoldResolution,
    RuntimeTransition,
    ScheduleFulfillment,
)
from .work_locks import lock_current_campaign_exports, lock_work_order

# The two decisions on a held email. Each is final (the SQL guard enforces
# it): an assumption may already have cancelled the unsent copy, so a later
# "send again" would send nothing. There is no "not needed": it would release
# the email like "send again" without saying so.
DECISIONS = frozenset({"assumed_delivered", "resend_authorized"})


@contextmanager
def _review_transaction(correlation_id):
    """One durable transaction in the lifecycle lock order.

    The global work order comes first, then the current campaign's export
    lock, since starting or ending a review changes export admission (#147).
    """
    if not isinstance(correlation_id, UUID):
        raise TypeError("A restore review step needs a correlation id.")
    with correlation(correlation_id), transaction.atomic(durable=True):
        lock_work_order()
        lock_current_campaign_exports()
        yield SystemConfiguration.objects.get()


def _uuid(*values):
    """Refuse anything but UUIDs, so a caller cannot pass a name or a string."""
    if not all(isinstance(value, UUID) for value in values):
        raise TypeError("Restore review identities must be UUIDs.")


class ReviewStart(NamedTuple):
    """What ``begin_review`` did: the review in force and whether it is new.

    ``transition`` is the new ``restore_begin`` row, or None when a review of
    the same backup was already open and nothing was written.
    """

    restore_id: UUID
    backup_at: datetime
    started: bool
    transition: RuntimeTransition | None


def begin_review(*, backup_at, reason, correlation_id):
    """Close the site for review after a restore; returns a ``ReviewStart``.

    ``backup_at`` is when the restored backup was taken (its set's name). The
    guard admits only the admin-recovery login and the schema owner, a backup
    time not in the future, and a new restore id, so a second restore starts
    a new review. Mode, campaign and every Family credential stay as restored.

    Re-running it while a review of the same backup is open (a recovery
    restart, say) writes nothing and reports that review (#799): a new
    transition would replace its restore id and move its held-email cutoff.
    The SQL guard refuses that insert too, so a caller that skipped this
    check still fails closed.
    """
    if not isinstance(backup_at, datetime) or backup_at.tzinfo is None:
        raise TypeError("The backup time must be timezone-aware.")
    if not isinstance(reason, str) or not reason.strip() or len(reason) > 1024:
        raise ValueError("A restore review needs a short reason.")
    with _review_transaction(correlation_id) as runtime:
        # The work-order lock is held, so no other transition can open or
        # release a review between this read and the insert below.
        if runtime.restore_review_required and runtime.restore_backup_at == backup_at:
            return ReviewStart(
                runtime.restore_id, runtime.restore_backup_at, False, None
            )
        transition = RuntimeTransition.objects.create(
            request_id=uuid4(),
            expected_version=runtime.version,
            action="restore_begin",
            before_mode=runtime.mode,
            after_mode=runtime.mode,
            before_campaign_id=runtime.current_campaign_id,
            after_campaign_id=runtime.current_campaign_id,
            restore_id=uuid4(),
            backup_at=backup_at,
            reason=reason.strip(),
            actor_id=None,
            correlation_id=correlation_id,
        )
        return ReviewStart(transition.restore_id, backup_at, True, transition)


def list_held_emails(*, actor_id, correlation_id):
    """Hold every due Production Family email with no recorded outcome.

    Safe to repeat: a slot this restore already holds is left alone. Returns
    the number of new holds. Refused outside a restore review.
    """
    _uuid(actor_id)
    with _review_transaction(correlation_id), connection.cursor() as cursor:
        cursor.execute(
            "SELECT public.stewardship_restore_hold_inventory_v1(%s,%s)",
            [actor_id, correlation_id],
        )
        return cursor.fetchone()[0]


def holds_needed():
    """How many emails still need a hold before the site can be released."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT count(*) FROM public.stewardship_restore_hold_candidates_v1()"
        )
        return cursor.fetchone()[0]


def settle_held_email(
    *,
    hold_id,
    expected_version,
    state,
    evidence,
    actor_id,
    session_id,
    authenticated_at,
    correlation_id,
):
    """Record one Administrator decision on a held email; returns the record.

    ``assumed_delivered`` keeps it from ever being sent; ``resend_authorized``
    lets the ordinary planner send the restored link (once the site is
    released, or at once after release). A hold is decided once, during the
    review or after release (#757), and only for the current campaign; a
    decided hold never changes. A resend is refused while another restore's
    hold still keeps the same email back (the SQL guard), and, for a
    reminder, once a later reminder was delivered to the same Family
    (``ValueError``): it would arrive out of order, so it can only be assumed
    sent. A repeat of the same decision returns the first record; a different
    one on a changed hold is stale.
    """
    _uuid(hold_id, actor_id, session_id)
    if state not in DECISIONS:
        raise ValueError("Unknown held-email decision.")
    if not isinstance(evidence, str) or not evidence.strip() or len(evidence) > 1024:
        raise ValueError("A held-email decision needs a short note.")
    if type(expected_version) is not int or expected_version < 1:
        raise ValueError("The hold version must be a positive integer.")
    with _review_transaction(correlation_id):
        # Lock only the hold: web may read the schedule definition but not
        # lock it.
        hold = (
            RestoreDeliveryHold.objects.select_for_update(of=("self",))
            .select_related("definition__current_revision")
            .get(pk=hold_id)
        )
        existing = RestoreHoldResolution.objects.filter(
            hold=hold, version=expected_version + 1
        ).first()
        if existing:
            if (existing.actor_id, existing.state, existing.evidence) != (
                actor_id,
                state,
                evidence,
            ):
                raise StaleRecordError("This held email changed; reload it.")
            return existing
        if hold.version != expected_version:
            raise StaleRecordError("This held email changed; reload it.")
        if state == "resend_authorized" and _later_reminder_delivered(hold):
            raise ValueError("A later reminder was already delivered; assume it sent.")
        return RestoreHoldResolution.objects.create(
            hold=hold,
            version=expected_version + 1,
            state=state,
            evidence=evidence,
            actor_id=actor_id,
            session_id=session_id,
            authenticated_at=authenticated_at,
            correlation_id=correlation_id,
        )


def release_review(
    *,
    request_id,
    expected_runtime_version,
    actor_id,
    session_id,
    authenticated_at,
    correlation_id,
):
    """Reopen the site after review; returns the transition.

    The SQL refuses while any email still needs a hold (``holds_needed``;
    find them first), then clears the restore gate. A repeat of the same
    request returns the first transition. Mode and campaign stay as
    restored.
    """
    _uuid(request_id, actor_id, session_id)
    if type(expected_runtime_version) is not int or expected_runtime_version < 1:
        raise ValueError("Runtime version must be a positive integer.")
    with _review_transaction(correlation_id) as runtime:
        existing = RuntimeTransition.objects.filter(request_id=request_id).first()
        if existing:
            if (existing.action, existing.actor_id) != ("restore_release", actor_id):
                raise StaleRecordError("This release request was used differently.")
            return existing
        if runtime.version != expected_runtime_version:
            raise StaleRecordError("The site changed; reload before releasing.")
        return RuntimeTransition.objects.create(
            request_id=request_id,
            expected_version=runtime.version,
            action="restore_release",
            before_mode=runtime.mode,
            after_mode=runtime.mode,
            before_campaign_id=runtime.current_campaign_id,
            after_campaign_id=runtime.current_campaign_id,
            restore_id=runtime.restore_id,
            backup_at=runtime.restore_backup_at,
            reason="",
            actor_id=actor_id,
            session_id=session_id,
            authenticated_at=authenticated_at,
            correlation_id=correlation_id,
        )


def _later_reminder_delivered(hold):
    """Whether a reminder due after this held one already reached its Family.

    After release the planner goes on sending later reminders while an
    earlier one stays held. Sending the earlier one again then would arrive
    out of order, so a held reminder superseded this way may only be assumed
    sent. Due times are read from each schedule's current revision, as the
    planner reads them; an invitation is never superseded.
    """
    definition = hold.definition
    # A removed schedule has no current revision; the planner sends nothing
    # for it, so there is nothing to put out of order.
    if definition.kind != "reminder" or definition.current_revision is None:
        return False
    return ScheduleFulfillment.objects.filter(
        definition__campaign_id=definition.campaign_id,
        definition__kind="reminder",
        definition__current_revision__due_at__gt=definition.current_revision.due_at,
        mode=hold.mode,
        target=hold.target,
        disposition="delivered",
    ).exists()

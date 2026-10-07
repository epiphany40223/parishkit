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
   undecided stays in force.

Every rule lives in the SQL guards of migration 0019; these functions only
order the locks and supply exact inputs. No Family code or link is created,
replaced or cancelled anywhere here: the backup's codes and links keep working.
Release and every decision need an Administrator who signed in with Google in
the last five minutes; the session and sign-in are recorded with the decision.
"""

from contextlib import contextmanager
from datetime import datetime
from uuid import UUID, uuid4

from django.db import connection, transaction

from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.observability import correlation
from parishkit.stewardship.storage import StaleRecordError

from .models import RestoreDeliveryHold, RestoreHoldResolution, RuntimeTransition
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


def begin_review(*, backup_at, reason, correlation_id):
    """Close the site for review after a restore; returns the transition.

    ``backup_at`` is when the restored backup was taken (its set's name). The
    guard admits only the admin-recovery login and the schema owner, a backup
    time not in the future, and a new restore id, so a second restore starts
    a new review. Mode, campaign and every Family credential stay as restored.
    """
    if not isinstance(backup_at, datetime) or backup_at.tzinfo is None:
        raise TypeError("The backup time must be timezone-aware.")
    if not isinstance(reason, str) or not reason.strip() or len(reason) > 1024:
        raise ValueError("A restore review needs a short reason.")
    with _review_transaction(correlation_id) as runtime:
        return RuntimeTransition.objects.create(
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
    lets the ordinary planner send the restored link once the site is
    released. Both are final and are refused once the site is released; a
    resend is also refused while another restore's hold still keeps the same
    email back. A repeat of the same decision returns the first record; a
    different one on a changed hold is stale.
    """
    _uuid(hold_id, actor_id, session_id)
    if state not in DECISIONS:
        raise ValueError("Unknown held-email decision.")
    if not isinstance(evidence, str) or not evidence.strip() or len(evidence) > 1024:
        raise ValueError("A held-email decision needs a short note.")
    if type(expected_version) is not int or expected_version < 1:
        raise ValueError("The hold version must be a positive integer.")
    with _review_transaction(correlation_id) as runtime:
        if not runtime.restore_review_required:
            # Decisions belong to the review; settling after release is #757.
            raise StaleRecordError("The site was already released.")
        hold = RestoreDeliveryHold.objects.select_for_update().get(pk=hold_id)
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

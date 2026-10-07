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
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID, uuid4

from django.db import connection, transaction
from django.db.models import Count, F

from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.observability import correlation
from parishkit.stewardship.storage import StaleRecordError

from .credential_models import FamilyCampaign
from .models import (
    Campaign,
    RestoreDeliveryHold,
    RestoreHoldResolution,
    RuntimeTransition,
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


# ------------------------------------------------- the Restore review page

# Group actions on the Restore review page: which holds each one settles.
# Both settle only what nobody has decided yet: every decision is final.
GROUP_ACTIONS = {
    "assume": ("assumed_delivered", frozenset({"unreviewed"})),
    "resend": ("resend_authorized", frozenset({"unreviewed"})),
}


@dataclass(frozen=True)
class HeldGroup:
    """One send's held emails: the invitation or one reminder, by state."""

    definition_id: UUID
    kind: str
    due_at: datetime
    unreviewed: int = 0
    assumed: int = 0
    resend: int = 0
    # Undecided holds of Families that can be emailed now (active,
    # email-eligible, deliverable, no response). The rest are inert: held
    # because eligibility may have changed since the backup, but nothing would
    # be sent to them. A decision still covers every undecided hold.
    reachable: int = 0

    @property
    def inert(self):
        """Undecided holds of Families that cannot be emailed now."""
        return self.unreviewed - self.reachable

    def count(self, action):
        """How many holds ``action`` (a GROUP_ACTIONS key) would settle."""
        return self.unreviewed if action in GROUP_ACTIONS else 0


@dataclass(frozen=True)
class ReviewState:
    """What the Restore review page shows; never a Family code or link."""

    restore_id: UUID
    runtime_version: int
    backup_at: datetime
    restored_at: datetime
    mode: str
    campaign_name: str | None
    campaign_state: str | None
    listed: bool
    groups: tuple
    # Emails being handed to the provider at the backup: never held; the
    # delivery warning settles them after release.
    in_flight: int = 0
    paused: bool = False
    # Emails that still need a hold: release is refused until this is 0.
    needed: int = 0
    # Emailable Families whose undecided invitation keeps their reminders back.
    reminders_blocked: int = 0

    @property
    def unsettled(self):
        """Undecided emails of Families that can be emailed now; they stay held."""
        return sum(group.reachable for group in self.groups)

    @property
    def inert(self):
        """Undecided holds that would send nothing now (counted apart)."""
        return sum(group.unreviewed for group in self.groups) - self.unsettled

    @property
    def release_outcome(self):
        """What Families get once the site is released, as one word for the page."""
        if self.mode != "production":
            return "testing"
        if self.campaign_state == "active":
            return "paused" if self.paused else "open"
        if self.campaign_state == "scheduled":
            return "scheduled"
        return "closed"


def review_state():
    """The current restore review, or None when the site is not under review."""
    runtime = SystemConfiguration.objects.get()
    if not runtime.restore_review_required:
        return None
    campaign = (
        Campaign.objects.select_related("active_configuration")
        .filter(pk=runtime.current_campaign_id)
        .first()
    )
    counts = {}
    rows = (
        RestoreDeliveryHold.objects.filter(restore_id=runtime.restore_id)
        .values(
            "definition_id",
            "definition__kind",
            "definition__current_revision__due_at",
            "state",
        )
        .annotate(total=Count("id"))
    )
    fields = {
        "unreviewed": "unreviewed",
        "assumed_delivered": "assumed",
        "resend_authorized": "resend",
    }
    reachable = {
        f"family:{identifier}"
        for identifier in FamilyCampaign.objects.filter(
            campaign_id=runtime.current_campaign_id,
            active=True,
            email_eligible=True,
            email_deliverable=True,
            effective_submission_id__isnull=True,
        ).values_list("pk", flat=True)
    }
    undecided = list(
        RestoreDeliveryHold.objects.filter(
            restore_id=runtime.restore_id, state="unreviewed"
        ).values_list("definition_id", "definition__kind", "target")
    )
    # A reminder waits for its Family's undecided invitation, and "send
    # again" on an invitation whose latest attempt ended (failed, or skipped
    # as undeliverable) sends nothing until deliverability changes: neither
    # would be sent now, so neither is counted as an email to decide.
    own_invitations = {target for _, kind, target in undecided if kind == "initial"}
    # Any restore's undecided invitation keeps the Family's reminders back.
    invitation_waits = own_invitations | set(
        RestoreDeliveryHold.objects.filter(
            definition__campaign_id=runtime.current_campaign_id,
            definition__kind="initial",
            mode="production",
            state="unreviewed",
        ).values_list("target", flat=True)
    )
    ended = _ended_invitations(runtime, own_invitations)
    reachable_counts = {}
    for definition_id, kind, target in undecided:
        if target not in reachable or (
            (kind == "reminder" and target in invitation_waits)
            or (kind == "initial" and target in ended)
        ):
            continue
        reachable_counts[definition_id] = reachable_counts.get(definition_id, 0) + 1
    for row in rows:
        if row["state"] not in fields:
            continue
        key = (
            row["definition_id"],
            row["definition__kind"],
            row["definition__current_revision__due_at"],
        )
        counts.setdefault(key, {})[fields[row["state"]]] = row["total"]
    groups = tuple(
        HeldGroup(
            definition_id,
            kind,
            due_at,
            reachable=reachable_counts.get(definition_id, 0),
            **values,
        )
        for (definition_id, kind, due_at), values in sorted(
            counts.items(), key=lambda item: (item[0][2], item[0][1] != "initial")
        )
    )
    return ReviewState(
        restore_id=runtime.restore_id,
        runtime_version=runtime.version,
        backup_at=runtime.restore_backup_at,
        restored_at=runtime.restore_activated_at,
        mode=runtime.mode,
        campaign_name=campaign.active_configuration.name if campaign else None,
        campaign_state=campaign.state if campaign else None,
        listed=bool(groups),
        groups=groups,
        in_flight=_in_flight(runtime),
        paused=bool(campaign and campaign.delivery_paused),
        needed=holds_needed(),
        reminders_blocked=len(invitation_waits & reachable),
    )


def _ended_invitations(runtime, targets):
    """The targets whose invitation's latest attempt ended without sending.

    Read for the current revision and Production cycle, as the hold
    candidates are (migration 0019).
    """
    from .schedule_models import ScheduleOccurrence

    if not targets:
        return set()
    campaign = Campaign.objects.get(pk=runtime.current_campaign_id)
    latest = {}
    rows = (
        ScheduleOccurrence.objects.filter(
            definition__campaign_id=campaign.pk,
            definition__kind="initial",
            revision_id=F("definition__current_revision_id"),
            production_cycle=campaign.production_cycle,
            mode="production",
            slot="once",
            target__in=targets,
        )
        .order_by("target", "-recovery_generation", "-created_at")
        .values_list("target", "state", "reason")
    )
    for target, state, reason in rows:
        latest.setdefault(target, (state, reason))
    return {
        target
        for target, (state, reason) in latest.items()
        if state == "failed"
        or (
            state == "skipped"
            and reason in {"no_deliverable_recipient", "family_ineligible"}
        )
    }


def _in_flight(runtime):
    """Invitations and reminders the restored data shows mid-hand-off."""
    from parishkit.stewardship.jobs.outbox_models import OutboxMessage

    if runtime.mode != "production" or runtime.current_campaign_id is None:
        return 0
    return OutboxMessage.objects.filter(
        campaign_id=runtime.current_campaign_id,
        mode="production",
        purpose__in=("initial", "reminder"),
        state__in=("submitting", "delivery_unknown"),
    ).count()


def settle_group(
    *,
    definition_id,
    action,
    expected_count,
    evidence,
    actor_id,
    session_id,
    authenticated_at,
    correlation_id,
):
    """Settle one send's held emails at once; returns how many were settled.

    ``action`` is a GROUP_ACTIONS key. ``expected_count`` is the count the
    Administrator confirmed in the preview: if the holds changed since (more
    were listed, or another Administrator decided some), nothing is settled.
    Each hold gets its own audited decision, all in one transaction.
    """
    _uuid(definition_id, actor_id, session_id)
    if action not in GROUP_ACTIONS:
        raise ValueError("Unknown held-email action.")
    if not isinstance(evidence, str) or not evidence.strip() or len(evidence) > 1024:
        raise ValueError("A held-email decision needs a short note.")
    if type(expected_count) is not int or expected_count < 1:
        raise ValueError("Nothing to settle.")
    state, from_states = GROUP_ACTIONS[action]
    with _review_transaction(correlation_id) as runtime:
        if not runtime.restore_review_required:
            # Decisions belong to the review; settling after release is #757.
            raise StaleRecordError("The site was already released.")
        holds = list(
            RestoreDeliveryHold.objects.select_for_update()
            .filter(
                restore_id=runtime.restore_id,
                definition_id=definition_id,
                state__in=from_states,
            )
            .order_by("pk")
        )
        if len(holds) != expected_count:
            raise StaleRecordError("These held emails changed; review them again.")
        for hold in holds:
            RestoreHoldResolution.objects.create(
                hold=hold,
                version=hold.version + 1,
                state=state,
                evidence=evidence.strip(),
                actor_id=actor_id,
                session_id=session_id,
                authenticated_at=authenticated_at,
                correlation_id=correlation_id,
            )
        return len(holds)

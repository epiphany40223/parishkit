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

The Restore review page (``accounts/restore_review_views.py``) reads
``review_state`` and decides a whole send at once with ``settle_group``.

Every rule lives in the SQL guards (migration 0017, and migration 0048 for
the after-release decisions of #757); these functions only order the locks,
supply exact inputs and, for a reminder a later one superseded, give a plain
reason before the guard would refuse. No Family code or link is created,
replaced or cancelled anywhere here: the backup's codes and links keep working.
Release and every decision need an Administrator who signed in with Google in
the last five minutes; the session and sign-in are recorded with the decision.
"""

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import NamedTuple
from uuid import UUID, uuid4

from django.db import connection, transaction
from django.db.models import Count, F, Q

from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.jobs.outbox_models import OutboxMessage
from parishkit.stewardship.observability import correlation
from parishkit.stewardship.storage import StaleRecordError

from .credential_models import FamilyCampaign
from .models import (
    Campaign,
    RestoreDeliveryHold,
    RestoreHoldResolution,
    RuntimeTransition,
    ScheduleDefinition,
    ScheduleFulfillment,
    ScheduleOccurrence,
)
from .runtime_models import CampaignWorkGate
from .work_locks import lock_current_campaign_exports, lock_work_order

# The two decisions on a held email. Each is final (the SQL guard enforces
# it): an assumption may already have cancelled the unsent copy, so a later
# "send again" would send nothing. There is no "not needed": it would release
# the email like "send again" without saying so.
DECISIONS = frozenset({"assumed_delivered", "resend_authorized"})

# Outbox states of a later reminder that reached its Family or may have:
# being submitted, delivery unknown, or delivered (as in migration 0048).
REACHED = ("submitting", "delivery_unknown", "delivered")


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
    hold still keeps the same email back, and, for a reminder, once a later
    reminder for the same Family was delivered, is on its way or its delivery
    is unknown (``ValueError`` here; the SQL guard refuses it too, and the
    Family dispatch check never sends such a resend after a later reminder):
    it would arrive out of order, so it can only be assumed sent. A repeat
    of the same decision returns the first record; a different one on a
    changed hold is stale.
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
        if state == "resend_authorized" and _superseded(hold):
            raise ValueError(
                "A later reminder reached this Family or is on its way; "
                "assume this one sent."
            )
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


class ReleaseRefused(Exception):
    """The site cannot be released yet; ``reason`` says why.

    ``needs_holds``: an email still needs a hold (find them again first);
    ``blocked``: a purge or campaign change is still running.
    """

    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


def release_refusal(runtime=None):
    """Why the release guard would refuse now, or "" (mirrors migration 0046).

    The guard refuses everything with one SQLSTATE (23514), so the page asks
    this first and names the reasons a person can act on. ``release_review``
    itself leaves every refusal to the guard.
    """
    runtime = runtime or SystemConfiguration.objects.get()
    if holds_needed():
        return "needs_holds"
    purging = Campaign.objects.filter(
        pk=runtime.current_campaign_id,
        state__in=("purging", "purge_cleanup_failed"),
    ).exists()
    if (
        purging
        or CampaignWorkGate.objects.filter(state__in=("preparing", "running")).exists()
    ):
        return "blocked"
    return ""


def _later_reached(definition, mode, due_at):
    """Later reminders of ``definition``'s campaign that reached a Family or may have.

    A later reminder counts once it was delivered, is being submitted or its
    delivery is unknown, even if it was removed since; ``definition`` never
    counts itself (migration 0048).
    """
    later = ScheduleOccurrence.objects.filter(
        definition__campaign_id=definition.campaign_id,
        definition__kind="reminder",
        mode=mode,
        due_at__gt=due_at,
    ).exclude(definition_id=definition.pk)
    return later.filter(
        Q(outbox_id__in=OutboxMessage.objects.filter(state__in=REACHED).values("id"))
        | Q(
            pk__in=ScheduleFulfillment.objects.filter(disposition="delivered").values(
                "occurrence_id"
            )
        )
    )


def later_reminder_reached(definition, mode, target, due_at):
    """Whether a reminder due after ``due_at`` reached this Family or may have.

    After release the planner goes on sending later reminders while an
    earlier one stays held. Sending the earlier one again then would arrive
    out of order (#757). A later reminder of the same campaign counts once it
    was delivered, is being submitted or its delivery is unknown, even if it
    was removed since. ``definition`` is the earlier reminder's, which never
    counts itself. This mirrors the SQL in migration 0048: the decision guard
    and the Family dispatch check enforce the rule for every login; the
    callers here only give a plain reason first.
    """
    return _later_reached(definition, mode, due_at).filter(target=target).exists()


def _superseded(hold):
    """Whether "send again" on this held email would arrive out of order.

    The held reminder's due time is its current revision's, as the planner
    reads it; an invitation is never superseded.
    """
    definition = hold.definition
    # A removed schedule has no current revision; the planner sends nothing
    # for it, so there is nothing to put out of order.
    if definition.kind != "reminder" or definition.current_revision is None:
        return False
    return later_reminder_reached(
        definition, hold.mode, hold.target, definition.current_revision.due_at
    )


# ------------------------------------------------- the Restore review page

# Group actions on the Restore review page: which holds each one settles.
# Both settle only what nobody has decided yet: every decision is final.
GROUP_ACTIONS = {
    "assume": ("assumed_delivered", frozenset({"unreviewed"})),
    "resend": ("resend_authorized", frozenset({"unreviewed"})),
}


class ResendBlocked(ValueError):
    """ "Send these again" is refused for a send; ``reason`` names why.

    The decision guard (migrations 0017 and 0048) refuses these too, but with
    one SQLSTATE and by rolling the whole send back; the page names them.
    """

    reason = ""


class SupersededResend(ResendBlocked):
    """A resend that would arrive after a later reminder (#757).

    A later reminder for one of the send's Families was delivered, is on its
    way or has an unknown delivery, so the send can only be assumed sent.
    """

    reason = "superseded"


class HeldByAnotherRestore(ResendBlocked):
    """Another restore's hold still keeps one of these emails back.

    That hold is undecided or assumed sent, so a resend here would send
    nothing; the guard refuses it.
    """

    reason = "held_elsewhere"


RESEND_BLOCKS = {
    error.reason: error for error in (SupersededResend, HeldByAnotherRestore)
}


def resend_block(definition, restore_id, keys):
    """Why "Send these again" is refused for these holds of one send, or "".

    ``definition`` is the send's schedule (with its current revision) and
    ``keys`` the undecided holds' ``(mode, target, slot)``. Mirrors the two
    resend rules of the decision guard (migration 0048).
    """
    keys = set(keys)
    if not keys:
        return ""
    others = (
        RestoreDeliveryHold.objects.filter(
            definition_id=definition.pk, state__in=("unreviewed", "assumed_delivered")
        )
        .exclude(restore_id=restore_id)
        .values_list("mode", "target", "slot")
    )
    if any(key in keys for key in others):
        return HeldByAnotherRestore.reason
    # A removed schedule has no current revision: nothing to put out of order.
    if definition.kind == "reminder" and definition.current_revision is not None:
        due_at = definition.current_revision.due_at
        for mode in {mode for mode, _, _ in keys}:
            targets = {target for key_mode, target, _ in keys if key_mode == mode}
            if (
                _later_reached(definition, mode, due_at)
                .filter(target__in=targets)
                .exists()
            ):
                return SupersededResend.reason
    return ""


@dataclass(frozen=True)
class HeldGroup:
    """One send's held emails: the invitation or one reminder, by state."""

    definition_id: UUID
    kind: str
    # None once the schedule was removed (it has no current revision).
    due_at: datetime | None
    unreviewed: int = 0
    assumed: int = 0
    resend: int = 0
    # Undecided holds of Families that can be emailed now (active,
    # email-eligible, deliverable, no response). The rest are inert: held
    # because eligibility may have changed since the backup, but nothing would
    # be sent to them. A decision still covers every undecided hold.
    reachable: int = 0
    # Why "Send these again" is refused for this send (a ResendBlocked
    # reason), or "" when it is not.
    resend_blocked: str = ""

    @property
    def inert(self):
        """Undecided holds of Families that cannot be emailed now."""
        return self.unreviewed - self.reachable

    @property
    def reason_id(self):
        """The id of the row's line saying why a choice is unavailable."""
        return f"restore-reason-{self.definition_id}"

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
    undecided_keys = list(
        RestoreDeliveryHold.objects.filter(
            restore_id=runtime.restore_id, state="unreviewed"
        ).values_list("definition_id", "definition__kind", "mode", "target", "slot")
    )
    undecided = [(d, kind, target) for d, kind, _, target, _ in undecided_keys]
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
    # Each send's undecided holds, for the resend rules.
    keys = {}
    for definition_id, _, mode, target, slot in undecided_keys:
        keys.setdefault(definition_id, []).append((mode, target, slot))
    definitions = ScheduleDefinition.objects.select_related("current_revision").in_bulk(
        list(keys)
    )
    blocked = {
        definition_id: resend_block(definition, runtime.restore_id, keys[definition_id])
        for definition_id, definition in definitions.items()
    }
    groups = tuple(
        HeldGroup(
            definition_id,
            kind,
            due_at,
            reachable=reachable_counts.get(definition_id, 0),
            resend_blocked=blocked.get(definition_id, ""),
            **values,
        )
        # By due time, the invitation before a reminder due at the same time;
        # a removed schedule (no due time) last.
        for (definition_id, kind, due_at), values in sorted(
            counts.items(),
            key=lambda item: (
                item[0][2] is None,
                item[0][2] or datetime.min,
                item[0][1] != "initial",
            ),
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
    candidates are (migration 0017).
    """
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
        # Lock only the holds: web may read the schedule definition but not
        # lock it (as in settle_held_email).
        holds = list(
            RestoreDeliveryHold.objects.select_for_update(of=("self",))
            .select_related("definition__current_revision")
            .filter(
                restore_id=runtime.restore_id,
                definition_id=definition_id,
                state__in=from_states,
            )
            .order_by("pk")
        )
        if len(holds) != expected_count:
            raise StaleRecordError("These held emails changed; review them again.")
        # A resend the decision guard would refuse (another restore's hold
        # keeps an email back, or a later reminder overtook it: migrations
        # 0017 and 0048) would roll back the whole send with one SQLSTATE;
        # name the reason instead, before anything is written.
        if action == "resend" and (
            reason := resend_block(
                holds[0].definition,
                runtime.restore_id,
                [(hold.mode, hold.target, hold.slot) for hold in holds],
            )
        ):
            raise RESEND_BLOCKS[reason](reason)
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

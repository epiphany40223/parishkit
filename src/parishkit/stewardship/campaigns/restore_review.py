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

    Returns None, writing nothing, when a review is already open for this
    same backup (a re-run): its cutoff and holds stay as they are.

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
        if runtime.restore_review_required and runtime.restore_backup_at == backup_at:
            # A re-run on the same restored data (a restart, say): the review
            # is already open. Writing a new one would move its cutoff, so the
            # SQL refuses that (#799); report the open review instead.
            return None
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
    lets the ordinary planner send the restored link (after release; during a
    review, once the site is released). A hold is decided once, during the
    review or afterwards on the Held emails page, and a decided hold never
    changes; a resend is also refused while another restore's hold still
    keeps the same email back. A resend of a reminder that a later delivered
    reminder superseded is refused (``ValueError``), as ``settle_group``
    records such a hold as assumed sent. A repeat of the same decision
    returns the first record; a different one on a changed hold is stale.
    """
    _uuid(hold_id, actor_id, session_id)
    if state not in DECISIONS:
        raise ValueError("Unknown held-email decision.")
    if not isinstance(evidence, str) or not evidence.strip() or len(evidence) > 1024:
        raise ValueError("A held-email decision needs a short note.")
    if type(expected_version) is not int or expected_version < 1:
        raise ValueError("The hold version must be a positive integer.")
    with _review_transaction(correlation_id):
        hold = (
            RestoreDeliveryHold.objects.select_for_update(of=("self",))
            .select_related("definition")
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
        if (
            state == "resend_authorized"
            and hold.definition.kind == "reminder"
            and _is_superseded(
                _later_reminders(hold.definition.campaign_id),
                hold.definition_id,
                hold.target,
            )
        ):
            # The same rule as settle_group: a later reminder already reached
            # this Family, so resending this one would arrive out of order.
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
    # Undecided reminder holds of Families that already got a later reminder:
    # sending this one now would arrive out of order. "Send these again"
    # records them as assumed sent instead (``settle_group``).
    superseded: int = 0

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


def reachable_targets(campaign_id, targets=None):
    """The hold targets (``family:<id>``) of Families that can be emailed now.

    Active, email-eligible, deliverable and not yet responded. Holds of other
    Families are inert. With ``targets``, only those are looked up.
    """
    families = FamilyCampaign.objects.filter(
        campaign_id=campaign_id,
        active=True,
        email_eligible=True,
        email_deliverable=True,
        effective_submission_id__isnull=True,
    )
    if targets is not None:
        identifiers = [target.removeprefix("family:") for target in targets]
        if not identifiers:
            return set()
        families = families.filter(pk__in=identifiers)
    return {
        f"family:{identifier}" for identifier in families.values_list("pk", flat=True)
    }


def held_after_release(campaign_id):
    """``(invitations, undecided)``: what the held-emails banner and link count.

    One read of the campaign's undecided Production holds; only when some are
    invitations, two more to keep the emailable Families among them and drop
    the invitations only a deliverability change would retry, as the page
    does.
    """
    rows = list(
        RestoreDeliveryHold.objects.filter(
            definition__campaign_id=campaign_id, mode="production", state="unreviewed"
        ).values_list("definition__kind", "target")
    )
    invitations = {target for kind, target in rows if kind == "initial"}
    if not invitations:
        return 0, len(rows)
    # As the page counts them: emailable Families, less invitations only a
    # deliverability change would retry (a resend would send nothing now).
    blocking = reachable_targets(campaign_id, invitations)
    return len(blocking - _ended_invitations(campaign_id, blocking)), len(rows)


def sign_in_lapsed(error):
    """Whether a database error is the guard's fresh-sign-in refusal.

    The decision and release guards raise SQLSTATE 42501 with a "fresh
    Administrator" message when the sign-in lapsed between the page's check
    and the write. Any other 42501 is a real privilege error and must not be
    shown as a step-up.
    """
    return getattr(
        error.__cause__, "sqlstate", None
    ) == "42501" and "fresh Administrator" in str(error)


def _scope(runtime):
    """The holds a decision may settle now.

    During a review: this restore's holds (the review page). After release:
    every restore's holds of the current campaign (the Held emails page,
    #757); the SQL guard refuses another campaign's holds.
    """
    if runtime.restore_review_required:
        return RestoreDeliveryHold.objects.filter(restore_id=runtime.restore_id)
    return RestoreDeliveryHold.objects.filter(
        definition__campaign_id=runtime.current_campaign_id, mode="production"
    )


def held_groups(runtime):
    """The held emails by send, and how many Families' reminders wait.

    Shared by the review page and the Held emails page: counts name only
    emails that would be sent now; the rest of a send's undecided holds are
    inert (``HeldGroup.inert``).
    """
    counts = {}
    rows = (
        _scope(runtime)
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
    reachable = reachable_targets(runtime.current_campaign_id)
    undecided = list(
        _scope(runtime)
        .filter(state="unreviewed")
        .values_list("definition_id", "definition__kind", "target")
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
    ended = _ended_invitations(runtime.current_campaign_id, own_invitations)
    later = _later_reminders(runtime.current_campaign_id)
    reachable_counts, superseded_counts = {}, {}
    for definition_id, kind, target in undecided:
        if kind == "reminder" and _is_superseded(later, definition_id, target):
            superseded_counts[definition_id] = (
                superseded_counts.get(definition_id, 0) + 1
            )
            continue
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
            superseded=superseded_counts.get(definition_id, 0),
            **values,
        )
        for (definition_id, kind, due_at), values in sorted(
            counts.items(), key=lambda item: (item[0][2], item[0][1] != "initial")
        )
    )
    # The banner counts the same Families (held_after_release).
    blocking = invitation_waits & reachable
    return groups, len(
        blocking - _ended_invitations(runtime.current_campaign_id, blocking)
    )


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
    groups, reminders_blocked = held_groups(runtime)
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
        reminders_blocked=reminders_blocked,
    )


def _later_reminders(campaign_id):
    """Per target, the due times of reminders already delivered to it.

    With each reminder definition's own due time, so a held reminder can be
    compared with the ones that went out after it.
    """
    from .schedule_models import ScheduleDefinition, ScheduleFulfillment

    due = dict(
        ScheduleDefinition.objects.filter(
            campaign_id=campaign_id, kind="reminder"
        ).values_list("pk", "current_revision__due_at")
    )
    delivered = {}
    for definition_id, target in ScheduleFulfillment.objects.filter(
        definition__campaign_id=campaign_id,
        definition__kind="reminder",
        mode="production",
        disposition="delivered",
    ).values_list("definition_id", "target"):
        if due.get(definition_id) is not None:
            delivered.setdefault(target, []).append(due[definition_id])
    return due, delivered


def _is_superseded(later, definition_id, target):
    """Whether a reminder due after this one was already delivered to target."""
    due, delivered = later
    own = due.get(definition_id)
    return own is not None and any(when > own for when in delivered.get(target, ()))


def _ended_invitations(campaign_id, targets):
    """The targets whose invitation's latest attempt ended without sending.

    Read for the current revision and Production cycle, as the hold
    candidates are (migration 0019).
    """
    from .schedule_models import ScheduleOccurrence

    if not targets:
        return set()
    campaign = Campaign.objects.get(pk=campaign_id)
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


@dataclass(frozen=True)
class Settled:
    """What one per-send decision did.

    ``count`` holds were decided; for "send again", ``assumed_instead`` of
    them were recorded as assumed sent, because a later reminder had already
    reached the Family.
    """

    count: int
    assumed_instead: int = 0


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
    """Settle one send's held emails at once; returns a ``Settled``.

    During a review this restore's holds of the send; after release every
    restore's undecided holds of it (``_scope``).

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
        holds = list(
            _scope(runtime)
            # Lock only the holds: after release the scope joins the schedule
            # definition, which web may read but not lock.
            .select_for_update(of=("self",))
            .select_related("definition")
            .filter(definition_id=definition_id, state__in=from_states)
            .order_by("pk")
        )
        if len(holds) != expected_count:
            raise StaleRecordError("These held emails changed; review them again.")
        later = _later_reminders(runtime.current_campaign_id)
        converted = 0
        for hold in holds:
            decided, note = state, evidence.strip()
            if (
                state == "resend_authorized"
                and hold.definition.kind == "reminder"
                and _is_superseded(later, hold.definition_id, hold.target)
            ):
                # A later reminder already reached this Family: resending
                # this one would arrive out of order, so it is recorded as
                # assumed sent, with the reason, instead.
                decided = "assumed_delivered"
                note = (note + " (A later reminder was already delivered.)")[:1024]
                converted += 1
            RestoreHoldResolution.objects.create(
                hold=hold,
                version=hold.version + 1,
                state=decided,
                evidence=note,
                actor_id=actor_id,
                session_id=session_id,
                authenticated_at=authenticated_at,
                correlation_id=correlation_id,
            )
        return Settled(len(holds), converted)

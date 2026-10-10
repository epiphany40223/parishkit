"""The restore review under the real logins and guards (#537, migration 0017).

The operator's admin-recovery login closes the site for review; a freshly
signed-in Administrator, through the web login, lists the held emails,
settles them and releases the site. Nothing here creates, replaces or
cancels a Family code or link, and a held email is never sent unless an
Administrator chose "send again".
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from django.db import DatabaseError, connection, transaction

from parishkit.stewardship.accounts.models import PortalSession
from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.audit.models import AuditEvent
from parishkit.stewardship.campaigns.credential_models import (
    FamilyAccessToken,
    FamilyAccessTokenGeneration,
    FamilyCampaign,
)
from parishkit.stewardship.campaigns.family_identity import FamilyStatus
from parishkit.stewardship.campaigns.lifecycle import Action
from parishkit.stewardship.campaigns.models import (
    Campaign,
    RestoreDeliveryHold,
    RestoreHoldResolution,
    RuntimeTransition,
    ScheduleDefinition,
    ScheduleFulfillment,
)
from parishkit.stewardship.campaigns.restore_review import (
    begin_review,
    holds_needed,
    list_held_emails,
    release_review,
    settle_held_email,
)
from parishkit.stewardship.campaigns.schedules import occurrence_key
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.storage import StaleRecordError

from .auth_builders import signed_in
from .campaign_builders import add_draft, campaign_clock, command
from .credential_builders import keys, populate
from .test_background_grants_postgresql import task_login
from .test_family_schedule_planning_postgresql import add_reminders

pytestmark = pytest.mark.django_db(transaction=True)

MIGRATION = (
    Path(__file__).resolve().parents[3]
    / "src/parishkit/stewardship/schema/migrations/0046_restore_begin_keeps_review.sql"
)

# The campaign is open from 2054-10-01; the initial invitation and two
# reminders (October 3 and 4) are due by this instant, the third (October 20)
# is not.
NOW = datetime(2054, 10, 5, 12, tzinfo=UTC)
BACKUP = datetime(2054, 10, 5, 2, tzinfo=UTC)


def live_campaign(auth_service):
    """An active Production campaign with three Families.

    Family 1 already had its invitation delivered before the backup, Family 2
    has no email address, and Family 3 is owed everything that is due.
    """
    store, actor = auth_service.store, uuid4()
    _, row, _ = add_draft(store, store.active(), actor)
    campaign = Campaign.objects.get(pk=UUID(row["id"]))
    populate(
        campaign,
        keys(),
        [
            FamilyStatus(1, True, True, True, True),
            FamilyStatus(2, True, True, False, False, "eligible", "no_email"),
            FamilyStatus(3, True, True, True, True),
        ],
    )
    add_reminders(store, campaign, actor)
    with campaign_clock(NOW):
        command(campaign, actor, Action.ACTIVATE)
    first = FamilyCampaign.objects.order_by("family_duid").first()
    initial = ScheduleDefinition.objects.get(kind="initial")
    restored_occurrence(initial, first)
    return campaign


def restored_occurrence(
    definition, family, state="succeeded", reason="", *, revision_id=None, cycle=None
):
    """Load one occurrence as backup state; a succeeded one with its outcome.

    Like campaign_builders.restored_runtime, this models offline restore input:
    the rows are written with user triggers off, inside one transaction.
    """
    target = f"family:{family.pk}"
    occurrence_id = uuid4()
    revision_id = revision_id or definition.current_revision_id
    if cycle is None:
        cycle = Campaign.objects.get(pk=definition.campaign_id).production_cycle
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute("SET LOCAL session_replication_role = replica")
        cursor.execute(
            "INSERT INTO stewardship_schedule_occurrence (id,actor_id,correlation_id,"
            "version,mode,routing,target,slot,due_at,occurrence_key,state,fence,"
            "attempts,reason,definition_id,revision_id,production_cycle) "
            "VALUES (%s,NULL,%s,3,'production','production',%s,'once',%s,%s,"
            "%s,1,1,%s,%s,%s,%s)",
            [
                occurrence_id,
                uuid4(),
                target,
                definition.current_revision.due_at,
                occurrence_key(revision_id, "production", target, "once"),
                state,
                reason,
                definition.pk,
                revision_id,
                cycle,
            ],
        )
        if state == "succeeded":
            cursor.execute(
                "INSERT INTO stewardship_schedule_fulfillment (id,actor_id,"
                "correlation_id,mode,target,slot,disposition,definition_id,"
                "occurrence_id) VALUES (%s,NULL,%s,'production',%s,'once',"
                "'delivered',%s,%s)",
                [uuid4(), uuid4(), target, definition.pk, occurrence_id],
            )
        cursor.execute("SET LOCAL session_replication_role = origin")


def begin(backup_at=BACKUP):
    """Start a review as the operator's admin-recovery login; a ReviewStart."""
    with task_login(ServiceRole.ADMIN_RECOVERY, exact=True):
        return begin_review(
            backup_at=backup_at, reason="restore", correlation_id=uuid4()
        )


def administrator():
    """The signed-in Administrator's newest live session."""
    return PortalSession.objects.order_by("-authenticated_at").first()


def as_web(function, **values):
    """Run one review step as the web login for the signed-in Administrator."""
    current = administrator()
    with task_login(ServiceRole.WEB, exact=True):
        return function(
            actor_id=current.principal_id,
            session_id=current.pk,
            authenticated_at=current.authenticated_at,
            correlation_id=uuid4(),
            **values,
        )


def held(**filters):
    """The holds of the current review, newest restore only."""
    restore = SystemConfiguration.objects.get().restore_id
    return RestoreDeliveryHold.objects.filter(restore_id=restore, **filters)


def excluded(hold):
    """Whether the shared slot exclusion keeps this hold's email from going out."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT stewardship_schedule_slot_excluded_v1(%s,%s,%s,%s)",
            [hold.definition_id, hold.mode, hold.target, hold.slot],
        )
        return cursor.fetchone()[0]


def test_operator_closes_the_site_and_nothing_else_changes(auth_service):
    """restore_begin sets the gate, keeps mode, campaign and every Family link."""
    campaign = live_campaign(auth_service)
    before = SystemConfiguration.objects.get()
    tokens = list(FamilyAccessToken.objects.order_by("pk").values_list("pk", "version"))
    generations = list(
        FamilyAccessTokenGeneration.objects.order_by("pk").values_list("pk", "state")
    )
    with campaign_clock(NOW):
        transition = begin().transition
    runtime = SystemConfiguration.objects.get()
    assert runtime.restore_review_required and runtime.restore_released_at is None
    assert runtime.restore_id == transition.restore_id
    assert runtime.restore_backup_at == BACKUP
    assert runtime.restore_activated_at == NOW
    assert (runtime.mode, runtime.current_campaign_id) == ("production", campaign.pk)
    assert runtime.version == before.version + 1
    # Codes and links are exactly as restored: nothing replaced or cancelled.
    assert list(
        FamilyAccessToken.objects.order_by("pk").values_list("pk", "version")
    ) == (tokens)
    assert (
        list(
            FamilyAccessTokenGeneration.objects.order_by("pk").values_list(
                "pk", "state"
            )
        )
        == generations
    )
    assert AuditEvent.objects.filter(
        event_type="restore_review_started", subject_id=transition.pk
    ).exists()
    # A second restore starts a new review with a new id.
    with campaign_clock(NOW):
        second = begin(BACKUP - timedelta(days=1))
    assert second.started and second.restore_id != transition.restore_id


def restore_begin_row(runtime, backup_at=BACKUP):
    """A restore_begin transition as the operator command would write it."""
    return dict(
        request_id=uuid4(),
        expected_version=runtime.version,
        action="restore_begin",
        before_mode=runtime.mode,
        after_mode=runtime.mode,
        before_campaign_id=runtime.current_campaign_id,
        after_campaign_id=runtime.current_campaign_id,
        restore_id=uuid4(),
        backup_at=backup_at,
        reason="restore",
        correlation_id=uuid4(),
    )


def test_rerunning_restore_begin_keeps_the_open_review(auth_service, google):
    """A re-run for the same backup writes nothing; SQL refuses one (#799).

    A new transition would replace the review's restore id and move
    restore_activated_at, the cutoff of the held-email list.
    """
    live_campaign(auth_service)
    with campaign_clock(NOW):
        first = begin()
    opened = SystemConfiguration.objects.get()
    later = NOW + timedelta(hours=2)
    with campaign_clock(later):
        again = begin()
    assert (again.started, again.transition) == (False, None)
    assert (again.restore_id, again.backup_at) == (first.restore_id, BACKUP)
    runtime = SystemConfiguration.objects.get()
    assert (
        runtime.version,
        runtime.restore_id,
        runtime.restore_backup_at,
        runtime.restore_activated_at,
    ) == (opened.version, first.restore_id, BACKUP, NOW)
    assert RuntimeTransition.objects.filter(action="restore_begin").count() == 1
    assert AuditEvent.objects.filter(event_type="restore_review_started").count() == 1
    # The SQL guard is the backstop: an insert that skipped the check fails
    # closed for the operator's login and the schema owner alike.
    for login in (ServiceRole.ADMIN_RECOVERY, None):
        with (
            campaign_clock(later),
            task_login(login, exact=True) if login else transaction.atomic(),
            pytest.raises(DatabaseError, match="already open for this backup"),
            transaction.atomic(),
        ):
            RuntimeTransition.objects.create(**restore_begin_row(runtime))
    assert SystemConfiguration.objects.get().restore_activated_at == NOW
    # A different backup is another restore: it starts a new review.
    with campaign_clock(later):
        other = begin(BACKUP - timedelta(hours=1))
    runtime = SystemConfiguration.objects.get()
    assert other.started and other.restore_id != first.restore_id
    assert (runtime.restore_id, runtime.restore_activated_at) == (
        other.restore_id,
        later,
    )


def test_migration_check_refuses_the_0017_guard():
    """The frozen file's DO block fails against the pre-#799 definition."""
    text = MIGRATION.read_text(encoding="utf-8")
    check = text[text.index("DO $check$") : text.index("$check$;") + len("$check$;")]
    earlier = (MIGRATION.parent / "0017_restore_review.sql").read_text(encoding="utf-8")
    start = earlier.index(
        "CREATE OR REPLACE FUNCTION public.stewardship_runtime_transition_v1()"
    )
    old = earlier[start : earlier.index("END $$;", start) + len("END $$;")]
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(check)  # The installed definition passes.
        with pytest.raises(DatabaseError, match="Migration 0046"), transaction.atomic():
            cursor.execute(old)
            cursor.execute(check)
        cursor.execute(check)  # Rolled back to the new definition.


def test_only_the_operator_starts_a_review(auth_service, google):
    """Web cannot start one; a future backup time or a stale version is refused."""
    live_campaign(auth_service)
    runtime = SystemConfiguration.objects.get()
    row = dict(
        request_id=uuid4(),
        expected_version=runtime.version,
        action="restore_begin",
        before_mode=runtime.mode,
        after_mode=runtime.mode,
        before_campaign_id=runtime.current_campaign_id,
        after_campaign_id=runtime.current_campaign_id,
        restore_id=uuid4(),
        backup_at=BACKUP,
        reason="restore",
        correlation_id=uuid4(),
    )
    with (
        campaign_clock(NOW),
        task_login(ServiceRole.WEB, exact=True),
        pytest.raises(DatabaseError, match="operator restore command"),
        transaction.atomic(),
    ):
        RuntimeTransition.objects.create(**row)
    with (
        campaign_clock(NOW),
        pytest.raises(DatabaseError, match="operator restore command"),
        transaction.atomic(),
    ):
        RuntimeTransition.objects.create(**(row | {"backup_at": NOW + timedelta(1)}))
    # The restore fields cannot be written directly, even with the owner's
    # rights and a fresh version: they move only with a restore transition.
    with pytest.raises(DatabaseError), transaction.atomic():
        SystemConfiguration.objects.update(restore_review_required=True)
    assert not SystemConfiguration.objects.get().restore_review_required


def test_held_emails_cover_what_may_have_gone_out(auth_service, google):
    """Due, unrecorded invitations and reminders of reachable Families are held."""
    live_campaign(auth_service)
    signed_in()
    third = FamilyCampaign.objects.get(family_duid=3)
    first = FamilyCampaign.objects.get(family_duid=1)
    with campaign_clock(NOW):
        begin()
        added = as_web(list_held_emails_for)
        # Family 1: two reminders (its invitation is recorded as delivered).
        # Families 2 and 3: the invitation and two reminders each. Family 2
        # has no email, but its emails are held anyway: its eligibility may
        # have changed after the backup (the holds are inert otherwise). The
        # October 20 reminder is not due.
        assert added == 8
        second = FamilyCampaign.objects.get(family_duid=2)
        assert held(target=f"family:{second.pk}").count() == 3
        assert held(target=f"family:{third.pk}").count() == 3
        assert held(target=f"family:{first.pk}").count() == 2
        assert not held(definition__kind="initial", target=f"family:{first.pk}")
        assert set(held().values_list("state", "mode", "slot", "discovery")) == {
            ("unreviewed", "production", "once", "restore_inventory")
        }
        hold = held().first()
        assert (hold.backup_at, hold.window_start, hold.window_end) == (
            BACKUP,
            BACKUP,
            NOW,
        )
        # Repeating adds nothing.
        assert as_web(list_held_emails_for) == 0


def list_held_emails_for(*, actor_id, correlation_id, **_):
    """``list_held_emails`` takes no session: the hold guard checks the gate."""
    return list_held_emails(actor_id=actor_id, correlation_id=correlation_id)


def test_inventory_is_refused_outside_a_review(auth_service, google):
    """No review, no held emails."""
    live_campaign(auth_service)
    signed_in()
    with pytest.raises(DatabaseError, match="only during a restore review"):
        as_web(list_held_emails_for)


def test_settling_needs_a_fresh_administrator_and_is_audited(auth_service, google):
    """Each decision is versioned, append-only and audited; a stale sign-in fails."""
    live_campaign(auth_service)
    signed_in()
    with campaign_clock(NOW):
        begin()
        as_web(list_held_emails_for)
    hold = held().order_by("target", "definition__kind").first()
    arguments = dict(
        hold_id=hold.pk,
        expected_version=hold.version,
        state="assumed_delivered",
        evidence="The provider's sent log shows it went out.",
    )
    decision = as_web(settle_held_email, **arguments)
    assert decision.session_id == administrator().pk
    hold.refresh_from_db()
    assert (hold.state, hold.version) == ("assumed_delivered", 2)
    assert not ScheduleFulfillment.objects.filter(
        definition_id=hold.definition_id, target=hold.target
    ).exists()
    assert AuditEvent.objects.filter(
        event_type="restore_hold_resolved", subject_id=decision.pk
    ).exists()
    # The same decision again returns the first; another on the old version
    # is stale.
    assert as_web(settle_held_email, **arguments).pk == decision.pk
    with pytest.raises(StaleRecordError):
        as_web(settle_held_email, **(arguments | {"state": "resend_authorized"}))
    # There is no "not needed" decision: it would release the email silently.
    with pytest.raises(ValueError):
        as_web(settle_held_email, **(arguments | {"state": "not_applicable"}))
    # A decision is final: the assumption may already have cancelled the
    # unsent copy, so a later "send again" would send nothing.
    with pytest.raises(DatabaseError, match="resend binding"):
        as_web(
            settle_held_email,
            **(
                arguments
                | {
                    "expected_version": 2,
                    "state": "resend_authorized",
                    "evidence": "Not in the provider's log; send again.",
                }
            ),
        )
    # Send again on another held email: no occurrence needed yet.
    resent = held(state="unreviewed").first()
    again = as_web(
        settle_held_email,
        hold_id=resent.pk,
        expected_version=1,
        state="resend_authorized",
        evidence="Not in the provider's log; send again.",
    )
    assert again.recovery_occurrence_id is None
    resent.refresh_from_db()
    assert resent.state == "resend_authorized"
    # Sent again: the shared exclusion no longer keeps it back.
    assert not excluded(resent)
    # A sign-in older than five minutes is refused, as is another login.
    other = held(state="unreviewed").first()
    current = administrator()
    with (
        task_login(ServiceRole.WEB, exact=True),
        pytest.raises(DatabaseError, match="fresh Administrator"),
    ):
        settle_held_email(
            hold_id=other.pk,
            expected_version=1,
            state="assumed_delivered",
            evidence="Family moved away.",
            actor_id=current.principal_id,
            session_id=current.pk,
            authenticated_at=current.authenticated_at - timedelta(minutes=10),
            correlation_id=uuid4(),
        )
    with (
        task_login(ServiceRole.WORKER, exact=True),
        pytest.raises(DatabaseError),
    ):
        settle_held_email(
            hold_id=other.pk,
            expected_version=1,
            state="assumed_delivered",
            evidence="Family moved away.",
            actor_id=current.principal_id,
            session_id=current.pk,
            authenticated_at=current.authenticated_at,
            correlation_id=uuid4(),
        )


def test_release_reopens_the_site_and_keeps_unsettled_holds(auth_service, google):
    """Release lists late arrivals first, clears the gate, and is audited."""
    live_campaign(auth_service)
    signed_in()
    with campaign_clock(NOW):
        begin()
    runtime = SystemConfiguration.objects.get()
    # Release is refused until the held emails are found: every hold must be
    # made during the review, where it can still be decided.
    with (
        campaign_clock(NOW + timedelta(hours=1)),
        pytest.raises(DatabaseError, match="stale"),
    ):
        as_web(
            release_review,
            request_id=uuid4(),
            expected_runtime_version=runtime.version,
        )
    assert SystemConfiguration.objects.get().restore_review_required
    with campaign_clock(NOW + timedelta(hours=1)):
        assert as_web(list_held_emails_for) == 8
        assert holds_needed() == 0
        release = as_web(
            release_review,
            request_id=uuid4(),
            expected_runtime_version=runtime.version,
        )
    after = SystemConfiguration.objects.get()
    assert not after.restore_review_required
    assert after.restore_released_at == NOW + timedelta(hours=1)
    assert (after.restore_id, after.restore_backup_at, after.mode) == (
        runtime.restore_id,
        BACKUP,
        "production",
    )
    assert release.session_id == administrator().pk
    assert held(state="unreviewed").count() == 8
    assert AuditEvent.objects.filter(
        event_type="restore_review_released", subject_id=release.pk
    ).exists()
    # Every unsettled hold still keeps its email from being prepared or sent
    # (the shared exclusion the planner, claim and dispatch guards use).
    assert all(excluded(row) for row in held(state="unreviewed"))
    # An undecided hold is still decided after release (#757), by a freshly
    # signed-in Administrator through the web login, and audited.
    hold = held().order_by("target", "definition__kind").first()
    signed_in()
    decision = as_web(
        settle_held_email,
        hold_id=hold.pk,
        expected_version=1,
        state="resend_authorized",
        evidence="Not in the provider's log; send again.",
    )
    hold.refresh_from_db()
    assert (hold.state, hold.version) == ("resend_authorized", 2)
    assert not excluded(hold)
    assert AuditEvent.objects.filter(
        event_type="restore_hold_resolved", subject_id=decision.pk
    ).exists()
    # A decision stays final after release too.
    with pytest.raises(DatabaseError, match="resend binding"), transaction.atomic():
        RestoreHoldResolution.objects.create(
            hold_id=hold.pk,
            version=3,
            state="assumed_delivered",
            evidence="Assume it went out.",
            actor_id=administrator().principal_id,
            correlation_id=uuid4(),
        )
    # Without a fresh sign-in, or from a login other than web, it is refused.
    other = held(state="unreviewed").first()
    current = administrator()
    stale = dict(
        hold_id=other.pk,
        expected_version=1,
        state="assumed_delivered",
        evidence="Family moved away.",
        actor_id=current.principal_id,
        session_id=current.pk,
        correlation_id=uuid4(),
    )
    with (
        task_login(ServiceRole.WEB, exact=True),
        pytest.raises(DatabaseError, match="fresh Administrator"),
    ):
        settle_held_email(
            authenticated_at=current.authenticated_at - timedelta(minutes=10), **stale
        )
    with task_login(ServiceRole.WORKER, exact=True), pytest.raises(DatabaseError):
        settle_held_email(authenticated_at=current.authenticated_at, **stale)
    other.refresh_from_db()
    assert other.state == "unreviewed" and excluded(other)
    # A second release is refused.
    with pytest.raises(DatabaseError, match="cannot be released"):
        as_web(
            release_review,
            request_id=uuid4(),
            expected_runtime_version=after.version,
        )


def test_restoring_the_same_backup_after_release_starts_a_new_review(
    auth_service, google
):
    """Once a review is released, the same backup restored again reopens it.

    Only an open review is kept (#799): restoring the same backup a second
    time after release is another restore, with a new restore id and cutoff.
    """
    live_campaign(auth_service)
    signed_in()
    with campaign_clock(NOW):
        first = begin()
    runtime = SystemConfiguration.objects.get()
    released_at = NOW + timedelta(hours=1)
    with campaign_clock(released_at):
        as_web(list_held_emails_for)
        as_web(
            release_review,
            request_id=uuid4(),
            expected_runtime_version=runtime.version,
        )
    assert not SystemConfiguration.objects.get().restore_review_required
    later = NOW + timedelta(hours=2)
    with campaign_clock(later):
        again = begin()
    assert again.started and again.transition is not None
    assert again.restore_id != first.restore_id
    runtime = SystemConfiguration.objects.get()
    assert runtime.restore_review_required and runtime.restore_released_at is None
    assert (
        runtime.restore_id,
        runtime.restore_backup_at,
        runtime.restore_activated_at,
    ) == (again.restore_id, BACKUP, later)
    assert RuntimeTransition.objects.filter(action="restore_begin").count() == 2


def released_with_holds(auth_service):
    """Release a review of the live campaign leaving all 8 holds undecided."""
    live_campaign(auth_service)
    signed_in()
    with campaign_clock(NOW):
        begin()
        as_web(list_held_emails_for)
        as_web(
            release_review,
            request_id=uuid4(),
            expected_runtime_version=SystemConfiguration.objects.get().version,
        )
    assert held(state="unreviewed").count() == 8
    signed_in()


def test_a_hold_of_a_campaign_no_longer_current_is_not_decided(auth_service, google):
    """After release only the current campaign's holds are decided (#757)."""
    released_with_holds(auth_service)
    hold = held().first()

    class Restored(Exception):
        """Rolls the simulated campaign change back."""

    # The runtime row is loaded with no current campaign as offline input,
    # like restored_occurrence; the planner would never act on the hold.
    with pytest.raises(Restored), transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute("SET LOCAL session_replication_role = replica")
            cursor.execute(
                "UPDATE stewardship_system_configuration SET current_campaign_id=NULL"
            )
            cursor.execute("SET LOCAL session_replication_role = origin")
        with (
            pytest.raises(DatabaseError, match="resend binding"),
            transaction.atomic(),
        ):
            RestoreHoldResolution.objects.create(
                hold_id=hold.pk,
                version=2,
                state="assumed_delivered",
                evidence="Assume it went out.",
                actor_id=administrator().principal_id,
                correlation_id=uuid4(),
            )
        raise Restored
    # Back on the current campaign, the same decision is admitted.
    with transaction.atomic():
        RestoreHoldResolution.objects.create(
            hold_id=hold.pk,
            version=2,
            state="assumed_delivered",
            evidence="Assume it went out.",
            actor_id=administrator().principal_id,
            correlation_id=uuid4(),
        )
    hold.refresh_from_db()
    assert hold.state == "assumed_delivered"


def test_a_reminder_a_later_one_superseded_may_only_be_assumed_sent(
    auth_service, google
):
    """Sending an earlier reminder after a later one would arrive out of order."""
    released_with_holds(auth_service)
    third = FamilyCampaign.objects.get(family_duid=3)
    first, second = ScheduleDefinition.objects.filter(kind="reminder").order_by(
        "current_revision__due_at"
    )[:2]
    earlier = held(target=f"family:{third.pk}", definition=first).get()
    later = held(target=f"family:{third.pk}", definition=second).get()
    arguments = dict(
        hold_id=earlier.pk,
        expected_version=1,
        state="resend_authorized",
        evidence="Not in the provider's log; send again.",
    )
    # The later reminder reached the Family (after release, say).
    restored_occurrence(second, third)
    with pytest.raises(ValueError, match="later reminder"):
        as_web(settle_held_email, **arguments)
    earlier.refresh_from_db()
    assert earlier.state == "unreviewed"
    as_web(settle_held_email, **(arguments | {"state": "assumed_delivered"}))
    earlier.refresh_from_db()
    assert earlier.state == "assumed_delivered"
    # The later reminder itself and the invitation are not superseded.
    as_web(settle_held_email, **(arguments | {"hold_id": later.pk}))
    invitation = held(target=f"family:{third.pk}", definition__kind="initial").get()
    as_web(settle_held_email, **(arguments | {"hold_id": invitation.pk}))
    assert held(target=f"family:{third.pk}", state="resend_authorized").count() == 2


def test_release_needs_a_fresh_administrator(auth_service, google):
    """A stale sign-in or another login cannot release; the gate stays closed."""
    live_campaign(auth_service)
    signed_in()
    with campaign_clock(NOW):
        begin()
    runtime = SystemConfiguration.objects.get()
    current = administrator()
    with (
        campaign_clock(NOW),
        task_login(ServiceRole.WEB, exact=True),
        pytest.raises(DatabaseError, match="fresh Administrator"),
    ):
        release_review(
            request_id=uuid4(),
            expected_runtime_version=runtime.version,
            actor_id=current.principal_id,
            session_id=current.pk,
            authenticated_at=current.authenticated_at - timedelta(minutes=10),
            correlation_id=uuid4(),
        )
    with (
        campaign_clock(NOW),
        task_login(ServiceRole.ADMIN_RECOVERY, exact=True),
        pytest.raises(DatabaseError),
    ):
        release_review(
            request_id=uuid4(),
            expected_runtime_version=runtime.version,
            actor_id=current.principal_id,
            session_id=current.pk,
            authenticated_at=current.authenticated_at,
            correlation_id=uuid4(),
        )
    assert SystemConfiguration.objects.get().restore_review_required
    assert not RestoreHoldResolution.objects.exists()


def test_inventory_leaves_out_what_cannot_be_resent_or_is_in_flight(
    auth_service, google
):
    """Ended-before-backup work is not held; in-flight work goes to resolution."""
    live_campaign(auth_service)
    signed_in()
    third = FamilyCampaign.objects.get(family_duid=3)
    initial = ScheduleDefinition.objects.get(kind="initial")
    reminder = (
        ScheduleDefinition.objects.filter(kind="reminder")
        .order_by("current_revision__due_at")
        .first()
    )
    # The invitation was being handed to the provider at the backup, and the
    # first reminder failed for good before it.
    restored_occurrence(initial, third, "delivery_unknown")
    restored_occurrence(reminder, third, "failed")
    with campaign_clock(NOW):
        begin()
        as_web(list_held_emails_for)
    assert list(
        held(target=f"family:{third.pk}").values_list("definition__kind", flat=True)
    ) == ["reminder"]
    assert not held(target=f"family:{third.pk}", definition=reminder).exists()


def test_an_earlier_restores_hold_keeps_its_email_and_blocks_a_resend(
    auth_service, google
):
    """A slot still kept back is not held again, and cannot be resent around."""
    live_campaign(auth_service)
    signed_in()
    with campaign_clock(NOW):
        begin()
        first = as_web(list_held_emails_for)
        earlier = held().first()
        # A second restore: every slot is still kept back by the first one's
        # undecided holds, so nothing new is held.
        begin(BACKUP - timedelta(hours=1))
        assert as_web(list_held_emails_for) == 0
        assert first == 8
        # Even a hold of this restore on the same email (loaded directly) cannot
        # be resent while the earlier hold still keeps it back.
        runtime = SystemConfiguration.objects.get()
        duplicate = RestoreDeliveryHold.objects.create(
            restore_id=runtime.restore_id,
            definition_id=earlier.definition_id,
            mode="production",
            target=earlier.target,
            slot="once",
            backup_at=runtime.restore_backup_at,
            window_start=runtime.restore_backup_at,
            window_end=NOW,
            discovery="restore_inventory",
            actor_id=administrator().principal_id,
            correlation_id=uuid4(),
        )
    with pytest.raises(DatabaseError, match="resend binding"):
        as_web(
            settle_held_email,
            hold_id=duplicate.pk,
            expected_version=1,
            state="resend_authorized",
            evidence="Send again.",
        )
    # An assumption is final.
    as_web(
        settle_held_email,
        hold_id=earlier.pk,
        expected_version=1,
        state="assumed_delivered",
        evidence="The provider's log shows it.",
    )
    with pytest.raises(DatabaseError, match="resend binding"), transaction.atomic():
        RestoreHoldResolution.objects.create(
            hold_id=earlier.pk,
            version=3,
            state="resend_authorized",
            evidence="Again",
            actor_id=administrator().principal_id,
            correlation_id=uuid4(),
        )


def test_a_testing_restore_holds_nothing(auth_service, google):
    """Testing email goes only to the testing address: nothing is held."""
    store, actor = auth_service.store, uuid4()
    add_draft(store, store.active(), actor)
    signed_in()
    with campaign_clock(NOW):
        begin()
        assert as_web(list_held_emails_for) == 0
    assert not RestoreDeliveryHold.objects.exists()


def test_the_recovery_login_only_starts_a_review(auth_service, google):
    """admin-recovery cannot release, return to Testing, or move the mode."""
    live_campaign(auth_service)
    with campaign_clock(NOW):
        begin()
    runtime = SystemConfiguration.objects.get()
    with (
        campaign_clock(NOW),
        task_login(ServiceRole.ADMIN_RECOVERY, exact=True),
        pytest.raises(DatabaseError, match="only starts a restore review"),
        transaction.atomic(),
    ):
        RuntimeTransition.objects.create(
            request_id=uuid4(),
            expected_version=runtime.version,
            action="restore_release",
            before_mode=runtime.mode,
            after_mode=runtime.mode,
            before_campaign_id=runtime.current_campaign_id,
            after_campaign_id=runtime.current_campaign_id,
            restore_id=runtime.restore_id,
            backup_at=runtime.restore_backup_at,
            actor_id=uuid4(),
            correlation_id=uuid4(),
        )


@pytest.mark.parametrize(
    ("state", "reason", "held_now"),
    [
        ("failed", "", True),
        ("skipped", "no_deliverable_recipient", True),
        ("skipped", "family_ineligible", True),
        ("skipped", "family_responded", False),
    ],
)
def test_an_invitation_a_deliverability_change_would_retry_is_held(
    auth_service, google, state, reason, held_now
):
    """Failed or undeliverable invitations can be revived after release: hold them."""
    live_campaign(auth_service)
    signed_in()
    third = FamilyCampaign.objects.get(family_duid=3)
    initial = ScheduleDefinition.objects.get(kind="initial")
    restored_occurrence(initial, third, state, reason)
    with campaign_clock(NOW):
        begin()
        as_web(list_held_emails_for)
    assert held(target=f"family:{third.pk}", definition=initial).exists() is held_now


def test_emails_due_during_the_review_are_not_held(auth_service, google):
    """The cutoff is the restore: what falls due later was never sent."""
    live_campaign(auth_service)
    signed_in()
    with campaign_clock(NOW):
        begin()
    # Listed three weeks later: the October 20 reminder is due by now, but
    # was not due when the site was restored, so nobody could have sent it.
    with campaign_clock(NOW + timedelta(days=21)):
        assert as_web(list_held_emails_for) == 8
    late = ScheduleDefinition.objects.filter(kind="reminder").order_by(
        "-current_revision__due_at"
    )[0]
    assert not held(definition=late).exists()
    assert all(
        hold.window_end == NOW for hold in held()
    )  # The window ends at the restore.


@pytest.mark.parametrize("history", ["old_revision", "old_cycle"])
def test_work_of_a_replaced_revision_or_old_cycle_is_not_this_emails_history(
    auth_service, google, history
):
    """A skipped row of another revision or cycle does not end the email."""
    import json

    live_campaign(auth_service)
    signed_in()
    third = FamilyCampaign.objects.get(family_duid=3)
    initial = ScheduleDefinition.objects.select_related("current_revision").get(
        kind="initial"
    )
    if history == "old_revision":
        revision = initial.current_revision
        old = uuid4()
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute("SET LOCAL session_replication_role = replica")
            cursor.execute(
                "INSERT INTO stewardship_schedule_revision (id,actor_id,correlation_id,"
                'record_id,campaign_id,kind,due_at,"values",configuration_id) '
                "VALUES (%s,NULL,%s,%s,%s,%s,%s,%s,%s)",
                [
                    old,
                    uuid4(),
                    revision.record_id,
                    revision.campaign_id,
                    revision.kind,
                    revision.due_at,
                    json.dumps(revision.values),
                    # Another applied configuration's revision of the schedule.
                    uuid4(),
                ],
            )
            cursor.execute("SET LOCAL session_replication_role = origin")
        restored_occurrence(
            initial, third, "skipped", "schedule_replaced", revision_id=old
        )
    else:
        # Another Production cycle's attempt (as after a Return to Testing).
        other = Campaign.objects.get(pk=initial.campaign_id).production_cycle + 1
        restored_occurrence(initial, third, "skipped", "schedule_replaced", cycle=other)
    with campaign_clock(NOW):
        begin()
        as_web(list_held_emails_for)
    assert held(target=f"family:{third.pk}", definition=initial).exists()

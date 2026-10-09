"""The paused-delivery resume skips Reminder WorkGroup Families' reminders (#866).

Planning skips a Reminder WorkGroup Family's reminders (#861). The resume
plan (the private ``stewardship_delivery_family_recovery`` view, behind both
the preview's counts and what confirming writes) now does the same, so the
preview counts them as skipped instead of as to be emailed or coalesced into
the invitation. These tests pause a real Production campaign, let its
invitation and reminders fall due unsent, and resume it through the real
Admin commands.
"""

# ruff: noqa: F811 -- pytest injects imported fixture dependencies by name.

from pathlib import Path
from uuid import uuid4

import pytest
from django.db import DatabaseError, connection, transaction
from test_parishsoft_source import family as source_family
from test_parishsoft_source import member as source_member

from parishkit.stewardship.accounts import delivery_control_commands as commands
from parishkit.stewardship.campaigns.credential_models import FamilyCampaign
from parishkit.stewardship.campaigns.models import (
    ScheduleFulfillment,
    ScheduleOccurrence,
)
from parishkit.stewardship.campaigns.schedule_models import ScheduleDefinition
from parishkit.stewardship.campaigns.schedules import (
    create_occurrence,
    record_fulfillment,
)
from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.jobs.outbox_models import OutboxMessage

from . import test_go_live_cleanup_postgresql as cleanup_tests
from .campaign_builders import (
    admit_test_work,
    advance,
    campaign_clock,
    change,
    claimed_task,
)
from .test_delivery_control_postgresql import (  # noqa: F401
    accepted_sender_check,
    delivery_scheduled,
)
from .test_reminder_workgroup_postgresql import NAME, record
from .test_setup_loading_postgresql import pages
from .test_setup_mail_views_postgresql import web_login
from .test_withdrawal_postgresql import (  # noqa: F401
    bootstrapped,
    config_role,
    ready_cleanup,
    ready_links,
    scheduled,
    setup_service,
)
from .test_withdrawal_work_postgresql import future_message

pytestmark = pytest.mark.django_db(transaction=True)

MIGRATION = (
    Path(commands.__file__).resolve().parents[1]
    / "schema/migrations/0035_workgroup_recovery_skip.sql"
)


def two_family_pages(**options):
    """The setup source with a second, ordinary Family (DUID 2) and its Head.

    The stock source has one Family, so a resume test could not tell "only
    WorkGroup members are skipped" from "every Family is skipped once any
    WorkGroup evidence exists".
    """
    values = pages(**options)
    families, members = values[1], values[4]
    families[0] |= {"totalResults": 2}
    families.append(
        source_family(2, total=2, row=2)
        | {"familyID": 12, "registeredOrganizationID": 1, "lastName": "Ordinary"}
    )
    members[0] |= {"recordCount": 2}
    members.append(
        source_member(2, total=2, row=2)
        | {
            "familyDUID": 2,
            "firstName": "Ordinary",
            "memberType": "Head",
            "emailAddress": "ordinary@example.org",
        }
    )
    return values


@pytest.fixture
def two_families(request, monkeypatch):
    """``delivery_scheduled`` loaded from the two-Family setup source."""
    monkeypatch.setattr(cleanup_tests, "pages", two_family_pages)
    return request.getfixturevalue("delivery_scheduled")


def set_workgroup(item, name):
    """Apply the campaign's Reminder WorkGroup name through the real owner."""
    patch = [
        {
            "operation": "update",
            "section": "campaigns",
            "id": str(item.campaign.pk),
            "values": {"reminder_workgroup": name},
        }
    ]
    store = item.arguments[1].store
    assert change(store, store.active(), uuid4(), patch).state == "applied"
    item.campaign.refresh_from_db()


def occurrences(family, kind):
    """The Production occurrences of one Family's definitions of ``kind``."""
    return ScheduleOccurrence.objects.filter(
        mode="production",
        target=f"family:{family.pk}",
        definition__kind=kind,
    )


def test_resume_skips_workgroup_reminders_and_keeps_the_invitation(
    two_families, monkeypatch, tmp_path
):
    """Preview and confirm agree: the WorkGroup Family's reminders are skipped,
    its invitation is still selected, the ordinary Family's reminders still
    coalesce into its invitation, and a different recorded name excludes
    nobody."""
    item = two_families
    # Only the first Family is in the WorkGroup; the second is ordinary.
    families = list(FamilyCampaign.objects.filter(campaign=item.campaign))
    count = len(families)
    assert count == 2
    excluded, *others = sorted(families, key=lambda family: family.family_duid)
    reminders = ScheduleDefinition.objects.filter(
        campaign=item.campaign, kind="reminder"
    ).count()
    assert reminders == 3
    # Every invitation and reminder is due and none has been sent.
    due = (
        ScheduleDefinition.objects.filter(campaign=item.campaign)
        .order_by("-current_revision__due_at")
        .values_list("current_revision__due_at", flat=True)
        .first()
    )
    set_workgroup(item, NAME)
    # A refresh that read a different name excludes nobody.
    record("Last year", {excluded.family_duid})
    with campaign_clock(due):
        with web_login():
            _, token = commands.preview_pause(*item.arguments, reason="Review backlog")
            commands.confirm(*item.arguments, token=token)
            impact = commands.family_recovery_impact(item.campaign.pk)
        assert (impact["selected"], impact["coalesced"], impact["skipped"]) == (
            count,
            reminders * count,
            0,
        )
        record(NAME, {excluded.family_duid})
        with web_login():
            impact = commands.family_recovery_impact(item.campaign.pk)
        # Each Family's invitation is selected; only the WorkGroup Family's
        # reminders are skipped instead of coalesced into it.
        assert (impact["selected"], impact["coalesced"], impact["skipped"]) == (
            count,
            reminders * (count - 1),
            reminders,
        )
        assert impact["blocked"] == 0 and impact["deferred"] == 0
        accepted_sender_check(item, monkeypatch, tmp_path)
        with web_login():
            preview, token = commands.preview_resume(
                *item.arguments, reason="Release the backlog"
            )
            assert preview["selection"]["plan"] == "family"
            assert preview["selection"]["family"]["skipped"] == reminders
            commands.confirm(*item.arguments, token=token)
    item.campaign.refresh_from_db()
    assert not item.campaign.delivery_paused
    # Confirming wrote what the preview counted.
    skipped = occurrences(excluded, "reminder")
    assert skipped.count() == reminders
    assert set(skipped.values_list("state", "reason")) == {
        ("skipped", "workgroup_excluded")
    }
    assert not ScheduleFulfillment.objects.filter(
        target=f"family:{excluded.pk}", definition__kind="reminder"
    ).exists()
    # The WorkGroup Family's invitation is still the selected, pending send.
    invitation = occurrences(excluded, "initial").get()
    assert (invitation.state, invitation.reason) == ("pending", "")
    # The ordinary Family is left alone: its reminders coalesce into its
    # invitation, as they would with no WorkGroup at all.
    for family in others:
        coalesced = occurrences(family, "reminder")
        assert coalesced.count() == reminders
        assert set(coalesced.values_list("state", flat=True)) == {"coalesced"}
        assert (
            ScheduleFulfillment.objects.filter(
                target=f"family:{family.pk}", disposition="coalesced"
            ).count()
            == reminders
        )
        assert occurrences(family, "initial").get().state == "pending"
    assert ScheduleFulfillment.objects.filter(disposition="coalesced").count() == (
        reminders * (count - 1)
    )
    assert ScheduleOccurrence.objects.filter(state="skipped").count() == reminders


def deliver_invitation(item, family):
    """Settle the Family's Production invitation as delivered, with coverage."""
    initial = ScheduleDefinition.objects.select_related("current_revision").get(
        campaign=item.campaign, kind="initial"
    )
    actor = uuid4()
    with campaign_clock(initial.current_revision.due_at):
        row = create_occurrence(
            definition_id=initial.pk,
            revision_id=initial.current_revision_id,
            mode="production",
            target=f"family:{family.pk}",
            slot="once",
            due_at=initial.current_revision.due_at,
            actor_id=actor,
            correlation_id=uuid4(),
            admit=admit_test_work,
        )
        task = claimed_task("schedule_occurrence", row.pk, actor)
        row = advance(row, actor, "running", task_id=task.run_id, fence=task.fence)
        row = advance(row, actor, "succeeded", fence=task.fence)
        record_fulfillment(
            occurrence_id=row.pk,
            disposition="delivered",
            actor_id=actor,
            correlation_id=uuid4(),
            admit=admit_test_work,
        )
    return row


def test_resume_cancels_a_delivered_workgroup_familys_prepared_reminder(
    delivery_scheduled, monkeypatch, tmp_path
):
    """The common Production case: the invitation went out, then the Family
    joined the WorkGroup while its reminders fell due during the pause.

    Without #866 the latest reminder would be selected and the rest
    coalesced into it. Now every reminder is skipped, the one already
    prepared for sending is cancelled as workgroup_excluded, and nothing is
    left to send.
    """
    item = delivery_scheduled
    family = FamilyCampaign.objects.get(campaign=item.campaign)
    invitation = deliver_invitation(item, family)
    reminders = ScheduleDefinition.objects.select_related("current_revision").filter(
        campaign=item.campaign, kind="reminder"
    )
    assert reminders.count() == 3
    first = reminders.order_by("current_revision__due_at").first()
    _, prepared = future_message(item, definition=first)
    due = max(reminder.current_revision.due_at for reminder in reminders)
    set_workgroup(item, NAME)
    record(NAME, {family.family_duid})
    with campaign_clock(due):
        with web_login():
            _, token = commands.preview_pause(*item.arguments, reason="Review backlog")
            commands.confirm(*item.arguments, token=token)
            impact = commands.family_recovery_impact(item.campaign.pk)
        assert (impact["selected"], impact["coalesced"], impact["skipped"]) == (
            0,
            0,
            3,
        )
        assert impact["blocked"] == 0 and impact["deferred"] == 0
        accepted_sender_check(item, monkeypatch, tmp_path)
        with web_login():
            preview, token = commands.preview_resume(
                *item.arguments, reason="Release the backlog"
            )
            assert preview["selection"]["plan"] == "family"
            assert preview["selection"]["family"]["skipped"] == 3
            commands.confirm(*item.arguments, token=token)
    item.campaign.refresh_from_db()
    assert not item.campaign.delivery_paused
    skipped = occurrences(family, "reminder")
    assert skipped.count() == 3
    assert set(skipped.values_list("state", "reason")) == {
        ("skipped", "workgroup_excluded")
    }
    # The prepared reminder is cancelled unsent, with its task, and released
    # from the pause hold; no Family mail is waiting to go out.
    cancelled = OutboxMessage.objects.get(pk=prepared.message_id)
    assert (cancelled.state, cancelled.action, cancelled.reason) == (
        "cancelled",
        "cancel_unsent",
        "workgroup_excluded",
    )
    assert cancelled.pause_hold_id is None and cancelled.sealed_substitutions is None
    assert TaskRun.objects.get(pk=cancelled.task_id).state == "cancelled"
    assert not OutboxMessage.objects.filter(
        family_id=family.pk, state__in=("pending", "retry_wait", "delivered")
    ).exists()
    # The delivered invitation and its coverage are untouched.
    invitation.refresh_from_db()
    assert invitation.state == "succeeded"
    assert set(
        ScheduleFulfillment.objects.filter(target=f"family:{family.pk}").values_list(
            "definition__kind", "disposition"
        )
    ) == {("initial", "delivered")}


def test_migration_check_refuses_the_old_view_and_admits_the_new():
    """The frozen file's DO block fails against the pre-#866 view."""
    text = MIGRATION.read_text(encoding="utf-8")
    start = text.index("CREATE OR REPLACE VIEW public.")
    view = text[start : text.index("FROM reasons;", start) + len("FROM reasons;")]
    acl = text[text.index("SELECT set_config(") : text.index("true);") + len("true);")]
    check = text[text.index("DO $check$") : text.index("$check$;") + len("$check$;")]
    # The pre-#866 view: no WorkGroup column or reason.
    scope = view[
        view.index("    -- workgroup_duids:") : view.index(
            "    FROM public.stewardship_system_configuration"
        )
    ]
    reason = view[
        view.index("        -- A Reminder WorkGroup") : view.index(
            "        WHEN NOT email_deliverable"
        )
    ]
    old = view.replace(scope, "    SELECT c.id,c.production_cycle,p.ends_at\n")
    old = old.replace(reason, "")
    assert "workgroup" not in old.lower()
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(acl)
        cursor.execute(check)  # The installed (new) view passes.
        with (
            pytest.raises(DatabaseError, match="was not replaced"),
            transaction.atomic(),
        ):
            cursor.execute(old)
            cursor.execute(check)
        cursor.execute(check)  # Rolled back to the new view.

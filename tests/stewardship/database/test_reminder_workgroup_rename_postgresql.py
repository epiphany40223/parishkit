"""A renamed Reminder WorkGroup keeps its exclusions until a refresh (#1305).

A refresh records the WorkGroup read for the name set when it ran
(``source.workgroups``). Renaming the setting keeps excluding the Families
that read found, until a refresh records the new name; clearing it stops
every exclusion at once; a refresh that finds no WorkGroup by the new name
keeps the earlier set; and the first name ever set excludes nobody until a
refresh reads it. These tests run the real scheduler on a Production
campaign with one reminder, planned after each step, and record the
evidence on the current snapshot as a refresh would
(``test_reminder_workgroup_postgresql.record``).
"""

import pytest
from django.db import transaction

from parishkit.stewardship.campaigns.credential_models import FamilyCampaign
from parishkit.stewardship.campaigns.models import Campaign
from parishkit.stewardship.jobs.ownership import database_now
from parishkit.stewardship.jobs.send_progress import read_send
from parishkit.stewardship.reports.timeline_views import read_identity
from parishkit.stewardship.source.workgroups import (
    applied_evidence,
    current_evidence,
    excluded_duids,
)

from .campaign_builders import campaign_clock
from .test_family_mail_bulk_postgresql import (  # noqa: F401
    families,
    plan,
    prepare_all,
    send_all,
    single,
)
from .test_family_mail_preparation_postgresql import family_mail  # noqa: F401
from .test_family_mail_prepare_ahead_postgresql import (  # noqa: F401
    FAMILIES,
    INSIDE,
    SHORT,
    invited,
    reminder_messages,
    reminders,
    wait_for_tasks,
)
from .test_family_mail_worker_postgresql import dispatch_worker  # noqa: F401
from .test_reminder_workgroup_postgresql import (
    NAME,
    missing_workgroup,
    record,
    set_workgroup,
    target,
    values,
)
from .test_source_families_postgresql import source_singletons  # noqa: F401

pytestmark = pytest.mark.django_db(transaction=True)

RENAMED = "Active: Stewardship 2028"
# A second mailable Family (the fixture adds DUIDs from 10000), in no
# WorkGroup until a refresh reads the new name.
OTHER = 10000


def skipped_targets(due_at):
    """The Family targets whose reminder due at ``due_at`` was skipped."""
    rows = reminders().filter(due_at=due_at)
    assert rows.count() == FAMILIES
    return {
        row.target
        for row in rows
        if (row.state, row.reason) == ("skipped", "workgroup_excluded")
    }


def owed_workgroup(harness):
    """The reminder send's WorkGroup count, as the send page reads it."""
    with transaction.atomic():
        campaign = Campaign.objects.get(pk=harness.campaign.pk)
        return read_send(
            campaign.pk, "production", campaign.production_cycle, database_now()
        ).workgroup


def identity_workgroup(harness, duid):
    """The WorkGroup a Family's timeline names as skipping its Reminders."""
    family = FamilyCampaign.objects.get(family_duid=duid)
    with transaction.atomic():
        return read_identity(
            family, codes=False, workgroup=values(harness)
        ).reminder_workgroup


def renamed(harness):
    """Set the name, record a refresh's read of it ({1}), then rename it."""
    set_workgroup(harness, NAME)
    record(NAME, {1})
    assert excluded_duids(values(harness)) == {1}
    set_workgroup(harness, RENAMED)


def test_a_rename_keeps_the_previous_families_until_a_refresh(invited):  # noqa: F811
    """Renamed, not yet refreshed: planning skips the previous WorkGroup's
    Families, and the timeline names that WorkGroup."""
    harness, _path, _provider, due_at = invited
    renamed(harness)
    assert excluded_duids(values(harness)) == {1}
    assert current_evidence(values(harness)) == (RENAMED, None)
    assert applied_evidence(values(harness))["name"] == NAME
    assert identity_workgroup(harness, 1) == NAME
    assert identity_workgroup(harness, OTHER) is None
    # System health does not call the new name missing before a refresh.
    assert missing_workgroup(harness) is None
    with campaign_clock(due_at - INSIDE):
        plan()
    assert skipped_targets(due_at) == {target(1)}


def test_a_refresh_of_the_new_name_moves_the_skip(invited):  # noqa: F811
    """Once a refresh reads the new name, only its Families are skipped."""
    harness, _path, _provider, due_at = invited
    renamed(harness)
    record(RENAMED, {OTHER})
    assert excluded_duids(values(harness)) == {OTHER}
    assert current_evidence(values(harness))[1]["name"] == RENAMED
    assert identity_workgroup(harness, 1) is None
    assert identity_workgroup(harness, OTHER) == RENAMED
    with campaign_clock(due_at - INSIDE):
        plan()
    assert skipped_targets(due_at) == {target(OTHER)}


def test_a_clear_stops_every_exclusion_at_once(invited):  # noqa: F811
    """Cleared after a rename, with no refresh: nobody is skipped."""
    harness, _path, _provider, due_at = invited
    renamed(harness)
    set_workgroup(harness, None)
    assert excluded_duids(values(harness)) == frozenset()
    assert applied_evidence(values(harness)) is None
    assert identity_workgroup(harness, 1) is None
    with campaign_clock(due_at - INSIDE):
        plan()
    assert skipped_targets(due_at) == set()


MISSPELLED = "Activ: Stewardship 2028"


def misspelled(harness):
    """Rename to a name ParishSoft lacks, and record the refresh that read it.

    The refresh keeps the set applied before (``keep_when_missing``), as
    ``test_reminder_workgroup_postgresql`` checks on the real refresh path.
    """
    set_workgroup(harness, NAME)
    record(NAME, {1})
    set_workgroup(harness, MISSPELLED)
    record(MISSPELLED, {1}, found=False)


def test_a_misspelled_rename_keeps_skipping_after_its_refresh(invited):  # noqa: F811
    """Refreshed under a name ParishSoft lacks: the notice shows and planning
    still skips the previous WorkGroup's Families."""
    harness, _path, _provider, due_at = invited
    misspelled(harness)
    assert excluded_duids(values(harness)) == {1}
    assert missing_workgroup(harness) == (MISSPELLED, "error")
    with campaign_clock(due_at - INSIDE):
        plan()
    assert skipped_targets(due_at) == {target(1)}


def test_a_correction_after_a_misspelling_applies_its_families(invited):  # noqa: F811
    """Once a refresh finds the corrected name, only its Families are skipped
    and the notice goes away."""
    harness, _path, _provider, due_at = invited
    misspelled(harness)
    set_workgroup(harness, RENAMED)
    # Not yet refreshed: the kept set still applies.
    assert excluded_duids(values(harness)) == {1}
    record(RENAMED, {OTHER})
    assert excluded_duids(values(harness)) == {OTHER}
    assert missing_workgroup(harness) is None
    with campaign_clock(due_at - INSIDE):
        plan()
    assert skipped_targets(due_at) == {target(OTHER)}


def test_the_first_name_set_excludes_nobody_until_a_refresh(invited):  # noqa: F811
    """With no WorkGroup recorded on the snapshot, a new name excludes nobody."""
    harness, _path, _provider, due_at = invited
    set_workgroup(harness, NAME)
    assert current_evidence(values(harness)) == (NAME, None)
    assert applied_evidence(values(harness)) is None
    assert excluded_duids(values(harness)) == frozenset()
    with campaign_clock(due_at - INSIDE):
        plan()
    assert skipped_targets(due_at) == set()


def test_a_reminder_prepared_ahead_is_not_sent_during_a_pending_rename(
    invited,  # noqa: F811
):
    """The send-time re-check uses the same set: a Family of the previous
    WorkGroup is skipped at send time while the rename waits for a refresh."""
    harness, path, provider, due_at = invited
    sent = len(provider.calls)
    with campaign_clock(due_at - SHORT):
        plan()
        prepare_all(harness)
    assert reminder_messages().count() == FAMILIES
    renamed(harness)
    wait_for_tasks(reminder_messages())
    with campaign_clock(due_at):
        send_all(harness, path)
        for message in reminder_messages():
            single(harness, path, message.task_id)
        # The send page's count of Families the WorkGroup skips agrees.
        assert owed_workgroup(harness) == 1
    assert len(provider.calls) - sent == FAMILIES - 1
    occurrence = reminders().get(target=target(1))
    assert (occurrence.state, occurrence.reason) == ("skipped", "workgroup_excluded")
    assert reminder_messages().get(pk=occurrence.outbox_id).state == "cancelled"

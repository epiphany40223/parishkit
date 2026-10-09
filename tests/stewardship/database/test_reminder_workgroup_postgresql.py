"""The campaign's Reminder WorkGroup skips its Families' Reminders only (#861).

A live campaign may name a ParishSoft Family WorkGroup; each refresh records
its members in the current snapshot's load evidence (``source.workgroups``).
These tests run the real scheduler, preparation and mail logins on a
Production campaign whose invitation reached every Family, and record the
evidence on the current snapshot as a refresh would. The refresh's own read
is checked against the fake ParishSoft in ``test_fake_parishsoft.py``, and
the refresh path that records it at the end of this file.
"""

from uuid import uuid4

import pytest
from django.db import transaction

from parishkit.stewardship.campaigns.credential_models import FamilyCampaign
from parishkit.stewardship.campaigns.models import Campaign
from parishkit.stewardship.jobs.ownership import database_now
from parishkit.stewardship.jobs.send_progress import read_send
from parishkit.stewardship.reports.family_timeline import read_timeline
from parishkit.stewardship.reports.family_timeline_views import read_identity
from parishkit.stewardship.reports.response_metrics import ResponseScope
from parishkit.stewardship.source import refreshing
from parishkit.stewardship.source.snapshot_models import SourceCurrent, SourceSnapshot
from parishkit.stewardship.source.workgroups import (
    EVIDENCE_KEY,
    current_evidence,
    excluded_duids,
)
from parishkit.stewardship.system_health import read_health

from .auth_builders import unguarded
from .campaign_builders import campaign_clock, change
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
    credential_rows,
    invited,
    reminder_messages,
    reminders,
    wait_for_tasks,
)
from .test_family_mail_worker_postgresql import dispatch_worker  # noqa: F401
from .test_source_families_postgresql import source_singletons  # noqa: F401
from .test_source_unchanged_postgresql import Refreshes

pytestmark = pytest.mark.django_db(transaction=True)

NAME = "Active: Stewardship 2027"


def set_workgroup(harness, name):
    """Apply the campaign's Reminder WorkGroup name (None clears it)."""
    campaign = Campaign.objects.get(pk=harness.campaign.pk)
    patch = [
        {
            "operation": "update",
            "section": "campaigns",
            "id": str(campaign.pk),
            "values": {"reminder_workgroup": name},
        }
    ]
    store = harness.service.store
    assert change(store, store.active(), uuid4(), patch).state == "applied"


def record(name, duids, *, found=True):
    """Record WorkGroup evidence on the current snapshot, as a refresh would."""
    current = SourceCurrent.objects.get().snapshot_id
    snapshot = SourceSnapshot.objects.get(pk=current)
    cursor = dict(snapshot.cursor)
    cursor["load"] = cursor.get("load", {}) | {
        EVIDENCE_KEY: {"name": name, "found": found, "family_duids": sorted(duids)}
    }
    with unguarded():
        SourceSnapshot.objects.filter(pk=current).update(cursor=cursor)


def values(harness):
    """The current campaign's configuration values."""
    return Campaign.objects.get(pk=harness.campaign.pk).active_configuration.values


def target(duid):
    """The occurrence target of the Family with this DUID."""
    return f"family:{FamilyCampaign.objects.get(family_duid=duid).pk}"


def test_a_live_campaign_sets_and_clears_its_reminder_workgroup(invited):  # noqa: F811
    """The setting stays editable on a live (structurally locked) campaign."""
    harness, *_ = invited
    assert Campaign.objects.get(pk=harness.campaign.pk).structural_locked
    set_workgroup(harness, NAME)
    assert values(harness)["reminder_workgroup"] == NAME
    # The name counts only once a refresh has read it.
    assert current_evidence(values(harness)) == (NAME, None)
    record(NAME, {1})
    assert excluded_duids(values(harness)) == {1}
    # A different recorded name, or a cleared setting, excludes nobody.
    record("Last year", {1})
    assert excluded_duids(values(harness)) == frozenset()
    set_workgroup(harness, None)
    assert "reminder_workgroup" not in values(harness)


def test_workgroup_family_gets_no_reminder_and_keeps_everything_else(invited):  # noqa: F811
    """Planned ahead, the Family's reminder is skipped; the others are sent."""
    harness, path, provider, due_at = invited
    before = credential_rows()
    set_workgroup(harness, NAME)
    record(NAME, {1})
    sent = len(provider.calls)
    with campaign_clock(due_at - INSIDE):
        plan()
    skipped = reminders().get(target=target(1))
    assert (skipped.state, skipped.reason) == ("skipped", "workgroup_excluded")
    assert reminders().filter(state="pending").count() == FAMILIES - 1
    with campaign_clock(due_at - SHORT):
        prepare_all(harness)
    assert reminder_messages().count() == FAMILIES - 1
    wait_for_tasks(reminder_messages())
    with campaign_clock(due_at):
        send_all(harness, path)
        for message in reminder_messages():
            single(harness, path, message.task_id)
        with transaction.atomic():
            campaign = Campaign.objects.get(pk=harness.campaign.pk)
            counts = read_send(
                campaign.pk, "production", campaign.production_cycle, database_now()
            )
    assert len(provider.calls) - sent == FAMILIES - 1
    assert counts.kind == "reminder" and counts.workgroup == 1
    assert counts.sent == FAMILIES - 1 and counts.unplanned == 0
    # Codes, links and the Family row are untouched; it stays in every count.
    assert credential_rows() == before
    family = FamilyCampaign.objects.get(family_duid=1)
    assert family.portal_eligible and family.email_eligible
    assert family.email_deliverable
    # The timeline names the skip and the WorkGroup.
    with transaction.atomic():
        timeline = read_timeline(
            ResponseScope(harness.campaign.pk, "production", None),
            family.pk,
            full=True,
        )
        identity = read_identity(family, codes=False, workgroup=values(harness))
    assert ("Reminder not sent", "in the ParishSoft Reminder WorkGroup") in [
        (str(line.what), str(line.detail)) for line in timeline.events
    ]
    assert identity.reminder_workgroup == NAME


def test_a_reminder_prepared_ahead_is_not_sent_once_the_family_is_excluded(
    invited,  # noqa: F811
):
    """Prepared before the Family joined the WorkGroup, skipped at send time."""
    harness, path, provider, due_at = invited
    before = credential_rows()
    sent = len(provider.calls)
    with campaign_clock(due_at - SHORT):
        plan()
        prepare_all(harness)
    assert reminder_messages().count() == FAMILIES
    # The Family joins the WorkGroup after its reminder was prepared.
    set_workgroup(harness, NAME)
    record(NAME, {1})
    wait_for_tasks(reminder_messages())
    with campaign_clock(due_at):
        send_all(harness, path)
        for message in reminder_messages():
            single(harness, path, message.task_id)
    assert len(provider.calls) - sent == FAMILIES - 1
    occurrence = reminders().get(target=target(1))
    assert (occurrence.state, occurrence.reason) == ("skipped", "workgroup_excluded")
    message = reminder_messages().get(pk=occurrence.outbox_id)
    assert message.state == "cancelled"
    others = reminder_messages().exclude(pk=message.pk)
    assert set(others.values_list("state", flat=True)) == {"delivered"}
    assert credential_rows() == before


def test_an_unknown_workgroup_shows_on_system_health_and_excludes_nobody(invited):  # noqa: F811
    """A name ParishSoft does not have is a visible notice, not a silent no-op."""
    harness, path, provider, due_at = invited
    set_workgroup(harness, "Typo")
    record("Typo", (), found=False)
    assert excluded_duids(values(harness)) == frozenset()
    _health, page = read_health(harness.service.store)
    assert page["workgroup_missing"] == "Typo"
    with campaign_clock(due_at - INSIDE):
        plan()
    assert reminders().filter(state="pending").count() == FAMILIES
    # Found, the notice goes away.
    record("Typo", {1})
    _health, page = read_health(harness.service.store)
    assert page["workgroup_missing"] is None


def test_refreshes_record_the_workgroup_and_a_change_is_promoted(tmp_path, monkeypatch):
    """Full and quick refreshes record the read; a quick update whose corpus
    is unchanged still promotes when the WorkGroup's members changed."""
    members = {"value": [1]}
    reads = []

    def read(client, name):
        """Stand in for the ParishSoft WorkGroup read (no HTTP)."""
        reads.append(name)
        return {"name": name, "found": True, "family_duids": list(members["value"])}

    monkeypatch.setattr(refreshing, "load_reminder_workgroup", read)
    monkeypatch.setattr(refreshing, "configured_name", lambda values: NAME)
    refreshes = Refreshes(tmp_path, monkeypatch)
    assert refreshes.full.cursor["load"][EVIDENCE_KEY]["family_duids"] == [1]
    same, *_ = refreshes.quick()
    assert same.state == "unchanged"
    members["value"] = [1, 2]
    changed, *_ = refreshes.quick()
    assert changed.state == "promoted"
    assert changed.cursor["load"][EVIDENCE_KEY]["family_duids"] == [1, 2]
    assert SourceCurrent.objects.get().snapshot_id == changed.pk
    assert reads == [NAME, NAME, NAME]


def test_migration_check_refuses_the_old_guard_and_admits_the_new():
    """The frozen file's DO block fails against the pre-#861 pointer guard."""
    from pathlib import Path

    from django.db import DatabaseError, connection

    text = (
        Path(refreshing.__file__).resolve().parents[1]
        / "schema/migrations/0015_reminder_workgroup_setting.sql"
    ).read_text(encoding="utf-8")
    start = text.index("CREATE OR REPLACE FUNCTION")
    body = text[start : text.index("END $$;", start) + len("END $$;")]
    check = text[text.index("DO $check$") : text.index("$check$;") + len("$check$;")]
    new = "'artwork','reminder_workgroup','ministry_duids'"
    old = body.replace(new, "'artwork','ministry_duids'")
    assert old != body
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(check)  # The installed (new) guard passes.
        with (
            pytest.raises(DatabaseError, match="was not replaced"),
            transaction.atomic(),
        ):
            cursor.execute(old)
            cursor.execute(check)
        cursor.execute(check)  # Rolled back to the new guard.

"""Final-round canonical commands, closed admission and downgrade defenses."""

from contextlib import contextmanager
from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from django.db import IntegrityError, connection, transaction

from parishkit.config import ConfigError
from parishkit.stewardship.campaigns.admission import validate_installation
from parishkit.stewardship.campaigns.catchup import bind_catchup, record_catchup_failure
from parishkit.stewardship.campaigns.lifecycle import Action
from parishkit.stewardship.campaigns.models import (
    ActivationCatchUpDemand,
    CatchUpFailure,
    ScheduleDefinition,
)
from parishkit.stewardship.campaigns.read_guards import (
    CampaignReadGuard,
    DownloadConfigurationUnavailable,
    DownloadPool,
    ReadLimits,
    ReadUnavailable,
    acquire_campaign_drain,
)
from parishkit.stewardship.campaigns.resolutions import (
    resolve_postclose,
    resolve_restore_hold,
)
from parishkit.stewardship.campaigns.schedules import (
    change_occurrence,
    create_occurrence,
    record_fulfillment,
    recover_occurrence,
)
from parishkit.stewardship.storage import StorageInvariantError

from .campaign_builders import (
    admit_test_work,
    campaign_clock,
    claimed_task,
    command,
    draft_campaign,
    occurrence,
    restored_runtime,
)

pytestmark = pytest.mark.django_db(transaction=True)


@contextmanager
def synthetic_campaign_state(identifier, state):
    """Load a future purge sentinel offline; never provide an application bypass."""
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute("ALTER TABLE stewardship_campaign DISABLE TRIGGER USER")
        cursor.execute(
            "UPDATE stewardship_campaign SET state=%s WHERE id=%s", [state, identifier]
        )
        cursor.execute("ALTER TABLE stewardship_campaign ENABLE TRIGGER USER")
    try:
        yield
    finally:
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute("ALTER TABLE stewardship_campaign DISABLE TRIGGER USER")
            cursor.execute(
                "UPDATE stewardship_campaign SET state='draft' WHERE id=%s",
                [identifier],
            )
            cursor.execute("ALTER TABLE stewardship_campaign ENABLE TRIGGER USER")


def reader(identifiers, **kwargs):
    """Synthetic transport and authority, with real SQL admission and cleanup."""
    return CampaignReadGuard(
        identifiers, authorize=lambda _: None, abort=lambda: None, **kwargs
    )


@pytest.mark.parametrize("state", ["purging", "purge_cleanup_failed", "purged"])
def test_read_rejects_every_destructive_state(tmp_path, state):
    """A real stored purge sentinel cannot stream, even with a permissive owner."""
    _, campaign, _ = draft_campaign(tmp_path)
    with (
        synthetic_campaign_state(campaign.pk, state),
        pytest.raises(ReadUnavailable, match="information is unavailable"),
        reader([campaign.pk]),
    ):
        pytest.fail("Destructive state was admitted")


def test_read_rejects_missing_identifiers_reuse_and_nested_transactions(tmp_path):
    """Missing members of a multi-campaign scope fail closed before serialization."""
    _, campaign, _ = draft_campaign(tmp_path)
    for identifiers in ([uuid4()], [campaign.pk, uuid4()]):
        with (
            pytest.raises(ReadUnavailable, match="information is unavailable"),
            reader(identifiers),
        ):
            pytest.fail("Incomplete scope was admitted")
    guard = reader([campaign.pk])
    with guard:
        pass
    with pytest.raises(StorageInvariantError, match="cannot be reused"), guard:
        pytest.fail("A response guard was reused")
    with (
        transaction.atomic(),
        pytest.raises(StorageInvariantError, match="must own"),
        reader([campaign.pk]),
    ):
        pytest.fail("A read borrowed an existing transaction")
    with pytest.raises(StorageInvariantError, match="owning transaction"):
        acquire_campaign_drain([campaign.pk])
    for identifiers in ([], [str(campaign.pk)], [None]):
        with pytest.raises(TypeError, match="canonical"):
            acquire_campaign_drain(identifiers)


@pytest.mark.parametrize("missing", [False, True])
def test_download_capacity_must_match_persisted_policy(tmp_path, missing):
    """An absent or mismatched deployment budget cannot allocate a download."""
    _, campaign, _ = draft_campaign(tmp_path)
    if missing:
        # Offline corruption fixture only; the application DELETE guard remains.
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute(
                "ALTER TABLE stewardship_download_policy DISABLE TRIGGER USER"
            )
            cursor.execute("DELETE FROM stewardship_download_policy")
            cursor.execute(
                "ALTER TABLE stewardship_download_policy ENABLE TRIGGER USER"
            )
    pool = DownloadPool(ReadLimits(download_capacity=5))
    try:
        with (
            pytest.raises(DownloadConfigurationUnavailable),
            reader([campaign.pk], pool=pool),
        ):
            pytest.fail("Unreconciled deployment budget was admitted")
        pool.acquire()
        pool.release()
    finally:
        if missing:
            with connection.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO stewardship_download_policy(id,capacity) VALUES(1,4)"
                )
    with reader([campaign.pk], pool=DownloadPool()) as guard:
        assert guard.db.settings_dict["CONN_MAX_AGE"] == 0
        assert guard.db.settings_dict["CONN_HEALTH_CHECKS"] is False


@pytest.mark.parametrize("value", [True, 1.0, 0, "1", -1])
def test_occurrence_and_resolution_versions_are_canonical(
    value, django_assert_num_queries
):
    """Reject coercible versions before any ORM lookup or owner callback."""
    common = dict(
        expected_version=value,
        actor_id=uuid4(),
        correlation_id=uuid4(),
        admit=admit_test_work,
    )
    with django_assert_num_queries(0):
        with pytest.raises(TypeError):
            change_occurrence(occurrence_id=uuid4(), state="skipped", **common)
        with pytest.raises(TypeError):
            recover_occurrence(occurrence_id=uuid4(), action="recovery_fail", **common)
        with pytest.raises(TypeError):
            resolve_restore_hold(
                hold_id=uuid4(),
                state="assumed_delivered",
                evidence="Reviewed",
                **common,
            )


@pytest.mark.parametrize(
    "field,value",
    [
        ("reason", None),
        ("reason", "x" * 65),
        ("reason", "private provider text"),
        ("actor_id", str(UUID(int=1))),
        ("occurrence_id", str(UUID(int=1))),
        ("fence", True),
    ],
)
def test_occurrence_metadata_is_canonical(field, value, django_assert_num_queries):
    """Identifiers, ownership and reason codes cannot silently coerce on write."""
    args = dict(
        occurrence_id=uuid4(),
        state="skipped",
        expected_version=1,
        actor_id=uuid4(),
        correlation_id=uuid4(),
        admit=admit_test_work,
    )
    with django_assert_num_queries(0), pytest.raises(TypeError):
        change_occurrence(**(args | {field: value}))


def test_create_requires_canonical_attribution(django_assert_num_queries):
    """Allocation cannot turn text attribution into a UUID only after lookup."""
    with django_assert_num_queries(0), pytest.raises(TypeError):
        create_occurrence(
            definition_id=uuid4(),
            revision_id=uuid4(),
            mode="testing",
            target="family:1",
            slot="once",
            due_at=None,
            actor_id=str(uuid4()),
            correlation_id=uuid4(),
            admit=admit_test_work,
        )


@pytest.mark.parametrize(
    "field,value",
    [
        ("reason", None),
        ("reason", "x" * 1025),
        ("obligation_key", []),
        ("mode", []),
        ("occurrence_id", str(UUID(int=1))),
        ("task_id", str(UUID(int=1))),
        ("outbox_id", str(UUID(int=1))),
    ],
)
def test_postclose_metadata_is_canonical(field, value, django_assert_num_queries):
    """Exact replay uses identical types; malformed text never raises AttributeError."""
    args = dict(
        campaign_id=uuid4(),
        mode="production",
        obligation_key="schedule:synthetic:once",
        coverage={},
        reason="Reviewed",
        actor_id=uuid4(),
        correlation_id=uuid4(),
        admit=admit_test_work,
    )
    with django_assert_num_queries(0), pytest.raises(TypeError):
        resolve_postclose(**(args | {field: value}))


@pytest.mark.parametrize(
    "field,value",
    [
        ("hold_id", str(UUID(int=1))),
        ("recovery_occurrence_id", str(UUID(int=1))),
        ("state", []),
        ("evidence", None),
        ("evidence", "x" * 1025),
    ],
)
def test_restore_resolution_metadata_is_canonical(
    field, value, django_assert_num_queries
):
    """Review decisions reject noncanonical identity, outcomes and evidence first."""
    args = dict(
        hold_id=uuid4(),
        expected_version=1,
        state="assumed_delivered",
        evidence="Reviewed",
        actor_id=uuid4(),
        correlation_id=uuid4(),
        admit=admit_test_work,
    )
    with django_assert_num_queries(0), pytest.raises(TypeError):
        resolve_restore_hold(**(args | {field: value}))


def test_occurrence_outcome_and_ownership_misuse_has_typed_errors(tmp_path):
    """Semantic evidence and state metadata fail before SQL constraint fallbacks."""
    _, _, actor = draft_campaign(tmp_path)
    definition = ScheduleDefinition.objects.get()
    with campaign_clock(definition.current_revision.due_at):
        row = occurrence(definition, actor)
        common = dict(
            occurrence_id=row.pk,
            actor_id=actor,
            correlation_id=uuid4(),
            admit=admit_test_work,
        )
        for disposition in ("delivered", "coalesced"):
            with pytest.raises(StorageInvariantError, match="exact outcome"):
                record_fulfillment(**common, disposition=disposition)
        for metadata in (
            {"fence": 1},
            {"task_id": uuid4()},
            {"replacement_id": uuid4()},
        ):
            with pytest.raises(
                StorageInvariantError, match="ownership metadata|replacement"
            ):
                change_occurrence(
                    **common, expected_version=1, state="skipped", **metadata
                )
        with pytest.raises(StorageInvariantError, match="replacement"):
            change_occurrence(**common, expected_version=1, state="coalesced")
        row.refresh_from_db()
        assert row.state == "pending" and row.version == 1


@pytest.mark.parametrize("mutation", ["added", "removed"])
def test_structural_preflight_rejects_divergent_key_sets(tmp_path, mutation):
    """Schema drift is a typed invalid candidate, not a stuck KeyError receipt."""
    store, campaign, actor = draft_campaign(tmp_path)
    with campaign_clock(campaign.active_configuration.starts_at - timedelta(days=1)):
        command(campaign, actor, Action.ACTIVATE)
    document = store.active().document()
    values = document["sections"]["campaigns"][0]["values"]
    if mutation == "added":
        values["future_structural_field"] = True
    else:
        del values["start_date"]
    with pytest.raises(ConfigError, match="structural settings are locked"):
        validate_installation(document)


def test_catchup_failure_obeys_restore_gate(tmp_path):
    """Failure receipts mutate progress metadata, so restore freezes them too."""
    _, campaign, actor = draft_campaign(tmp_path)
    with campaign_clock(campaign.active_configuration.starts_at):
        command(campaign, actor, Action.ACTIVATE)
    demand = ActivationCatchUpDemand.objects.get()
    task = claimed_task("activation_catchup", demand.pk, actor)
    bind_catchup(
        demand_id=demand.pk,
        task_root_id=task.root_id,
        source_snapshot_id=uuid4(),
        actor_id=actor,
        correlation_id=uuid4(),
        admit=admit_test_work,
    )
    demand.refresh_from_db()
    args = dict(
        demand_id=demand.pk,
        request_id=uuid4(),
        expected_version=demand.version,
        task_id=task.run_id,
        fence=task.fence,
        code="enumeration_failed",
        actor_id=actor,
        correlation_id=uuid4(),
        admit=admit_test_work,
    )
    with (
        restored_runtime(campaign.active_configuration.starts_at),
        pytest.raises(IntegrityError, match="exact fenced evidence"),
    ):
        record_catchup_failure(**args)
    assert not CatchUpFailure.objects.exists()
    assert record_catchup_failure(**args).expected_version == demand.version


def test_runtime_helpers_pin_search_path():
    """Direct invocations and trigger entry points use the same trusted schema path."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT proname,proconfig FROM pg_proc WHERE proname = ANY(%s)",
            [
                [
                    "stewardship_activation_global_v1",
                    "stewardship_campaign_now_v1",
                    "stewardship_campaign_quiet_v1",
                ]
            ],
        )
        rows = cursor.fetchall()
    assert len(rows) == 3
    assert all(
        "search_path=pg_catalog, public, pg_temp" in config for _, config in rows
    )

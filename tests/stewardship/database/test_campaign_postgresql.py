"""Real installer/SQL campaign isolation, immutable history and concurrent intent."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import UUID, uuid4

import pytest
from django.db import IntegrityError, close_old_connections, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.db.models import F

from parishkit.stewardship.accounts.configuration_installation import install_request
from parishkit.stewardship.accounts.configuration_models import (
    AppliedConfigurationVersion,
)
from parishkit.stewardship.accounts.configuration_requests import record_request
from parishkit.stewardship.accounts.configuration_snapshots import (
    is_prepared,
    prepare_snapshot,
)
from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.audit.models import AuditEvent
from parishkit.stewardship.campaigns.models import (
    Campaign,
    CampaignConfiguration,
    ScheduleRevision,
)
from parishkit.stewardship.storage import StorageInvariantError

from ..campaign_factory import campaign, schedule
from ..configuration_factory import configuration_version
from .test_policy_postgresql import change, initialized

pytestmark = pytest.mark.django_db(transaction=True)


def add_draft(store, version, actor, row=None):
    """Use the ordinary configuration request and activation, never ORM draft CRUD."""
    row = campaign() if row is None else row
    mail = schedule(row["id"])
    result = change(
        store,
        version,
        actor,
        [
            {"operation": "add", "section": "campaigns", **row},
            {"operation": "add", "section": "schedules", **mail},
        ],
    )
    return result, row, mail


def test_draft_activation_history_and_parish_timezone(tmp_path):
    """One atomic activation selects a draft; later parish edits cannot rebucket it."""
    store, root, actor = initialized(tmp_path)
    result, row, mail = add_draft(store, root, actor)
    assert result.state == "applied"
    runtime = SystemConfiguration.objects.get()
    assert runtime.current_campaign_id == UUID(row["id"])
    draft = Campaign.objects.select_related("active_configuration").get()
    assert draft.pk == runtime.current_campaign_id and draft.version == 1
    assert (
        draft.active_configuration.configuration_id == runtime.active_configuration_id
    )
    original = draft.active_configuration
    assert is_prepared(result.applied_digest)
    version = configuration_version(
        AppliedConfigurationVersion.objects.get(
            pk=result.applied_version_id
        ).canonical_document
    )
    parish = version.document()["sections"]["parish"][0]
    edited = change(
        store,
        version,
        actor,
        [
            {
                "operation": "update",
                "section": "parish",
                "id": parish["id"],
                "values": {"timezone": "America/Chicago"},
            }
        ],
    )
    assert edited.state == "applied" and is_prepared(edited.applied_digest)
    draft.refresh_from_db()
    assert (
        draft.version == 2 and draft.active_configuration.timezone == "America/New_York"
    )
    assert draft.active_configuration.starts_at == original.starts_at
    assert (
        CampaignConfiguration.objects.count() == 2
        and ScheduleRevision.objects.count() == 2
    )
    assert (
        AuditEvent.objects.filter(
            event_type="campaign_configured", campaign_reference=draft.pk
        ).count()
        == 2
    )


def test_preparation_is_not_creation_and_retry_is_idempotent(tmp_path):
    """Prepared candidates do not become current or create campaign audit effects."""
    store, root, actor = initialized(tmp_path)
    document = root.document()
    row = campaign()
    document.update(version_id=str(uuid4()), predecessor_digest=root.digest)
    document["sections"]["campaigns"] = [row]
    version = configuration_version(document)
    prepare_snapshot(version, actor_id=actor, correlation_id=uuid4())
    assert is_prepared(version.digest)
    assert not Campaign.objects.exists()
    assert SystemConfiguration.objects.get().current_campaign_id is None
    result, _, _ = add_draft(store, root, actor, row)
    assert result.state == "applied"
    count = AuditEvent.objects.count()
    assert (
        install_request(
            store, request_id=result.request_id, correlation_id=uuid4()
        ).state
        == "applied"
    )
    assert Campaign.objects.get().version == 1 and AuditEvent.objects.count() == count


@pytest.mark.parametrize(
    "mutation", ["state", "delete", "pointer", "configuration", "version"]
)
def test_runtime_raw_writes_are_denied(tmp_path, mutation):
    """Neither direct SQL nor a forged optimistic version bypasses activation
    evidence.
    """
    store, root, actor = initialized(tmp_path)
    add_draft(store, root, actor)
    with (
        pytest.raises(IntegrityError),
        transaction.atomic(),
        connection.cursor() as cursor,
    ):
        if mutation == "state":
            cursor.execute(
                "UPDATE stewardship_campaign SET state='active', version=version+1"
            )
        elif mutation == "delete":
            cursor.execute("DELETE FROM stewardship_campaign")
        elif mutation == "pointer":
            cursor.execute(
                "UPDATE stewardship_system_configuration "
                "SET current_campaign_id=NULL, version=version+1"
            )
        elif mutation == "configuration":
            cursor.execute(
                "UPDATE stewardship_campaign "
                "SET active_configuration_id=%s, version=version+1",
                [uuid4()],
            )
        else:
            Campaign.objects.update(version=F("version") + 1)
    assert Campaign.objects.get().state == "draft"


@pytest.mark.parametrize("model", [CampaignConfiguration, ScheduleRevision])
def test_configuration_is_immutable(tmp_path, model):
    """Historical references cannot be changed through raw SQL or the ORM."""
    store, root, actor = initialized(tmp_path)
    add_draft(store, root, actor)
    with pytest.raises(StorageInvariantError):
        model.objects.all().delete()
    with (
        pytest.raises(IntegrityError),
        transaction.atomic(),
        connection.cursor() as cursor,
    ):
        cursor.execute(f'DELETE FROM "{model._meta.db_table}"')


def test_invalid_creation_and_timezone_fail_before_manifest_change(tmp_path):
    """Unsupported lifecycle actions become terminal invalid-candidate receipts."""
    store, root, actor = initialized(tmp_path)
    failed, _, _ = add_draft(store, root, actor, campaign(timezone="America/Chicago"))
    assert failed.state == "failed" and failed.failure_code == "invalid_candidate"
    assert SystemConfiguration.objects.get().active_configuration_id == root.version_id
    assert not Campaign.objects.exists()
    applied, _, _ = add_draft(store, root, actor)
    version = configuration_version(
        AppliedConfigurationVersion.objects.get(
            pk=applied.applied_version_id
        ).canonical_document
    )
    other = campaign(name="Other campaign")
    failed = change(
        store, version, actor, [{"operation": "add", "section": "campaigns", **other}]
    )
    assert failed.state == "failed" and failed.failure_code == "invalid_candidate"
    assert (
        SystemConfiguration.objects.get().active_configuration_id
        == applied.applied_version_id
    )
    assert Campaign.objects.count() == 1


def test_draft_edits_and_schedule_removal_are_atomic(tmp_path):
    """A timezone/end-date change and affected schedule removal share one YAML
    version.
    """
    store, root, actor = initialized(tmp_path)
    applied, row, mail = add_draft(store, root, actor)
    version = configuration_version(
        AppliedConfigurationVersion.objects.get(
            pk=applied.applied_version_id
        ).canonical_document
    )
    result = change(
        store,
        version,
        actor,
        [
            {
                "operation": "update",
                "section": "campaigns",
                "id": row["id"],
                "values": {"timezone": "America/Chicago", "end_date": "2026-10-20"},
            },
            {"operation": "remove", "section": "schedules", "id": mail["id"]},
        ],
    )
    assert result.state == "applied" and is_prepared(result.applied_digest)
    draft = Campaign.objects.get()
    assert draft.active_configuration.timezone == "America/Chicago"
    assert not ScheduleRevision.objects.filter(
        configuration_id=result.applied_version_id
    ).exists()
    assert ScheduleRevision.objects.filter(
        configuration_id=applied.applied_version_id
    ).exists()
    # Retyping a removed logical UUID is refused before YAML activation.
    latest = configuration_version(
        AppliedConfigurationVersion.objects.get(
            pk=result.applied_version_id
        ).canonical_document
    )
    mail["values"].update(kind="daily_digest", date=None)
    failed = change(
        store, latest, actor, [{"operation": "add", "section": "schedules", **mail}]
    )
    assert failed.state == "failed" and failed.failure_code == "invalid_candidate"


def test_competing_draft_requests_have_one_winner(tmp_path):
    """Independent database connections may stage, but only the exact-base winner
    applies.
    """
    store, root, actor = initialized(tmp_path)
    barrier = Barrier(2)

    def stage(number):
        """Use a genuinely independent PostgreSQL connection and fresh request key."""
        close_old_connections()
        try:
            row = campaign(name=f"Campaign {number}")
            barrier.wait(timeout=10)
            return record_request(
                base_digest=root.digest,
                actor_id=actor,
                request_key=uuid4(),
                correlation_id=uuid4(),
                patch=[{"operation": "add", "section": "campaigns", **row}],
            )
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as executor:
        requests = list(executor.map(stage, [1, 2]))
    first = install_request(
        store, request_id=requests[0].request_id, correlation_id=uuid4()
    )
    second = install_request(
        store, request_id=requests[1].request_id, correlation_id=uuid4()
    )
    assert first.state == "applied" and second.failure_code == "stale_base"
    assert Campaign.objects.count() == 1


def test_activation_failure_rolls_back_pointer_and_campaign(tmp_path):
    """A downstream failure cannot commit half an activation; retry reuses the
    candidate.
    """
    store, root, actor = initialized(tmp_path)
    with connection.cursor() as cursor:
        cursor.execute("""CREATE FUNCTION fail_campaign_audit()
            RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN IF NEW.event_type = 'campaign_configured' THEN
                RAISE EXCEPTION 'test failure'; END IF;
            RETURN NEW; END $$;
            CREATE TRIGGER fail_campaign_audit BEFORE INSERT ON stewardship_audit_event
            FOR EACH ROW EXECUTE FUNCTION fail_campaign_audit();""")
    row = campaign()
    request = record_request(
        base_digest=root.digest,
        actor_id=actor,
        request_key=uuid4(),
        correlation_id=uuid4(),
        patch=[{"operation": "add", "section": "campaigns", **row}],
    )
    try:
        with pytest.raises(Exception, match="test failure"):
            install_request(
                store, request_id=request.request_id, correlation_id=uuid4()
            )
        assert not Campaign.objects.exists()
        assert (
            SystemConfiguration.objects.get().active_configuration_id == root.version_id
        )
    finally:
        with connection.cursor() as cursor:
            cursor.execute(
                "DROP TRIGGER fail_campaign_audit ON stewardship_audit_event; "
                "DROP FUNCTION fail_campaign_audit();"
            )
    assert (
        install_request(
            store, request_id=request.request_id, correlation_id=uuid4()
        ).state
        == "applied"
    )
    assert Campaign.objects.count() == 1


def test_empty_reverse_and_reapply_and_populated_refusal(tmp_path):
    """Reversal works on an empty subset and refuses loss of durable campaign
    history.
    """
    leaves = MigrationExecutor(connection).loader.graph.leaf_nodes()
    target = [("stewardship_campaigns", "0001_initial")]
    MigrationExecutor(connection).migrate(target)
    MigrationExecutor(connection).migrate(leaves)
    store, root, actor = initialized(tmp_path)
    add_draft(store, root, actor)
    try:
        with pytest.raises(IntegrityError, match="Campaign history prevents"):
            MigrationExecutor(connection).migrate(target)
        assert Campaign.objects.count() == 1
        with pytest.raises(IntegrityError), transaction.atomic():
            Campaign.objects.update(state="active", version=F("version") + 1)
    finally:
        MigrationExecutor(connection).migrate(leaves)


def test_offline_recovery_preserves_campaign_schema(tmp_path):
    """The new recovery discriminator retains campaigns and revokes old sessions."""
    from parishkit.stewardship.accounts.operator_recovery import recover_admin
    from parishkit.stewardship.accounts.request_models import ConfigurationChangeRequest

    from .test_recovery_postgresql import arguments, session

    store, root, actor = initialized(tmp_path)
    add_draft(store, root, actor)
    original = Campaign.objects.get().active_configuration.values
    portal = session()
    kwargs = arguments()
    result = recover_admin(store, **kwargs)
    assert result.state == "applied" and is_prepared(result.applied_digest)
    assert (
        ConfigurationChangeRequest.objects.get(pk=result.request_id).request_schema
        == "operator-recovery-patch-v2"
    )
    assert Campaign.objects.get().active_configuration.values == original
    portal.refresh_from_db()
    assert portal.revoked_at is not None
    assert recover_admin(store, **kwargs).request_id == result.request_id

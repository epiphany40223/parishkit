"""Real installer/SQL campaign isolation, immutable history and concurrent intent."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
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

from ..campaign_factory import campaign, financial, schedule
from ..configuration_factory import configuration_version
from ..test_campaign_configuration import document as campaign_document
from .campaign_builders import add_draft, change, initialized

pytestmark = pytest.mark.django_db(transaction=True)


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
        == 1
    )
    assert (
        AuditEvent.objects.filter(
            event_type="campaign_reprojected", campaign_reference=draft.pk
        ).count()
        == 1
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
    from importlib import import_module

    guard = import_module(
        "parishkit.stewardship.campaigns.migrations.0002_campaign_guards"
    )
    with (
        pytest.raises(IntegrityError, match="Campaign history prevents"),
        transaction.atomic(),
        connection.schema_editor() as editor,
    ):
        guard.restore_policy_schema(None, editor)
    try:
        with pytest.raises(IntegrityError, match="Schedule history prevents"):
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


def test_bootstrap_campaign_activates_and_policy_guards_survive(tmp_path):
    """Bootstrap INSERT stays empty; its atomic activation UPDATE creates the draft."""
    from parishkit.stewardship.accounts.authority import AuthorityStore
    from parishkit.stewardship.accounts.configuration_installation import (
        prepare_initial_configuration,
    )
    from parishkit.stewardship.accounts.configuration_schema import validate_sections

    version = configuration_version(campaign_document())
    store = AuthorityStore(tmp_path, validate_sections)
    actor = uuid4()
    prepare_initial_configuration(
        store,
        version,
        testing_recipient="test@example.org",
        actor_id=actor,
        correlation_id=uuid4(),
    )
    runtime = SystemConfiguration.objects.get()
    assert runtime.current_campaign_id == Campaign.objects.get().pk
    assert (
        Campaign.objects.get().active_configuration.configuration_id
        == version.version_id
    )
    assert is_prepared(version.digest)
    with connection.cursor() as cursor:
        for name in (
            "stewardship_policy_projection_v1",
            "stewardship_policy_complete_v1",
        ):
            cursor.execute("SELECT pg_get_functiondef(%s::regprocedure)", [name + "()"])
            assert "campaign-foundation-v3" in cursor.fetchone()[0]
    parish = version.document()["sections"]["parish"][0]
    assert (
        change(
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
        ).state
        == "applied"
    )


def candidate(root):
    """Prepare fresh envelope identity while retaining the synthetic root policy."""
    document = root.document()
    document.update(version_id=str(uuid4()), predecessor_digest=root.digest)
    row = campaign()
    document["sections"]["campaigns"] = [row]
    document["sections"]["schedules"] = [schedule(row["id"])]
    return configuration_version(document)


@pytest.mark.parametrize(
    "model,field",
    [
        (CampaignConfiguration, "values"),
        (CampaignConfiguration, "name"),
        (CampaignConfiguration, "start_date"),
        (CampaignConfiguration, "starts_at"),
        (CampaignConfiguration, "ends_at"),
        (ScheduleRevision, "campaign_id"),
        (ScheduleRevision, "kind"),
        (ScheduleRevision, "due_at"),
    ],
)
def test_forged_projection_insert_is_rejected(tmp_path, monkeypatch, model, field):
    """Raw SQL after parsing cannot replace canonical or resolved projection fields."""
    from psycopg.types.json import Jsonb

    _, root, actor = initialized(tmp_path)
    version = candidate(root)

    def insert_forged(**kwargs):
        """Bypass Python model validation to exercise the database INSERT trigger."""
        attrs = kwargs | {"id": uuid4(), "configuration_id": kwargs["configuration"].pk}
        del attrs["configuration"]
        if field in {"starts_at", "ends_at", "due_at"}:
            attrs[field] += timedelta(seconds=1)
        elif field == "values":
            attrs[field] = attrs[field] | {"modules": []}
        elif field == "campaign_id":
            attrs[field] = uuid4()
        elif field == "start_date":
            attrs[field] = "2026-10-02"
        else:
            attrs[field] = "forged"
        attrs["values"] = Jsonb(attrs["values"])
        columns = ", ".join(connection.ops.quote_name(name) for name in attrs)
        placeholders = ", ".join(["%s"] * len(attrs))
        with connection.cursor() as cursor:
            cursor.execute(
                f'INSERT INTO "{model._meta.db_table}" ({columns}) '
                f"VALUES ({placeholders})",
                list(attrs.values()),
            )

    monkeypatch.setattr(model.objects, "create", insert_forged)
    with pytest.raises(IntegrityError):
        prepare_snapshot(version, actor_id=actor, correlation_id=uuid4())
    assert not AppliedConfigurationVersion.objects.filter(
        pk=version.version_id
    ).exists()


@pytest.mark.parametrize("omit", ["campaign", "schedule"])
def test_missing_projections_fail_at_commit(tmp_path, monkeypatch, omit):
    """Deferred completeness cannot be skipped by an incomplete materializer."""
    from parishkit.stewardship.campaigns import projections

    _, root, actor = initialized(tmp_path)
    version = candidate(root)
    if omit == "campaign":
        monkeypatch.setattr(projections, "prepare_campaigns", lambda *_: None)
    else:
        monkeypatch.setattr(ScheduleRevision.objects, "create", lambda **_: None)
    with pytest.raises(IntegrityError, match="Campaign projections are incomplete"):
        prepare_snapshot(version, actor_id=actor, correlation_id=uuid4())
    assert not AppliedConfigurationVersion.objects.filter(
        pk=version.version_id
    ).exists()


@pytest.mark.parametrize("model", [CampaignConfiguration, ScheduleRevision])
def test_raw_projection_update_denied(tmp_path, model):
    """Both immutable tables reject UPDATE, not only DELETE."""
    store, root, actor = initialized(tmp_path)
    add_draft(store, root, actor)
    with (
        pytest.raises(IntegrityError),
        transaction.atomic(),
        connection.cursor() as cursor,
    ):
        cursor.execute(f'UPDATE "{model._meta.db_table}" SET values=values')


@pytest.mark.parametrize(
    "zone,local",
    [
        ("America/New_York", "2026-03-08T02:30:00"),
        ("America/New_York", "2026-11-01T01:30:00"),
        ("Australia/Lord_Howe", "2026-10-04T02:15:00"),
        ("Australia/Lord_Howe", "2026-04-05T01:45:00"),
        ("Pacific/Apia", "2011-12-30T00:00:00"),
        ("Pacific/Apia", "2011-12-30T23:59:59.999999"),
        ("America/Havana", "2026-03-08T00:30:00"),
        ("America/Havana", "2026-11-01T00:30:00"),
        ("UTC", "2026-01-01T00:00:00"),
    ],
)
def test_sql_resolver_matches_canonical_gap_fold_policy(zone, local):
    """Raw-write defense agrees on ordinary, half-hour and whole-day transitions."""
    from parishkit.stewardship.campaigns.intervals import resolve_local

    wall = datetime.fromisoformat(local)
    with connection.cursor() as cursor:
        cursor.execute("SELECT stewardship_resolve_local_v1(%s, %s)", [wall, zone])
        assert cursor.fetchone()[0] == resolve_local(wall, zone)


def test_direct_concurrent_schedule_identity_is_serialized(tmp_path):
    """Raw writers cannot race two incompatible bindings into immutable history."""
    from parishkit.stewardship.accounts.configuration_models import (
        AppliedIntegration,
        Parish,
    )
    from parishkit.stewardship.accounts.configuration_snapshots import (
        _digest,
        _normalized,
    )
    from parishkit.stewardship.accounts.policy_projections import prepare_policy
    from parishkit.stewardship.campaigns.configuration import (
        campaign_values,
        schedule_values,
    )

    _, root, actor = initialized(tmp_path)
    row, identifier, barrier = campaign(), str(uuid4()), Barrier(2)

    def insert(kind):
        """Construct complete raw projections without preparation's advisory lock."""
        close_old_connections()
        document = root.document()
        document.update(version_id=str(uuid4()), predecessor_digest=root.digest)
        mail = schedule(
            row["id"], kind=kind, date=None if kind == "daily_digest" else "2026-10-01"
        )
        mail["id"] = identifier
        document["sections"].update(campaigns=[row], schedules=[mail])
        version = configuration_version(document)
        attribution = dict(actor_id=actor, correlation_id=uuid4())
        try:
            with transaction.atomic():
                snapshot = AppliedConfigurationVersion.objects.create(
                    id=version.version_id,
                    digest=version.digest,
                    schema_version=1,
                    predecessor_id=root.version_id,
                    canonical_document=version.document(),
                    normalized_digest=_digest(_normalized(version.document())),
                    validation_schema="campaign-foundation-v3",
                    **attribution,
                )
                for model in (Parish, AppliedIntegration):
                    for values in model.objects.filter(
                        configuration_id=root.version_id
                    ).values():
                        values.update(
                            id=uuid4(), configuration_id=snapshot.pk, **attribution
                        )
                        model.objects.create(**values)
                prepare_policy(
                    snapshot, document["sections"]["login_rules"], attribution
                )
                values, interval = row["values"], campaign_values(row["values"])
                CampaignConfiguration.objects.create(
                    configuration=snapshot,
                    record_id=row["id"],
                    values=values,
                    **{
                        key: values[key]
                        for key in ("name", "timezone", "start_date", "end_date")
                    },
                    starts_at=interval.start,
                    ends_at=interval.end,
                    **attribution,
                )
                barrier.wait(timeout=10)
                ScheduleRevision.objects.create(
                    configuration=snapshot,
                    record_id=identifier,
                    values=mail["values"],
                    campaign_id=row["id"],
                    kind=kind,
                    due_at=schedule_values(mail["values"], values),
                    **attribution,
                )
            return "committed"
        except IntegrityError as error:
            assert "Logical schedule identity is immutable" in str(error)
            return "rejected"
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(insert, ["initial", "daily_digest"]))
    assert sorted(results) == ["committed", "rejected"]
    assert ScheduleRevision.objects.filter(record_id=identifier).count() == 1


@pytest.mark.parametrize(
    "modules", [[], ["unknown"], ["census", "census"], ["ministry", "census"]]
)
def test_raw_canonical_header_cannot_admit_invalid_modules(tmp_path, modules):
    """The SQL module check is independent of the Python document validator."""
    from parishkit.stewardship.campaigns.configuration import campaign_values

    _, root, actor = initialized(tmp_path)
    version = candidate(root)
    document = version.document()
    row = document["sections"]["campaigns"][0]
    interval = campaign_values(row["values"])
    row["values"]["modules"] = modules
    with (
        pytest.raises(IntegrityError, match="Invalid indexed campaign projection"),
        transaction.atomic(),
    ):
        snapshot = AppliedConfigurationVersion.objects.create(
            id=uuid4(),
            digest="b" * 64,
            normalized_digest="c" * 64,
            schema_version=1,
            validation_schema="campaign-foundation-v3",
            predecessor_id=root.version_id,
            canonical_document=document,
        )
        CampaignConfiguration.objects.create(
            configuration=snapshot,
            record_id=row["id"],
            values=row["values"],
            **{
                key: row["values"][key]
                for key in ("name", "timezone", "start_date", "end_date")
            },
            starts_at=interval.start,
            ends_at=interval.end,
        )


def raw_campaign_snapshot(root, row, mails):
    """Insert complete projections without the Python document validation layer."""
    from parishkit.stewardship.accounts.configuration_models import (
        AppliedIntegration,
        Parish,
    )
    from parishkit.stewardship.accounts.policy_projections import prepare_policy
    from parishkit.stewardship.campaigns.intervals import (
        campaign_interval,
        resolve_local,
    )

    document = root.document()
    document.update(version_id=str(uuid4()), predecessor_digest=root.digest)
    document["sections"].update(campaigns=[row], schedules=mails)
    snapshot = AppliedConfigurationVersion.objects.create(
        id=document["version_id"],
        digest=uuid4().hex * 2,
        normalized_digest=uuid4().hex * 2,
        schema_version=1,
        validation_schema="campaign-foundation-v3",
        predecessor_id=root.version_id,
        canonical_document=document,
    )
    for model in (Parish, AppliedIntegration):
        for values in model.objects.filter(configuration_id=root.version_id).values():
            values.update(id=uuid4(), configuration_id=snapshot.pk)
            model.objects.create(**values)
    prepare_policy(snapshot, document["sections"]["login_rules"], {})
    values = row["values"]
    interval = campaign_interval(
        datetime.fromisoformat(values["start_date"]).date(),
        datetime.fromisoformat(values["end_date"]).date(),
        values["timezone"],
    )
    CampaignConfiguration.objects.create(
        configuration=snapshot,
        record_id=row["id"],
        values=values,
        **{key: values[key] for key in ("name", "timezone", "start_date", "end_date")},
        starts_at=interval.start,
        ends_at=interval.end,
    )
    for mail in mails:
        attrs = mail["values"]
        due = (
            None
            if attrs["date"] is None
            else resolve_local(
                datetime.fromisoformat(attrs["date"] + "T" + attrs["time"]),
                values["timezone"],
            )
        )
        ScheduleRevision.objects.create(
            configuration=snapshot,
            record_id=mail["id"],
            values=attrs,
            campaign_id=row["id"],
            kind=attrs["kind"],
            due_at=due,
        )
    return snapshot


@pytest.mark.parametrize(
    "period", [financial(), financial(start="2028-02-29", end="2029-02-27")]
)
def test_financial_period_real_installation(tmp_path, period):
    """SQL accepts the same full-year and leap-day convention as Python."""
    store, root, actor = initialized(tmp_path)
    result, _, _ = add_draft(
        store, root, actor, campaign(modules=["financial"], financial=period)
    )
    assert result.state == "applied" and is_prepared(result.applied_digest)


@pytest.mark.parametrize("field", ["end", "comparison_end"])
def test_raw_financial_period_rejected(tmp_path, field):
    """A forged canonical header cannot make SQL accept a partial pledge year."""
    _, root, _ = initialized(tmp_path)
    period = financial()
    period[field] = (
        (datetime.fromisoformat(period[field]) - timedelta(days=1)).date().isoformat()
    )
    row = campaign(modules=["financial"], financial=period)
    with (
        pytest.raises(IntegrityError, match="Invalid financial period"),
        transaction.atomic(),
    ):
        raw_campaign_snapshot(root, row, [])


@pytest.mark.parametrize(
    "date,time", [("2026-09-30", "23:59:59"), ("2026-11-01", "00:00:00")]
)
def test_raw_schedule_outside_interval_rejected(tmp_path, date, time):
    """Even correctly resolved UTC instants must belong to the owning campaign."""
    _, root, _ = initialized(tmp_path)
    row = campaign()
    with (
        pytest.raises(IntegrityError, match="outside campaign interval"),
        transaction.atomic(),
    ):
        raw_campaign_snapshot(root, row, [schedule(row["id"], date=date, time=time)])


@pytest.mark.parametrize(
    "case",
    [
        "duplicate_initial",
        "reminder_only",
        "early_reminder",
        "same_due",
        "daily_digest",
        "weekly_digest",
        "valid",
    ],
)
def test_raw_schedule_relationships(tmp_path, case):
    """Deferred checks reject complete but semantically contradictory schedules."""
    _, root, _ = initialized(tmp_path)
    row = campaign()
    initial = schedule(row["id"], time="00:00:00" if case == "valid" else "09:00:00")
    reminder = schedule(row["id"], kind="reminder", date="2026-10-02")
    mails = [initial, reminder]
    if case == "duplicate_initial":
        mails.append(schedule(row["id"], time="10:00:00"))
    elif case == "reminder_only":
        mails = [reminder]
    elif case in {"early_reminder", "same_due"}:
        reminder["values"].update(
            date="2026-10-01",
            time="08:00:00" if case == "early_reminder" else "09:00:00",
        )
    elif case in {"daily_digest", "weekly_digest"}:
        mails = [
            schedule(
                row["id"],
                kind=case,
                date=None,
                weekday=0 if case == "weekly_digest" else None,
            )
            for _ in range(2)
        ]
    else:
        mails.append(schedule(row["id"], kind="reminder", date="2026-10-03"))
    if case == "valid":
        with transaction.atomic():
            snapshot = raw_campaign_snapshot(root, row, mails)
        assert snapshot.schedule_revisions.count() == 3
    else:
        with (
            pytest.raises(IntegrityError, match="Invalid schedule relationships"),
            transaction.atomic(),
        ):
            raw_campaign_snapshot(root, row, mails)


def test_temporary_campaign_gate_keeps_request_retryable(tmp_path, monkeypatch):
    """A restore hold is operational, not a terminal rejection of valid intent."""
    from parishkit.stewardship.accounts.request_models import (
        ConfigurationRequestCheckpoint,
    )
    from parishkit.stewardship.campaigns.admission import CampaignAdmissionUnavailable

    store, root, actor = initialized(tmp_path)
    row = campaign()
    request = record_request(
        base_digest=root.digest,
        patch=[{"operation": "add", "section": "campaigns", **row}],
        actor_id=actor,
        request_key=uuid4(),
        correlation_id=uuid4(),
    )
    runtime = SystemConfiguration.objects.get()
    runtime.restore_review_required = True
    with monkeypatch.context() as patch:
        patch.setattr(SystemConfiguration.objects, "first", lambda: runtime)
        with pytest.raises(CampaignAdmissionUnavailable):
            install_request(
                store, request_id=request.request_id, correlation_id=uuid4()
            )
    assert (
        ConfigurationRequestCheckpoint.objects.filter(request_id=request.request_id)
        .latest("sequence")
        .state
        == "staged"
    )
    assert (
        install_request(
            store, request_id=request.request_id, correlation_id=uuid4()
        ).state
        == "applied"
    )


def test_timezone_rule_drift_fails_closed_without_rewriting_history(
    tmp_path, monkeypatch
):
    """Restoring matching rules re-verifies the unchanged immutable projection."""
    from parishkit.stewardship.campaigns import projections
    from parishkit.stewardship.campaigns.domain import UTCInterval

    store, root, actor = initialized(tmp_path)
    result, _, _ = add_draft(store, root, actor)
    original = projections.campaign_values

    def drift(values):
        """Simulate an interpreter timezone-data update affecting stored boundaries."""
        interval = original(values)
        return UTCInterval(interval.start + timedelta(hours=1), interval.end)

    with monkeypatch.context() as patch:
        patch.setattr(projections, "campaign_values", drift)
        assert not is_prepared(result.applied_digest)
    assert is_prepared(result.applied_digest)


@pytest.mark.parametrize("local", ["2026-01-15T12:00:00", "2026-07-15T12:00:00"])
def test_database_timezone_catalog_resolves_every_frozen_name(local):
    """All names, including omitted aliases and ambiguous abbreviations, agree."""
    from parishkit.stewardship.campaigns.intervals import resolve_local
    from parishkit.stewardship.schema_primitives import timezone_names

    wall = datetime.fromisoformat(local)
    names = sorted(timezone_names())
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT zone, stewardship_resolve_local_v1(%s, zone) "
            "FROM unnest(%s::text[]) zone",
            [wall, names],
        )
        actual = dict(cursor.fetchall())
    assert actual == {name: resolve_local(wall, name) for name in names}


@pytest.mark.parametrize("confirmed", [False, True])
def test_raw_financial_overlap_requires_confirmation(tmp_path, confirmed):
    """Valid year length does not imply consent to overlap the campaign interval."""
    _, root, _ = initialized(tmp_path)
    row = campaign(
        modules=["financial"],
        financial=financial(
            start="2026-01-01",
            end="2026-12-31",
            overlap_confirmed=confirmed,
        ),
    )
    if confirmed:
        with transaction.atomic():
            raw_campaign_snapshot(root, row, [])
    else:
        with (
            pytest.raises(IntegrityError, match="overlap requires confirmation"),
            transaction.atomic(),
        ):
            raw_campaign_snapshot(root, row, [])


@pytest.mark.parametrize("affected", ["campaign", "schedule"])
def test_database_rule_drift_invalidates_retained_lineage(tmp_path, affected):
    """SQL-only drift must fail verification, even when Python rules are unchanged."""
    from django.test.utils import CaptureQueriesContext

    store, root, actor = initialized(tmp_path)
    first, row, mail = add_draft(store, root, actor)
    version = configuration_version(
        AppliedConfigurationVersion.objects.get(
            pk=first.applied_version_id
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
                "values": {"end_date": "2026-11-01"},
            },
            {
                "operation": "update",
                "section": "schedules",
                "id": mail["id"],
                "values": {"time": "10:00:00"},
            },
        ],
    )
    assert is_prepared(result.applied_digest)
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(
            "SELECT pg_get_functiondef("
            "'stewardship_resolve_local_v1(timestamp,text)'::regprocedure)"
        )
        original = cursor.fetchone()[0]
        cursor.execute(
            original.replace(
                "stewardship_resolve_local_v1", "stewardship_test_original_resolver", 1
            )
        )
        condition = (
            "wall = timestamp '2026-11-01 00:00:00'"
            if affected == "campaign"
            else "wall::time = time '09:00:00'"
        )
        cursor.execute(
            "CREATE OR REPLACE FUNCTION stewardship_resolve_local_v1"
            "(wall timestamp, zone text) RETURNS timestamptz LANGUAGE sql "
            "STABLE STRICT AS $$ "
            "SELECT stewardship_test_original_resolver(wall, zone) + "
            f"CASE WHEN {condition} THEN interval '1 hour' ELSE interval '0' END $$"
        )
        # Only the older projection differs; the whole lineage must still fail.
        with CaptureQueriesContext(connection) as queries:
            assert not is_prepared(result.applied_digest)
        assert sum("SELECT NOT EXISTS" in query["sql"] for query in queries) == 1
        cursor.execute(original)
        cursor.execute(
            "DROP FUNCTION stewardship_test_original_resolver(timestamp,text)"
        )
    assert is_prepared(result.applied_digest)

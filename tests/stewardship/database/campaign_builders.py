"""Shared Phase 1A database-backed campaign scenarios, never operational permits."""

from contextlib import contextmanager
from datetime import timedelta
from uuid import UUID, uuid4

from django.db import connection, transaction

from parishkit.stewardship.accounts.authority import AuthorityStore
from parishkit.stewardship.accounts.configuration_installation import (
    install_request,
    prepare_initial_configuration,
)
from parishkit.stewardship.accounts.configuration_requests import (
    policy_operation_id,
    record_request,
)
from parishkit.stewardship.accounts.configuration_schema import validate_sections
from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.campaigns.boundaries import apply_due_boundaries
from parishkit.stewardship.campaigns.catchup import bind_catchup, checkpoint_catchup
from parishkit.stewardship.campaigns.configuration_intents import (
    bind_configuration_intent,
)
from parishkit.stewardship.campaigns.lifecycle import Action
from parishkit.stewardship.campaigns.models import (
    ActivationCatchUpDemand,
    Campaign,
    ScheduleDefinition,
)
from parishkit.stewardship.campaigns.runtime import transition_campaign
from parishkit.stewardship.campaigns.schedules import (
    change_occurrence,
    create_occurrence,
)
from parishkit.stewardship.jobs.storage import change_run, enqueue

from ..campaign_factory import campaign as campaign_record
from ..campaign_factory import schedule
from ..configuration_factory import configuration_document, configuration_version
from ..policy_factory import address


def admit_test_work(action, campaign, runtime, subject):
    """Isolated storage fixture; deliberately cannot establish external readiness."""


def admit_task_work(action, status):
    """TaskRun has a separate fixed-arity verifier; never mask campaign arity drift."""
    return True


def draft_campaign(tmp_path, row=None):
    """Create through the real installer and return the persisted current draft."""
    store, root, actor = initialized(tmp_path)
    result, row, _ = add_draft(store, root, actor, row)
    assert result.state == "applied"
    return store, Campaign.objects.get(pk=UUID(row["id"])), actor


@contextmanager
def campaign_clock(instant):
    """Replace only the domain test clock, never TaskRun's genuine lease clock."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT pg_get_functiondef('stewardship_campaign_now_v1()'::regprocedure)"
        )
        original = cursor.fetchone()[0]
        cursor.execute(
            "CREATE OR REPLACE FUNCTION stewardship_campaign_now_v1() "
            "RETURNS timestamptz "
            "LANGUAGE sql STABLE AS %s",
            ["SELECT '" + instant.isoformat() + "'::timestamptz"],
        )
    try:
        yield
    finally:
        with connection.cursor() as cursor:
            cursor.execute(original)


def command(campaign, actor, action, **kwargs):
    """Fresh expected versions plus synthetic owning admission for storage tests."""
    campaign.refresh_from_db()
    runtime = SystemConfiguration.objects.get()
    if action is Action.ACTIVATE and "token_generation_id" not in kwargs:
        kwargs["token_generation_id"] = prepared_tokens(campaign, actor)
    return transition_campaign(
        campaign_id=campaign.pk,
        action=action,
        request_id=uuid4(),
        expected_version=campaign.version,
        expected_runtime_version=runtime.version,
        actor_id=actor,
        correlation_id=uuid4(),
        admit=admit_test_work,
        **kwargs,
    )


@contextmanager
def restored_runtime(backup_at):
    """Emulate offline restore input, not an application writer or release API.

    OPS-06 owns the future journalled restore operation. These fixtures require
    the disposable database's schema-owner privileges to load its otherwise
    frozen gate fields, with user triggers disabled only inside each fixture
    transaction. All application operations execute with every guard enabled.
    """
    restore_id = uuid4()
    columns = (
        "restore_review_required",
        "restore_id",
        "restore_backup_at",
        "restore_activated_at",
        "restore_released_at",
    )
    saved = SystemConfiguration.objects.values_list(*columns).get()

    def load(values):
        """Install synthetic backup state atomically and immediately restore guards."""
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute(
                "ALTER TABLE stewardship_system_configuration DISABLE TRIGGER USER"
            )
            cursor.execute(
                "UPDATE stewardship_system_configuration "
                "SET restore_review_required=%s, restore_id=%s, restore_backup_at=%s, "
                "restore_activated_at=%s, restore_released_at=%s",
                values,
            )
            cursor.execute(
                "ALTER TABLE stewardship_system_configuration ENABLE TRIGGER USER"
            )

    load((True, restore_id, backup_at, backup_at, None))
    try:
        yield restore_id
    finally:
        load(saved)


def initialized(tmp_path, records=None):
    """Install synthetic policy through the real manifest/database protocol."""
    document = configuration_document()
    document["sections"]["login_rules"] = [address()] if records is None else records
    version = configuration_version(document)
    store, actor = AuthorityStore(tmp_path, validate_sections), uuid4()
    prepare_initial_configuration(
        store,
        version,
        testing_recipient="test@example.org",
        actor_id=actor,
        correlation_id=uuid4(),
    )
    return store, version, actor


def change(store, version, actor, patch):
    """Record and install one exact-base intent, retaining its durable receipt."""
    key = uuid4()
    bind_operations(patch, policy_operation_id(actor, key))
    receipt = record_request(
        base_digest=version.digest,
        patch=patch,
        actor_id=actor,
        request_key=key,
        correlation_id=uuid4(),
    )
    result = install_request(
        store, request_id=receipt.request_id, correlation_id=uuid4()
    )
    return result


def bind_operations(patch, operation_id):
    """Make valid new manual test records refer to their actual request identity."""
    for operation in patch:
        if operation["section"] != "login_rules" or operation["operation"] != "add":
            continue
        values = operation["values"]
        if values["kind"] == "address":
            values["creation_operation"] = str(operation_id)
            for origins in values["grants"].values():
                if "manual" in origins:
                    origins["manual"] = str(operation_id)
        elif values["kind"] == "assignment":
            values["operation_id"] = str(operation_id)


def add_draft(store, version, actor, row=None):
    """Use the ordinary configuration request and activation, never ORM draft CRUD."""
    row = campaign_record() if row is None else row
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


def claimed_task(kind, domain_id, actor):
    """Allocate and claim actual durable TaskRun metadata for a synthetic worker."""
    run = enqueue(
        task_type=kind,
        domain_request_id=domain_id,
        actor_id=actor,
        correlation_id=uuid4(),
        admit=admit_task_work,
    )
    return change_run(
        run_id=run.run_id,
        action="claim",
        expected_version=run.version,
        actor_id=actor,
        correlation_id=uuid4(),
        admit=admit_task_work,
        lease_seconds=300,
    )


def complete_empty_catchup(campaign, actor):
    """Finish actual direct-activation work; pre-start activation has no demand."""
    campaign.refresh_from_db()
    demand = ActivationCatchUpDemand.objects.filter(campaign=campaign).first()
    if demand is None:
        assert campaign.state == "scheduled"
        return
    run = claimed_task("activation_catchup", demand.pk, actor)
    bind_catchup(
        demand_id=demand.pk,
        task_root_id=run.root_id,
        source_snapshot_id=uuid4(),
        actor_id=actor,
        correlation_id=uuid4(),
        admit=admit_test_work,
    )
    checkpoint_catchup(
        demand_id=demand.pk,
        group_key="complete",
        cursor="end",
        items=0,
        phase="complete",
        complete=True,
        task_id=run.run_id,
        fence=run.fence,
        actor_id=actor,
        correlation_id=uuid4(),
        admit=admit_test_work,
    )


def end_request(store, campaign, actor, action, end_date="2026-11-10"):
    """Stage the date candidate, then bind reviewed runtime inputs separately."""
    campaign.refresh_from_db()
    runtime = SystemConfiguration.objects.get()
    request = record_request(
        base_digest=store.active().digest,
        patch=[
            {
                "operation": "update",
                "section": "campaigns",
                "id": str(campaign.pk),
                "values": {"end_date": end_date},
            }
        ],
        actor_id=actor,
        request_key=uuid4(),
        correlation_id=uuid4(),
    )
    token = (
        prepared_tokens(campaign, actor, configuration_request_id=request.request_id)
        if action == "reopen"
        else None
    )
    bind_configuration_intent(
        campaign_id=campaign.pk,
        request_id=request.request_id,
        action=action,
        expected_version=campaign.version,
        expected_runtime_version=runtime.version,
        actor_id=actor,
        correlation_id=uuid4(),
        token_generation_id=token,
        admit=admit_test_work,
    )
    return request, token


def prepared_tokens(campaign, actor, *, configuration_request_id=None):
    """Prepare a real empty-corpus generation for foundation lifecycle scenarios.

    Credential-specific tests populate their own Families. This helper never
    resets that population or accepts an arbitrary UUID in place of readiness.
    """
    from parishkit.stewardship.campaigns.credential_models import (
        CampaignCredentialState,
    )
    from parishkit.stewardship.campaigns.link_tokens import (
        begin_generation,
        prepare_generation_batch,
    )

    from .credential_builders import keys, populate

    ring = keys()
    population = CampaignCredentialState.objects.filter(campaign=campaign).first()
    if population is None:
        populate(campaign, ring, [])
        population = CampaignCredentialState.objects.get(campaign=campaign)

    def admit(campaign, deployment, generation):
        """Credential storage seam only; real external task readiness is not claimed."""
        return True

    generation = begin_generation(
        campaign_id=campaign.pk,
        operation_id=uuid4(),
        source_snapshot_id=population.source_snapshot_id,
        source_generation=population.source_generation,
        configuration_request_id=configuration_request_id,
        actor_id=actor,
        public=ring.public,
        admit=admit,
    )
    while generation.state == "building":
        generation = prepare_generation_batch(
            generation_id=generation.pk,
            public=ring.public,
            admit=admit,
        )
    return generation.pk


def close_campaign(campaign, actor):
    """Complete the empty corpus and apply due boundaries through real ledgers."""
    complete_empty_catchup(campaign, actor)
    run = claimed_task("campaign_boundary", campaign.pk, actor)
    with campaign_clock(campaign.active_configuration.ends_at):
        apply_due_boundaries(
            campaign_id=campaign.pk,
            task_id=run.run_id,
            fence=run.fence,
            actor_id=actor,
            correlation_id=uuid4(),
            admit=admit_test_work,
        )
    campaign.refresh_from_db()


def closed_digest(tmp_path):
    """Build a real post-close digest skip with no TaskRun/outbox yet allocated."""

    store, campaign, actor = draft_campaign(tmp_path)
    digest = schedule(str(campaign.pk), kind="daily_digest", date=None)
    assert (
        change(
            store,
            store.active(),
            actor,
            [{"operation": "add", "section": "schedules", **digest}],
        ).state
        == "applied"
    )
    campaign.refresh_from_db()
    with campaign_clock(campaign.active_configuration.starts_at):
        command(campaign, actor, Action.ACTIVATE)
    close_campaign(campaign, actor)
    definition = ScheduleDefinition.objects.get(pk=digest["id"])
    with campaign_clock(campaign.active_configuration.ends_at):
        row = create_occurrence(
            definition_id=definition.pk,
            revision_id=definition.current_revision_id,
            mode="production",
            target="admins",
            slot="final-day",
            due_at=campaign.active_configuration.ends_at,
            actor_id=actor,
            correlation_id=uuid4(),
            admit=admit_test_work,
        )
        row = advance(row, actor, "skipped", reason="admin_post_close_skip")
    return store, campaign, actor, row


def occurrence(definition, actor, target="family:1"):
    """Allocate ordinary rehearsal work under a real canonical schedule revision."""
    return create_occurrence(
        definition_id=definition.pk,
        revision_id=definition.current_revision_id,
        mode="testing",
        target=target,
        slot="once",
        due_at=definition.current_revision.due_at,
        actor_id=actor,
        correlation_id=uuid4(),
        admit=admit_test_work,
    )


def advance(row, actor, state, **kwargs):
    """Supply fresh optimistic versions without bypassing occurrence SQL guards."""
    row.refresh_from_db()
    return change_occurrence(
        occurrence_id=row.pk,
        state=state,
        expected_version=row.version,
        actor_id=actor,
        correlation_id=uuid4(),
        admit=admit_test_work,
        **kwargs,
    )


def inventory_values(campaign, actor, restore_id):
    """Return a valid, identifiers-only inventory bound to a synthetic restore."""
    start = campaign.active_configuration.starts_at
    return dict(
        restore_id=restore_id,
        definition=ScheduleDefinition.objects.get(),
        mode="testing",
        target="family:1",
        slot="once",
        backup_at=start,
        window_start=start,
        window_end=start + timedelta(days=1),
        discovery="inventory",
        actor_id=actor,
        correlation_id=uuid4(),
    )

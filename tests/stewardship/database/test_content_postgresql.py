"""Real content installation, immutable projection history and SQL defense."""

from copy import deepcopy
from datetime import timedelta
from uuid import uuid4

import pytest
from django.db import IntegrityError, connection, transaction

from parishkit.config import ConfigError
from parishkit.stewardship.accounts.configuration_installation import install_request
from parishkit.stewardship.accounts.configuration_models import (
    AppliedConfigurationVersion,
)
from parishkit.stewardship.accounts.configuration_requests import record_request
from parishkit.stewardship.accounts.configuration_snapshots import (
    is_prepared,
    prepare_snapshot,
)
from parishkit.stewardship.accounts.content_models import ContentVersion
from parishkit.stewardship.accounts.operator_recovery import recover_admin
from parishkit.stewardship.accounts.request_models import ConfigurationChangeRequest
from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.campaigns.admission import (
    CampaignAdmissionUnavailable,
    _validate_content_installation,
)
from parishkit.stewardship.campaigns.controls import reserve_work_gate
from parishkit.stewardship.campaigns.lifecycle import Action
from parishkit.stewardship.campaigns.runtime import return_to_testing
from parishkit.stewardship.campaigns.work_locks import work_transaction

from ..configuration_factory import configuration_version, successor_document
from ..content_factory import content, content_document
from .campaign_builders import (
    admit_test_work,
    campaign_clock,
    change,
    close_campaign,
    command,
    draft_campaign,
    restored_runtime,
)
from .test_configuration_service_postgresql import (
    as_config_installer,
    config_role,  # noqa: F401
)
from .test_recovery_postgresql import arguments

pytestmark = pytest.mark.django_db(transaction=True)


def prepare(version):
    """Use real outer durable transactions with all projection guards enabled."""
    return prepare_snapshot(version, actor_id=uuid4(), correlation_id=uuid4())


@pytest.mark.parametrize("cadence", [False, True])
def test_content_installation_replacement_and_recovery(tmp_path, cadence):
    """The installer retains earlier revisions while activating one new selection."""
    store, campaign, actor = draft_campaign(tmp_path)
    if cadence:
        base = store.active()
        record = base.document()["sections"]["integrations"][0]
        assert (
            change(
                store,
                base,
                actor,
                [
                    {
                        "operation": "update",
                        "section": "integrations",
                        "id": record["id"],
                        "values": {
                            "settings": record["values"]["settings"]
                            | {"nightly_time": "03:15"}
                        },
                    }
                ],
            ).state
            == "applied"
        )
    old = content(str(campaign.pk))
    assert (
        change(
            store,
            store.active(),
            actor,
            [
                {"operation": "add", "section": "content", **old},
                {
                    "operation": "update",
                    "section": "campaigns",
                    "id": str(campaign.pk),
                    "values": {"content_versions": {"welcome": old["id"]}},
                },
            ],
        ).state
        == "applied"
    )
    historical = SystemConfiguration.objects.get().active_configuration
    assert historical.validation_schema == (
        "source-cadence-v8" if cadence else "campaign-content-v5"
    )
    assert is_prepared(historical.digest)
    new = content(str(campaign.pk), html="<p>Updated</p>", text="Updated")
    assert (
        change(
            store,
            store.active(),
            actor,
            [
                {"operation": "remove", "section": "content", "id": old["id"]},
                {"operation": "add", "section": "content", **new},
                {
                    "operation": "update",
                    "section": "campaigns",
                    "id": str(campaign.pk),
                    "values": {"content_versions": {"welcome": new["id"]}},
                },
            ],
        ).state
        == "applied"
    )
    assert historical.content_versions.get().text == old["values"]["text"]
    assert (
        SystemConfiguration.objects.get()
        .active_configuration.content_versions.get()
        .text
        == "Updated"
    )
    options = arguments()
    result = recover_admin(store, **options)
    assert result.state == "applied"
    assert ConfigurationChangeRequest.objects.get(
        pk=result.request_id
    ).request_schema == (
        "operator-recovery-cadence-v8" if cadence else "operator-recovery-content-v5"
    )
    assert recover_admin(store, **options) == result
    assert is_prepared(store.active().digest)


def test_cadence_preparation_retains_nonempty_projection_families():
    """Versioned time does not drop campaign, policy, ministry or content records."""
    document = content_document()
    document["sections"]["integrations"][0]["values"]["settings"]["nightly_time"] = (
        "03:15"
    )
    document["sections"]["ministries"] = [
        {
            "id": str(uuid4()),
            "values": {
                "organization_id": 12345,
                "ministry_duid": 1,
                "active": False,
            },
        }
    ]
    version = configuration_version(document)
    prepare(version)
    row = AppliedConfigurationVersion.objects.get(pk=version.version_id)
    assert row.validation_schema == "source-cadence-v8"
    assert row.ministry_activity.get().active is False
    assert row.content_versions.get().slot == "welcome"
    assert is_prepared(version.digest)


def test_retired_revision_identity_is_immutable_at_intake(tmp_path):
    """Removed revisions can be selected unchanged, never rebound to new text."""
    store, campaign, actor = draft_campaign(tmp_path)
    row = content(str(campaign.pk), slot="login_help")
    assert (
        change(
            store,
            store.active(),
            actor,
            [{"operation": "add", "section": "content", **row}],
        ).state
        == "applied"
    )
    assert (
        change(
            store,
            store.active(),
            actor,
            [{"operation": "remove", "section": "content", "id": row["id"]}],
        ).state
        == "applied"
    )
    replacement = deepcopy(row)
    replacement["values"]["text"] = "Rebound"
    with pytest.raises(ConfigError, match="immutable"):
        record_request(
            base_digest=store.active().digest,
            patch=[{"operation": "add", "section": "content", **replacement}],
            actor_id=actor,
            request_key=uuid4(),
            correlation_id=uuid4(),
        )
    assert (
        change(
            store,
            store.active(),
            actor,
            [{"operation": "add", "section": "content", **row}],
        ).state
        == "applied"
    )
    assert is_prepared(store.active().digest)


@pytest.mark.parametrize("action", ["update", "delete"])
def test_sql_cannot_mutate_revisions(action):
    """Neither direct UPDATE nor DELETE bypasses append-only content history."""
    prepare(configuration_version(content_document()))
    with (
        pytest.raises(IntegrityError),
        transaction.atomic(),
        connection.cursor() as cursor,
    ):
        cursor.execute(
            "UPDATE stewardship_content_version SET text='Changed'"
            if action == "update"
            else "DELETE FROM stewardship_content_version"
        )


@pytest.mark.parametrize("defect", ["missing", "forged"])
def test_projection_completeness_and_exact_yaml(monkeypatch, defect):
    """Missing or false content projections roll back the entire candidate."""
    create = ContentVersion.objects.create

    def forged(**kwargs):
        """Change only the projection, leaving the canonical document intact."""
        return create(**(kwargs | {"text": "Forged"}))

    monkeypatch.setattr(
        ContentVersion.objects,
        "create",
        (lambda **kwargs: None) if defect == "missing" else forged,
    )
    with pytest.raises(IntegrityError, match="incomplete|differs"):
        prepare(configuration_version(content_document()))
    assert not AppliedConfigurationVersion.objects.exists()


@pytest.mark.parametrize("bypass", [False, True])
def test_history_identity_survives_preparation_and_sql(bypass, monkeypatch):
    """Preparation checks ancestry, and SQL repeats it if that preflight is bypassed."""
    root = configuration_version(content_document())
    prepare(root)
    document = successor_document(root)
    document["sections"]["content"][0]["values"]["text"] = "Rebound"
    if bypass:
        monkeypatch.setattr(
            "parishkit.stewardship.accounts.configuration_snapshots._verify_history",
            lambda *args, **kwargs: True,
        )
    with pytest.raises(
        IntegrityError if bypass else ConfigError, match="immutable|predecessor"
    ):
        prepare(configuration_version(document))
    assert AppliedConfigurationVersion.objects.count() == 1


def test_content_preserves_locked_structure_and_is_held_during_restore(tmp_path):
    """Live content edits are nonstructural; restore review still freezes them."""
    store, campaign, actor = draft_campaign(tmp_path)
    instant = campaign.active_configuration.starts_at - timedelta(days=1)
    with campaign_clock(instant):
        command(campaign, actor, Action.ACTIVATE)
        campaign.refresh_from_db()
        assert campaign.structural_locked
        row = content(str(campaign.pk), slot="login_help")
        patch = [{"operation": "add", "section": "content", **row}]
        with restored_runtime(instant), pytest.raises(CampaignAdmissionUnavailable):
            change(store, store.active(), actor, patch)
        assert change(store, store.active(), actor, patch).state == "applied"


@pytest.fixture
def archived_content(tmp_path):
    """Retain real content after lifecycle owners archive and clear current scope."""
    store, campaign, actor = draft_campaign(tmp_path)
    rows = [content(str(campaign.pk), slot=slot) for slot in ("welcome", "login_help")]
    assert (
        change(
            store,
            store.active(),
            actor,
            [{"operation": "add", "section": "content", **row} for row in rows],
        ).state
        == "applied"
    )
    campaign.refresh_from_db()
    with campaign_clock(campaign.active_configuration.starts_at):
        command(campaign, actor, Action.ACTIVATE)
    close_campaign(campaign, actor)
    command(campaign, actor, Action.ARCHIVE)
    return_to_testing(
        campaign_id=campaign.pk,
        request_id=uuid4(),
        expected_runtime_version=SystemConfiguration.objects.get().version,
        actor_id=actor,
        correlation_id=uuid4(),
        admit=admit_test_work,
    )
    return store, campaign, actor


@pytest.mark.parametrize("target", [None, "different-campaign"])
def test_archived_content_is_not_editable_without_current_ownership(
    archived_content, target
):
    """Isolate content admission so another structural check cannot mask refusal."""
    store, _, _ = archived_content
    runtime = SystemConfiguration.objects.select_related("active_configuration").get()
    assert runtime.current_campaign_id is None
    document = store.active().document()
    document["sections"]["content"][0]["values"]["text"] = "Must not be installed"
    with (
        work_transaction(),
        pytest.raises(ConfigError, match="Content can only be edited"),
    ):
        _validate_content_installation(
            document, runtime, target_id=None if target is None else uuid4()
        )


def test_content_reordering_is_a_noop_even_without_current_or_during_work_hold(
    archived_content,
):
    """A list-order-only change cannot become permission to mutate retained text."""
    store, campaign, actor = archived_content
    reserve_work_gate(
        campaign_id=campaign.pk,
        request_id=uuid4(),
        actor_id=actor,
        correlation_id=uuid4(),
        admit=admit_test_work,
    )
    runtime = SystemConfiguration.objects.select_related("active_configuration").get()
    document = store.active().document()
    document["sections"]["content"].reverse()
    with work_transaction():
        _validate_content_installation(document, runtime, target_id=None)
    document["sections"]["content"][0]["values"]["text"] = "Held change"
    # Pin the correct campaign deliberately to isolate the global work gate
    # from the separately tested historical-campaign ownership refusal.
    with (
        work_transaction(),
        pytest.raises(
            CampaignAdmissionUnavailable, match="Content changes are currently held"
        ),
    ):
        _validate_content_installation(document, runtime, target_id=campaign.pk)


def test_restricted_installer_can_prepare_content_without_private_reads(
    tmp_path,
    config_role,  # noqa: F811
):
    """Projection verification and trigger effects fit the exact installer registry."""
    store, campaign, actor = draft_campaign(tmp_path)
    row = content(str(campaign.pk), slot="login_help")
    request = record_request(
        base_digest=store.active().digest,
        patch=[{"operation": "add", "section": "content", **row}],
        actor_id=actor,
        request_key=uuid4(),
        correlation_id=uuid4(),
    )
    with as_config_installer():
        assert (
            install_request(
                store, request_id=request.request_id, correlation_id=uuid4()
            ).state
            == "applied"
        )
        assert is_prepared(store.active().digest)

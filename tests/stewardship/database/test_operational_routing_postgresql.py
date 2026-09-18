"""Actual configured recipients and modes, without SMTP or Slack side effects."""

from uuid import uuid4

import pytest

from parishkit.config import ConfigError
from parishkit.stewardship.accounts.authority import AuthorityStore
from parishkit.stewardship.accounts.configuration_installation import (
    prepare_initial_configuration,
)
from parishkit.stewardship.accounts.configuration_schema import validate_sections
from parishkit.stewardship.accounts.key_files import file_fingerprint
from parishkit.stewardship.campaigns.domain import SystemMode
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.jobs.operational_content import IncidentKind, IncidentLevel
from parishkit.stewardship.jobs.operational_models import OperationalNotice
from parishkit.stewardship.jobs.operational_policy import IncidentPolicy
from parishkit.stewardship.jobs.operational_routing import (
    current_mail,
    current_routing,
    current_slack,
    notice_alert,
)
from parishkit.stewardship.jobs.operational_storage import record_observation
from parishkit.stewardship.jobs.ownership import database_now
from parishkit.stewardship.storage import StorageInvariantError

from ..configuration_factory import configuration_document, configuration_version
from ..policy_factory import address, domain
from .campaign_builders import change, restored_runtime
from .test_background_grants_postgresql import task_login

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def routing(tmp_path):
    """Install one coherent configuration without a campaign or provider keys."""
    return configured_routing(tmp_path)


def configured_routing(tmp_path, *, slack_fingerprint=None):
    """Reuse coherent setup while making only the requested channel installed."""
    document = configuration_document()
    records = [
        address(),
        address("second@example.org"),
        address("staff@example.org", ("staff",)),
        address("minister@example.org", ("ministry_leader",)),
        domain(),
    ]
    document["sections"]["login_rules"] = records
    document["sections"]["integrations"].extend(
        {
            "id": str(uuid4()),
            "values": {
                "kind": kind,
                "settings": settings,
                "credential_fingerprint": slack_fingerprint
                if kind == "slack"
                else None,
            },
        }
        for kind, settings in (
            (
                "email",
                {"sender": "sender@example.org", "reply_to": "reply@example.org"},
            ),
            ("slack", {"channel_id": "C123"}),
        )
    )
    document["sections"]["integrations"].append(
        {
            "id": str(uuid4()),
            "values": {
                "kind": "google_workspace",
                "settings": {"delegated_email": "sender@example.org"},
                "credential_fingerprint": file_fingerprint(b"synthetic-workspace"),
            },
        }
    )
    store, actor = AuthorityStore(tmp_path, validate_sections), uuid4()
    prepare_initial_configuration(
        store,
        configuration_version(document),
        testing_recipient="test@example.org",
        actor_id=actor,
        correlation_id=uuid4(),
    )
    record_observation(
        IncidentKind.STORAGE_INTEGRITY,
        level=IncidentLevel.CRITICAL,
        policy=IncidentPolicy(),
    )
    return store, actor, OperationalNotice.objects.get().pk, records


def test_current_exact_admin_envelopes_ignore_testing_and_restore_holds(routing):
    """Both private transports stay operational without a current campaign."""
    store, _, notice, _ = routing
    with work_transaction():
        instant = database_now()
    with restored_runtime(instant):
        for role in (ServiceRole.WORKER, ServiceRole.MAIL_DISPATCH):
            with task_login(role, exact=True), work_transaction():
                selected = current_routing(store)
                assert selected.admins == ("admin@example.org", "second@example.org")
                assert "example.org" not in repr(selected)
                for recipient in selected.admins:
                    mail, render = current_mail(
                        store, notice_id=notice, address=recipient, semantic_key=uuid4()
                    )
                    assert mail.recipients == (recipient,)
                    assert render.routed_recipients == render.intended_recipients
                    assert render.template_id is None
                    assert render.subject.startswith("[TESTING] CRITICAL:")
                    assert "test@example.org" not in render.text
                    assert mail.alert.mode is SystemMode.TESTING
                for denied in (
                    "staff@example.org",
                    "minister@example.org",
                    "x@example.org",
                ):
                    with pytest.raises(PermissionError):
                        current_mail(
                            store,
                            notice_id=notice,
                            address=denied,
                            semantic_key=uuid4(),
                        )
                slack = current_slack(store, notice_id=notice, delivery_id=uuid4())
                assert slack.channel_id == "C123"
                assert slack.alert.incident_id == mail.alert.incident_id
                assert all(
                    address not in slack.message()["text"]
                    for address in selected.admins
                )


def test_current_routing_rechecks_revoked_admin_and_optional_channel(routing):
    """Historical grants and an earlier captured cohort never authorize a send."""
    store, actor, notice, records = routing
    with work_transaction():
        old = current_routing(store)
    email = next(
        row
        for row in store.active().document()["sections"]["integrations"]
        if row["values"]["kind"] == "email"
    )
    assert (
        change(
            store,
            store.active(),
            actor,
            [
                {
                    "operation": "remove",
                    "section": "login_rules",
                    "id": records[1]["id"],
                },
                {"operation": "remove", "section": "integrations", "id": email["id"]},
            ],
        ).state
        == "applied"
    )
    with task_login(ServiceRole.MAIL_DISPATCH, exact=True), work_transaction():
        selected = current_routing(store)
        assert selected.configuration_id != old.configuration_id
        assert selected.admins == ("admin@example.org",)
        for recipient in old.admins:
            with pytest.raises(PermissionError):
                current_mail(
                    store, notice_id=notice, address=recipient, semantic_key=uuid4()
                )
        assert (
            current_slack(store, notice_id=notice, delivery_id=uuid4()).channel_id
            == "C123"
        )


def test_operational_read_requires_work_order_and_configuration_coherence(
    routing, monkeypatch
):
    """An operational classification is not a file/SQL authorization exemption."""
    store, _, notice, _ = routing
    with pytest.raises(StorageInvariantError):
        current_routing(store)
    with work_transaction():
        for identifier, mode in (
            ("private@example.org", SystemMode.TESTING),
            (notice, "testing"),
        ):
            with pytest.raises(TypeError):
                notice_alert(identifier, mode)
        monkeypatch.setattr(store, "manifest_reference", lambda: (uuid4(), "a" * 64))
        with pytest.raises(ConfigError):
            current_routing(store)

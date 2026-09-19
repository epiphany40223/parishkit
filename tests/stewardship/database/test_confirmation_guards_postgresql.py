"""Final confirmation rejects stale evidence and rolls back all late effects."""

# ruff: noqa: F811 -- imported fixture dependencies are injected by pytest name.

from datetime import timedelta
from time import time
from types import SimpleNamespace
from uuid import uuid4

import pytest
from django.core import signing
from django.db import connection
from django.db.models import F
from django.test import override_settings

from parishkit.stewardship.accounts.confirmation_commands import (
    SALT,
    confirm,
    verify_preview,
)
from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.accounts.sessions import database_now, issue_admin
from parishkit.stewardship.campaigns.activation_cleanup import request_cancellation
from parishkit.stewardship.campaigns.confirmation_models import ProductionConfirmation
from parishkit.stewardship.campaigns.credential_models import (
    CampaignCredentialState,
    FamilyCampaign,
)
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.storage import StaleRecordError

from .campaign_builders import campaign_clock
from .test_confirmation_readiness_postgresql import (  # noqa: F401
    bootstrapped,
    config_role,
    prepare,
    ready_cleanup,
    ready_links,
    setup_service,
)
from .test_setup_mail_views_postgresql import web_login

pytestmark = pytest.mark.django_db(transaction=True)


def test_stale_preview_scope_expiry_and_cancellation_never_activate(
    ready_links, settings, monkeypatch
):
    """Share one genuine setup/preparation across independent rejected commands."""
    preparation, arguments = prepare(ready_links)
    login, service = arguments[:2]
    with web_login():
        issue_admin(
            login,
            login.portal_session.principal_id,
            store=service.store,
            authenticated_at=database_now(),
        )
        preview, _, token = verify_preview(*arguments)
        bound = signing.loads(token, salt=SALT)
        for field in ("actor", "campaign", "request", "preparation"):
            changed = signing.dumps(bound | {field: str(uuid4())}, salt=SALT)
            with pytest.raises(PermissionError, match="another scope"):
                confirm(*arguments, token=changed, typed="Production")
        with pytest.raises(signing.BadSignature):
            confirm(*arguments, token=token + "x", typed="Production")
        with monkeypatch.context() as patch:
            patch.setattr(signing, "time", SimpleNamespace(time=lambda: time() + 301))
            with pytest.raises(signing.SignatureExpired):
                confirm(*arguments, token=token, typed="Production")
        with (
            override_settings(STEWARDSHIP_PUBLIC_ORIGIN="http://localhost:8001"),
            pytest.raises(StaleRecordError, match="origin changed"),
        ):
            confirm(*arguments, token=token, typed="Production")
    campaign = preparation.transition.campaign
    for instant in (
        preview.expires_at,
        campaign.active_configuration.starts_at,
        campaign.active_configuration.ends_at,
        preview.readiness.observed_at + timedelta(days=1),
    ):
        with campaign_clock(instant), web_login(), pytest.raises(StaleRecordError):
            confirm(*arguments, token=token, typed="Production")
    # Both mutations commit before the independently owned final transaction.
    with work_transaction():
        FamilyCampaign.objects.update(email_deliverable=False, version=F("version") + 1)
    with web_login(), pytest.raises(StaleRecordError):
        confirm(*arguments, token=token, typed="Production")
    with work_transaction():
        FamilyCampaign.objects.update(email_deliverable=True, version=F("version") + 1)
    with web_login():
        _, _, token = verify_preview(*arguments)
    with work_transaction():
        request_cancellation(
            preparation_id=preparation.pk,
            request_key=uuid4(),
            actor_id=login.portal_session.principal_id,
            correlation_id=uuid4(),
            admit=lambda *args: True,
        )
    with web_login(), pytest.raises(StaleRecordError):
        confirm(*arguments, token=token, typed="Production")
    assert not ProductionConfirmation.objects.exists()
    assert SystemConfiguration.objects.get().mode == "testing"
    campaign.refresh_from_db()
    assert campaign.state == "draft" and campaign.active_token_generation_id is None


def test_late_failure_rolls_back_activation_gate_and_generation(ready_links):
    """A failure after SQL effects cannot leave any partially live state."""
    preparation, arguments = prepare(ready_links)
    login, service = arguments[:2]
    reached = []

    def fail_after_effect(execute, sql, params, many, context):
        """Inject only after the real private effect has performed its writes."""
        result = execute(sql, params, many, context)
        if sql.startswith('INSERT INTO "stewardship_production_confirmation"'):
            assert SystemConfiguration.objects.get().mode == "production"
            assert not CampaignCredentialState.objects.get().go_live_gate
            reached.append(True)
            raise RuntimeError("synthetic response failure after activation")
        return result

    with web_login():
        issue_admin(
            login,
            login.portal_session.principal_id,
            store=service.store,
            authenticated_at=database_now(),
        )
        _, _, token = verify_preview(*arguments)
        with (
            connection.execute_wrapper(fail_after_effect),
            pytest.raises(RuntimeError, match="synthetic response"),
        ):
            confirm(*arguments, token=token, typed="Production")
    assert reached == [True]
    assert not ProductionConfirmation.objects.exists()
    assert SystemConfiguration.objects.get().mode == "testing"
    assert CampaignCredentialState.objects.get().go_live_gate
    preparation.transition.campaign.refresh_from_db()
    assert preparation.transition.campaign.state == "draft"
    assert preparation.transition.campaign.active_token_generation_id is None
    with web_login():
        assert confirm(*arguments, token=token, typed="Production")

"""System setup finishes without a campaign (#142).

The same real path as ``test_setup_completion_postgresql``: the original
owner's draft, preview, accepted mail test, frozen request, credential
installers, YAML preparation, the restricted worker's final load and the
atomic completion. The draft simply has no campaign, content or schedule
sections, as the wizard after #142 saves none.
"""

# ruff: noqa: F811 -- imported pytest fixtures.

from functools import partial
from types import SimpleNamespace

import pytest

from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.accounts.setup_completion import setup_is_complete
from parishkit.stewardship.accounts.setup_delivery_models import SetupMailDelivery
from parishkit.stewardship.accounts.setup_install_models import SetupCompletion
from parishkit.stewardship.accounts.setup_mail import setup_test_message
from parishkit.stewardship.accounts.setup_models import SetupAttempt
from parishkit.stewardship.campaigns.credential_models import FamilyCampaign
from parishkit.stewardship.campaigns.models import Campaign
from parishkit.stewardship.readiness_mail import ReadinessMail
from parishkit.stewardship.source.setup_completion import complete_setup
from parishkit.stewardship.source.snapshot_models import SourceCurrent

from ..test_setup_forms import VALUES
from . import test_setup_preview_postgresql as preview
from .credential_builders import keys
from .test_bootstrap_postgresql import bootstrapped  # noqa: F401
from .test_configuration_service_postgresql import config_role  # noqa: F401
from .test_runtime_auth_grants_postgresql import web_login
from .test_setup_campaign_postgresql import completed
from .test_setup_final_loading_postgresql import load, prepared, queued
from .test_setup_loading_postgresql import pages
from .test_setup_staging_postgresql import setup_service  # noqa: F401
from .test_source_refreshing_postgresql import fake_provider

pytestmark = pytest.mark.django_db(transaction=True)


def without_campaign(service, monkeypatch):
    """The loaded setup draft, with no first-campaign sections at all."""
    request, attempt = completed(service, monkeypatch)
    status = SimpleNamespace(attempt_id=attempt.pk, version=attempt.version)
    return request, status, None, None


def test_system_setup_completes_without_a_campaign(
    setup_service, monkeypatch, tmp_path, config_role
):
    """No campaign, no Families and no codes; the parish data is still loaded."""
    from parishkit.stewardship.accounts import setup_preview

    # The back end is switched off until the wizard changes (#142 part b).
    monkeypatch.setattr(setup_preview, "SYSTEM_ONLY_SETUP", True)
    monkeypatch.setattr(preview, "with_schedules", without_campaign)
    _, attempt, identifier = prepared(setup_service, monkeypatch, tmp_path)
    # The accepted setup test was the fixed message, not a campaign email.
    sent = SetupMailDelivery.objects.get()
    assert sent.state == "accepted"
    expected = setup_test_message({"name": VALUES["parish"]["name"]})
    mail = ReadinessMail.from_payload(sent.mail)
    assert mail.subject == expected["subject"] == "Stewardship setup test"
    assert (mail.text, mail.html) == (expected["text"], expected["html"])
    ring = keys()
    task = queued(setup_service, identifier)
    fake_provider(monkeypatch, pages())
    finalize = partial(
        complete_setup,
        store=setup_service.store,
        general=ring.general,
        mac=ring.mac,
        public=ring.public,
    )
    completed_row = load(
        setup_service,
        task,
        tmp_path / "parishsoft" / "credential",
        finalize=lambda execution, claim, snapshot: finalize(
            execution, claim, snapshot.pk
        ),
    )
    assert SetupCompletion.objects.get() == completed_row
    with web_login():
        assert setup_is_complete()
    assert SetupAttempt.objects.get().state == "completed"
    runtime = SystemConfiguration.objects.get()
    assert runtime.current_campaign_id is None
    assert runtime.mode == "testing"
    assert runtime.active_configuration_id == completed_row.activation.configuration_id
    assert SourceCurrent.objects.get().snapshot_id == completed_row.snapshot_id
    assert not Campaign.objects.exists()
    assert not FamilyCampaign.objects.exists()
    document = setup_service.store.active().document()["sections"]
    assert not document.get("campaigns") and not document.get("schedules")


def test_switched_off_review_still_requires_the_first_campaign(
    setup_service, monkeypatch
):
    """Until the wizard changes, a draft with no campaign cannot be reviewed.

    Otherwise an Admin could skip the campaign pages and finish setup with no
    campaign and, before Create the campaign exists, no way to make one.
    """
    from parishkit.config import ConfigError
    from parishkit.stewardship.accounts.setup_preview import prepare_preview

    request, _, _, _ = without_campaign(setup_service, monkeypatch)
    with web_login(), pytest.raises(ConfigError):
        prepare_preview(request, setup_service)

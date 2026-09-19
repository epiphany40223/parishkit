"""Current-Admin pause commits exact control and unsent holds without rerouting."""

# ruff: noqa: F811 -- pytest injects imported fixture dependencies by name.

import pytest
from django.urls import reverse

from parishkit.stewardship.accounts import delivery_control_commands as commands
from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.campaigns.delivery_control_models import (
    DeliveryControlCommand,
)
from parishkit.stewardship.jobs.outbox_models import OutboxMessage
from parishkit.stewardship.storage import StaleRecordError

from .test_setup_mail_views_postgresql import web_login
from .test_setup_views_postgresql import post
from .test_withdrawal_postgresql import (  # noqa: F401
    bootstrapped,
    config_role,
    ready_cleanup,
    ready_links,
    scheduled,
    setup_service,
)
from .test_withdrawal_work_postgresql import future_message

pytestmark = pytest.mark.django_db(transaction=True)


def test_pause_is_atomic_exact_and_keeps_delivery_payload(scheduled):
    """A changed preview cannot hold new work; a current one preserves its bytes."""
    item = scheduled
    path = f"/admin/campaign/{item.campaign.pk}/delivery"
    with web_login():
        page = item.browser.get(path)
        assert page.status_code == 200 and "no-store" in page["Cache-Control"]
        assert (
            item.browser.post(
                path, {"action": "preview_pause", "reason": "Check"}
            ).status_code
            == 403
        )
        assert (
            post(
                item.browser,
                path,
                {"action": "preview_pause", "reason": "Check", "override": "yes"},
            ).status_code
            == 400
        )
        before = commands.page(*item.arguments)
        binding, stale = commands.preview_pause(*item.arguments, reason="Check sender")
        assert before["available"] and binding["inventory"]["queued"] == 0
    _, message = future_message(item)
    original = OutboxMessage.objects.get(pk=message.message_id)
    with web_login():
        with pytest.raises(StaleRecordError, match="inputs changed"):
            commands.confirm(*item.arguments, token=stale)
        preview, token = commands.preview_pause(*item.arguments, reason="Check sender")
        assert preview["inventory"]["queued"] == 1
        receipt = commands.confirm(*item.arguments, token=token)
        assert commands.confirm(*item.arguments, token=token).pk == receipt.pk
        status = commands.page(*item.arguments)
        assert status["inventory"]["held"] == 1
        assert status["campaign"].delivery_paused
        page = item.browser.get(path)
        assert page.status_code == 200
        assert page.context["admin_chrome"]["delivery_pause"]["inventory"]["held"] == 1
        assert (
            page.context["admin_chrome"]["delivery_pause"]["reason"] == "Check sender"
        )
        assert item.browser.get(reverse("admin:background")).status_code == 200
    item.campaign.refresh_from_db()
    held = OutboxMessage.objects.get(pk=original.pk)
    assert held.pause_hold_id is not None and held.action == "hold"
    assert held.state == original.state == "pending" and held.attempt == 0
    assert held.render_id == original.render_id
    assert held.sealed_substitutions == original.sealed_substitutions
    assert held.routing == original.routing == "production"
    assert item.campaign.state == "scheduled"
    assert SystemConfiguration.objects.get().mode == "production"
    assert DeliveryControlCommand.objects.count() == 1

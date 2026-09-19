"""Current-Admin pause commits exact control and unsent holds without rerouting."""

# ruff: noqa: F811 -- pytest injects imported fixture dependencies by name.

import pytest
from django.urls import reverse

from parishkit.stewardship.accounts import delivery_control_commands as commands
from parishkit.stewardship.accounts.campaign_mail_models import CampaignMailTest
from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.campaigns.delivery_control_models import (
    DeliveryControlCommand,
)
from parishkit.stewardship.jobs.outbox_models import OutboxMessage
from parishkit.stewardship.readiness_delivery import DeliveryOutcome
from parishkit.stewardship.storage import StaleRecordError

from .test_campaign_mail_postgresql import deliver
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


def test_pause_is_atomic_exact_and_keeps_delivery_payload(
    scheduled, monkeypatch, tmp_path
):
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
        assert before["next_due"]["count"] == 1
        assert before["next_due"]["types"] == {"initial": 1}
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
        # The successful go-live test predates this pause and cannot release it.
        assert not status["health"]["ready"]
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
    test_path = reverse(
        "admin:campaign_mail", args=[item.campaign.pk, status["test_template"]]
    )
    for outcome, ready in (
        (DeliveryOutcome.NOT_SENT, False),
        (DeliveryOutcome.ACCEPTED, True),
        (DeliveryOutcome.UNKNOWN, False),
    ):
        with web_login():
            page = item.browser.get(test_path)
            assert page.status_code == 200, page.content
            token = page.context["form"]["preview_token"].value()
            assert (
                post(item.browser, test_path, {"preview_token": token}).status_code
                == 302
            )
            assert not commands.health(item.campaign.pk)["ready"]
        monkeypatch.setattr(
            "parishkit.stewardship.accounts.campaign_mail_tasks.submit_sample",
            lambda *args, result=outcome, **kwargs: result,
        )
        result = deliver(
            (
                item.arguments[1],
                None,
                None,
                tmp_path / "google_workspace" / "credential",
            )
        )
        assert result.state == outcome.value
        with web_login():
            proof = commands.health(item.campaign.pk)
            assert proof["ready"] is ready
            if ready:
                assert proof["proof"] == str(result.pk)
        held.refresh_from_db()
        item.campaign.refresh_from_db()
        assert held.pause_hold_id and item.campaign.delivery_paused
        assert held.attempt == 0 and held.routing == "production"
    assert CampaignMailTest.objects.filter(state="delivery_unknown").count() == 1

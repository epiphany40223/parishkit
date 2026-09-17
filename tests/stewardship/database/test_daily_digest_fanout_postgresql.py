"""Fenced one-Admin message allocation is atomic with its retained report owner."""

from dataclasses import replace
from functools import partial
from unittest.mock import patch
from uuid import uuid4

import pytest
from django.db import DatabaseError

from parishkit.stewardship.campaigns.schedule_models import ScheduleDefinition
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.jobs.outbox_models import OutboxMessage
from parishkit.stewardship.reports.digest_building import retain_daily_content
from parishkit.stewardship.reports.digest_fanout import fanout_daily, retained_render
from parishkit.stewardship.reports.digest_models import DailyDigestRecipient

from ..content_factory import content
from ..policy_factory import address
from .campaign_builders import campaign_clock, change
from .test_background_grants_postgresql import task_login
from .test_daily_digest_building_postgresql import build
from .test_daily_digest_planning_postgresql import INSTANT
from .test_family_mail_preparation_postgresql import family_mail  # noqa: F401

pytestmark = pytest.mark.django_db(transaction=True)


def configure_content(harness, *, additional_admins=()):
    """Configuration is complete before the preparation snapshots its owner."""
    definition = ScheduleDefinition.objects.get(kind="daily_digest")
    template = content(str(harness.campaign.pk), kind="email", slot="daily_digest")
    template["id"] = definition.current_revision.values["template_version"]
    template["values"]["subject"] = definition.current_revision.values["subject"]
    store = harness.service.store
    assert (
        change(
            store,
            store.active(),
            uuid4(),
            [{"operation": "add", "section": "content", **template}]
            + [
                {"operation": "add", "section": "login_rules", **address(email)}
                for email in additional_admins
            ],
        ).state
        == "applied"
    )


def ready_report(harness, *, additional_admins=()):
    """Add the selected immutable digest prose before rendering any envelope."""
    claim, document, compiled = build(
        harness,
        configure=partial(
            configure_content, harness, additional_admins=additional_admins
        ),
    )
    with task_login(ServiceRole.WORKER, exact=True), work_transaction():
        ready = retain_daily_content(claim, document, compiled)
    return claim, ready


def test_fanout_routes_one_testing_message_with_exact_report(family_mail):  # noqa: F811
    with campaign_clock(INSTANT):
        claim, ready = ready_report(family_mail)
        with task_login(ServiceRole.WORKER, exact=True), work_transaction():
            assert fanout_daily(claim).phase == "complete"
        recipient = DailyDigestRecipient.objects.select_related("outbox__render").get()
        message, render = recipient.outbox, recipient.outbox.render
        assert recipient.address == "admin@example.org"
        assert message.semantic_key == recipient.pk
        assert message.purpose == "daily_digest" and message.state == "pending"
        assert message.family_id is None and message.credential_namespace == "none"
        assert render.intended_recipients == [recipient.address]
        assert len(render.routed_recipients) == 1
        assert render.routed_recipients != render.intended_recipients
        assert render.subject.startswith("[TEST]")
        assert render.html.endswith(ready.html) and render.text.endswith(ready.text)
        with (
            task_login(ServiceRole.WORKER, exact=True),
            work_transaction(),
            pytest.raises(PermissionError),
        ):
            fanout_daily(claim)
        assert (
            OutboxMessage.objects.count() == DailyDigestRecipient.objects.count() == 1
        )


def test_omitted_recipient_rolls_back_entire_message(family_mail):  # noqa: F811
    with campaign_clock(INSTANT):
        claim, _ = ready_report(family_mail)
        with (
            patch(
                "parishkit.stewardship.reports.digest_fanout.DailyDigestRecipient.objects.create"
            ),
            task_login(ServiceRole.WORKER, exact=True),
            pytest.raises(DatabaseError),
            work_transaction(),
        ):
            fanout_daily(claim)
        assert not OutboxMessage.objects.exists()
        assert not DailyDigestRecipient.objects.exists()
        with task_login(ServiceRole.WORKER, exact=True), work_transaction():
            assert fanout_daily(claim).phase == "complete"


def test_bounded_pages_survive_public_configuration_edits(family_mail, monkeypatch):  # noqa: F811
    """A changed parish label neither orphans work nor rewrites retained messages."""
    monkeypatch.setattr("parishkit.stewardship.reports.digest_fanout.LIMIT", 1)
    with campaign_clock(INSTANT):
        claim, ready = ready_report(
            family_mail, additional_admins=("second@example.org", "third@example.org")
        )
        with task_login(ServiceRole.WORKER, exact=True), work_transaction():
            assert fanout_daily(claim).phase == "fanout"
        first = OutboxMessage.objects.select_related("render").get()
        old_render = first.render_id
        store = family_mail.service.store
        parish = store.active().document()["sections"]["parish"][0]
        assert (
            change(
                store,
                store.active(),
                uuid4(),
                [
                    {
                        "operation": "update",
                        "section": "parish",
                        "id": parish["id"],
                        "values": {"name": "Renamed Example Parish"},
                    }
                ],
            ).state
            == "applied"
        )
        with task_login(ServiceRole.WORKER, exact=True), work_transaction():
            assert fanout_daily(claim).phase == "fanout"
        with task_login(ServiceRole.WORKER, exact=True), work_transaction():
            assert fanout_daily(claim).phase == "complete"
        assert DailyDigestRecipient.objects.count() == 3
        assert OutboxMessage.objects.count() == 3
        first.refresh_from_db()
        assert first.render_id == old_render
        for recipient in DailyDigestRecipient.objects.select_related("outbox__render"):
            render = recipient.outbox.render
            assert render.intended_recipients == [recipient.address]
            assert render.html.endswith(ready.html) and render.text.endswith(ready.text)


def test_fanout_page_interruption_retries_without_duplicate(family_mail):  # noqa: F811
    """A rolled-back page owns neither private recipients nor sendable messages."""
    with campaign_clock(INSTANT):
        claim, _ = ready_report(family_mail)
        with (
            task_login(ServiceRole.WORKER, exact=True),
            pytest.raises(RuntimeError),
            work_transaction(),
        ):
            fanout_daily(claim)
            raise RuntimeError("Synthetic page interruption")
        assert not OutboxMessage.objects.exists()
        with task_login(ServiceRole.WORKER, exact=True), work_transaction():
            assert fanout_daily(claim).phase == "complete"
        assert (
            OutboxMessage.objects.count() == DailyDigestRecipient.objects.count() == 1
        )


def test_missing_recipient_is_rejected_at_commit_without_phase_advance(family_mail):  # noqa: F811
    """Suppressing the phase check cannot leave an unowned outbox behind."""
    with campaign_clock(INSTANT):
        claim, _ = ready_report(family_mail)
        with (
            patch(
                "parishkit.stewardship.reports.digest_fanout.DailyDigestRecipient.objects.create"
            ),
            patch("parishkit.stewardship.reports.digest_fanout.checkpoint_preparation"),
            task_login(ServiceRole.WORKER, exact=True),
            pytest.raises(DatabaseError, match="atomic recipient ownership"),
            work_transaction(),
        ):
            fanout_daily(claim)
        assert not OutboxMessage.objects.exists()


@pytest.mark.parametrize(
    "field,value",
    [
        ("sender", "forged@example.org"),
        ("routed_recipients", ("unintended@example.org",)),
        ("intended_recipients", ("unlisted@example.org",)),
        ("text", "Different report values"),
        ("html", "<p>Different report values</p>"),
    ],
)
def test_sql_rejects_detached_envelope_values(family_mail, monkeypatch, field, value):  # noqa: F811
    """An independently guarded worker insertion cannot replace routing or facts."""

    def tampered(*args, **kwargs):
        """Change a typed render after Python's validation to exercise SQL proof."""
        return replace(retained_render(*args, **kwargs), **{field: value})

    monkeypatch.setattr(
        "parishkit.stewardship.reports.digest_fanout.retained_render", tampered
    )
    with campaign_clock(INSTANT):
        claim, _ = ready_report(family_mail)
        with (
            task_login(ServiceRole.WORKER, exact=True),
            pytest.raises(DatabaseError),
            work_transaction(),
        ):
            fanout_daily(claim)
        assert not OutboxMessage.objects.exists()

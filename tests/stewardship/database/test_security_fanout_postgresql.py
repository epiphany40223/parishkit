"""Durable security alert cohorts and outbox ownership, with no external providers."""

import html
import json
from dataclasses import asdict
from uuid import uuid4

import pytest
from django.db import IntegrityError, connection
from django.utils import timezone

from parishkit.stewardship.accounts.policy_models import (
    PolicySecurityEvent,
    PortalUser,
)
from parishkit.stewardship.campaigns.domain import SystemMode
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.jobs import security_owner
from parishkit.stewardship.jobs.dispatch import claim_hint, execute_hint
from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.jobs.operational_fanout import (
    fanout_handler,
    prepare_page,
    produce_fanout,
)
from parishkit.stewardship.jobs.outbox_models import OutboxMessage
from parishkit.stewardship.jobs.queues import WorkQueue
from parishkit.stewardship.jobs.scheduler import scheduler_session
from parishkit.stewardship.jobs.security_content import render_security_alert
from parishkit.stewardship.jobs.security_models import SecurityCohort, SecurityRecipient
from parishkit.stewardship.jobs.security_owner import SECURITY, TASK_TYPE
from parishkit.stewardship.jobs.security_routing import event_alert

from ..policy_factory import address, domain
from .campaign_builders import change
from .test_background_grants_postgresql import task_login
from .test_operational_routing_postgresql import routing as routing_fixture

routing = routing_fixture
pytestmark = pytest.mark.django_db(transaction=True)


def schedule():
    """Use only actual scheduler SQL rights for opaque event production."""
    with task_login(ServiceRole.SCHEDULER, exact=True), scheduler_session() as guard:
        return produce_fanout(guard, SECURITY)


def consume(identifier, store):
    """Exercise the maintained worker and durable Task completion, not a stub."""
    with task_login(ServiceRole.WORKER, exact=True, reconnect=True):
        return execute_hint(
            identifier,
            queue=WorkQueue.GENERAL,
            worker_id=uuid4(),
            handlers={TASK_TYPE: fanout_handler(store, owner=SECURITY)},
        )


def granted(routing, email="new@example.org"):
    """Grant Administrator to a new address; the trigger records the expansion."""
    store, actor, _, _ = routing
    assert (
        change(
            store,
            store.active(),
            actor,
            [{"operation": "add", "section": "login_rules", **address(email)}],
        ).state
        == "applied"
    )
    return PolicySecurityEvent.objects.get(target=email)


def test_an_expansion_allocates_one_durable_outbox_per_recorded_recipient(routing):
    """The cohort is the event's recorded recipients; a reload keeps each identity."""
    store, _, _, _ = routing
    # The root activation's own event has nobody to tell and is never scheduled.
    root = PolicySecurityEvent.objects.get(target="admin@example.org")
    assert root.recipients == [] and schedule() == ()
    event = granted(routing)
    assert event.recipients == ["admin@example.org", "second@example.org"]
    (identifier,) = schedule()
    assert schedule() == ()
    assert consume(identifier, store)
    cohort = SecurityCohort.objects.get(event=event)
    assert cohort.addresses == ["admin@example.org", "second@example.org"]
    assert cohort.recipient_count == 2 and cohort.mode == "testing"
    messages = OutboxMessage.objects.filter(purpose="security_event")
    assert SecurityRecipient.objects.count() == messages.count() == 2
    for recipient in SecurityRecipient.objects.select_related("outbox__render"):
        message = recipient.outbox
        assert message.semantic_key == recipient.pk
        assert message.scope_id == cohort.parish_id
        assert message.campaign_id is message.family_id is None
        assert message.credential_namespace == "none"
        assert message.purpose == "security_event"
        assert message.routing == "operational"
        assert message.render.routed_recipients == [recipient.address]
        assert message.render.routed_recipients == message.render.intended_recipients
        assert message.render.subject == (
            "[TESTING] SECURITY: Administrator added to an exact address"
        )
        assert "Target: new@example.org" in message.render.text
        assert "Roles after: Administrator" in message.render.text
        assert message.state == "pending" and message.attempt == 0
    assert not consume(identifier, store)
    assert TaskRun.objects.get(pk=identifier).state == "succeeded"
    assert TaskRun.objects.filter(task_type="outbox_delivery").count() == 2


def replaced_key(routing):
    """Apply a new backup key in the portal; return the event and its key ID."""
    import base64

    from parishkit.stewardship.backup_sealing import generate_keypair, parse_public_key

    store, actor, _, _ = routing
    public = generate_keypair()[1].strip()
    document = store.active().document()
    record = next(
        (
            item
            for item in document["sections"]["integrations"]
            if item["values"]["kind"] == "backup_key"
        ),
        None,
    )
    patch = (
        {
            "operation": "update",
            "section": "integrations",
            "id": record["id"],
            "values": {"settings": {"public_key": public}},
        }
        if record
        else {
            "operation": "add",
            "section": "integrations",
            "id": str(uuid4()),
            "values": {
                "kind": "backup_key",
                "settings": {"public_key": public},
                "credential_fingerprint": None,
            },
        }
    )
    assert change(store, store.active(), actor, [patch]).state == "applied"
    assert base64.b64decode(public)
    event = PolicySecurityEvent.objects.filter(kind="backup_key_replaced").latest(
        "created_at"
    )
    return event, parse_public_key(public).fingerprint


def test_a_replaced_backup_key_emails_every_prior_administrator(routing):
    """A portal key change is a security event with both key IDs (#198)."""
    from datetime import timedelta

    from parishkit.stewardship.jobs.backup_models import BackupRun

    store, actor, _, _ = routing
    now = timezone.now()
    BackupRun.objects.create(
        started_at=now - timedelta(minutes=1),
        completed_at=now,
        database_bytes=1,
        files_bytes=1,
        manifest_digest="a" * 64,
        recipient_fingerprint="c" * 16,
        application_version="0.1.0",
    )
    event, first = replaced_key(routing)
    # Before a key is configured, the key before is the newest backup's.
    assert (event.before_roles, event.after_roles) == (["c" * 16], [first])
    assert event.actor_id == actor and event.target == "Backup encryption key"
    assert event.recipients == ["admin@example.org", "second@example.org"]
    (identifier,) = schedule()
    assert consume(identifier, store)
    messages = OutboxMessage.objects.filter(purpose="security_event")
    assert messages.count() == 2
    for message in messages.select_related("render"):
        assert message.render.subject == (
            "[TESTING] SECURITY: Backup encryption key replaced"
        )
        assert f"Key before: {'c' * 16}\nKey after: {first}" in message.render.text
    # A second change names the previously configured key as the key before.
    second, after = replaced_key(routing)
    assert (second.before_roles, second.after_roles) == ([first], [after])


def test_a_removed_administrator_still_hears_of_a_key_change(routing):
    """Removing the other Administrator first does not hide a key change.

    The re-review's probe (#367): one activation removes second@, the next
    replaces the key. Every Administrator of the last 30 days is told, and
    the maker's own acknowledgement settles it for nobody else.
    """
    from parishkit.stewardship.accounts.security_events import cleared

    store, actor, _, _ = routing
    rule = next(
        item
        for item in store.active().document()["sections"]["login_rules"]
        if item["values"].get("email") == "second@example.org"
    )
    removal = [{"operation": "remove", "section": "login_rules", "id": rule["id"]}]
    assert change(store, store.active(), actor, removal).state == "applied"
    event, _ = replaced_key(routing)
    assert event.recipients == ["admin@example.org", "second@example.org"]
    (identifier,) = schedule()
    assert consume(identifier, store)
    assert sorted(
        message.render.intended_recipients
        for message in OutboxMessage.objects.filter(
            purpose="security_event"
        ).select_related("render")
    ) == [["admin@example.org"], ["second@example.org"]]
    own = [{"email": "admin@example.org", "own": True}]
    assert not cleared(event, own, viewer_email="later@example.org")


def remove_second(routing):
    """Activate a configuration without second@'s Administrator rule."""
    store, actor, _, _ = routing
    rule = next(
        item
        for item in store.active().document()["sections"]["login_rules"]
        if item["values"].get("email") == "second@example.org"
    )
    removal = [{"operation": "remove", "section": "login_rules", "id": rule["id"]}]
    assert change(store, store.active(), actor, removal).state == "applied"


def age_activations(days):
    """Move every activation so far back, as months of history would have."""
    from .auth_builders import unguarded

    # Raw SQL: the model refuses any update of an append-only record, and
    # replica mode skips the database's own immutability trigger.
    with unguarded(), connection.cursor() as cursor:
        cursor.execute(
            "UPDATE stewardship_config_activation "
            "SET created_at = created_at - make_interval(days => %s)",
            [days],
        )


def test_a_long_standing_configuration_counts_when_the_others_are_removed(routing):
    """The configuration in effect when the window opened is in it (#367 round 3).

    A has been live for 60 days with admin@ and second@; admin@ activates B
    without second@, then C with a new key. second@ was an Administrator
    until yesterday, so second@ is told.
    """
    age_activations(60)
    remove_second(routing)
    event, _ = replaced_key(routing)
    assert event.recipients == ["admin@example.org", "second@example.org"]


def test_a_removal_older_than_the_window_is_the_documented_residual(routing):
    """Someone removed 31 or more days ago is no longer told, as the spec says."""
    remove_second(routing)
    age_activations(31)
    event, _ = replaced_key(routing)
    assert event.recipients == ["admin@example.org"]


def test_an_unrelated_change_with_a_key_configured_records_no_key_event(routing):
    """Each configuration carries the key forward; only a change is announced."""
    store, actor, _, _ = routing
    replaced_key(routing)
    before = PolicySecurityEvent.objects.filter(kind="backup_key_replaced").count()
    slack = next(
        item
        for item in store.active().document()["sections"]["integrations"]
        if item["values"]["kind"] == "slack"
    )
    patch = [
        {
            "operation": "update",
            "section": "integrations",
            "id": slack["id"],
            "values": {"settings": {"channel_id": "C999"}},
        }
    ]
    assert change(store, store.active(), actor, patch).state == "applied"
    after = PolicySecurityEvent.objects.filter(kind="backup_key_replaced").count()
    assert after == before == 1


def test_sql_binds_the_cohort_to_the_recorded_recipients(routing, monkeypatch):
    """A cohort naming anyone but the event's recipients is refused by SQL."""
    store, _, _, _ = routing
    granted(routing)
    (identifier,) = schedule()
    monkeypatch.setattr(security_owner, "_recipients", lambda event: ("x@example.org",))
    with task_login(ServiceRole.WORKER, exact=True):
        execution = claim_hint(
            identifier,
            queue=WorkQueue.GENERAL,
            worker_id=uuid4(),
            handlers={TASK_TYPE: fanout_handler(store, owner=SECURITY)},
        )
        with (
            pytest.raises(IntegrityError, match="recorded recipients"),
            work_transaction(),
        ):
            prepare_page(execution.claim, store, SECURITY)
    assert not SecurityCohort.objects.exists()
    assert not OutboxMessage.objects.filter(purpose="security_event").exists()


def test_sql_rejects_private_text_and_missing_recipient_receipt(routing, monkeypatch):
    """Privileged role grants alone cannot allocate arbitrary security content."""
    from dataclasses import replace

    from parishkit.stewardship.jobs.outbox_models import OutboxRender
    from parishkit.stewardship.jobs.ownership import TaskClaim

    store, _, _, _ = routing
    granted(routing)
    (identifier,) = schedule()
    original = security_owner.mail_render

    def private_text(**kwargs):
        """Keep every binding valid while attempting an unauthorized body."""
        mail, render = original(**kwargs)
        return mail, replace(render, text="private-family-information")

    monkeypatch.setattr(security_owner, "mail_render", private_text)
    with pytest.raises(IntegrityError):
        consume(identifier, store)
    assert not OutboxRender.objects.exists()
    monkeypatch.setattr(security_owner, "mail_render", original)
    monkeypatch.setattr(SecurityRecipient.objects, "create", lambda **kwargs: None)
    # Reuse the still-owned claim for a second faulty page, not a fabricated hint.
    row = TaskRun.objects.get(pk=identifier)
    claim = TaskClaim(row.pk, row.fence, row.worker_id)
    with (
        pytest.raises(IntegrityError, match="recipient receipt"),
        task_login(ServiceRole.WORKER, exact=True),
        work_transaction(),
    ):
        prepare_page(claim, store, SECURITY)
    assert not OutboxMessage.objects.filter(purpose="security_event").exists()


def test_metadata_scheduler_cannot_read_addresses_or_prepare(routing):
    """The scheduler allocates opaque work from a count and sees no address."""
    from django.db import DatabaseError

    store, _, _, _ = routing
    granted(routing)
    (identifier,) = schedule()
    with task_login(ServiceRole.SCHEDULER, exact=True):
        with pytest.raises(PermissionError):
            fanout_handler(store, scheduler=True, owner=SECURITY).execute(None)
        for statement in (
            "SELECT recipients FROM stewardship_policy_security_event",
            "SELECT addresses FROM stewardship_security_cohort",
            "SELECT address FROM stewardship_security_recipient",
        ):
            with pytest.raises(DatabaseError), work_transaction():
                connection.cursor().execute(statement)
        with work_transaction(), connection.cursor() as cursor:
            cursor.execute(
                "SELECT recipient_count FROM stewardship_security_notifiable "
                "ORDER BY recipient_count"
            )
            # The fixture's own activations recorded events with nobody to
            # tell; only the grant above has recipients.
            counts = [row[0] for row in cursor.fetchall()]
            assert counts.count(2) == 1 and set(counts) == {0, 2}
    assert consume(identifier, store)
    with task_login(ServiceRole.SCHEDULER, exact=True), work_transaction():
        assert list(
            SecurityCohort.objects.values_list("recipient_count", flat=True)
        ) == [2]


def test_closed_content_compilers_agree_for_every_kind_mode_and_actor(routing):
    """The SQL twin recompiles every kind, both modes, with and without an actor."""
    store, actor, _, _ = routing
    rule = domain("partner.example", ("ministry_leader",))
    assert (
        change(
            store,
            store.active(),
            actor,
            [
                {
                    "operation": "add",
                    "section": "login_rules",
                    **address("new@example.org"),
                },
                {"operation": "add", "section": "login_rules", **rule},
            ],
        ).state
        == "applied"
    )
    widened = domain("partner.example", ("ministry_leader", "staff"))
    assert (
        change(
            store,
            store.active(),
            actor,
            [
                {
                    "operation": "update",
                    "section": "login_rules",
                    "id": rule["id"],
                    "values": widened["values"],
                }
            ],
        ).state
        == "applied"
    )
    replaced_key(routing)
    events = list(PolicySecurityEvent.objects.exclude(recipients=[]))
    assert sorted(event.kind for event in events) == [
        "administrator_granted",
        "backup_key_replaced",
        "domain_created",
        "domain_staff_granted",
    ]
    for present in (False, True):
        if present:
            PortalUser.objects.create(
                id=actor,
                google_subject="synthetic-actor",
                email="admin@example.org",
                verified_at=timezone.now(),
            )
        for event in events:
            for mode in SystemMode:
                with work_transaction():
                    alert = event_alert(event.pk, mode)
                    with connection.cursor() as cursor:
                        cursor.execute(
                            "SELECT stewardship_security_content_v1(%s,%s)",
                            [event.pk, mode.value],
                        )
                        compiled = cursor.fetchone()[0]
                if isinstance(compiled, str):
                    compiled = json.loads(compiled)
                assert compiled == asdict(render_security_alert(alert))
                assert ("By: admin@example.org" in compiled["text"]) is present
                assert ("By: Operator recovery" in compiled["text"]) is not present
    # Targets are validated addresses and domains, so no markup can reach the
    # body; the escape twin is still held to Python's escaping exactly.
    with connection.cursor() as cursor:
        cursor.execute("SELECT stewardship_security_escape_v1(%s)", ["a&<>\"'b"])
        assert cursor.fetchone()[0] == html.escape("a&<>\"'b")

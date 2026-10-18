"""Clear a halted mail sender, server side (ADM-13 PR 4a, #530).

The two mail consumers share one halt identity through their stop marker;
from their idle loop they record the halt, mail dispatch runs a mailbox
check an Administrator asked for, and once an Administrator's clear signal
names the halt each consumer lifts it, removes the marker only while it
still names that halt, and admits one probe. The three guards of frozen
migration 0115 are the boundary on their own: who may write each record,
the one live mailbox check, and every check the clear repeats under its
lock. ``system mail-clear-preview`` and ``system mail-clear`` run the
shared service functions on the restricted web login, with no recent
sign-in (#1547).
"""

import re
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path
from time import monotonic
from types import SimpleNamespace
from uuid import uuid4

import pytest
from django.db import DatabaseError, connection, transaction

from parishkit.stewardship import provider_checks
from parishkit.stewardship.accounts.automation_models import AutomationSession
from parishkit.stewardship.accounts.models import PortalSession
from parishkit.stewardship.audit.models import AuditEvent
from parishkit.stewardship.campaigns.schedule_models import ScheduleDefinition
from parishkit.stewardship.deployment import DeploymentProfile, ServiceRole
from parishkit.stewardship.family_delivery import ProviderHealth
from parishkit.stewardship.jobs import mail_halt
from parishkit.stewardship.jobs.family_mail_delivery_tasks import DeliveryCircuit
from parishkit.stewardship.jobs.mail_halt_models import (
    MailboxCheck,
    MailHalt,
    MailHaltClear,
)

from .auth_builders import signed_in, unguarded
from .automation_builders import paired
from .campaign_builders import campaign_clock
from .test_admin_test_sample_cli_postgresql import runner
from .test_background_grants_postgresql import task_login
from .test_delivery_resolution_postgresql import failed_delivery
from .test_family_mail_preparation_postgresql import family_mail  # noqa: F401

pytestmark = pytest.mark.django_db(transaction=True)
MIGRATIONS = Path(__file__).resolve().parents[3] / (
    "src/parishkit/stewardship/schema/migrations"
)
TEXT = (MIGRATIONS / "0115_mail_halt_clear.sql").read_text(encoding="utf-8")
CHECK = re.search(r"^DO \$check\$.*?^\$check\$;", TEXT, re.M | re.S)[0]


def now():
    """The database's clock."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT now()")
        return cursor.fetchone()[0]


def sql(statement, params=()):
    """Run one statement as the current login; return its first row."""
    with connection.cursor() as cursor:
        cursor.execute(statement, params)
        return cursor.fetchone() if cursor.description else None


@contextmanager
def refused(message):
    """A change refused with ``message`` and rolled back."""
    with pytest.raises(DatabaseError, match=message), transaction.atomic():
        yield


def sender_status(state="halted", age=timedelta(), started=timedelta(hours=1)):
    """A mail consumer's status record, as the schema owner writes it.

    Its process started ``started`` before it last reported (``age`` ago),
    so by default every halt dated within the last hour began during it.
    """
    sql(
        "INSERT INTO stewardship_service_status (id,service,process,target,"
        "started_at,reported_at,application_version,debug_logging,sender_state,"
        "sender_since) VALUES (%s,'mail-dispatch','main',NULL,now()-%s,now()-%s,"
        "'1.0.0',false,%s,now()-%s)",
        [uuid4(), age + started, age, state, age],
    )


def halt_row(began=timedelta(minutes=1), kind="before_data"):
    """A recorded halt, written as the schema owner."""
    return MailHalt.objects.create(id=uuid4(), began_at=now() - began, kind=kind)


def session():
    """The newest Admin session: ``(principal, session id)``."""
    row = PortalSession.objects.order_by("-authenticated_at").first()
    return row.principal_id, row.pk


def web_check(actor, session_id):
    """Ask for a mailbox check as the web login would."""
    with transaction.atomic():
        return MailboxCheck.objects.create(actor_id=actor, session_id=session_id)


def dispatch_settle(check, outcome="passed"):
    """Claim and settle ``check`` as mail dispatch would."""
    with task_login(ServiceRole.MAIL_DISPATCH, exact=True):
        with transaction.atomic():
            MailboxCheck.objects.filter(pk=check.pk).update(state="running")
        with transaction.atomic():
            MailboxCheck.objects.filter(pk=check.pk).update(state=outcome)


def web_clear(halt, actor, session_id):
    """Record a clear signal as the web login would."""
    with transaction.atomic():
        return MailHaltClear.objects.create(
            halt_id=halt.pk, actor_id=actor, session_id=session_id
        )


def test_only_mail_dispatch_records_a_halt(auth_service):
    """Its own start within the last hour; never changed, never deleted, and
    the web only reads halts."""
    with task_login(ServiceRole.MAIL_DISPATCH, exact=True):
        halt = uuid4()
        sql(
            "INSERT INTO stewardship_mail_halt (id,began_at,kind) "
            "VALUES (%s,statement_timestamp()-interval '10 seconds','after_data')",
            [halt],
        )
        with refused("starts within the last hour"):
            sql(
                "INSERT INTO stewardship_mail_halt (id,began_at,kind) "
                "VALUES (%s,statement_timestamp()-interval '2 hours','after_data')",
                [uuid4()],
            )
        with refused("starts within the last hour"):
            sql(
                "INSERT INTO stewardship_mail_halt (id,began_at,kind) "
                "VALUES (%s,statement_timestamp()+interval '1 minute','after_data')",
                [uuid4()],
            )
        with refused("mail_halt_kind"):
            sql(
                "INSERT INTO stewardship_mail_halt (id,kind) VALUES (%s,'guess')",
                [uuid4()],
            )
        with refused("permission denied"):
            sql("UPDATE stewardship_mail_halt SET kind='before_data'")
        with refused("permission denied"):
            sql("DELETE FROM stewardship_mail_halt")
        # Its own clear signals and mailbox check requests are the web's.
        with refused("permission denied"):
            sql(
                "INSERT INTO stewardship_mail_halt_clear (id,halt_id,actor_id,"
                "session_id) VALUES (%s,%s,%s,%s)",
                [uuid4(), halt, uuid4(), uuid4()],
            )
    row = MailHalt.objects.get(pk=halt)
    assert timedelta(seconds=5) < now() - row.began_at < timedelta(seconds=60)
    with task_login(ServiceRole.WEB, exact=True):
        assert MailHalt.objects.filter(pk=halt).exists()
        with refused("permission denied"):
            sql(
                "INSERT INTO stewardship_mail_halt (id,kind) VALUES (%s,'after_data')",
                [uuid4()],
            )
    # Not even the schema owner changes or deletes a halt.
    with refused("never changed or deleted"):
        MailHalt.objects.filter(pk=halt).update(kind="before_data")
    with refused("never changed or deleted"):
        MailHalt.objects.filter(pk=halt).delete()


def test_one_live_mailbox_check_claimed_and_settled_by_mail_dispatch(
    auth_service, google
):
    """The web asks; one live check at a time; only mail dispatch claims a
    check under two minutes old and settles it once; times are the
    database's."""
    signed_in()
    actor, session_id = session()
    with task_login(ServiceRole.WEB, exact=True):
        check = web_check(actor, session_id)
        with refused("already waiting or running"):
            web_check(actor, session_id)
        with refused("requires an Administrator"):
            web_check(uuid4(), session_id)
        with refused("permission denied"):
            MailboxCheck.objects.filter(pk=check.pk).update(state="running")
    check.refresh_from_db()
    assert check.state == "waiting" and check.claimed_at is None
    with task_login(ServiceRole.MAIL_DISPATCH, exact=True):
        with refused("moves only forward"):
            MailboxCheck.objects.filter(pk=check.pk).update(state="passed")
        with transaction.atomic():
            MailboxCheck.objects.filter(pk=check.pk).update(
                state="running", claimed_at=now() - timedelta(days=1)
            )
        check.refresh_from_db()
        assert check.claimed_at > now() - timedelta(minutes=1)
        with refused("keeps its claim"):
            MailboxCheck.objects.filter(pk=check.pk).update(
                state="passed", claimed_at=now()
            )
        with transaction.atomic():
            MailboxCheck.objects.filter(pk=check.pk).update(state="refused")
        with refused("moves only forward"):
            MailboxCheck.objects.filter(pk=check.pk).update(state="passed")
        with refused("permission denied"):
            MailboxCheck.objects.filter(pk=check.pk).delete()
    check.refresh_from_db()
    assert check.state == "refused" and check.finished_at is not None
    # A check that waited past two minutes is not live: another may be
    # asked for, and mail dispatch never runs the old one.
    with task_login(ServiceRole.WEB, exact=True):
        old = web_check(actor, session_id)
    with unguarded():
        MailboxCheck.objects.filter(pk=old.pk).update(
            created_at=now() - timedelta(minutes=3)
        )
    with task_login(ServiceRole.WEB, exact=True):
        web_check(actor, session_id)
    with (
        task_login(ServiceRole.MAIL_DISPATCH, exact=True),
        refused("can no longer be run"),
    ):
        MailboxCheck.objects.filter(pk=old.pk).update(state="running")


def test_the_clear_guard_repeats_every_check(auth_service, google, family_mail):  # noqa: F811
    """No current halt, no passed check after the halt began, an unsettled
    delivery_unknown email, another login or a non-Administrator: each is
    refused by the guard alone; then the clear is recorded once."""
    signed_in()
    actor, session_id = session()
    old = halt_row(began=timedelta(minutes=20))
    halt = halt_row()
    # No mail consumer reports Halted.
    with task_login(ServiceRole.WEB, exact=True), refused("no longer current"):
        web_clear(halt, actor, session_id)
    sender_status("halted", age=timedelta(minutes=5))
    with task_login(ServiceRole.WEB, exact=True), refused("no longer current"):
        web_clear(halt, actor, session_id)
    sender_status("halted")
    with task_login(ServiceRole.WEB, exact=True):
        # Only the newest halt is current.
        with refused("no longer current"):
            web_clear(old, actor, session_id)
        with refused("No mailbox check passed"):
            web_clear(halt, actor, session_id)
        check = web_check(actor, session_id)
    dispatch_settle(check, "passed")
    # A check claimed before the halt began does not count.
    with unguarded():
        MailboxCheck.objects.filter(pk=check.pk).update(
            claimed_at=halt.began_at - timedelta(seconds=1)
        )
    with task_login(ServiceRole.WEB, exact=True), refused("No mailbox check passed"):
        web_clear(halt, actor, session_id)
    # Nor one that passed more than five minutes ago.
    with unguarded():
        MailboxCheck.objects.filter(pk=check.pk).update(
            claimed_at=now(), finished_at=now() - timedelta(minutes=6)
        )
    with task_login(ServiceRole.WEB, exact=True), refused("No mailbox check passed"):
        web_clear(halt, actor, session_id)
    with unguarded():
        MailboxCheck.objects.filter(pk=check.pk).update(finished_at=now())
    # An email Gmail may have accepted is not settled yet.
    with campaign_clock(ScheduleDefinition.objects.get().current_revision.due_at):
        message = failed_delivery(family_mail)
    assert message.state == "delivery_unknown"
    with task_login(ServiceRole.WEB, exact=True), refused("not settled yet"):
        web_clear(halt, actor, session_id)
    # Settled (written directly: the delivery workflow's own tests cover how).
    with unguarded():
        type(message).objects.filter(pk=message.pk).update(
            state="delivered",
            finished_at=now(),
            sealed_substitutions=None,
            sealed_key_id=None,
        )
    # No recent sign-in is required (#1547).
    with unguarded():
        PortalSession.objects.filter(pk=session_id).update(
            authenticated_at=now() - timedelta(minutes=30)
        )
    with task_login(ServiceRole.WEB, exact=True):
        with refused("requires an Administrator"):
            web_clear(halt, uuid4(), session_id)
        with refused("requires an Administrator"):
            web_clear(halt, actor, uuid4())
        signal = web_clear(halt, actor, session_id)
        with refused("already cleared|no longer current"):
            web_clear(halt, actor, session_id)
        with refused("permission denied"):
            MailHaltClear.objects.filter(pk=signal.pk).delete()
    signal.refresh_from_db()
    assert signal.created_at > now() - timedelta(minutes=1)
    # Mail dispatch reads the signal and writes none.
    with task_login(ServiceRole.MAIL_DISPATCH, exact=True):
        assert MailHaltClear.objects.filter(halt_id=halt.pk).exists()
        with refused("permission denied"):
            web_clear(halt_row(), actor, session_id)
    with refused("never changed or deleted"):
        MailHaltClear.objects.filter(pk=signal.pk).update(actor_id=uuid4())


def passed_check(actor, session_id, outcome="passed"):
    """A mailbox check asked for by the web and settled by mail dispatch."""
    with task_login(ServiceRole.WEB, exact=True):
        check = web_check(actor, session_id)
    dispatch_settle(check, outcome)
    return check


def test_only_the_newest_mailbox_check_counts(auth_service, google):
    """A later refused or unavailable check cancels an earlier pass, in the
    guard alone and in the preview's rule."""
    from parishkit.stewardship.mail_halt_clear import _newest_check, current_halt
    from parishkit.stewardship.mail_halt_clear import reason as why

    signed_in()
    actor, session_id = session()
    halt = halt_row()
    sender_status("halted")
    passed_check(actor, session_id)
    for outcome in ("refused", "unavailable"):
        later = passed_check(actor, session_id, outcome)
        with (
            task_login(ServiceRole.WEB, exact=True),
            refused("No mailbox check passed"),
        ):
            web_clear(halt, actor, session_id)
        moment = now()
        assert why(current_halt(moment), _newest_check(moment), 0, moment) == (
            "check_failed"
        )
        assert _newest_check(moment).check_id == later.pk
    passed_check(actor, session_id)
    with task_login(ServiceRole.WEB, exact=True):
        web_clear(halt, actor, session_id)


def test_a_split_halt_is_cleared_one_halt_at_a_time(auth_service, google):
    """Two consumers holding different halts: the newest uncleared halt is
    current, so after one is cleared the other can be, without a restart. A
    halt that began before the halted process started is never current."""
    from parishkit.stewardship.mail_halt_clear import current_halt

    signed_in()
    actor, session_id = session()
    # A halt left from before the restart: the new process started after it.
    stale = halt_row(began=timedelta(minutes=30))
    older, newer = halt_row(began=timedelta(minutes=3)), halt_row()
    sender_status("halted", started=timedelta(minutes=10))
    passed_check(actor, session_id)
    assert current_halt(now()).halt_id == newer.pk
    with task_login(ServiceRole.WEB, exact=True):
        with refused("no longer current"):
            web_clear(older, actor, session_id)
        web_clear(newer, actor, session_id)
    assert current_halt(now()).halt_id == older.pk
    with task_login(ServiceRole.WEB, exact=True):
        with refused("already cleared"):
            web_clear(newer, actor, session_id)
        with refused("no longer current"):
            web_clear(stale, actor, session_id)
        web_clear(older, actor, session_id)
    assert current_halt(now()) is None
    with task_login(ServiceRole.WEB, exact=True), refused("no longer current"):
        web_clear(stale, actor, session_id)


def test_the_guards_refuse_an_enabled_non_administrator(auth_service, google):
    """A real enabled Staff user with a live session (an Administrator whose
    role became Staff after signing in) may neither ask for a mailbox check
    nor clear a halt; the guards alone refuse both."""
    signed_in()
    actor, session_id = session()
    halt = halt_row()
    sender_status("halted")
    passed_check(actor, session_id)
    with unguarded(), connection.cursor() as cursor:
        cursor.execute(
            "UPDATE stewardship_address_rule SET roles='[\"staff\"]'::jsonb "
            "WHERE email='admin@example.org'"
        )
    login = PortalSession.objects.get(pk=session_id)
    assert login.revoked_at is None and login.expires_at > now()
    # Enabled, and allowed what Staff may do, but not an Administrator.
    assert sql(
        "SELECT NOT u.disabled, public.stewardship_export_authorized_v1(u.id),"
        " public.stewardship_export_authorized_v1(u.id,true)"
        " FROM stewardship_portal_user u WHERE u.id=%s",
        [actor],
    ) == (True, True, False)
    with task_login(ServiceRole.WEB, exact=True):
        with refused("requires an Administrator"):
            web_check(actor, session_id)
        with refused("requires an Administrator"):
            web_clear(halt, actor, session_id)
    assert not MailHaltClear.objects.exists()


def circuits(tmp_path):
    """The container's two Family mail circuits, sharing one stop marker."""
    marker = tmp_path / "private" / "mail-systemic-stop"
    return marker, [
        DeliveryCircuit(recovery_seconds=600, systemic_stops=True, shared_stop=marker)
        for _ in range(2)
    ]


def as_dispatch(function, *args, **kwargs):
    """Call ``function`` on the mail dispatch login (on every connection it opens)."""
    with task_login(ServiceRole.MAIL_DISPATCH, exact=True, reconnect=True):
        return function(*args, **kwargs)


@pytest.fixture
def admin(auth_service, google, monkeypatch):
    """Commands over the authentication service the page runs on."""
    return runner(auth_service, monkeypatch)


def test_the_consumers_share_record_and_lift_one_halt(admin, tmp_path, monkeypatch):
    """The handshake end to end: one identity in the marker, one halt row,
    the mailbox check through the helper, the commands' preview and clear,
    and both consumers lifting it with one probe each."""
    marker, (first, second) = circuits(tmp_path)
    assert first.observe(ProviderHealth.SYSTEMIC, uncertain=True) is True
    assert second.blocks_new_send() is True
    assert first.current_halt() == second.current_halt()
    halt_id, kind, _ = first.current_halt()
    assert kind == "after_data"
    # The second consumer's own SYSTEMIC result keeps the shared identity.
    second.observe(ProviderHealth.SYSTEMIC)
    assert second.current_halt()[0] == halt_id
    # Both record it from their idle loop; the first row stands.
    assert as_dispatch(mail_halt.sync, first) is False
    assert as_dispatch(mail_halt.sync, second) is False
    [row] = MailHalt.objects.all()
    assert (row.pk, row.kind) == (halt_id, "after_data")
    sender_status("halted")
    _, secret, session_row = paired(admin.service)
    # A recent sign-in is not needed (#1547): an old one is fine.
    older = session_row.authenticated_at - timedelta(minutes=30)
    with unguarded():
        AutomationSession.objects.filter(pk=session_row.pk).update(
            authenticated_at=older
        )
        PortalSession.objects.filter(principal_id=session_row.principal_id).update(
            authenticated_at=older
        )
    code, document, _ = admin(
        "system", "mail-clear-preview", "--no-wait", secret=secret
    )
    assert code == 0, document
    result = document["result"]
    assert result["halt"]["halt_id"] == str(halt_id)
    assert result["halt"]["kind"] == "after_data"
    assert result["mailbox_check"]["status"] == "waiting"
    assert result["reason"] == "checking" and result["preview"]["token"] is None
    # Opening it again while the check is live asks for no second one.
    admin("system", "mail-clear-preview", "--no-wait", secret=secret)
    assert MailboxCheck.objects.count() == 1
    assert not AuditEvent.objects.filter(event_type__startswith="admin_cmd_system_mail")
    # Mail dispatch's main process runs it through the helper.
    calls = []

    def helper(target, settings, value, *, seconds, check, profile):
        """The private helper's answer: Google accepted the sign-in."""
        calls.append((target, settings["delegated_email"], seconds, profile))
        return True

    monkeypatch.setattr(provider_checks, "check_candidate", helper)
    credential = tmp_path / "workspace.json"
    credential.write_bytes(b"{}")
    credential.chmod(0o600)
    monkeypatch.setattr(
        mail_halt,
        "_workspace_settings",
        lambda: dict.fromkeys(
            ("delegated_email", "sender", "reply_to", "recipient"), "mail@example.org"
        ),
    )
    # Not halted, and no status record says Halted: the waiting check is
    # never claimed (nothing to clear).
    with unguarded():
        sql("UPDATE stewardship_service_status SET sender_state='running'")
    assert (
        as_dispatch(
            mail_halt.run_mailbox_check,
            credential,
            DeploymentProfile.LOCAL,
            check=lambda: None,
            halted=False,
        )
        is None
    )
    assert MailboxCheck.objects.get().state == "waiting" and calls == []
    with unguarded():
        sql("UPDATE stewardship_service_status SET sender_state='halted'")
    # The main process may be idle and not yet following the other's
    # marker: the marker alone makes it run the check.
    assert mail_halt._halted(SimpleNamespace(stopped=False, shared_stop=marker))
    outcome = as_dispatch(
        mail_halt.run_mailbox_check,
        credential,
        DeploymentProfile.LOCAL,
        check=lambda: None,
        halted=mail_halt._halted(first),
    )
    assert outcome == "passed"
    assert calls == [
        ("google_workspace", "mail@example.org", 20, DeploymentProfile.LOCAL)
    ]
    assert (
        as_dispatch(
            mail_halt.run_mailbox_check, credential, None, check=None, halted=True
        )
        is None
    )
    code, document, _ = admin(
        "system", "mail-clear-preview", "--no-wait", secret=secret
    )
    result = document["result"]
    assert result["mailbox_check"]["status"] == "passed" and result["reason"] is None
    token = result["preview"]["token"]
    # The confirmation always asks first.
    code, refusal, errors = admin(
        "system", "mail-clear", "--token", token, secret=secret
    )
    assert code == 4 and refusal["error"]["code"] == "confirmation_required"
    assert "Clear the mail sender's halt" in errors
    assert not MailHaltClear.objects.exists()
    code, document, _ = admin(
        "system", "mail-clear", "--token", token, secret=secret, answer=b"yes\n"
    )
    assert code == 0, document
    assert document["result"]["halt_id"] == str(halt_id)
    assert document["result"]["created"] is True
    [signal] = MailHaltClear.objects.all()
    [cleared] = AuditEvent.objects.filter(event_type="mail_halt_cleared")
    assert cleared.subject_id == signal.pk
    assert cleared.actor_id == session_row.principal_id
    [command] = AuditEvent.objects.filter(event_type="admin_cmd_system_mail_clear")
    assert command.subject_id == session_row.pk
    # A repeat returns the first signal and records nothing new.
    code, again, _ = admin(
        "system", "mail-clear", "--token", token, "--yes", secret=secret
    )
    assert code == 0 and again["result"]["created"] is False
    assert AuditEvent.objects.filter(event_type="mail_halt_cleared").count() == 1
    # The first consumer lifts it and removes the marker; one probe is let
    # through, and an outage result then pauses at once.
    assert as_dispatch(mail_halt.sync, first) is True
    assert not marker.exists() and first.current_halt() is None
    assert first.blocks_new_send() is False and first.sender_state()[0] == "running"
    # Its probe meets the fault again before the second consumer has
    # looked: a new halt, under a new identity.
    first.observe(ProviderHealth.SYSTEMIC)
    renewed = first.current_halt()[0]
    assert renewed != halt_id and marker.exists()
    # The second lifts the cleared halt, leaves the new marker, and then
    # follows the new halt.
    assert as_dispatch(mail_halt.sync, second) is True
    assert marker.exists()
    assert second.blocks_new_send() is True
    assert second.current_halt()[0] == renewed
    # A clear of the old halt never lifts the new one.
    assert second.lift(halt_id) is False and first.lift(halt_id) is False


def test_a_second_consumer_halted_without_a_marker_still_gets_its_check(
    auth_service, google, tmp_path, monkeypatch
):
    """The second consumer is halted, but no marker exists (its write
    failed, or a split halt's marker went with the other halt's clear), so
    the main process sees nothing halted locally. Its status record says
    Halted, and that database fact alone makes the main process run the
    mailbox check, so the halt can be cleared without a restart."""
    marker, (main, second) = circuits(tmp_path)
    assert second.observe(ProviderHealth.SYSTEMIC) is True
    marker.unlink()
    assert second.stopped and not mail_halt._halted(main)
    signed_in()
    actor, session_id = session()
    with task_login(ServiceRole.WEB, exact=True):
        check = web_check(actor, session_id)
    monkeypatch.setattr(mail_halt, "check_mailbox", lambda *_, **__: "passed")

    def run():
        """The main process's mailbox check pass, as its idle loop runs it."""
        return as_dispatch(
            mail_halt.run_mailbox_check,
            tmp_path / "workspace.json",
            None,
            check=None,
            halted=mail_halt._halted(main),
        )

    # No record says Halted (none at all, a running one, or a Halted one
    # older than three minutes): the check stays waiting.
    assert run() is None
    sender_status("running")
    sender_status("halted", age=timedelta(minutes=4))
    assert run() is None
    check.refresh_from_db()
    assert check.state == "waiting"
    sender_status("halted")
    assert run() == "passed"
    check.refresh_from_db()
    assert check.state == "passed" and check.claimed_at is not None


def test_a_preview_without_a_halt_is_stale(admin):
    """Nothing halted: the preview and the confirmation are refused."""
    _, secret, _ = paired(admin.service)
    code, document, _ = admin(
        "system", "mail-clear-preview", "--no-wait", secret=secret
    )
    assert code == 1 and document["error"]["code"] == "stale_version"
    code, document, _ = admin(
        "system", "mail-clear", "--token", "not-a-token", "--yes", secret=secret
    )
    assert code == 1 and document["error"]["code"] == "invalid"
    assert not MailboxCheck.objects.exists() and not MailHaltClear.objects.exists()


def test_a_marker_from_before_the_upgrade_still_halts(tmp_path):
    """An older release's marker names no halt: the consumer stops, and has
    no halt to record or lift until the container restarts."""
    marker, (first, _) = circuits(tmp_path)
    marker.parent.mkdir(mode=0o700)
    marker.write_bytes(b"stopped")
    marker.chmod(0o600)
    assert first.blocks_new_send() is True
    assert first.current_halt() is None
    assert mail_halt.sync(first) is False
    assert first.sender_state()[0] == "halted"


def test_a_halt_records_its_start_from_the_decision(tmp_path):
    """The halt row's start is the moment the consumer halted, not the moment
    its idle loop wrote it."""
    _, (first, _) = circuits(tmp_path)
    first.observe(ProviderHealth.SYSTEMIC)
    halt_id, kind, at = first.current_halt()
    first.halt = (halt_id, kind, at - 120)
    assert as_dispatch(mail_halt.sync, first) is False
    row = MailHalt.objects.get(pk=halt_id)
    assert row.kind == "before_data"
    assert timedelta(seconds=110) < now() - row.began_at < timedelta(seconds=180)
    # Recorded once: the next idle pass only reads the signal.
    first.observe(ProviderHealth.SYSTEMIC)
    assert first.recorded == halt_id
    assert monotonic() > at - 120


def test_the_migration_check_passes_and_refuses_drift():
    """0115's DO block passes as installed and raises on a guard that lost a
    boundary, a definer guard, a PUBLIC grant or a missing trigger; all
    rolled back."""
    start = TEXT.index("CREATE FUNCTION public.stewardship_mail_halt_clear_guard_v1(")
    guard = TEXT[start : TEXT.index("END $$;", start) + len("END $$;")].replace(
        "CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1
    )
    start = TEXT.index("CREATE FUNCTION public.stewardship_mailbox_check_guard_v1(")
    check_guard = TEXT[start : TEXT.index("END $$;", start) + len("END $$;")].replace(
        "CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1
    )
    unknown = "WHERE o.state='delivery_unknown'"
    assert unknown in guard
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(CHECK)
        transaction.set_rollback(True)
    for change_sql in (
        guard.replace(unknown, "WHERE false").replace("delivery_unknown", "unknown"),
        guard.replace("pg_advisory_xact_lock(736241, 6)", "pg_sleep(0)"),
        guard.replace("'pk_stewardship_web'", "'pk_stewardship_worker'"),
        guard.replace("NEW.actor_id,true", "NEW.actor_id,false"),
        guard.replace("login.principal_id=NEW.actor_id", "true"),
        guard.replace("ORDER BY m.created_at DESC, m.id DESC LIMIT 1", ""),
        guard.replace("+interval '1 minute'", "+interval '1 day'"),
        check_guard.replace("'pk_stewardship_mail_dispatch'", "'pk_stewardship_web'"),
        check_guard.replace("NEW.actor_id,true", "NEW.actor_id,false"),
        check_guard.replace("'pk_stewardship_web'", "'pk_stewardship_worker'"),
        check_guard.replace("login.principal_id=NEW.actor_id", "true"),
        "ALTER FUNCTION public.stewardship_mailbox_check_guard_v1() SECURITY DEFINER",
        "GRANT EXECUTE ON FUNCTION public.stewardship_mail_halt_guard_v1() TO PUBLIC",
        "GRANT SELECT ON public.stewardship_mail_halt_clear TO PUBLIC",
        "DROP TRIGGER stewardship_mail_halt_guard ON public.stewardship_mail_halt",
    ):
        with (
            connection.cursor() as cursor,
            pytest.raises(DatabaseError, match="0115"),
            transaction.atomic(),
        ):
            cursor.execute(change_sql)
            cursor.execute(CHECK)
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(CHECK)
        transaction.set_rollback(True)

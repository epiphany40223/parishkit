"""An automation session passing a fresh gate is recorded (ADM-11 PR 5b).

Once per state-changing command, in the action's own transaction: the
``automation_fresh_gate`` audit event (the Administrator as actor, the
automation session as subject) and a dashboard notice naming the command,
``irreversible`` with its ``automation_irreversible`` incident for the
Production confirmation and pre-start withdrawal, ``fresh_gated`` otherwise.
A read or preview (no command type) records nothing, and a rolled-back
action leaves no trace.
"""

import pytest
from django.db import transaction

from parishkit.stewardship.accounts import sessions
from parishkit.stewardship.accounts.automation_models import AutomationNotice
from parishkit.stewardship.audit.models import AuditEvent
from parishkit.stewardship.jobs.operational_models import OperationalIncident
from parishkit.stewardship.observability import correlation

from .automation_builders import command, paired

pytestmark = pytest.mark.django_db(transaction=True)

EVENT = "automation_fresh_gate"


class Rollback(Exception):
    """Abandon the action's transaction."""


def recorded():
    """The fresh-gate audit events and the notices, apart from approval's."""
    return (
        list(AuditEvent.objects.filter(event_type=EVENT)),
        list(AutomationNotice.objects.exclude(kind="approved").order_by("created_at")),
    )


def test_a_fresh_gated_command_is_recorded_once(auth_service, google):
    """One event and one fresh_gated notice per command, however many gates."""
    _, secret, row = paired(auth_service)
    # The command line binds one correlation per invocation before admission.
    with correlation():
        caller = command(auth_service, secret)
        caller.command_type = "admin_cmd_send_confirm"
        with transaction.atomic():
            sessions.require_fresh(caller)
            sessions.require_fresh(caller)
    [event], [notice] = recorded()
    assert event.actor_id == row.principal_id and event.subject_id == row.pk
    assert notice.kind == "fresh_gated"
    assert notice.automation_session_id == row.pk
    assert notice.command_type == "admin_cmd_send_confirm"
    assert not OperationalIncident.objects.filter(
        kind="automation_irreversible"
    ).exists()


def test_an_irreversible_action_opens_its_incident(auth_service, google):
    """The Production confirmation and withdrawal: irreversible, emailed."""
    _, secret, _ = paired(auth_service)
    caller = command(auth_service, secret)
    caller.command_type = "admin_cmd_go_live_confirm"
    with transaction.atomic():
        sessions.require_fresh(caller, irreversible=True)
    [_], [notice] = recorded()
    assert notice.kind == "irreversible"
    assert OperationalIncident.objects.filter(kind="automation_irreversible").exists()


def test_reads_previews_and_rollbacks_record_nothing(auth_service, google):
    """No command type, or an action that rolled back: no event, no notice."""
    _, secret, _ = paired(auth_service)
    caller = command(auth_service, secret)
    with transaction.atomic():
        sessions.require_fresh(caller)
    assert recorded() == ([], [])
    caller.command_type = "admin_cmd_send_confirm"
    with pytest.raises(Rollback), transaction.atomic():
        sessions.require_fresh(caller)
        raise Rollback
    assert recorded() == ([], [])
    # A retried attempt after the rollback records it.
    with transaction.atomic():
        sessions.require_fresh(caller)
    assert len(recorded()[1]) == 1


def test_an_irreversible_gate_after_a_fresh_one_still_records(auth_service, google):
    """Dedupe is per kind: the irreversible notice and incident still come."""
    _, secret, _ = paired(auth_service)
    with correlation():
        caller = command(auth_service, secret)
        caller.command_type = "admin_cmd_go_live_confirm"
        with transaction.atomic():
            sessions.require_fresh(caller)
            sessions.require_fresh(caller, irreversible=True)
            sessions.require_fresh(caller, irreversible=True)
    events, notices = recorded()
    assert len(events) == 2
    assert [notice.kind for notice in notices] == ["fresh_gated", "irreversible"]
    assert OperationalIncident.objects.filter(kind="automation_irreversible").exists()


def test_a_passive_probe_records_nothing(auth_service, google):
    """record=False: a page asking whether a sign-in is fresh is no action."""
    _, secret, _ = paired(auth_service)
    caller = command(auth_service, secret)
    caller.command_type = "admin_cmd_send_pause"
    with transaction.atomic():
        sessions.require_fresh(caller, record=False)
    assert recorded() == ([], [])


@pytest.mark.parametrize(
    "module,function,probe",
    [
        ("confirmation_commands", "confirm", None),
        ("withdrawal_commands", "withdraw", "page"),
        ("delivery_control_commands", None, "page"),
    ],
)
def test_the_call_sites_mark_irreversible_actions_and_passive_probes(
    module, function, probe
):
    """Confirmation and withdrawal are irreversible; status pages only probe."""
    import inspect
    from importlib import import_module

    source = import_module(f"parishkit.stewardship.accounts.{module}")
    if function is not None:
        assert "require_fresh(request, irreversible=True)" in inspect.getsource(
            getattr(source, function)
        )
    if probe is not None:
        assert "require_fresh(request, record=False)" in inspect.getsource(
            getattr(source, probe)
        )

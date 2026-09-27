"""The "Finishing setup" steps explain every finalization state in plain words."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest

from parishkit.stewardship.accounts.setup_finishing import finishing
from parishkit.stewardship.campaigns.domain import Percentage

NOW = datetime(2026, 9, 27, 12, 0, tzinfo=UTC)


def status(**changes):
    """Frozen setup just confirmed by a fresh sign-in, with nothing started."""
    return {
        "attempt": SimpleNamespace(state="frozen"),
        "checkpoint": "staged",
        "failure_code": "",
        "confirmed_at": NOW - timedelta(minutes=3, seconds=10),
        "authenticated_at": NOW - timedelta(minutes=1),
        "server_now": NOW,
        "targets": ["parishsoft", "google_workspace"],
        "credentials": [],
        "source": None,
        "prepared": False,
    } | changes


def credential(target, state, *, acknowledged=0, consumers=2, reason=""):
    """One credential install row as finalization_status reads it."""
    return {
        "target": target,
        "request_id": uuid4(),
        "request__state": state,
        "request__cleanup_reason": reason,
        "consumers": consumers,
        "acknowledged": acknowledged,
    }


def by_key(result):
    """Step rows keyed by step name."""
    return {row["key"]: row for row in result["steps"]}


def test_just_confirmed_setup_is_working_and_waiting():
    """Before any installer runs, every step waits and nothing asks for sign-in."""
    result = finishing(status())
    rows = by_key(result)
    assert list(rows) == [
        "parishsoft",
        "google_workspace",
        "configuration",
        "source",
        "completed",
    ]
    assert {row["state"] for row in rows.values()} == {"waiting"}
    assert result["overall"] == "working" and not result["reauthenticate"]
    assert result["elapsed"] == 190 and result["elapsed_minutes"] == 3


@pytest.mark.parametrize(
    "state,expected,text",
    [
        ("staged", "active", "Queued"),
        ("testing", "active", "Checking the credential"),
        ("installing", "active", "Installing the credential"),
        ("expired", "failed", "rolled back"),
        ("cancelled", "failed", "rolled back"),
    ],
)
def test_credential_states_have_plain_language(state, expected, text):
    """Each installer state names what is happening to that credential."""
    rows = by_key(finishing(status(credentials=[credential("parishsoft", state)])))
    assert rows["parishsoft"]["state"] == expected
    assert text in str(rows["parishsoft"]["text"])


def test_awaiting_operator_acknowledgement_counts_services():
    """An unacknowledged install waits on the operator, not on the Admin."""
    result = finishing(
        status(
            credentials=[
                credential("parishsoft", "awaiting_ack", acknowledged=1),
                credential("google_workspace", "awaiting_ack", acknowledged=2),
            ]
        )
    )
    rows = by_key(result)
    assert rows["parishsoft"]["state"] == "operator"
    assert rows["parishsoft"]["count"] == {"done": 1, "total": 2}
    assert "deployment runbook" in str(rows["parishsoft"]["text"])
    assert rows["google_workspace"]["state"] == "done"
    assert rows["configuration"]["state"] == "waiting"
    assert result["overall"] == "operator"


def test_acknowledged_credentials_start_configuration_then_source():
    """After every acknowledgement the configuration and final load follow."""
    installed = [
        credential("parishsoft", "awaiting_ack", acknowledged=2),
        credential("google_workspace", "awaiting_ack", acknowledged=2),
    ]
    assert by_key(finishing(status(credentials=installed)))["configuration"][
        "state"
    ] == ("active")
    source = {"id": uuid4(), "state": "running", "progress": Percentage(3, 10)}
    rows = by_key(
        finishing(status(credentials=installed, prepared=True, source=source))
    )
    assert rows["configuration"]["state"] == "done"
    assert rows["source"]["state"] == "active"
    assert rows["source"]["progress"] == Percentage(3, 10)
    assert rows["source"]["task"] == source["id"]


def test_completion_marks_every_step_done():
    """A configured system shows setup complete."""
    result = finishing(status(prepared=True), completed=True)
    rows = by_key(result)
    assert rows["completed"]["state"] == "done"
    assert rows["source"]["state"] == "done"
    assert result["overall"] == "completed"


def test_old_sign_in_asks_to_sign_in_again_while_a_credential_cannot_start():
    """The installer needs a sign-in from the last five minutes to begin."""
    old = status(authenticated_at=NOW - timedelta(minutes=6))
    assert finishing(old)["reauthenticate"]
    started = [
        credential("parishsoft", "staged"),
        credential("google_workspace", "staged"),
    ]
    assert not finishing(old | {"credentials": started})["reauthenticate"]
    assert not finishing(old | {"attempt": SimpleNamespace(state="expired")})[
        "reauthenticate"
    ]


@pytest.mark.parametrize(
    "code,text",
    [
        ("stale_base", "configuration changed"),
        ("invalid_candidate", "could not be validated"),
        ("actor_unauthorized", "Administrator access"),
    ],
)
def test_configuration_failures_explain_what_to_do(code, text):
    """A failed installer checkpoint explains itself and fails the whole page."""
    result = finishing(status(checkpoint="failed", failure_code=code))
    assert by_key(result)["configuration"]["state"] == "failed"
    assert text in str(by_key(result)["configuration"]["text"])
    assert result["overall"] == "failed"


def test_failed_final_load_gives_a_task_reference():
    """A failed final load names its task so the operator can find its logs."""
    source = {"id": uuid4(), "state": "failed", "progress": Percentage(0, 0)}
    rows = by_key(finishing(status(prepared=True, source=source)))
    assert rows["source"]["state"] == "failed"
    assert rows["source"]["task"] == source["id"]


def test_signature_changes_only_with_what_the_page_shows():
    """Polling reloads for a visible change, not for the passing of time."""
    first = finishing(status())
    later = finishing(status(server_now=NOW + timedelta(seconds=30)))
    assert first["signature"] == later["signature"]
    changed = finishing(status(credentials=[credential("parishsoft", "testing")]))
    assert changed["signature"] != first["signature"]


def test_enabled_slack_and_unexpected_rows_are_listed():
    """Slack appears when enabled, and any installed target is always shown."""
    rows = by_key(
        finishing(
            status(
                targets=["parishsoft", "google_workspace", "slack"],
                credentials=[credential("slack", "staged")],
            )
        )
    )
    assert rows["slack"]["state"] == "active"

"""The "Finishing setup" steps explain every finalization state in plain words."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest

from parishkit.stewardship.accounts import stages
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
    """Before any installer runs, every step waits."""
    result = finishing(status())
    rows = by_key(result)
    assert list(rows) == [
        "parishsoft",
        "google_workspace",
        "configuration",
        "start",
        "source",
        "completed",
    ]
    assert {row["state"] for row in rows.values()} == {"waiting"}
    assert result["overall"] == "working"
    assert "reauthenticate" not in result
    assert result["elapsed"] == 190


@pytest.mark.parametrize(
    "state,expected,text",
    [
        ("staged", "active", "Queued"),
        ("testing", "active", "Checking the credential"),
        ("installing", "active", "Installing the credential"),
        ("expired", "failed", "not installed in time"),
        ("cancelled", "failed", "installation was cancelled"),
    ],
)
def test_credential_states_have_plain_language(state, expected, text):
    """Each installer state names what is happening to that credential."""
    rows = by_key(finishing(status(credentials=[credential("parishsoft", state)])))
    assert rows["parishsoft"]["state"] == expected
    assert text in str(rows["parishsoft"]["text"])


@pytest.mark.parametrize(
    "target,check",
    [
        ("parishsoft", "Check the key and the organization ID"),
        ("google_workspace", "domain-wide delegation"),
        ("slack", "invited to the channel"),
    ],
)
@pytest.mark.parametrize(
    "state,reason", [("failed", ""), ("cleanup_pending", "failed")]
)
def test_rejected_credential_says_what_to_check(target, check, state, reason):
    """A provider rejection names what to check and how to try again (#456 L2).

    It reads the same while the installer is still removing the rejected
    file (cleanup pending) as once it is done.
    """
    result = finishing(
        status(targets=[target], credentials=[credential(target, state, reason=reason)])
    )
    text = str(by_key(result)[target]["text"])
    assert by_key(result)[target]["state"] == "failed"
    assert check in text and "did not accept" in text
    assert "cancel this setup and start a new one" in text
    assert result["overall"] == "failed"


@pytest.mark.parametrize(
    "reason,text",
    [
        ("expired", "credential installers are running"),
        ("cancelled", "installation was cancelled"),
        ("unknown", "was not installed"),
    ],
)
def test_cleanup_reason_explains_a_credential_being_removed(reason, text):
    """While cleanup is pending, its reason says why the credential failed."""
    item = credential("parishsoft", "cleanup_pending", reason=reason)
    rows = by_key(finishing(status(credentials=[item])))
    assert rows["parishsoft"]["state"] == "failed"
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
    source = {
        "id": uuid4(),
        "state": "running",
        "phase": "fetching",
        "progress": Percentage(3, 10),
    }
    rows = by_key(
        finishing(status(credentials=installed, prepared=True, source=source))
    )
    assert rows["configuration"]["state"] == "done"
    assert rows["start"]["state"] == "done"
    assert rows["source"]["state"] == "active"
    assert rows["source"]["progress"] == Percentage(3, 10)
    # No step carries its background task's reference (#1969).
    assert "task" not in rows["source"]


def test_completion_marks_every_step_done():
    """A configured system shows setup complete."""
    result = finishing(status(prepared=True), completed=True)
    rows = by_key(result)
    assert rows["completed"]["state"] == "done"
    assert rows["source"]["state"] == "done"
    assert result["overall"] == "completed"


def test_an_old_sign_in_changes_nothing_while_credentials_wait():
    """An installer may start however old the original sign-in is (#1547)."""
    old = status(authenticated_at=NOW - timedelta(hours=3))
    assert finishing(old) == finishing(status())


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
    source = {
        "id": uuid4(),
        "state": "failed",
        "phase": "fetching",
        "progress": Percentage(0, 0),
    }
    rows = by_key(finishing(status(prepared=True, source=source)))
    assert rows["start"]["state"] == "done"
    assert rows["source"]["state"] == "failed"
    assert "task" not in rows["source"]
    assert "failed final parish data load" in str(rows["source"]["text"])


def queued_source(state="queued", phase="unspecified"):
    """The final load as finalization_status reads it, before a worker took it."""
    return {"id": uuid4(), "state": state, "phase": phase, "progress": None}


def test_a_queued_final_load_is_in_start_the_load():
    """Queued: Start the load in progress with the shared sentence (#1916).

    The final load's own step waits until a worker picks it up, and then
    Start the load is done and the load in progress at once.
    """
    from parishkit.stewardship.accounts.stages import QUEUED

    source = queued_source()
    rows = by_key(finishing(status(prepared=True, source=source)))
    assert rows["start"]["state"] == "active"
    assert rows["start"]["status"] == "In progress"
    assert rows["start"]["text"] == QUEUED
    assert str(QUEUED) == (
        "Waiting for the background service to pick up the job. This can take "
        "a minute or two."
    )
    assert rows["source"]["state"] == "waiting" and "task" not in rows["source"]
    started = by_key(
        finishing(status(prepared=True, source=queued_source("running", "starting")))
    )
    assert started["start"]["state"] == "done"
    assert started["source"]["state"] == "active"


def test_a_final_load_that_ended_before_pickup_fails_start_the_load():
    """A final load no worker ever took fails on Start the load, which shows
    no task reference (#1969)."""
    source = queued_source("cancelled")
    result = finishing(status(prepared=True, source=source))
    rows = by_key(result)
    assert rows["start"]["state"] == "failed"
    assert "task" not in rows["start"]
    assert rows["source"]["state"] == "waiting"
    assert result["overall"] == "failed"


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


def forms(row):
    """A row's reserved forms as (state, text) pairs."""
    return {(form["state"], str(form["text"])) for form in row["reserve"]}


def test_each_box_reserves_every_running_form():
    """Every step's box reserves each form it can take while setup runs,
    the one it shows included, so its height holds as it moves on
    (#1903); Setup complete reserves nothing, since the page's result
    replaces the boxes."""
    rows = by_key(finishing(status()))
    credential_forms = forms(rows["parishsoft"])
    assert {state for state, _text in credential_forms} == {
        "waiting",
        "active",
        "operator",
        "done",
    }
    assert len(credential_forms) == 6
    operator = next(
        form for form in rows["parishsoft"]["reserve"] if form["state"] == "operator"
    )
    assert operator["count"] == {"done": 8, "total": 8}
    assert operator["status"] == "Waiting for the server operator"
    assert forms(rows["configuration"]) == {
        ("waiting", "Starts after every credential is installed and acknowledged."),
        ("active", "Preparing the new configuration."),
        ("done", "The configuration is prepared."),
    }
    # Start the load and the final load reserve the words that swap when a
    # worker picks the final load up (#1916), so no box resizes at that moment.
    assert forms(rows["start"]) == {
        ("waiting", "Starts after the configuration is prepared."),
        ("active", str(stages.QUEUED)),
        ("done", "The background service started the load."),
    }
    assert forms(rows["source"]) >= {
        ("waiting", "Starts after the configuration is prepared."),
        ("waiting", "Starts once the background service picks up the job."),
    }
    source = {form["state"]: form for form in rows["source"]["reserve"]}
    assert set(source) == {"waiting", "active", "done"}
    # The final load's counts are reserved before it starts; no task
    # reference is shown or reserved (#1969).
    assert "progress" in source["active"] and "task" not in source["active"]
    assert rows["completed"]["reserve"] == []
    # A running final load reserves the stand-in counts, not its own.
    running = {"id": uuid4(), "state": "running", "progress": Percentage(1, 2)}
    rows = by_key(finishing(status(prepared=True, source=running)))
    assert len(rows["source"]["reserve"]) == 4


def test_a_box_never_reserves_a_failure():
    """A failure's long explanation is not reserved: it stops the updates
    and comes with a notice above the boxes (#1903)."""
    rows = by_key(finishing(status(credentials=[credential("parishsoft", "failed")])))
    assert rows["parishsoft"]["state"] == "failed"
    for row in rows.values():
        assert "failed" not in {form["state"] for form in row["reserve"]}
    assert len(rows["parishsoft"]["reserve"]) == 6

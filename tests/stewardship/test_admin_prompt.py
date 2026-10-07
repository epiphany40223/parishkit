"""The Admin automation command line's confirmation prompt (ADM-11 PR 5b).

A prompting command writes its summary to standard error and reads the
answer from standard input after the preamble; ``--yes`` answers it. A
wrong answer, end of input or an oversize line changes nothing (exit 4,
``confirmation_required``). A prompting command that reads a ``-`` input
needs ``--yes``. The log records ``confirmation`` as ``prompt`` or ``yes``.
No command of this release prompts yet, so a stand-in command is added to
the catalog for these tests.
"""

import io
import json
import logging
from dataclasses import replace
from types import SimpleNamespace

import pytest

from parishkit.stewardship import admin_cli, observability
from parishkit.stewardship.observability import Event

PREAMBLE = f"pk-admin-session/1 {'A' * 43} {'f' * 64}\n".encode()
ACTED = []


def acting(args, preamble, runtime, context):
    """A stand-in fresh-gated command: confirm, then act."""
    admin_cli.confirm(context, ["Campaign: test", "This cannot be undone."])
    ACTED.append(args.reason)
    return {"done": True}


def typed(args, preamble, runtime, context):
    """A stand-in command with a typed value, as the Production confirmation."""
    admin_cli.confirm(context, ["Confirm Production."], typed="Production")
    ACTED.append("typed")
    return {"done": True}


def _reason(parser):
    """A free-text input that may be ``-``."""
    parser.add_argument("--reason", required=True)


@pytest.fixture
def prompting(monkeypatch):
    """Two prompting stand-ins in the catalog, and admission replaced."""
    base = admin_cli.BY_NAME["schedule confirm"]
    specs = (
        replace(
            base,
            name="test act",
            handler=acting,
            options=(_reason,),
            prompts=True,
            fresh_gated=True,
        ),
        replace(base, name="test typed", handler=typed, options=(), prompts=True),
    )
    commands = admin_cli.COMMANDS + specs
    monkeypatch.setattr(admin_cli, "COMMANDS", commands)
    monkeypatch.setattr(admin_cli, "BY_NAME", {spec.name: spec for spec in commands})
    monkeypatch.setattr(
        "parishkit.stewardship.observability.configure_logging", lambda: None
    )
    monkeypatch.setattr(
        "parishkit.stewardship.deployment.load_deployment", lambda path: object()
    )

    class Admitted:
        """Admission stand-in; no runtime is needed."""

        def __init__(self, configuration):
            """Nothing to assemble."""

        def __enter__(self):
            """No runtime."""
            return None

        def __exit__(self, *exc):
            """Nothing to release."""
            return False

    monkeypatch.setattr(admin_cli, "ADMISSION", Admitted)
    monkeypatch.setattr(
        admin_cli,
        "admit_session",
        lambda *args: SimpleNamespace(automation_session=None, portal_session=None),
    )
    monkeypatch.setattr(
        "parishkit.stewardship.accounts.automation_sessions.close_command_session",
        lambda portal_session: None,
    )
    ACTED.clear()


def run(argv, answer=b""):
    """Run one command; returns (exit code, document, standard error)."""
    out, err = io.StringIO(), io.StringIO()
    code = admin_cli.main(
        [*argv, "--config", "x", "--session-stdin"],
        stdin=io.BytesIO(PREAMBLE + answer),
        stdout=out,
        stderr=err,
    )
    return code, json.loads(out.getvalue().splitlines()[-1]), err.getvalue()


@pytest.mark.parametrize(
    "answer", [b"", b"no\n", b"y\n", b"YES\n", b"\tyes\n", b"yes!\n", b"x" * 300]
)
def test_anything_but_the_value_changes_nothing(prompting, answer):
    """A wrong answer, end of input or an oversize line: exit 4, no action."""
    code, document, errors = run(["test", "act", "--reason", "r"], answer)
    assert code == 4 and document["error"]["code"] == "confirmation_required"
    assert "This cannot be undone." in errors and "Type yes" in errors
    assert ACTED == []


def test_the_answer_or_yes_acts_and_is_logged(prompting, caplog):
    """The typed answer acts; --yes acts without asking; both are logged."""
    with caplog.at_level(logging.INFO, logger="parishkit.stewardship"):
        code, document, errors = run(["test", "act", "--reason", "r"], b"yes\n")
        assert code == 0 and document["result"] == {"done": True}
        code, _, quiet = run(["test", "act", "--reason", "r", "--yes"])
        assert code == 0 and "Type yes" not in quiet
    assert ACTED == ["r", "r"]
    formatter = observability.SafeJsonFormatter()
    lines = [
        json.loads(formatter.format(record))
        for record in caplog.records
        if record.msg is Event.TASK_STARTED
    ]
    assert [line["extra"].get("confirmation") for line in lines] == [
        "prompt",
        "yes",
    ]


def test_spaces_and_a_carriage_return_around_the_answer_are_trimmed(prompting):
    """Only the line ending and surrounding ASCII spaces are ignored."""
    code, _, _ = run(["test", "act", "--reason", "r"], b"  yes \r\n")
    assert code == 0 and ACTED == ["r"]


def test_a_typed_value_must_match_exactly(prompting):
    """The Production confirmation's value: yes is not enough."""
    code, _, _ = run(["test", "typed"], b"yes\n")
    assert code == 4 and ACTED == []
    code, _, _ = run(["test", "typed"], b"Production\n")
    assert code == 0 and ACTED == ["typed"]


def test_a_dash_input_needs_yes(prompting):
    """Standard input cannot carry both the input and the answer."""
    code, document, _ = run(["test", "act", "--reason", "-"], b"yes\n")
    assert code == 2 and document["error"]["code"] == "usage"
    code, _, _ = run(["test", "act", "--reason", "-", "--yes"], b"text")
    assert code == 0 and ACTED == ["-"]


def test_the_catalog_lists_yes_for_prompting_commands(prompting):
    """Each prompting command takes --yes and says that it prompts."""
    entries = {entry["name"]: entry for entry in admin_cli.catalog()}
    for name, entry in entries.items():
        names = {option["name"] for option in entry["options"]}
        assert ("--yes" in names) == entry["prompts"], name
    assert entries["test act"]["prompts"] and entries["test act"]["fresh_gated"]


def test_confirmation_is_only_logged_as_a_command_starts():
    """The log field is a closed word, on task_started only."""
    with pytest.raises(ValueError):
        observability.emit(Event.TASK_COMPLETED, confirmation="yes")
    with pytest.raises(ValueError):
        observability.emit(Event.TASK_STARTED, confirmation="maybe")

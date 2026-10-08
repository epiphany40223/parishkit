"""A watch timeout says which command and record it followed (#807).

``emit`` accepts the two fields only beside ``automation_watch``, the
command only as a watchable catalog name and the record only as a UUID;
the formatter re-checks both.
"""

import json
import logging
from uuid import uuid4

import pytest

from parishkit.logging import STRUCTURED_EXTRA_FIELD
from parishkit.stewardship import admin_cli, observability
from parishkit.stewardship.observability import Event, emit


def formatted(caplog):
    """The formatted lines so far."""
    return [
        json.loads(observability.SafeJsonFormatter().format(record))
        for record in caplog.records
    ]


def test_every_watch_declares_its_subject_or_none():
    """Each watchable command names a UUID argument it parses, or follows no
    single record; the watchable set is exactly the catalog's."""
    from argparse import ArgumentParser

    watching = [spec for spec in admin_cli.COMMANDS if spec.watch]
    assert observability.watch_commands() == {spec.name for spec in watching}
    for spec in watching:
        if spec.watch_subject is None:
            continue
        parser = ArgumentParser()
        for option in spec.options:
            option(parser)
        actions = {action.dest: action for action in parser._actions}
        assert actions[spec.watch_subject].type is admin_cli._uuid, spec.name
    assert {spec.name: spec.watch_subject for spec in watching}["task show"] == (
        "task_id"
    )
    for spec in admin_cli.COMMANDS:
        assert spec.watch or spec.watch_subject is None, spec.name


def test_the_timeout_line_carries_the_command_and_record(caplog):
    """Both fields reach the formatted line with the limit and elapsed time."""
    caplog.set_level(logging.WARNING, logger="parishkit.stewardship")
    subject = uuid4()
    emit(
        Event.TASK_TIMED_OUT,
        level=logging.WARNING,
        timeout="automation_watch",
        limit_seconds=30,
        elapsed_seconds=31,
        watched_command="task show",
        subject_id=subject,
    )
    [line] = formatted(caplog)
    assert line["extra"]["watched_command"] == "task show"
    assert line["extra"]["subject_id"] == str(subject)
    assert (line["extra"]["limit_seconds"], line["extra"]["elapsed_seconds"]) == (
        30,
        31,
    )


@pytest.mark.parametrize(
    "fields",
    [
        {"timeout": "automation_watch", "watched_command": "task list"},
        {"timeout": "automation_watch", "watched_command": "rm -rf /"},
        {"timeout": "automation_watch", "subject_id": "not-a-uuid"},
        {"timeout": "lease", "watched_command": "task show"},
        {"subject_id": uuid4()},
    ],
)
def test_emit_refuses_anything_else(fields):
    """A non-watchable or unknown command, a non-UUID record, or either field
    without a watch's timeout is refused."""
    with pytest.raises(ValueError):
        emit(Event.TASK_TIMED_OUT, level=logging.WARNING, **fields)


def test_the_formatter_drops_hand_built_values(caplog):
    """A record built around emit cannot smuggle text through either field."""
    caplog.set_level(logging.WARNING, logger="parishkit.stewardship")
    logging.getLogger("parishkit.stewardship").warning(
        Event.TASK_TIMED_OUT,
        extra={
            STRUCTURED_EXTRA_FIELD: {
                "timeout": "automation_watch",
                "watched_command": "free text here",
                "subject_id": "also text",
            }
        },
    )
    [line] = formatted(caplog)
    assert line["extra"] == {"timeout": "automation_watch"}

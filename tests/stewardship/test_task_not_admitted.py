"""A refused task admission is logged as routine, not as a failure (#1659).

While Family mail is paused or stopped, its handler refuses every new
claim. The refusal used to be a bare PermissionError, which the consumer
logged as an ERROR ``task_failed`` line classed ``filesystem_unavailable``
(PermissionError is an OSError).
"""

import logging
from uuid import uuid4

import pytest

from parishkit.stewardship.jobs import storage
from parishkit.stewardship.jobs.storage import TaskNotAdmitted
from parishkit.stewardship.observability import Event, FailureKind, failure_kind_of


def test_a_refused_admission_names_its_own_category():
    """Still a PermissionError for existing callers, but never a filesystem one."""
    with pytest.raises(TaskNotAdmitted) as caught:
        storage._admit(lambda action, status: False, "claim", None)
    assert isinstance(caught.value, PermissionError)
    assert failure_kind_of(caught.value) is FailureKind.TASK_NOT_ADMITTED
    # A genuine filesystem error keeps its category.
    assert failure_kind_of(PermissionError("synthetic")) is FailureKind.FILESYSTEM
    assert failure_kind_of(OSError("synthetic")) is FailureKind.FILESYSTEM


def consumer_lines(monkeypatch, caplog, error):
    """Run one broker hint whose dispatch raises ``error``; its task_failed lines.

    Each line is (level, failure_kind) as the structured process log has it.
    """
    from parishkit.logging import STRUCTURED_EXTRA_FIELD
    from parishkit.stewardship.deployment import ServiceRole, ValkeyConfiguration
    from parishkit.stewardship.jobs import broker

    def consume(*args, **kwargs):
        """The claim (or the effect) met ``error``."""
        raise error

    monkeypatch.setattr(broker, "consume_hint", consume)
    runtime = broker.build_broker(
        endpoint=ValkeyConfiguration("valkey", 6379, 0, None),
        password="synthetic-password",
        service=ServiceRole.MAIL_DISPATCH,
        handlers={},
    )
    with caplog.at_level(logging.INFO, logger="parishkit.stewardship"):
        runtime.app.tasks[broker.HINT_TASK].run(str(uuid4()))
    return [
        (record.levelno, getattr(record, STRUCTURED_EXTRA_FIELD)["failure_kind"])
        for record in caplog.records
        if record.msg == Event.TASK_FAILED
    ]


def test_a_claim_refused_while_halted_is_logged_at_info(monkeypatch, caplog):
    """The halt's refusal says task_not_admitted at INFO, no ERROR at all."""
    refused = TaskNotAdmitted("Task operation is not admitted.")
    lines = consumer_lines(monkeypatch, caplog, refused)
    assert lines == [(logging.INFO, FailureKind.TASK_NOT_ADMITTED)]
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


def test_a_real_filesystem_failure_is_still_an_error(monkeypatch, caplog):
    """Any other PermissionError or OSError keeps its ERROR filesystem line."""
    lines = consumer_lines(monkeypatch, caplog, PermissionError("synthetic"))
    assert lines == [(logging.ERROR, FailureKind.FILESYSTEM)]


def test_only_a_refused_admission_is_lowered_to_info(monkeypatch, caplog):
    """The shared reporter (hint and bulk batch paths) lowers nothing else."""
    from parishkit.stewardship.jobs import broker

    reported = []
    monkeypatch.setattr(
        broker,
        "emit_failure",
        lambda error, level=logging.ERROR: reported.append((type(error), level)),
    )
    broker.report_failure(TaskNotAdmitted("synthetic"))
    broker.report_failure(OSError("synthetic"))
    assert reported == [
        (TaskNotAdmitted, logging.INFO),
        (OSError, logging.ERROR),
    ]

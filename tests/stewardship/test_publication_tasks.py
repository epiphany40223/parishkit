"""The census publication tasks without a database (#528, BG-09).

The PostgreSQL behaviour is in database/test_publication_tasks_postgresql.py;
these pin the handlers' shape, their bindings and the #1245 review note 5
rule that no approve, edit or resolve path acts on a queued change.
"""

import re
from pathlib import Path
from uuid import uuid4

import pytest

from parishkit.stewardship.jobs.queues import WorkQueue
from parishkit.stewardship.jobs.storage import TaskStatus
from parishkit.stewardship.responses import census_resolution, census_review
from parishkit.stewardship.responses import publication_tasks as tasks
from parishkit.stewardship.responses.publication_worker import PublicationDisabled

PACKAGE = Path(__file__).resolve().parents[2] / "src/parishkit/stewardship"


def status(task_type, *, request=True):
    """A task status of ``task_type``, bound to a request unless told not."""
    run = uuid4()
    request_id = uuid4() if request else None
    return TaskStatus(run, run, task_type, request_id, "queued", 1, 0, 0, None, None, 0)


def test_handlers_need_the_real_setting_and_a_gateway():
    """A handler is built only from a real boolean and a gateway factory, and
    runs on the source queue."""
    for factory in (tasks.preflight_handler, tasks.publication_handler):
        handler = factory(enabled=False, gateway=lambda execution: None)
        assert handler.queue is WorkQueue.SOURCE
        assert handler.execute.keywords["enabled"] is False
        with pytest.raises(TypeError):
            factory(enabled="1", gateway=lambda execution: None)
        with pytest.raises(TypeError):
            factory(enabled=True, gateway=None)


def test_admission_needs_the_type_and_its_binding():
    """Another type or an unbound task is refused before any read."""
    with pytest.raises(PermissionError):
        tasks.admit_publication("hint", status(tasks.PREFLIGHT_TASK_TYPE))
    with pytest.raises(PermissionError):
        tasks.admit_preflight("hint", status(tasks.PREFLIGHT_TASK_TYPE, request=False))
    # The scheduler's actions read nothing past the binding.
    for action in ("hint", "recovery_hint", "lease_expired", "complete"):
        assert tasks.admit_publication(action, status(tasks.PUBLICATION_TASK_TYPE))


def test_web_calls_refuse_while_switched_off():
    """Off (the default), nothing is queued, checked before any database use."""
    with pytest.raises(PublicationDisabled):
        tasks.request_preflight(
            None, uuid4(), request_key=uuid4(), correlation_id=uuid4(), enabled=False
        )
    with pytest.raises(PublicationDisabled):
        tasks.confirm_and_publish(
            None, uuid4(), uuid4(), correlation_id=uuid4(), enabled=False
        )
    with pytest.raises(PublicationDisabled):
        tasks.rerun_publication(
            None,
            uuid4(),
            uuid4(),
            command_id=uuid4(),
            correlation_id=uuid4(),
            enabled=False,
        )


def test_the_scheduler_cannot_run_either_type():
    """The scheduler's handlers are metadata only."""

    def unavailable(execution):
        """What the scheduler registry runs."""
        raise PermissionError

    handlers = tasks.scheduler_handlers(unavailable)
    assert set(handlers) == {tasks.PREFLIGHT_TASK_TYPE, tasks.PUBLICATION_TASK_TYPE}
    for handler in handlers.values():
        with pytest.raises(PermissionError):
            handler.execute(None)


# A module that changes a proposal's decision or Administrator edit: an
# update naming those fields, or a dict of them passed to one.
_UPDATE = re.compile(r"\.update\((?:[^()]|\([^()]*\))*\b(decision|admin_value)\b", re.S)
_CHANGE = re.compile(r'\{\s*"(decision|admin_value)"')
# Each reviewed decision-changing path and the executions it acts on.
GATES = {
    "responses/census_review.py": census_review.REVIEWABLE,
    "responses/census_resolution.py": census_resolution.OPEN_EXECUTIONS,
}


def test_no_decision_or_edit_path_acts_on_a_queued_change():
    """Review note 5: every path that approves, ignores, reopens or edits a
    census change is listed here, and none acts while it is queued (the
    publish worker owns it then). A new path, such as Edit value, fails
    until it is added with a gate that leaves out ``queued``."""
    found = set()
    for path in PACKAGE.rglob("*.py"):
        if "migrations" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        if "ProposedChange" in text and (_UPDATE.search(text) or _CHANGE.search(text)):
            found.add(path.relative_to(PACKAGE).as_posix())
    assert found == set(GATES)
    for gate in GATES.values():
        assert "queued" not in gate

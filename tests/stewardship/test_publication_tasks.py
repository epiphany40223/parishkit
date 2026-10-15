"""The census publication tasks without a database (#528, BG-09).

The PostgreSQL behaviour is in database/test_publication_tasks_postgresql.py;
these pin the handlers' shape, their bindings and the #1245 review note 5
rule that no approve, edit or resolve path acts on a queued change.
"""

import inspect
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


# A source scan for every module that changes a proposal's decision or
# Administrator edit. It is a tripwire for review, not the protection: the
# SQL guards (frozen migrations 0060 and 0096) refuse a decision or edit
# change to a queued proposal whatever Python does. So the patterns cover
# the ORM and SQL forms this code base uses, not every way Python could
# hide a write. A module counts when it names the model (or, for raw SQL,
# its table) and has any of:
_FIELD = r"\b(?:decision|admin_value)\b"
_ORM_CHANGES = (
    # an update naming the fields, a dict of them, or an unpacked mapping;
    re.compile(r"\.update\((?:[^()]|\([^()]*\))*" + _FIELD, re.S),
    re.compile(r'\{\s*"(?:decision|admin_value)"'),
    re.compile(r"\.update\(\s*\*\*"),
    # a bulk update listing either field;
    re.compile(
        r"bulk_update\((?:[^()]|\([^()]*\))*[\"'](?:decision|admin_value)[\"']", re.S
    ),
)
# an assignment to either attribute, when the module also saves a model;
_ASSIGNED = re.compile(r"\.(?:decision|admin_value)\s*=(?!=)")
# or raw SQL that sets either column.
_SQL_SET = re.compile(r"\bSET\b[^;]*?" + _FIELD + r"\s*=", re.I | re.S)
# Each reviewed decision-changing path, its gate function and the
# executions that gate admits.
GATES = {
    "responses/census_review.py": (census_review.reviewable, census_review.REVIEWABLE),
    "responses/census_resolution.py": (
        census_resolution.allowed,
        census_resolution.OPEN_EXECUTIONS,
    ),
}


def changes_a_decision(text):
    """Whether module source ``text`` changes a proposal's decision or edit."""
    if "ProposedChange" in text:
        if any(pattern.search(text) for pattern in _ORM_CHANGES):
            return True
        if _ASSIGNED.search(text) and ".save(" in text:
            return True
    return "stewardship_proposed_change" in text and bool(_SQL_SET.search(text))


@pytest.mark.parametrize(
    "source",
    [
        'ProposedChange.objects.filter(pk=1).update(decision="approved")',
        'ProposedChange.objects.filter(pk=1).update(**{"decision": "x"})',
        "ProposedChange.objects.filter(pk=1).update(**changes)",
        'ProposedChange.objects.bulk_update(rows, ["version", "decision"])',
        'row = ProposedChange.objects.get(pk=1)\nrow.decision = "x"\nrow.save()',
        "row = ProposedChange.objects.get(pk=1)\nrow.admin_value = 1\nrow.save()",
        '"UPDATE stewardship_proposed_change SET version = 2, decision = %s"',
    ],
)
def test_the_scan_sees_each_way_to_change_a_decision(source):
    """Each form the scan looks for is found; a read is not."""
    assert changes_a_decision(source)
    assert not changes_a_decision('ProposedChange.objects.filter(decision="x")')


def test_no_decision_or_edit_path_acts_on_a_queued_change():
    """Review note 5: every path that approves, ignores, reopens or edits a
    census change is listed here, and none acts while it is queued (the
    publish worker owns it then). A new path, such as Edit value, fails
    until it is added with a gate that leaves out ``queued`` and that the
    module calls."""
    found = set()
    for path in PACKAGE.rglob("*.py"):
        if "migrations" in path.parts:
            continue
        if changes_a_decision(path.read_text(encoding="utf-8")):
            found.add(path.relative_to(PACKAGE).as_posix())
    assert found == set(GATES)
    for module, (gate, executions) in GATES.items():
        assert "queued" not in executions
        # The gate checks its execution set, and the module calls the gate
        # somewhere besides its own definition.
        assert re.search(
            rf"\bnot in \w*{_constant(gate, executions)}\b", inspect.getsource(gate)
        )
        text = (PACKAGE / module).read_text(encoding="utf-8")
        assert len(re.findall(rf"\b{gate.__name__}\(", text)) > 1


def _constant(gate, executions):
    """The name the gate's module gives its execution set."""
    module = inspect.getmodule(gate)
    return next(name for name, value in vars(module).items() if value is executions)

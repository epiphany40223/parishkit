"""Closed fact-runtime contracts reject malformed calls before touching storage."""

import pytest

from parishkit.stewardship.reports.fact_grants import add_fact_grants
from parishkit.stewardship.reports.fact_production import produce_facts
from parishkit.stewardship.reports.fact_tasks import fact_handler
from parishkit.stewardship.reports.materialization import materialize_fact_set


def test_scheduler_has_no_executable_calculation_port():
    with pytest.raises(PermissionError, match="scheduler"):
        fact_handler(scheduler=True).execute(None)
    with pytest.raises(TypeError):
        fact_handler(scheduler=1)
    with pytest.raises(TypeError):
        produce_facts(None)
    with pytest.raises(ValueError):
        materialize_fact_set(None, None, admit=None, interactive=1)


@pytest.mark.parametrize("worker", [False, True])
def test_fact_grants_separate_scheduler_metadata_from_calculation_writes(worker):
    tables, columns = {}, {}
    add_fact_grants(tables, columns, worker=worker)
    assert "INSERT" in tables["stewardship_fact_demand"]
    assert ("INSERT" in tables["stewardship_daily_fact_set"]) == worker
    assert ("annual_pledge" in columns["stewardship_submission"]["SELECT"]) == worker
    assert "answers" not in columns["stewardship_submission"]["SELECT"]
    assert "claimed_task_id" not in columns["stewardship_fact_demand"]["UPDATE"]
    assert not any("DELETE" in permissions for permissions in tables.values())

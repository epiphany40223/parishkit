"""An alert owner is assembled only from closed, typed parts."""

from dataclasses import replace

import pytest

from parishkit.stewardship.jobs.operational_owner import OPERATIONAL
from parishkit.stewardship.jobs.security_owner import SECURITY


def test_the_two_owners_are_distinct_and_closed():
    """Purpose, Task type, namespace and tables never coincide between owners."""
    for name in (
        "purpose",
        "task_type",
        "namespace",
        "cohort_table",
        "recipient_table",
    ):
        assert getattr(OPERATIONAL, name) != getattr(SECURITY, name)
    assert OPERATIONAL.source_field == "notice_id"
    assert SECURITY.source_field == "event_id"


@pytest.mark.parametrize(
    "values",
    [
        {"purpose": "initial"},
        {"namespace": "not-a-uuid"},
        {"configured": None},
        {"submit": "submit_security_mail"},
    ],
)
def test_an_owner_with_an_open_part_is_refused(values):
    """A purpose outside the Administrator-routed pair or a non-callable is refused."""
    with pytest.raises(TypeError):
        replace(SECURITY, **values)

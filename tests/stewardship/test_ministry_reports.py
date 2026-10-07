"""Private report filters and capability scope without database startup."""

from uuid import uuid4

import pytest
from django.http import QueryDict

from parishkit.stewardship.accounts.policy import Principal
from parishkit.stewardship.audit.schemas import ContextKind, sanitize
from parishkit.stewardship.reports.ministries import MinistryQuery, can_report


@pytest.mark.parametrize(
    "values",
    [
        {"page": "0"},
        {"page": "01"},
        {"page": "10001"},
        {"page": "١"},
        {"search": "x" * 201},
        {"search": "private\x00value"},
        {"start": "2026-02-30"},
        {"start": "20261001"},
        {"start": "2026-10-02", "end": "2026-10-01"},
        {"activity": "removed"},
        {"state": "complete"},
        {"history": "yes"},
        {"sort": "name;SELECT"},
        {"scope": "9"},
        {"operational": "true"},
    ],
)
def test_filters_fail_closed_without_reflecting_private_values(values):
    """Browser input cannot select projection authority or escape bounded filters."""
    with pytest.raises(ValueError):
        MinistryQuery.parse(values, detail=True)


def test_native_filters_keep_private_state_and_reject_duplicates():
    """Summary-only and detailed forms have explicit filter semantics."""
    value = MinistryQuery.parse(
        {
            "search": "Private Member",
            "history": "all",
            "start": "2026-01-01",
            "zone": "America/New_York",
            "page": "2",
        },
        detail=True,
    )
    assert "Private" not in repr(value) and value.page == 2
    assert (
        "page" not in value.form_values()
        and value.form_values()["search"] == "Private Member"
    )
    with pytest.raises(ValueError):
        MinistryQuery.parse(QueryDict("search=one&search=two"), detail=True)
    for values in (
        {"history": "all"},
        {"state": "resolved"},
        {"sort": "newest"},
        {"start": "2026-01-01"},
    ):
        with pytest.raises(ValueError):
            MinistryQuery.parse(values)


def test_dates_need_the_browsers_zone_and_an_unused_zone_is_dropped():
    """Days are the viewer's browser zone (#558): never guessed, never another."""
    from parishkit.stewardship.web.dates import UnknownZone

    for zone in ("", "Etc/Unknown", "../../etc/passwd", "EST5EDT "):
        with pytest.raises(UnknownZone):
            MinistryQuery.parse({"end": "2026-01-01", "zone": zone}, detail=True)
    query = MinistryQuery.parse({"zone": "Etc/Unknown"}, detail=True)
    assert query.zone == "" and query.form_values()["zone"] == ""
    query = MinistryQuery.parse(
        {"start": "2026-01-01", "zone": "Asia/Kathmandu"}, detail=True
    )
    assert query.form_values()["zone"] == "Asia/Kathmandu"


@pytest.mark.parametrize(
    "roles,scope,expected",
    [
        (set(), set(), False),
        ({"ministry_leader"}, set(), False),
        ({"ministry_leader"}, {9}, True),
        ({"staff"}, set(), True),
        ({"administrator"}, set(), True),
    ],
)
def test_report_entry_requires_operational_role_or_current_assignment(
    roles, scope, expected
):
    """Leader entry does not imply parish-wide campaign/report access."""
    assert (
        can_report(Principal(uuid4(), frozenset(roles), frozenset(scope))) is expected
    )
    assert not can_report(None)


def test_audit_scope_is_only_a_canonical_ministry_duid():
    """Neither contact text nor browser-shaped booleans fit scope metadata."""
    assert sanitize(ContextKind.ACTION, {"ministry_duid": 9}) == {"ministry_duid": 9}
    for value in (True, 0, -1, 2**31, "9", "private@example.org", [9]):
        with pytest.raises(ValueError):
            sanitize(ContextKind.ACTION, {"ministry_duid": value})

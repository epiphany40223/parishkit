"""Weekly page selection is bounded, closed and independent of captured cohorts."""

from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from django.core.exceptions import ObjectDoesNotExist
from django.http import QueryDict

from parishkit.stewardship.campaigns.read_guards import ReadUnavailable
from parishkit.stewardship.reports import weekly_presentation as presentation
from parishkit.stewardship.reports.weekly_selection import WeeklyHistory, select_weekly

from .test_weekly_selection import CAMPAIGN, item, observation


@pytest.mark.parametrize("query,page", [("", 1), ("page=1", 1), ("page=25", 25)])
def test_canonical_page_parameters(query, page):
    assert presentation.page_number(QueryDict(query)) == page


@pytest.mark.parametrize(
    "query",
    [
        "page=0",
        "page=-1",
        "page=01",
        "page=+1",
        "page=1.0",
        "page=",
        "page=10000000",
        "page=١",
        "page=1&page=2",
        "filter=all",
    ],
)
def test_unknown_or_noncanonical_pages_are_rejected(query):
    with pytest.raises(ValueError):
        presentation.page_number(QueryDict(query))


def test_detail_query_cannot_select_another_page():
    assert presentation.page_number(QueryDict(), detail=True) == 1
    with pytest.raises(ValueError):
        presentation.page_number(QueryDict("page=1"), detail=True)


@pytest.fixture
def report(monkeypatch):
    """Create a detached selection and prove status reads are limited to its page."""
    values = tuple(item(number) for number in range(1, 121))
    selection = select_weekly(observation(*values), WeeklyHistory(CAMPAIGN))
    snapshot = SimpleNamespace(pk=uuid4(), campaign_id=CAMPAIGN)
    requested = []
    monkeypatch.setattr(presentation, "retained_selection", lambda row: selection)

    def query(**filters):
        """Current-status projection receives only the visible report identities."""
        assert filters["submission__campaign_id"] == CAMPAIGN
        assert filters["submission__mode"] == "live"
        requested.append(filters["pk__in"])
        return SimpleNamespace(
            values_list=lambda *args: [
                (identifier, "current_actionable") for identifier in filters["pk__in"]
            ]
        )

    monkeypatch.setattr(presentation.AdditionalInformationItem.objects, "filter", query)
    return snapshot, requested


@pytest.mark.parametrize("page,count", [(1, 50), (2, 50), (3, 20)])
def test_report_pages_do_not_drop_or_repeat_items(report, page, count):
    snapshot, requested = report
    result = presentation.snapshot_context(snapshot, page=page)
    assert result["total"] == 120 and result["pages"] == 3
    assert len(result["rows"]) == len(requested[0]) == count
    assert result["rows"][0]["value"].item_id == UUID(int=101 + (page - 1) * 50)


def test_only_selected_detail_identity_is_queried(report):
    snapshot, requested = report
    result = presentation.snapshot_context(snapshot, item_id=UUID(int=190))
    assert requested == [[UUID(int=190)]]
    assert len(result["rows"]) == 1 and result["detail"]
    with pytest.raises(ObjectDoesNotExist):
        presentation.snapshot_context(snapshot, item_id=uuid4())
    with pytest.raises(ObjectDoesNotExist):
        presentation.snapshot_context(snapshot, page=4)
    assert len(requested) == 1


def test_missing_current_item_fails_closed(report, monkeypatch):
    snapshot, _ = report
    monkeypatch.setattr(
        presentation.AdditionalInformationItem.objects,
        "filter",
        lambda **kwargs: SimpleNamespace(values_list=lambda *args: []),
    )
    with pytest.raises(ReadUnavailable):
        presentation.snapshot_context(snapshot)

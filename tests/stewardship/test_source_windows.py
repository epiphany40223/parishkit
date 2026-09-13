"""Source refresh never expands its giving scope to historical campaigns."""

from uuid import uuid4

import pytest

from parishkit.stewardship.source.canonical import InvalidSourcePayload
from parishkit.stewardship.source.windows import refresh_window

from .campaign_factory import campaign, financial


@pytest.mark.parametrize("state", ["draft", "scheduled", "active", "closed"])
def test_current_campaign_retains_both_exact_periods_through_reconciliation(state):
    """Closed is still current: contributions can change during post-campaign work."""
    identifier = uuid4()
    values = campaign(modules=["financial"], financial=financial())["values"]
    window = refresh_window(campaign_id=identifier, state=state, values=values)
    assert window.document() == {
        "campaign_id": str(identifier),
        "periods": [
            {"start": "2027-01-01", "end": "2027-12-31", "funds": [1]},
            {"start": "2026-01-01", "end": "2026-12-31", "funds": [2]},
        ],
    }


def test_no_current_or_archived_campaign_adds_no_live_giving():
    """An archived current pointer is not permission to refresh its retained period."""
    empty = refresh_window(campaign_id=None, state=None, values=None)
    assert empty.document() == {"campaign_id": None, "periods": []}
    values = campaign(modules=["financial"], financial=financial())["values"]
    assert (
        refresh_window(campaign_id=uuid4(), state="archived", values=values).periods
        == ()
    )


def test_unrelated_configuration_edits_preserve_query_scope_but_period_changes_do_not():
    """A queued load compares relevant scope, not every parish/content version."""
    identifier = uuid4()
    values = campaign(modules=["financial"], financial=financial())["values"]
    original = refresh_window(campaign_id=identifier, state="draft", values=values)
    renamed = refresh_window(
        campaign_id=identifier, state="draft", values=values | {"name": "Renamed"}
    )
    changed = refresh_window(
        campaign_id=identifier,
        state="draft",
        values=values | {"financial": financial(fund_duids=[3])},
    )
    assert original.digest == renamed.digest and original.digest != changed.digest
    assert (
        original.digest
        != refresh_window(campaign_id=uuid4(), state="draft", values=values).digest
    )


@pytest.mark.parametrize(
    "state", ["purging", "purged", "purge_cleanup_failed", "unknown"]
)
def test_unavailable_campaign_state_never_supplies_giving_scope(state):
    """Purge cannot be treated as an ordinary empty-period refresh exception."""
    with pytest.raises(InvalidSourcePayload):
        refresh_window(campaign_id=uuid4(), state=state, values=campaign()["values"])


def test_null_pointer_requires_no_detached_campaign_values():
    """Callers cannot pass an old campaign's configuration with no current pointer."""
    with pytest.raises(InvalidSourcePayload):
        refresh_window(campaign_id=None, state="closed", values=campaign()["values"])

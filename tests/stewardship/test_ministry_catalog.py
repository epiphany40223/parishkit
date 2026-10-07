"""ParishSoft Ministry catalog differences for the Admin home notice (#342)."""

import json
import logging
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID

import pytest
from django.template.loader import render_to_string

from parishkit.stewardship.observability import (
    Event,
    SafeJsonFormatter,
    emit,
    emit_failure,
)
from parishkit.stewardship.source.ministry_catalog import (
    MAX_LISTED,
    _entries,
    catalog_changes,
    retired_name,
    selected_ministries,
)

CAMPAIGN = UUID("00000000-0000-4000-8000-000000000342")


def test_an_unchanged_catalog_records_nothing():
    """Identical catalogs, and changes the Admin could not see, are no change."""
    assert catalog_changes({}, {}) is None
    assert catalog_changes({3: "Choir"}, {3: "Choir"}) is None
    # Names are compared as shown, so trimming alone is not a rename.
    assert catalog_changes({3: "Choir "}, {3: " Choir"}) is None


def test_added_removed_and_renamed_ministries_are_recorded_by_duid():
    """Each kind lists DUIDs in order, with cleaned names and full counts."""
    before = {3: "Choir", 5: "Ushers", 8: "Old Ministry", 9: None}
    after = {3: "X-Choir", 5: "Ushers", 9: "Lectors", 12: "Food\x00 pantry"}
    assert catalog_changes(before, after) == {
        "counts": {"added": 1, "removed": 1, "renamed": 2},
        "added": [{"duid": 12, "name": "Food pantry"}],
        "removed": [{"duid": 8, "name": "Old Ministry"}],
        "renamed": [
            {"duid": 3, "before": "Choir", "name": "X-Choir"},
            # A blank name shows as "Ministry <DUID>" (#341).
            {"duid": 9, "before": "Ministry 9", "name": "Lectors"},
        ],
    }


def test_long_lists_are_capped_but_counted():
    """A wholesale change keeps the cursor bounded and still says how many."""
    after = {duid: f"Ministry name {duid}" for duid in range(1, MAX_LISTED + 21)}
    changes = catalog_changes({}, after)
    assert changes["counts"] == {"added": MAX_LISTED + 20, "removed": 0, "renamed": 0}
    assert len(changes["added"]) == MAX_LISTED
    assert changes["added"][-1]["duid"] == MAX_LISTED


@pytest.mark.parametrize(
    ("name", "retired"),
    [("X-Choir", True), ("x-Choir", True), ("X Choir", False), ("Choir", False)],
)
def test_the_retired_prefix_is_a_hint(name, retired):
    """Only a leading "X-" (either case) reads as possibly retired."""
    assert retired_name(name) is retired


def test_only_a_newly_prefixed_rename_is_marked_possibly_retired():
    """A rename of an already retired name is not newly retired."""
    record = {
        "renamed": [
            {"duid": 3, "before": "Choir", "name": "X-Choir"},
            {"duid": 4, "before": "X-Ushers", "name": "X-Ushers (old)"},
        ]
    }
    rows = _entries(record, "renamed", frozenset({3}))
    assert [(row["duid"], row["retired"], row["in_campaign"]) for row in rows] == [
        (3, True, True),
        (4, False, False),
    ]


def _configuration(state, duids):
    """A configuration and campaign carrying only what the notice reads."""
    document = {
        "sections": {
            "campaigns": [
                {"id": str(CAMPAIGN), "values": {"ministry_duids": duids}},
                {"id": "other", "values": {"ministry_duids": [99]}},
            ]
        }
    }
    configuration = SimpleNamespace(
        current_campaign_id=CAMPAIGN,
        active_configuration=SimpleNamespace(canonical_document=document),
    )
    return configuration, SimpleNamespace(pk=CAMPAIGN, state=state)


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        ("draft", {3, 5}),
        ("scheduled", {3, 5}),
        ("active", {3, 5}),
        ("closed", set()),
        ("archived", set()),
    ],
)
def test_selected_ministries_only_while_the_campaign_is_open(state, expected):
    """A closed or archived campaign's selections no longer need attention."""
    configuration, campaign = _configuration(state, [3, 5])
    assert selected_ministries(configuration, campaign) == expected
    assert selected_ministries(configuration, None) == frozenset()


def test_the_notice_names_changes_and_links_to_ministry_activity():
    """The panel lists every kind and points at the pages that act on it."""
    notice = {
        "missing": [{"duid": 77, "name": "Ministry 77"}],
        "retired": [{"duid": 3, "name": "X-Choir"}],
        "refreshes": [
            {
                "promoted_at": datetime(2026, 9, 30, 6, tzinfo=UTC),
                "added": [{"duid": 12, "name": "Lectors", "in_campaign": False}],
                "removed": [{"duid": 77, "name": "Greeters", "in_campaign": True}],
                "renamed": [
                    {
                        "duid": 3,
                        "before": "Choir",
                        "name": "X-Choir",
                        "retired": True,
                        "in_campaign": True,
                    }
                ],
                "more": {"added": 2, "removed": 0, "renamed": 0},
            }
        ],
        "campaign_id": CAMPAIGN,
    }
    html = render_to_string(
        "stewardship/ministry-catalog-notice.html", {"notice": notice}
    )
    assert "ParishSoft Ministry changes" in html
    assert "no longer in ParishSoft" in html and "Ministry 77 (DUID 77)" in html
    assert "Lectors (DUID 12)" in html and "and 2 more" in html
    assert "Greeters (DUID 77) — in the current campaign" in html
    assert "Choir → X-Choir (DUID 3) — possibly retired" in html
    assert 'href="/admin/parish/ministries/"' in html
    assert f"/admin/campaign/{CAMPAIGN}/settings#ministry-selections" in html


@pytest.mark.parametrize("step", ["ministry_catalog", "source_changes"])
def test_a_shaping_failure_names_its_comparison(caplog, step):
    """Two display-only comparisons share one event; a closed word tells them apart."""
    with caplog.at_level(logging.DEBUG):
        emit_failure(
            ValueError("PRIVATE"),
            event=Event.REPORT_SHAPING_FAILED,
            level=logging.WARNING,
            shaping=step,
        )
    (record,) = caplog.records
    output = SafeJsonFormatter().format(record)
    payload = json.loads(output)
    assert payload["message"] == "report_shaping_failed"
    assert payload["extra"]["shaping"] == step
    assert "PRIVATE" not in output


@pytest.mark.parametrize(
    ("event", "step"),
    [
        (Event.REPORT_SHAPING_FAILED, "free text"),
        (Event.TASK_FAILED, "ministry_catalog"),
    ],
)
def test_a_shaping_step_is_a_reviewed_word_on_its_event(event, step):
    """No other event, and no unreviewed text, can carry the field."""
    with pytest.raises(ValueError):
        emit(event, shaping=step)

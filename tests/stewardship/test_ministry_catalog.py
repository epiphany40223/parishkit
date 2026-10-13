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
from parishkit.stewardship.source.catalog_names import clean_ministry_name
from parishkit.stewardship.source.ministry_catalog import (
    MAX_LISTED,
    _entries,
    catalog_changes,
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
    "name", ["102-Altar Servers", "A-Lectors", "X-Choir", "x-Choir", "1. Ushers"]
)
def test_a_prefixed_name_is_an_opaque_string(name):
    """A leading number, letter or "X-" is kept and means nothing (#971).

    Such prefixes are one parish's naming convention, not ParishSoft's, so
    the full name is shown and a rename to it carries no hint.
    """
    assert clean_ministry_name(5, name) == (name, False)
    assert catalog_changes({5: "Choir"}, {5: name})["renamed"] == [
        {"duid": 5, "before": "Choir", "name": name}
    ]
    record = {"renamed": [{"duid": 5, "before": "Choir", "name": name}]}
    assert _entries(record, "renamed", frozenset({5})) == [
        {"duid": 5, "name": name, "in_campaign": True, "before": "Choir"}
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
    # Each Ministry is named, never shown by its DUID (#1123).
    assert "no longer in ParishSoft" in html and "<li>Ministry 77</li>" in html
    assert "<li>Lectors</li>" in html and "and 2 more" in html
    assert "<li>Greeters — in the current campaign</li>" in html
    # The full new name, with no "possibly retired" guess (#971).
    assert "Choir → X-Choir — in the current campaign" in html
    assert "DUID" not in html
    assert "retired" not in html and "X-”" not in html
    assert 'href="/admin/parish/ministries/"' in html
    assert "/admin/campaign/settings/#ministry-selections" in html


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

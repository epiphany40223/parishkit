"""Synthetic published-contract responses; no provider credentials or network."""

from datetime import date, datetime

import pytest
from test_parishsoft import Response, client

from parishkit.parishsoft import ParishSoftAPIError
from parishkit.parishsoft_changes import ChangeFeedIncomplete, load_family_changes

START, END = date(2026, 9, 10), date(2026, 9, 11)


def indication(identifier=7, **changes):
    """Names/contact fields are provider input but never retained in the result."""
    return {
        "family_DUID": identifier,
        "logDate": "2026-09-11T12:00:00Z",
        "currentParishID": 5,
        "previousParishID": 5,
        "lastName": "PRIVATE-NAME",
        "currentEmail": "private@example.org",
        **changes,
    }


def provider(tmp_path, payload):
    """Use the real shared HTTP client with only a queued in-memory transport."""
    return client(
        tmp_path,
        [
            Response([{"organizationID": 5, "organizationReportName": "Test"}]),
            Response(payload),
        ],
    )


def load(source, **overrides):
    """Keep every test invocation bound to an explicit expected tenant/window."""
    return load_family_changes(
        source,
        **{
            "organization_id": 5,
            "start_date": START,
            "end_date": END,
            **overrides,
        },
    )


def test_date_feed_returns_sorted_distinct_family_ids_without_contact_values(tmp_path):
    """Transport names follow the published v2 contract without retaining its PII."""
    source = provider(tmp_path, [indication(9), indication(7), indication(9)])
    result = load(source)
    assert result.family_ids == (7, 9) and result.indication_count == 3
    assert (
        result.organization_id == 5
        and result.start_date == START
        and result.end_date == END
    )
    assert "PRIVATE" not in repr(result) and "private@" not in repr(result)
    method, url, arguments = source.session.calls[1]
    assert method == "get" and url.endswith("/families/change/list")
    assert arguments["params"] == {"StartDate": "2026-09-10", "EndDate": "2026-09-11"}
    assert not tuple(tmp_path.glob("*.json"))


def test_empty_feed_does_not_fabricate_a_cursor_or_change_time(tmp_path):
    """The polling transaction, not the last returned event, owns durable progress."""
    result = load(provider(tmp_path, []))
    assert result.family_ids == () and result.indication_count == 0
    assert not hasattr(result, "cursor") and not hasattr(result, "watermark")


def test_change_feed_ignores_cached_organization_and_indications(tmp_path):
    """Earlier successful responses cannot suppress newly reported changes."""
    source = provider(tmp_path, [indication(9)])
    source._save_cache(
        "families/change/list",
        {"StartDate": START.isoformat(), "EndDate": END.isoformat()},
        [indication(1)],
    )
    assert load(source).family_ids == (9,)
    assert len(source.session.calls) == 2


def test_wrong_organization_stops_before_fetching_family_data(tmp_path):
    """The expected numeric tenant identity is checked before the feed request."""
    source = provider(tmp_path, [])
    with pytest.raises(ChangeFeedIncomplete, match="another organization"):
        load(source, organization_id=9)
    assert len(source.session.calls) == 1


@pytest.mark.parametrize(
    "payload",
    [
        None,
        {},
        {"data": []},
        [None],
        [indication(family_DUID=True)],
        [indication(family_DUID="7")],
        [indication(family_DUID=0)],
        [indication(family_DUID=2**31)],
        [indication(currentParishID=9)],
        [indication(previousParishID=9)],
        [indication(currentParishID=None)],
        [indication(previousParishID=False)],
        [indication(logDate=None)],
        [indication(logDate="PRIVATE-TIMESTAMP")],
        [indication(logDate="2026-09-11T12:00:00")],
        [indication(logDate="2026-09-09T23:00:00Z")],
        [indication(logDate="2026-09-15T12:00:00Z")],
        [indication(logDate="0001-01-01T00:00:00+01:00")],
    ],
)
def test_ambiguous_feed_requires_full_refresh_without_partial_result_or_private_error(
    tmp_path, payload
):
    """Invalid envelopes or records never yield a partial set of Family IDs."""
    with pytest.raises(ChangeFeedIncomplete) as error:
        load(provider(tmp_path, payload))
    assert "PRIVATE" not in str(error.value) and "private@" not in str(error.value)


def test_invalid_later_duplicate_cannot_hide_behind_identity_deduplication(tmp_path):
    """Every record validates even when its Family was already observed."""
    source = provider(tmp_path, [indication(), indication(logDate="PRIVATE-TIMESTAMP")])
    with pytest.raises(ChangeFeedIncomplete):
        load(source)


def test_reaching_feed_ceiling_is_ambiguous_not_a_truncated_delta(tmp_path):
    """No provider count contract proves completeness at the ceiling."""
    with pytest.raises(ChangeFeedIncomplete, match="incomplete"):
        load(provider(tmp_path, [indication(), indication()]), maximum=2)


@pytest.mark.parametrize(
    "log_date", ["2026-09-10T01:00:00+02:00", "2026-09-12T01:00:00+02:00"]
)
def test_offset_day_overlap_accepts_every_indication_without_timestamp_filtering(
    tmp_path, log_date
):
    """Date-boundary overlap does not discard an otherwise valid indication."""
    assert load(provider(tmp_path, [indication(logDate=log_date)])).family_ids == (7,)


@pytest.mark.parametrize("status", [400, 404, 405, 501])
def test_unsupported_feed_has_static_full_refresh_disposition(tmp_path, status):
    """Unsupported read capability falls back without reflecting provider errors."""
    source = client(
        tmp_path,
        [
            Response([{"organizationID": 5, "organizationReportName": "Test"}]),
            Response("PRIVATE-ERROR", status_code=status),
        ],
    )
    with pytest.raises(ChangeFeedIncomplete, match="unsupported") as error:
        load(source)
    assert "PRIVATE" not in str(error.value)


def test_authentication_error_is_not_misclassified_as_missing_feed(tmp_path):
    """A revoked provider key retains its distinct failed-authentication outcome."""
    source = client(
        tmp_path,
        [
            Response([{"organizationID": 5, "organizationReportName": "Test"}]),
            Response("synthetic denial", status_code=401),
        ],
    )
    with pytest.raises(ParishSoftAPIError) as error:
        load(source)
    assert error.value.status_code == 401


@pytest.mark.parametrize(
    "overrides",
    [
        {"start_date": datetime(2026, 9, 10)},
        {"end_date": "2026-09-11"},
        {"start_date": END, "end_date": START},
        {"start_date": date(2026, 1, 1)},
        {"maximum": True},
        {"maximum": 0},
        {"maximum": 10001},
    ],
)
def test_invalid_input_stops_before_any_provider_call(tmp_path, overrides):
    """Configuration mistakes cannot cause an unbounded feed query."""
    source = provider(tmp_path, [])
    with pytest.raises(ValueError):
        load(source, **overrides)
    assert not source.session.calls


def test_uncached_get_neither_reads_nor_updates_existing_cache(tmp_path):
    """Explicit uncached reads preserve ordinary shared cache behavior."""
    source = client(tmp_path, [Response({"version": 1}), Response({"version": 2})])
    assert source.get("lookup") == {"version": 1}
    assert source.get_uncached("lookup") == {"version": 2}
    assert source.get("lookup") == {"version": 1}
    assert len(source.session.calls) == 2

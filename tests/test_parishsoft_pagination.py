"""Synthetic published pagination dialects, including discontinuity/fault cases."""

from dataclasses import replace

import pytest

from parishkit.parishsoft_pagination import (
    IncompleteSourceCollection,
    PageContract,
    read_pages,
)

ARRAY = PageContract("size", "position", envelope=False)
ENVELOPE = replace(ARRAY, envelope=True)
ZERO = replace(ARRAY, zero_probe=True)


def records(*identities):
    """Minimal distinct source identities; private values are not needed here."""
    return [{"id": value} for value in identities]


def envelope(rows, *, page=1, size=2, total=3, **overrides):
    """Return a fresh strict envelope with explicit server count evidence."""
    return {
        "data": rows,
        "pagingInfo": dict(
            pageNumber=page,
            pageSize=size,
            totalRecords=total,
            totalPages=(total + size - 1) // size,
        )
        | overrides,
    }


def load(responses, *, contract=ARRAY, **overrides):
    """Track requested positions while supplying fixed deterministic provider pages."""
    calls = []

    def fetch(parameters):
        """An unexpected extra call exposes missing termination or wrong offsets."""
        calls.append(parameters)
        return responses[parameters["position"]]

    result = read_pages(
        fetch,
        contract=contract,
        identify=lambda row: row["id"],
        **dict(page_size=2) | overrides,
    )
    return result, calls


def test_array_requires_empty_terminator_not_a_short_page():
    """A server page-size quirk cannot silently discard later source records."""
    result, calls = load({1: records(1), 2: records(2), 3: []})
    assert result == records(1, 2)
    assert calls == [{"size": 2, "position": position} for position in (1, 2, 3)]


def test_envelope_ends_at_exact_validated_total_without_a_speculative_extra_page():
    """All envelope pages must agree with their complete count/size contract."""
    result, calls = load(
        {1: envelope(records(1, 2)), 2: envelope(records(3), page=2)},
        contract=ENVELOPE,
    )
    assert result == records(1, 2, 3) and len(calls) == 2


@pytest.mark.parametrize("data", [None, []])
@pytest.mark.parametrize("total_pages", [0, 1])
def test_explicit_zero_total_permits_nullable_empty_envelope(data, total_pages):
    """Only a zero count proves that nullable data means a complete empty list."""
    result, calls = load(
        {1: envelope(data, total=0, totalPages=total_pages)}, contract=ENVELOPE
    )
    assert result == [] and len(calls) == 1


@pytest.mark.parametrize(
    "bad",
    [
        None,
        [],
        {},
        {"data": []},
        {"data": [], "pagingInfo": None},
        envelope(None),
        envelope(records(1)),
        envelope(records(1, 2), page=2),
        envelope(records(1, 2), totalPages=5),
        envelope(records(1, 2), pageNumber=True),
        envelope(records(1, 2), pageSize=500),
        envelope(records(1, 2), totalRecords=-1),
        envelope(records(1, 2), totalRecords=100001),
    ],
)
def test_invalid_incomplete_or_wrong_envelope_is_not_partial_success(bad):
    """Missing or inconsistent metadata does not inherit permissive defaults."""
    with pytest.raises(IncompleteSourceCollection):
        load({1: bad}, contract=ENVELOPE)


@pytest.mark.parametrize("bad", [None, {}, [None], records(1, 2, 3)])
def test_invalid_array_shape_is_not_treated_as_empty(bad):
    """A missing page is failure, not an exhausted collection."""
    with pytest.raises(IncompleteSourceCollection):
        load({1: bad})


def test_changed_envelope_total_rejects_the_entire_scan():
    """Two individually valid pages cannot describe different underlying totals."""
    with pytest.raises(IncompleteSourceCollection, match="total changed"):
        load(
            {
                1: envelope(records(1, 2)),
                2: envelope(records(3, 4), total=4, page=2),
            },
            contract=ENVELOPE,
        )


@pytest.mark.parametrize("second", [records(2, 3), records(3, 3)])
def test_duplicate_identity_within_or_across_pages_is_rejected(second):
    """A dict-comprehension overwrite cannot hide duplicate provider records."""
    with pytest.raises(IncompleteSourceCollection, match="repeats"):
        load({1: records(1, 2), 2: second})


def test_embedded_total_and_ordinal_prove_contiguous_array_scan():
    """Family/member search metadata can validate a bare array without an envelope."""
    contract = replace(ARRAY, total_field="total", ordinal_field="row")
    rows = [{"id": value, "total": 3, "row": value} for value in (1, 2, 3)]
    result, _ = load({1: rows[:2], 2: rows[2:], 3: []}, contract=contract)
    assert result == rows


@pytest.mark.parametrize(
    "rows",
    [
        [{"id": 1, "total": 2, "row": 1}],
        [{"id": 1, "total": 1, "row": 2}],
        [{"id": 1, "total": True, "row": 1}],
        [{"id": 1, "total": 1, "row": True}],
        [{"id": 1, "total": 1}],
        [{"id": 1, "total": 2, "row": 1}, {"id": 2, "total": 3, "row": 2}],
        [{"id": 1, "total": 2, "row": 1}, {"id": 2, "total": 2, "row": 3}],
    ],
)
def test_embedded_total_or_ordinal_discontinuity_rejects_scan(rows):
    """An empty next page cannot excuse gaps, changed totals or wrong scalar types."""
    with pytest.raises(IncompleteSourceCollection):
        load(
            {1: rows, 2: []},
            contract=replace(ARRAY, total_field="total", ordinal_field="row"),
        )


def test_zero_based_page_contract_reads_first_page_instead_of_skipping_it():
    """Default-zero provider positions retain both disjoint initial pages."""
    result, calls = load({0: records(1, 2), 1: records(3), 2: []}, contract=ZERO)
    assert result == records(1, 2, 3)
    assert [call["position"] for call in calls] == [0, 1, 2]


def test_zero_and_one_alias_is_only_a_probe_not_a_duplicate_or_completion():
    """A provider clamping zero to one must still advance on page two."""
    result, calls = load(
        {0: records(1, 2), 1: records(1, 2), 2: records(3), 3: []}, contract=ZERO
    )
    assert result == records(1, 2, 3)
    assert [call["position"] for call in calls] == [0, 1, 2, 3]


def test_ignored_position_fails_instead_of_looping_or_successfully_truncating():
    """Allowing the initial zero/one alias does not allow repeated subsequent pages."""
    with pytest.raises(IncompleteSourceCollection, match="repeats"):
        load({0: records(1, 2), 1: records(1, 2), 2: records(1, 2)}, contract=ZERO)


def test_verified_row_offset_dialect_advances_by_retained_rows():
    """An exact one-row overlap distinguishes offset from page-number semantics."""
    result, calls = load(
        {0: records(1, 2), 1: records(2, 3), 2: records(3, 4), 4: []}, contract=ZERO
    )
    assert result == records(1, 2, 3, 4)
    assert [call["position"] for call in calls] == [0, 1, 2, 4]


@pytest.mark.parametrize("second", [records(1, 3), records(2, 2)])
def test_ambiguous_or_invalid_probe_overlap_is_rejected(second):
    """A duplicate or arbitrary partial overlap is not proof of an offset dialect."""
    with pytest.raises(IncompleteSourceCollection):
        load({0: records(1, 2), 1: second}, contract=ZERO)


def test_empty_zero_probe_checks_one_before_concluding_no_records():
    """Servers returning empty for invalid zero must not hide a valid first page."""
    result, calls = load({0: [], 1: records(1, 2), 2: []}, contract=ZERO)
    assert result == records(1, 2)
    assert [call["position"] for call in calls] == [0, 1, 2]
    result, calls = load({0: [], 1: []}, contract=ZERO)
    assert not result and len(calls) == 2


def test_page_and_record_bounds_are_failures_not_successful_cutoffs():
    """Whole-collection success needs completeness proof before any resource limit."""
    with pytest.raises(IncompleteSourceCollection, match="page bound"):
        load({1: records(1, 2)}, maximum_pages=1)
    with pytest.raises(IncompleteSourceCollection, match="row bound"):
        load({1: records(1, 2)}, maximum_records=1)
    with pytest.raises(IncompleteSourceCollection, match="page bound"):
        load({0: records(1, 2)}, contract=ZERO, maximum_pages=1)


@pytest.mark.parametrize(
    "options",
    [
        {"page_size": True},
        {"page_size": 0},
        {"page_size": 501},
        {"maximum_records": True},
        {"maximum_records": 0},
        {"maximum_pages": True},
        {"maximum_pages": 0},
        {"contract": replace(ENVELOPE, zero_probe=True)},
    ],
)
def test_invalid_internal_pagination_limits_do_not_contact_provider(options):
    """Configuration/programming errors fail before even a synthetic first page."""
    with pytest.raises(ValueError):
        load({}, **options)

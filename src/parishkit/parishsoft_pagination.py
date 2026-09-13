"""Strict bounded pagination for source-of-truth loads, independent of HTTP.

Legacy shared callers retain their existing pagination behavior. Coherent
loaders opt into this parser with a frozen endpoint contract and identity
validator. No malformed page is returned as a partial successful collection.
"""

from dataclasses import dataclass


class IncompleteSourceCollection(ValueError):
    """A bounded read cannot prove a complete, continuous source collection."""


@dataclass(frozen=True)
class PageContract:
    """Published field names and response shape, not caller-entered HTTP options."""

    size_field: str
    position_field: str
    envelope: bool
    zero_probe: bool = False
    total_field: str | None = None
    ordinal_field: str | None = None


class _Collection:
    """Validate identity, counts and embedded ordinals before retaining rows."""

    def __init__(self, contract, identify, *, maximum):
        """The owning endpoint supplies its exact immutable identity extractor."""
        self.contract, self.identify, self.maximum = contract, identify, maximum
        self.rows, self.identities = [], set()
        self.expected_total = None
        self.ordinal_origin = None

    def add(self, rows):
        """Duplicates and changing embedded totals indicate an incoherent scan."""
        if len(self.rows) + len(rows) > self.maximum:
            raise IncompleteSourceCollection("Source collection exceeds its row bound.")
        for row in rows:
            identity = self.identify(row)
            if identity in self.identities:
                raise IncompleteSourceCollection(
                    "Source collection repeats an identity."
                )
            if self.contract.total_field is not None:
                total = row.get(self.contract.total_field)
                if type(total) is not int or not 1 <= total <= self.maximum:
                    raise IncompleteSourceCollection("Source total is unavailable.")
                if self.expected_total is not None and total != self.expected_total:
                    raise IncompleteSourceCollection(
                        "Source total changed during read."
                    )
                self.expected_total = total
            if self.contract.ordinal_field is not None:
                ordinal = row.get(self.contract.ordinal_field)
                if type(ordinal) is not int:
                    raise IncompleteSourceCollection("Source ordinal is unavailable.")
                if self.ordinal_origin is None:
                    if ordinal not in (0, 1):
                        raise IncompleteSourceCollection("Source first row is missing.")
                    self.ordinal_origin = ordinal
                if ordinal != len(self.rows) + self.ordinal_origin:
                    raise IncompleteSourceCollection(
                        "Source row order is discontinuous."
                    )
            self.identities.add(identity)
            self.rows.append(row)

    def finish(self):
        """An empty next page is not sufficient if a declared total remains unmet."""
        if self.expected_total is not None and self.expected_total != len(self.rows):
            raise IncompleteSourceCollection(
                "Source collection does not match its total."
            )
        return self.rows


def _array(value, size):
    """No permissive envelope guessing or truthiness-based empty success."""
    if type(value) is not list or len(value) > size:
        raise IncompleteSourceCollection("Source page has an invalid array shape.")
    if any(type(row) is not dict for row in value):
        raise IncompleteSourceCollection("Source page contains an invalid record.")
    return value


def _envelope(value, *, position, size, maximum):
    """Require exact, internally consistent server paging evidence on every page."""
    if type(value) is not dict or set(value) != {"data", "pagingInfo"}:
        raise IncompleteSourceCollection("Source paging envelope is unavailable.")
    info = value["pagingInfo"]
    if (
        type(info) is not dict
        or set(info) != {"totalRecords", "totalPages", "pageSize", "pageNumber"}
        or any(type(item) is not int for item in info.values())
    ):
        raise IncompleteSourceCollection("Source paging metadata is invalid.")
    total = info["totalRecords"]
    pages = (total + size - 1) // size
    if (
        not 0 <= total <= maximum
        or info["pageSize"] != size
        or info["pageNumber"] != position
        or info["totalPages"] not in ({0, 1} if total == 0 else {pages})
        or position > max(pages, 1)
    ):
        raise IncompleteSourceCollection("Source paging metadata is inconsistent.")
    # The published envelope permits nullable data; only an explicit zero count
    # proves that null represents a complete empty collection.
    rows = _array([] if value["data"] is None and total == 0 else value["data"], size)
    if len(rows) != min(size, max(0, total - (position - 1) * size)):
        raise IncompleteSourceCollection("Source page is incomplete.")
    return rows, total, position >= max(pages, 1)


def _probe(first, second, *, size, collection):
    """Resolve zero/one origin or a demonstrable row-offset dialect without loss.

    Two identical first responses prove only a zero/one alias, not completion;
    page two must still advance or end. An exactly one-row overlapping response
    proves offset behavior only when all overlapping values agree. Other overlap
    is ambiguous and rejected. Preliminary probe rows are never double-counted.
    """
    collection.add(first)
    if not first:
        collection.add(second)
        return 2, 1, not second
    if first == second:
        return 2, 1, False
    first_ids = [collection.identify(row) for row in first]
    second_ids = [collection.identify(row) for row in second]
    if len(set(second_ids)) != len(second_ids):
        raise IncompleteSourceCollection("Source probe repeats an identity.")
    overlap = set(first_ids) & set(second_ids)
    if overlap:
        if (
            len(first) > 1
            and len(second) >= len(first) - 1
            and first[1:] == second[: len(first) - 1]
        ):
            # Re-read the next full offset window instead of retaining just the
            # probe's boundary row; this preserves one contiguous validated scan.
            return len(first), size, False
        raise IncompleteSourceCollection("Source zero-origin probe is ambiguous.")
    collection.add(second)
    return 2, 1, not second


def read_pages(
    fetch,
    *,
    contract,
    identify,
    page_size=500,
    maximum_records=100000,
    maximum_pages=2000,
):
    """Fetch one complete collection using only bounded, validated page positions.

    ``fetch`` receives a new mapping containing the contract's two paging fields.
    It owns HTTP, per-attempt fencing and aggregate byte/time budgets. Responses
    are private data and never appear in errors. The page bound includes probes.
    """
    if (
        not isinstance(contract, PageContract)
        or not callable(fetch)
        or not callable(identify)
        or type(page_size) is not int
        or not 1 <= page_size <= 500
        or type(maximum_records) is not int
        or not 1 <= maximum_records <= 1000000
        or type(maximum_pages) is not int
        or not 1 <= maximum_pages <= 10000
        or (contract.envelope and contract.zero_probe)
    ):
        raise ValueError("Invalid source pagination contract or bounds.")
    collection = _Collection(contract, identify, maximum=maximum_records)
    requests = 0

    def page(position):
        """A page limit is failure, not successful truncation of the collection."""
        nonlocal requests
        if requests >= maximum_pages:
            raise IncompleteSourceCollection(
                "Source collection exceeds its page bound."
            )
        requests += 1
        return fetch(
            {contract.size_field: page_size, contract.position_field: position}
        )

    position, step = 1, 1
    if contract.zero_probe:
        first, second = _array(page(0), page_size), _array(page(1), page_size)
        position, step, done = _probe(
            first, second, size=page_size, collection=collection
        )
        if done:
            return collection.finish()
    while True:
        response = page(position)
        if contract.envelope:
            rows, total, done = _envelope(
                response, position=position, size=page_size, maximum=maximum_records
            )
            if (
                collection.expected_total is not None
                and collection.expected_total != total
            ):
                raise IncompleteSourceCollection("Source total changed during read.")
            collection.expected_total = total
        else:
            rows = _array(response, page_size)
            done = not rows
        collection.add(rows)
        if done:
            return collection.finish()
        # Offset scans advance by actual rows, not an assumed server page size.
        position += len(rows) if step == page_size and contract.zero_probe else step

"""Strict bounded pagination for source-of-truth loads, independent of HTTP.

Legacy shared callers retain their existing pagination behavior. Coherent
loaders opt into this parser with a frozen endpoint contract and identity
validator. No malformed page is returned as a partial successful collection.
"""

from dataclasses import dataclass


class IncompleteSourceCollection(ValueError):
    """A bounded read cannot prove a complete, continuous source collection."""


class ShiftedSourceScan(IncompleteSourceCollection):
    """The collection moved while its pages were read; a fresh scan may succeed.

    ParishSoft pages lists by position. When its ordering is not stable between
    page requests (or records change mid-scan), one record can appear on two
    pages while another is skipped. Such a scan is still rejected, since it
    cannot prove completeness, but it is transient: callers may retry the
    whole read instead of treating the provider data as invalid.
    """


class SourceLoadBudgetExceeded(IncompleteSourceCollection):
    """The provider answered too slowly for the load to finish in its time.

    A slow provider, not invalid data: a later read may finish in time.
    Request-count and byte bounds stay IncompleteSourceCollection, since
    runaway paging or an oversized corpus does not fix itself by retrying.

    ``limit_seconds`` and ``elapsed_seconds`` are the load's bound and how
    long it had run, in whole seconds, for the caller's timeout record; the
    message names neither and is never logged.
    """

    def __init__(self, message, *, limit_seconds=None, elapsed_seconds=None):
        super().__init__(message)
        self.limit_seconds = limit_seconds
        self.elapsed_seconds = elapsed_seconds


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
        # Rows the provider sent, counting exact same-page repeats that are not
        # retained; embedded totals, ordinals and envelope totals count them.
        self.received = 0
        self.expected_total = None
        self.ordinal_origin = None

    def add(self, rows):
        """Duplicates and changing embedded totals indicate an incoherent scan.

        ParishSoft data really can list one record twice: some family workgroups
        enroll a Family twice, and the list returns two identical, adjacent rows
        on one page. Such an exact repeat within a page is kept once. A repeat
        with any differing field, or one spanning pages (the sign of a shifted
        or overlapping scan), still rejects the whole collection.
        """
        if self.received + len(rows) > self.maximum:
            raise IncompleteSourceCollection("Source collection exceeds its row bound.")
        page = {}
        for row in rows:
            identity = self.identify(row)
            repeat = identity in page and page[identity] == row
            if identity in self.identities and not repeat:
                if identity not in page:
                    # Seen on an earlier page: the scan shifted between pages.
                    raise ShiftedSourceScan("Source collection repeats an identity.")
                raise IncompleteSourceCollection(
                    "Source collection repeats an identity."
                )
            if self.contract.total_field is not None:
                total = row.get(self.contract.total_field)
                if type(total) is not int or not 1 <= total <= self.maximum:
                    raise IncompleteSourceCollection("Source total is unavailable.")
                if self.expected_total is not None and total != self.expected_total:
                    raise ShiftedSourceScan("Source total changed during read.")
                self.expected_total = total
            if self.contract.ordinal_field is not None:
                ordinal = row.get(self.contract.ordinal_field)
                if type(ordinal) is not int:
                    raise IncompleteSourceCollection("Source ordinal is unavailable.")
                if self.ordinal_origin is None:
                    if ordinal not in (0, 1):
                        raise IncompleteSourceCollection("Source first row is missing.")
                    self.ordinal_origin = ordinal
                if ordinal != self.received + self.ordinal_origin:
                    raise ShiftedSourceScan("Source row order is discontinuous.")
            self.received += 1
            if repeat:
                continue
            page[identity] = row
            self.identities.add(identity)
            self.rows.append(row)

    def finish(self):
        """An empty next page is not sufficient if a declared total remains unmet."""
        if self.expected_total is not None and self.expected_total != self.received:
            raise ShiftedSourceScan("Source collection does not match its total.")
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
    field = collection.contract.total_field
    if field is not None and any(
        type(row.get(field)) is int and row.get(field) != collection.expected_total
        for row in second
    ):
        # The embedded total moved between the two probe requests: the
        # collection changed mid-read, so this is a retryable shifted scan
        # rather than an ambiguous probe.
        raise ShiftedSourceScan("Source total changed during read.")
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
                raise ShiftedSourceScan("Source total changed during read.")
            collection.expected_total = total
        else:
            rows = _array(response, page_size)
            done = not rows
        collection.add(rows)
        if done:
            return collection.finish()
        # Offset scans advance by actual rows, not an assumed server page size.
        position += len(rows) if step == page_size and contract.zero_probe else step

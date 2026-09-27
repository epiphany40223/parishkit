"""Per-collection download progress carried in a task's two progress counters.

A setup source load spends most of its time downloading from ParishSoft, and
its TaskRun has only ``progress_current`` and ``progress_total``, neither of
which PostgreSQL lets decrease within one claim. Rather than change the
schema, the worker encodes each finished collection as one progress step:

* ``current`` counts finished collections ("units", one per ministry roster);
* ``total`` is ``current`` plus every record downloaded so far.

Both only grow, and ``current <= total`` always holds. Every progress change
is kept in the append-only task event history, so the page reads the
sequence back and recovers, per collection, how many records it brought.
The staging and validating phases that follow keep counting from the last
download value (``base``) so the counters still never move backwards.
"""

from dataclasses import dataclass

# Collections in the order ``load_full_source`` reports them. The shared
# ParishSoft loader reports one ``ministry_roster`` per ministry, so their
# number is known only after ``ministry_types`` arrives.
BEFORE_ROSTERS = (
    "families",
    "family_groups",
    "members",
    "member_contactinfos",
    "ministry_types",
)
AFTER_ROSTERS = ("funds",)
ROSTER = "ministry_roster"
KNOWN = frozenset((*BEFORE_ROSTERS, ROSTER, *AFTER_ROSTERS))


class DownloadProgress:
    """Turn loader ``(collection, count)`` reports into monotonic counters."""

    def __init__(self, report):
        """``report(current, total)`` persists one step; it may raise to stop."""
        self.report = report
        self.units = 0
        self.records = 0

    @property
    def base(self):
        """The last download ``total``, where saving and checking start counting."""
        return self.units + self.records

    def __call__(self, collection, count):
        """Record one finished collection; unknown names are not displayed."""
        if collection not in KNOWN or type(count) is not int or count < 0:
            return
        self.units += 1
        self.records += count
        self.report(self.units, self.base)


@dataclass(frozen=True)
class Collection:
    """One download line: records found, and for rosters how many of how many."""

    key: str
    count: int
    done: bool
    finished: int | None = None
    expected: int | None = None


def collections(observations):
    """Decode ordered download ``(current, total)`` observations per collection.

    ``observations`` are the download-phase progress values of one claim in
    history order. Each step adds one unit and that collection's records, so
    the difference between consecutive ``total - current`` values is the
    record count of the collection that just finished.
    """
    records = {0: 0}
    for current, total in observations:
        records[current] = total - current
    finished = max(records)

    def count(unit):
        """Records brought by the collection finishing at ``unit``, if known."""
        if unit in records and unit - 1 in records:
            return records[unit] - records[unit - 1]
        return 0

    result = [
        Collection(key, count(unit), unit <= finished)
        for unit, key in enumerate(BEFORE_ROSTERS, start=1)
    ]
    ministries_unit = len(BEFORE_ROSTERS)
    ministries = count(ministries_unit) if finished >= ministries_unit else None
    rosters_done = max(0, min(finished - ministries_unit, ministries or 0))
    result.append(
        Collection(
            ROSTER,
            sum(count(ministries_unit + index) for index in range(1, rosters_done + 1)),
            ministries is not None and rosters_done == ministries,
            rosters_done,
            ministries,
        )
    )
    after = ministries_unit + (ministries or 0)
    result.extend(
        Collection(
            key,
            count(after + unit),
            ministries is not None and after + unit <= finished,
        )
        for unit, key in enumerate(AFTER_ROSTERS, start=1)
    )
    return result

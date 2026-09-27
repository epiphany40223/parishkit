"""Per-collection download progress survives the two monotonic task counters."""

from parishkit.stewardship.source.load_progress import DownloadProgress, collections


def encoded(events):
    """Run loader reports through the worker encoder; return persisted values."""
    persisted = []
    progress = DownloadProgress(
        lambda current, total: persisted.append((current, total))
    )
    for event in events:
        progress(*event)
    return progress, persisted


def by_key(observations):
    """Decode observations into a key-indexed mapping for readable assertions."""
    return {item.key: item for item in collections(observations)}


LOADER = [
    ("families", 2687),
    ("family_groups", 12),
    ("members", 6409),
    ("member_contactinfos", 6409),
    ("ministry_types", 3),
    ("ministry_roster", 40),
    ("ministry_roster", 0),
    ("ministry_roster", 7),
    ("funds", 25),
]


def test_counters_never_decrease_and_stay_within_bounds():
    """PostgreSQL rejects a decreasing counter or current above total."""
    progress, persisted = encoded(LOADER)
    assert all(current <= total for current, total in persisted)
    for before, after in zip(persisted, persisted[1:], strict=False):
        assert after[0] > before[0] and after[1] >= before[1]
    assert progress.base == persisted[-1][1]


def test_complete_download_decodes_every_collection_count():
    """Each collection shows the records it brought, rosters as a total."""
    _, persisted = encoded(LOADER)
    items = by_key([(0, 0), *persisted])
    assert [item.key for item in collections(persisted)] == [
        "families",
        "family_groups",
        "members",
        "member_contactinfos",
        "ministry_types",
        "ministry_roster",
        "funds",
    ]
    assert items["families"].count == 2687 and items["families"].done
    assert items["members"].count == 6409
    assert items["ministry_types"].count == 3
    roster = items["ministry_roster"]
    assert (roster.count, roster.finished, roster.expected, roster.done) == (
        47,
        3,
        3,
        True,
    )
    assert items["funds"].count == 25 and items["funds"].done


def test_partial_download_shows_rosters_so_far():
    """Mid-roster, earlier collections are done and later ones are not."""
    _, persisted = encoded(LOADER[:6])
    items = by_key(persisted)
    assert items["members"].done
    roster = items["ministry_roster"]
    assert (roster.finished, roster.expected, roster.done) == (1, 3, False)
    assert not items["funds"].done


def test_before_the_ministry_list_the_roster_total_is_unknown():
    """Nothing claims a roster count until ParishSoft has listed the Ministries."""
    _, persisted = encoded(LOADER[:2])
    items = by_key(persisted)
    assert items["family_groups"].done and not items["members"].done
    assert items["ministry_roster"].expected is None
    assert not items["ministry_roster"].done
    assert not any(item.done for item in collections([]))


def test_a_parish_without_ministries_has_nothing_to_wait_for():
    """Zero Ministries means zero rosters, which counts as finished."""
    _, persisted = encoded([*LOADER[:4], ("ministry_types", 0), ("funds", 1)])
    items = by_key(persisted)
    assert items["ministry_roster"].done and items["ministry_roster"].expected == 0
    assert items["funds"].done and items["funds"].count == 1


def test_unknown_or_malformed_reports_do_not_shift_the_order():
    """Only the expected collections advance the unit count."""
    progress, persisted = encoded(
        [("families", 1), ("family_workgroups", 9), ("members", -1), ("members", 2)]
    )
    assert persisted == [(1, 2), (2, 5)]
    assert progress.base == 5

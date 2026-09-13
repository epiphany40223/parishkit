"""Affected households replace atomically only when their relationship scope is safe."""

from datetime import UTC, date, datetime
from uuid import uuid4

import pytest
from test_parishsoft_source import source as client_factory

from parishkit.parishsoft_changes import ChangeFeedIncomplete
from parishkit.stewardship.source.corpus import normalize_core
from parishkit.stewardship.source.cursors import refresh_cursor
from parishkit.stewardship.source.delta import load_delta_source
from parishkit.stewardship.source.windows import RefreshWindow

from .test_source_corpus import TODAY, source

BEFORE = date(2026, 9, 10)
START = datetime(2026, 9, 11, 12, tzinfo=UTC)


def indication(identifier=1):
    """Use one typed same-parish event inside the overlapping query interval."""
    return {
        "family_DUID": identifier,
        "logDate": START.isoformat(),
        "currentParishID": 5,
        "previousParishID": 5,
    }


def household(*, family_change=None, member_change=None):
    """Full-search-equivalent DTOs differ only where a test explicitly changes them."""
    data = source()
    member = {**data.members[3], "birthdate": "1960-01-01", **(member_change or {})}
    return [
        data.families[1] | (family_change or {}),
        [{"memberDUID": 3, "familyDUID": 1, "middleName": "Middle"}],
        member,
    ]


def arguments():
    """Current base/cursor are coherent and independent of staged delta output."""
    base = normalize_core(source(), as_of=BEFORE)
    selected = RefreshWindow(uuid4(), ())
    return dict(
        base=base,
        base_cursor=refresh_cursor(
            snapshot_id=uuid4(),
            kind="full",
            started_at=datetime(2026, 9, 10, 12, tzinfo=UTC),
            window_digest=selected.digest,
            evidence={
                "giving_as_of_date": BEFORE.isoformat(),
                "anonymous_pledges": 0,
                "anonymous_contributions": 0,
            },
        ),
        window=selected,
        started_at=START,
        as_of=TODAY,
        previous_full_counts={kind: len(rows) for kind, rows in base.items()},
    )


def client(tmp_path, rows):
    return client_factory(tmp_path, [[{"organizationID": 5}], *rows])


def test_unchanged_household_round_trip_does_not_copy_any_source_payload(tmp_path):
    """Full and scoped DTO conversions produce identical canonical data."""
    options = arguments()
    connection = client(
        tmp_path,
        [[indication()], *household(), [{"famGroupID": 7, "famGroup": "Active"}]],
    )
    result = load_delta_source(connection, **options)
    assert result.corpus == options["base"]
    assert result.evidence["families_reloaded"] == 1
    assert result.evidence["giving_as_of_date"] == BEFORE.isoformat()
    assert not connection.session.responses


def test_one_contact_changes_without_reloading_ministry_fund_or_giving(tmp_path):
    """A contact edit neither copies other entities nor expands provider reads."""
    options = arguments()
    connection = client(
        tmp_path,
        [
            [indication()],
            *household(member_change={"emailAddress": "changed@example.org"}),
            [{"famGroupID": 7, "famGroup": "Active"}],
        ],
    )
    result = load_delta_source(connection, **options)
    for kind in options["base"]:
        assert (result.corpus[kind] == options["base"][kind]) == (kind != "contact")
    assert (
        options["base"]["contact"]["member:3"]["emails"]
        != result.corpus["contact"]["member:3"]["emails"]
    )
    assert not any(
        "offering" in call[1] or "ministry/" in call[1]
        for call in connection.session.calls
    )


def test_family_inactivation_updates_eligibility_without_erasing_identity(tmp_path):
    """The whole household remains retained when its last active head inactivates."""
    connection = client(
        tmp_path,
        [
            [indication()],
            *household(member_change={"memberStatus": "Inactive"}),
            [{"famGroupID": 7, "famGroup": "Active"}],
        ],
    )
    result = load_delta_source(connection, **arguments())
    assert not result.corpus["family"]["1"]["portal_eligible"]
    assert set(result.corpus["member"]) == {"3"}


def test_empty_indications_preserve_giving_age_but_reevaluate_roster_dates(tmp_path):
    """Date-derived currentness advances without inventing a newer giving read."""
    options = arguments()
    row = next(iter(options["base"]["roster"].values()))
    row["endDate"] = TODAY.isoformat()
    assert row["current"] is True
    connection = client(tmp_path, [[]])
    result = load_delta_source(connection, **options)
    assert next(iter(result.corpus["roster"].values()))["current"] is False
    assert row["current"] is True
    assert result.evidence["giving_as_of_date"] == BEFORE.isoformat()
    assert len(connection.session.calls) == 2


@pytest.mark.parametrize("member_rows", [[], [{"memberDUID": 4, "familyDUID": 1}]])
def test_removed_or_replaced_member_requires_full_refresh(tmp_path, member_rows):
    """Changed composition cannot silently leave dangling outside relationships."""
    rows = [household()[0], member_rows]
    if member_rows:
        rows.append({"memberDUID": 4, "familyDUID": 1})
    connection = client(tmp_path, [[indication()], *rows])
    with pytest.raises(ChangeFeedIncomplete):
        load_delta_source(connection, **arguments())


def test_changed_global_family_group_definition_requires_full_refresh(tmp_path):
    """A global eligibility lookup change may affect unqueried households."""
    connection = client(
        tmp_path,
        [[indication()], *household(), [{"famGroupID": 7, "famGroup": "Inactive"}]],
    )
    with pytest.raises(ChangeFeedIncomplete, match="group definitions"):
        load_delta_source(connection, **arguments())


def test_unavailable_family_requires_full_without_a_partial_corpus(tmp_path):
    """Missing source identity is not permission to delete a Family via delta."""
    connection = client(tmp_path, [[indication()], {}])
    connection.session.responses[2].status_code = 404
    with pytest.raises(ChangeFeedIncomplete):
        load_delta_source(connection, **arguments())


def test_new_family_with_new_members_can_be_added_without_erasing_existing_data(
    tmp_path,
):
    """A wholly new household has no pre-existing outside Member relationships."""
    rows = [
        {
            "familyDUID": 4,
            "familyID": 14,
            "registeredOrganizationID": 5,
            "famGroupID": 7,
        },
        [{"memberDUID": 5, "familyDUID": 4}],
        {
            "memberDUID": 5,
            "familyDUID": 4,
            "memberType": "Head",
            "memberStatus": "Active",
        },
    ]
    connection = client(
        tmp_path, [[indication(4)], *rows, [{"famGroupID": 7, "famGroup": "Active"}]]
    )
    options = arguments()
    result = load_delta_source(connection, **options)
    assert set(result.corpus["family"]) == {"1", "2", "4"}
    assert result.corpus["family"]["4"]["portal_eligible"]
    assert result.corpus["family"]["1"] == options["base"]["family"]["1"]


def test_member_move_into_new_household_requires_full(tmp_path):
    """A retained Member's old household must not remain inconsistent."""
    rows = [
        {"familyDUID": 4},
        [{"memberDUID": 3, "familyDUID": 4}],
        {"memberDUID": 3, "familyDUID": 4},
    ]
    connection = client(tmp_path, [[indication(4)], *rows])
    with pytest.raises(ChangeFeedIncomplete, match="Moved"):
        load_delta_source(connection, **arguments())


def test_changed_window_or_missing_giving_coverage_falls_back_before_network(tmp_path):
    """An incompatible base cannot issue any provider reads as a valid delta."""
    for changes in ({"window": RefreshWindow(uuid4(), ())}, {"load": {}}):
        options = arguments()
        if "load" in changes:
            options["base_cursor"]["load"] = changes["load"]
        else:
            options.update(changes)
        connection = client(tmp_path, [])
        with pytest.raises(ChangeFeedIncomplete):
            load_delta_source(connection, **options)
        assert not connection.session.calls

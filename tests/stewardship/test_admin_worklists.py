"""The worklist commands' own pieces (#1735): vocabularies, JSON Lines, refusals.

The pages' reads, files, admissions, services and events are the pages'
own, proven against the pages and the SQL guard in
``database/test_admin_worklists_cli_postgresql.py``; these tests cover what
only the commands add.
"""

import io
import json
from datetime import UTC, datetime
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest

from parishkit.stewardship import (
    admin_cli,
    admin_family_exports,
    admin_limitations_export,
    admin_worklists,
)
from parishkit.stewardship.admin_followup import InvalidFollowup
from parishkit.stewardship.admin_worklists import INFORMATION, LIMITATIONS
from parishkit.stewardship.pipeline import Decision, Placement, Stage, Stream
from parishkit.stewardship.reports import information_worklist, limitations
from parishkit.stewardship.reports.information_worklist import EXPORT_FILTERS
from parishkit.stewardship.responses import information, limitation_review
from parishkit.stewardship.responses.census_review import bulk_key_for

from .test_admin_cli import quiet  # noqa: F401

ZONE = ZoneInfo("America/New_York")
WHEN = datetime(2054, 10, 3, 14, 5, tzinfo=UTC)


def test_the_command_line_vocabularies_are_the_pages():
    """Stage and decision choices match the pages' own."""
    assert set(admin_worklists.INFORMATION_STAGES) == information_worklist.STAGE_CHOICES
    assert set(admin_worklists.INFORMATION_STAGES) == set(EXPORT_FILTERS)
    assert (
        *(stage.value for stage in limitations.STAGES),
        "all",
    ) == admin_worklists.LIMITATION_STAGES
    for decisions in (information.DECISIONS, limitation_review.DECISIONS):
        assert set(admin_worklists.DECIDE_ACTIONS) == {
            action.value for action in decisions
        }
    # Each key names its heading, in the files' order.
    assert dict(
        zip(
            limitations.MEMBER_HEADINGS,
            admin_limitations_export.MEMBER_KEYS,
            strict=True,
        )
    ) == {
        "Member": "member",
        "Member DUID": "member_duid",
        "Family": "family",
        "ParishSoft status": "parishsoft_status",
        "Family DUID": "family_duid",
        "Cannot participate in ministries": "cannot_participate",
        "Latest response": "latest_response",
        "Stage": "stage",
    }
    assert dict(
        zip(
            limitations.FAMILY_HEADINGS,
            admin_limitations_export.FAMILY_KEYS,
            strict=True,
        )
    ) == {
        "Family": "family",
        "ParishSoft status": "parishsoft_status",
        "Family DUID": "family_duid",
        "Limitation": "limitation",
        "Latest response": "latest_response",
        "Stage": "stage",
    }


def answer(kind, member_key, placement, *, version=0, member_duid=None):
    """One Limitations row as the report shapes it."""
    return {
        "kind": kind,
        "member_key": member_key,
        "member_name": "Pat Example",
        "member_duid": member_duid,
        "proposed": kind == "proposed_member",
        "family_name": "Example, Pat",
        "family_duid": 41,
        "submitted_at": WHEN,
        "placement": placement,
        "version": version,
    }


def test_each_record_is_the_csv_row_with_its_item_version_and_actions():
    """The cells are the CSV's under fixed keys; the kind, item id, version
    and the actions the row's stage offers are the row's."""
    local = str(uuid4())
    result = {
        "members": [
            answer("member", "3", Placement(Stage.TO_REVIEW), member_duid=3),
            answer(
                "proposed_member",
                local,
                Placement(Stage.TO_HANDLE, Decision.ACCEPTED),
                version=1,
            ),
        ],
        "families": [answer("family", "", Placement(Stage.IGNORED, Decision.IGNORED))],
    }
    body = admin_limitations_export.limitations_jsonl(result, ZONE)
    assert body.endswith(b"\n")
    found = [json.loads(line) for line in body.decode().splitlines()]
    members, families = limitations.export_tables(result, ZONE)
    for record, cells, keys in zip(
        found,
        [*members, *families],
        [admin_limitations_export.MEMBER_KEYS] * 2
        + [admin_limitations_export.FAMILY_KEYS],
        strict=True,
    ):
        assert set(record) == {*keys, "kind", "item_id", "version", "actions"}
        assert [record[key] for key in keys] == list(cells)
    assert [record["item_id"] for record in found] == ["m41-3", f"p41-{local}", "f41"]
    assert [record["kind"] for record in found] == [
        "member",
        "proposed_member",
        "family",
    ]
    assert [record["version"] for record in found] == [0, 1, 0]
    assert [record["actions"] for record in found] == [
        ["accept", "ignore"],
        ["back_to_review", "handled"],
        ["back_to_review"],
    ]
    assert found[0]["latest_response"] == "2054-10-03T10:05:00-04:00"
    # Nothing is offered while other campaign work holds the campaign.
    held = admin_limitations_export.limitations_jsonl(result, ZONE, mutable=False)
    assert all(not json.loads(line)["actions"] for line in held.decode().splitlines())
    empty = {"members": [], "families": []}
    assert admin_limitations_export.limitations_jsonl(empty, ZONE) == b""


def test_handled_offers_back_to_review_and_history_offers_nothing():
    """Handled is undone by Back to review (#1840), and a request in
    History takes no decision."""
    placement = Placement(Stage.HANDLED, Decision.ACCEPTED)
    for stream in (Stream.ADDITIONAL_INFORMATION, Stream.CANNOT_PARTICIPATE):
        assert admin_worklists.row_actions(stream, placement, True) == [
            "back_to_review"
        ]
    history = Placement(Stage.HISTORY)
    assert (
        admin_worklists.row_actions(Stream.ADDITIONAL_INFORMATION, history, True) == []
    )


def problems(error):
    """The fields an ``InvalidFollowup`` names."""
    return [found["field"] for found in error.value.fields]


def test_choices_the_page_could_not_post_are_refused():
    """More than 100 items, a long or unbounded note, or an answer id the
    page never draws; a Limitations id is returned in its canonical form."""
    many = {uuid4(): 1 for _ in range(101)}
    with pytest.raises(InvalidFollowup) as error:
        admin_worklists.check_choice(INFORMATION, many, "")
    assert problems(error) == ["item"]
    with pytest.raises(InvalidFollowup) as error:
        admin_worklists.check_choice(INFORMATION, {uuid4(): 1}, "x" * 2001)
    assert problems(error) == ["note"]
    with pytest.raises(InvalidFollowup) as error:
        admin_worklists.check_choice(INFORMATION, {uuid4(): 1}, "nul\x00")
    assert problems(error) == ["note"]
    for item in ("m041-3", "x41", "f41-3", "p41-NOT-A-UUID", "m41"):
        with pytest.raises(InvalidFollowup) as error:
            admin_worklists.check_choice(LIMITATIONS, {item: 0}, "")
        assert problems(error) == ["item"], item
    assert admin_worklists.check_choice(LIMITATIONS, {"m41-3": 2, "f41": 0}, "ok") == {
        "m41-3": 2,
        "f41": 0,
    }


def test_each_item_s_key_is_the_service_s():
    """One item takes the key itself; several each take the key derived from
    it, as the services derive them."""
    key = uuid4()
    first, second = uuid4(), uuid4()
    assert admin_worklists._keys({first: 1}, key) == {first: key}
    assert admin_worklists._keys({first: 1, second: 2}, key) == {
        first: bulk_key_for(key, first),
        second: bulk_key_for(key, second),
    }
    assert admin_worklists._keys({"m41-3": 0, "f41": 0}, key) == {
        "m41-3": bulk_key_for(key, "m41-3"),
        "f41": bulk_key_for(key, "f41"),
    }


@pytest.mark.parametrize(
    "argv",
    [
        ["information", "decide", "--action", "edit", "--item", f"{uuid4()}:1"],
        ["information", "decide", "--action", "accept", "--item", "nine:1"],
        ["information", "decide", "--action", "accept", "--item", str(uuid4())],
        ["information", "decide", "--action", "accept"],
        ["limitations", "decide", "--action", "accept", "--item", "m41-3"],
        ["limitations", "decide", "--action", "accept", "--item", "m41-3:x"],
        ["limitations", "decide", "--action", "entered_by_hand", "--item", "f41:0"],
        ["information", "list", "--stage", "conflict"],
        ["information", "list", "--search", "Private"],
        ["export", "information", "--stage", "updated"],
        ["export", "limitations", "--format", "json"],
    ],
)
def test_options_outside_the_pages_vocabulary_are_usage_errors(argv):
    """Only the pages' decisions and stages, and ID:VERSION items."""
    out = io.StringIO()
    code = admin_cli.main(
        [*argv, "--config", "web.yaml", "--session-stdin"],
        stdin=io.BytesIO(b""),
        stdout=out,
        stderr=out,
    )
    assert code == 2, argv
    assert json.loads(out.getvalue())["error"]["code"] == "usage"


def test_an_information_export_stage_is_the_export_pane_s_filters():
    """``export information --stage`` posts what the worklist's export pane
    posts for that Stage; with a follow-up --filter it is invalid."""
    for stage, (disposition, needed, completed) in EXPORT_FILTERS.items():
        assert admin_family_exports.stage_filters(["start=2054-10-01"], stage) == {
            "start": "2054-10-01",
            "sort": "name",
            "disposition": disposition,
            "needed": needed,
            "completed": completed,
        }
    assert admin_family_exports.stage_filters(["needed=yes"], None) == {"needed": "yes"}
    # A sort the operator chose is kept, as the pane posts the table's.
    assert admin_family_exports.stage_filters(["sort=newest"], "handled")["sort"] == (
        "newest"
    )
    for name in ("disposition", "needed", "completed"):
        with pytest.raises(ValueError):
            admin_family_exports.stage_filters([f"{name}=any"], "to_review")


def test_a_cannot_contribute_record_names_its_pledge_and_offers_nothing(monkeypatch):
    """A Family's Cannot contribute financially row (``contributions``, which
    #1734's report adds, R39 finding 11) follows the Families' answers, with
    its Pledges item id, its version and no action: it is decided on
    Pledges. ``export_tables`` is pinned to its #1734 shape, the Families
    cells followed by the contributions' cells."""
    from parishkit.stewardship.reports import limitations

    pledge = str(uuid4())
    result = {
        "members": [],
        "families": [answer("family", "", Placement(Stage.TO_REVIEW))],
        "contributions": [
            answer("cannot_give", "", Placement(Stage.TO_ENTER_BY_HAND), version=2)
            | {"pledge_item": pledge}
        ],
    }
    row = ("Example, Pat", "", "41", "x", "2054-10-03T10:05:00-04:00", "To review")
    monkeypatch.setattr(
        limitations, "export_tables", lambda result, zone: ([], [row] * 2)
    )
    found = [
        json.loads(line)
        for line in admin_limitations_export.limitations_jsonl(result, ZONE)
        .decode()
        .splitlines()
    ]
    assert [record["kind"] for record in found] == ["family", "cannot_give"]
    assert found[1]["item_id"] == f"pledge-{pledge}"
    assert (found[1]["version"], found[1]["actions"]) == (2, [])
    assert set(found[1]) == {
        *admin_limitations_export.FAMILY_KEYS,
        "kind",
        "item_id",
        "version",
        "actions",
    }

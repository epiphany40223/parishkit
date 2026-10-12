"""Remembered reminder series: the campaign-series-v11 schema (#469).

A ``schedule_series`` record remembers the repeat rule some reminders came
from. These tests cover the schema's choice and validation, the users
request formats that build its candidates, and the upkeep an ordinary
schedule edit adds ("this reminder only").
"""

from copy import deepcopy
from uuid import uuid4

import pytest

from parishkit.config import ConfigError
from parishkit.stewardship.accounts import operator_recovery
from parishkit.stewardship.accounts import series_schema as series
from parishkit.stewardship.accounts import users_schema as users
from parishkit.stewardship.accounts.authority import parse_version
from parishkit.stewardship.accounts.configuration_schema import (
    schema_for,
    validate_sections,
    validator_for,
)
from parishkit.stewardship.accounts.request_patch import build_candidate
from parishkit.stewardship.campaigns.recurrence import expand

from .campaign_factory import campaign, schedule
from .configuration_factory import configuration_document
from .test_users_upgrade import user

# Every Monday of the fixture campaign after its initial invitation.
RULE = {
    "frequency": "weekly",
    "start": "2054-10-02",
    "end": "2054-10-31",
    "weekdays": [0],
}
MONDAYS = expand(RULE)["dates"]


def series_document(*, remembered=True):
    """A users configuration whose campaign has a reminder each Monday.

    With ``remembered``, one series names every reminder.
    """
    document = configuration_document()
    document["sections"]["login_rules"] = [user()]
    owner = campaign()
    reminders = [
        schedule(owner["id"], kind="reminder", date=day, subject="Reminder")
        for day in MONDAYS
    ]
    document["sections"]["campaigns"] = [owner]
    document["sections"]["schedules"] = [schedule(owner["id"]), *reminders]
    if remembered:
        document["sections"][series.SECTION] = [
            record(owner["id"], sorted(row["id"] for row in reminders))
        ]
    return document


def record(campaign_id, members, rule=RULE):
    """One remembered series."""
    return {
        "id": str(uuid4()),
        "values": {"campaign_id": campaign_id, "rule": rule, "schedules": members},
    }


def version(document):
    """Parse ``document`` under the schema it selects."""
    return parse_version(document, validate_sections=validate_sections)


def reminders(document):
    """The reminders' records, by date."""
    return {
        row["values"]["date"]: row
        for row in document["sections"]["schedules"]
        if row["values"]["kind"] == "reminder"
    }


def only_series(document):
    """The document's one series' values."""
    [row] = document["sections"][series.SECTION]
    return row["values"]


def test_the_section_selects_the_series_schema_and_stays():
    """A users document with the section is v11, even once it is empty."""
    document = series_document()
    assert schema_for(document) == users.SERIES_SCHEMA == series.SCHEMA
    version(document)
    assert schema_for(series_document(remembered=False)) == users.SCHEMA
    document["sections"][series.SECTION] = []
    assert schema_for(document) == series.SCHEMA
    version(document)
    # users-v10 keeps refusing a series, as every older schema does.
    with pytest.raises(ConfigError):
        parse_version(series_document(), validate_sections=validator_for(users.SCHEMA))


def test_sign_in_rules_never_hold_a_series():
    """The section exists only in the users form."""
    document = series_document()
    document["sections"]["login_rules"] = [
        {
            "id": str(uuid4()),
            "values": {
                "kind": "address",
                "email": "admin@example.org",
                "roles": ["administrator"],
                "creation_origin": "manual",
                "creation_operation": str(uuid4()),
                "grants": {"administrator": {"manual": str(uuid4())}},
            },
        }
    ]
    assert schema_for(document) != series.SCHEMA
    with pytest.raises(ConfigError):
        version(document)


def later_campaign():
    """A second campaign, in November, under its own name."""
    return campaign(
        name="November campaign", start_date="2054-11-01", end_date="2054-11-30"
    )


def _break(change):
    """A valid series document with ``change(document, values)`` applied."""

    def broken():
        document = series_document()
        change(document, only_series(document))
        return document

    return broken


def _second_series(document, values):
    """A second series claiming a reminder the first already holds."""
    document["sections"][series.SECTION].append(
        record(values["campaign_id"], values["schedules"][:1])
    )


def _initial_member(document, values):
    """The initial invitation named as a member of a rule giving its date."""
    initial = document["sections"]["schedules"][0]
    values["rule"] = {"frequency": "daily", "start": "2054-10-01", "end": "2054-10-31"}
    values["schedules"] = sorted([*values["schedules"], initial["id"]])


def _other_campaign(document, values):
    """A reminder of another campaign named as a member."""
    other = later_campaign()
    late = schedule(other["id"], kind="reminder", date="2054-11-02")
    document["sections"]["campaigns"].append(other)
    document["sections"]["schedules"] += [
        schedule(other["id"], date="2054-11-01"),
        late,
    ]
    values["schedules"] = sorted([*values["schedules"], late["id"]])


def _off_rule(document, values):
    """A member moved to a Tuesday the rule does not give."""
    member = reminders(document)[MONDAYS[1]]
    member["values"]["date"] = "2054-10-14"


@pytest.mark.parametrize(
    "change",
    [
        # Exactly the three values.
        lambda document, values: values.update(note="x"),
        lambda document, values: values.pop("rule"),
        # The campaign must exist.
        lambda document, values: values.update(campaign_id=str(uuid4())),
        # The rule is valid and canonical.
        lambda document, values: values.update(rule=None),
        lambda document, values: values.update(rule=RULE | {"weekdays": [0, 0]}),
        lambda document, values: values.update(rule=RULE | {"monthly": {"day": 1}}),
        lambda document, values: values.update(rule=RULE | {"end": "2054-09-30"}),
        # Members: a non-empty sorted list of distinct canonical IDs.
        lambda document, values: values.update(schedules=[]),
        lambda document, values: values.update(schedules=values["schedules"][::-1]),
        lambda document, values: values.update(schedules=values["schedules"][:1] * 2),
        lambda document, values: values.update(
            schedules=[values["schedules"][0].upper()]
        ),
        lambda document, values: values.update(schedules=values["schedules"][0]),
        # Each an existing reminder of the campaign on a date the rule gives.
        lambda document, values: values.update(schedules=[str(uuid4())]),
        _initial_member,
        _other_campaign,
        _off_rule,
        # No reminder belongs to two series.
        _second_series,
    ],
)
def test_validation_refuses_a_malformed_series(change):
    """Every rule of a series record is checked on the whole document."""
    document = _break(change)()
    # Only the series is wrong: the same document without it is valid.
    without = deepcopy(document)
    del without["sections"][series.SECTION]
    version(without)
    with pytest.raises(ConfigError):
        version(document)


def test_members_may_differ_in_time_and_email():
    """Each reminder keeps its own send time and email."""
    document = series_document()
    member = reminders(document)[MONDAYS[1]]["values"]
    member["time"] = "17:30:00"
    member["template_version"] = str(uuid4())
    version(document)


def edit(base, *operations, request_schema=None):
    """Build the candidate of these operations, or raise."""
    return build_candidate(
        base, list(operations), candidate_id=uuid4(), request_schema=request_schema
    )


def test_an_ordinary_edit_remembers_a_series():
    """The users format adds the section; its candidate is campaign-series-v11."""
    base = version(series_document(remembered=False))
    assert schema_for(base.document()) == users.SCHEMA
    document = base.document()
    owner = document["sections"]["campaigns"][0]["id"]
    members = sorted(row["id"] for row in reminders(document).values())
    row = record(owner, members)
    result = edit(base, {"operation": "add", "section": series.SECTION, **row})
    candidate = result.candidate.document()
    assert schema_for(candidate) == series.SCHEMA
    assert only_series(candidate) == row["values"]
    # Every schedule is exactly as it was: no revision is replaced.
    assert candidate["sections"]["schedules"] == document["sections"]["schedules"]
    # Removing the last series keeps the (empty) section and the schema.
    removed = edit(
        version(candidate),
        {"operation": "remove", "section": series.SECTION, "id": row["id"]},
    )
    assert removed.candidate.document()["sections"][series.SECTION] == []
    assert schema_for(removed.candidate.document()) == series.SCHEMA


def test_a_series_never_moves_to_another_campaign():
    """Its campaign is fixed, as a schedule's is; a new series may be added."""
    document = series_document()
    other = later_campaign()
    november = {**RULE, "start": "2054-11-02", "end": "2054-11-30"}
    late = schedule(other["id"], kind="reminder", date=expand(november)["dates"][0])
    document["sections"]["campaigns"].append(other)
    document["sections"]["schedules"] += [
        schedule(other["id"], date="2054-11-01"),
        late,
    ]
    base = version(document)
    moved = {"campaign_id": other["id"], "rule": november, "schedules": [late["id"]]}
    # The same values are a valid new series of the other campaign.
    edit(
        base,
        {
            "operation": "add",
            "section": series.SECTION,
            "id": str(uuid4()),
            "values": moved,
        },
    )
    row = document["sections"][series.SECTION][0]
    with pytest.raises(ConfigError):
        edit(
            base,
            {
                "operation": "update",
                "section": series.SECTION,
                "id": row["id"],
                "values": moved,
            },
        )


def test_credential_updates_and_recovery_keep_the_series():
    """The users credential and recovery formats work over campaign-series-v11."""
    base = version(series_document())
    integration = base.document()["sections"]["integrations"][0]["id"]
    credential = edit(
        base,
        {
            "operation": "update",
            "section": "integrations",
            "id": integration,
            "values": {"credential_fingerprint": "b" * 64},
        },
        request_schema=users.CREDENTIAL_SCHEMA,
    )
    assert schema_for(credential.candidate.document()) == series.SCHEMA
    assert operator_recovery.RECOVERY_SCHEMAS[series.SCHEMA] == users.RECOVERY_SCHEMA
    patch = operator_recovery._patch(base, "rescue@example.org", uuid4(), None)
    recovered = build_candidate(
        base, patch, candidate_id=uuid4(), request_schema=users.RECOVERY_SCHEMA
    ).candidate.document()
    assert (
        recovered["sections"][series.SECTION]
        == (base.document()["sections"][series.SECTION])
    )
    # Neither may edit a series itself.
    row = base.document()["sections"][series.SECTION][0]
    for schema in (users.CREDENTIAL_SCHEMA, users.RECOVERY_SCHEMA):
        with pytest.raises(ConfigError):
            edit(
                base,
                {"operation": "remove", "section": series.SECTION, "id": row["id"]},
                request_schema=schema,
            )


def schedule_update(row, **values):
    """A schedule update as the page's forms write it: every value."""
    return {
        "operation": "update",
        "section": "schedules",
        "id": row["id"],
        "values": row["values"] | values,
    }


def test_upkeep_follows_this_reminder_only_edits():
    """Removed or re-dated reminders leave; time and email changes stay."""
    document = series_document()
    dated = reminders(document)
    row = document["sections"][series.SECTION][0]
    removed, moved, retimed, kept = (dated[day] for day in MONDAYS[:4])
    patch = [
        {"operation": "remove", "section": "schedules", "id": removed["id"]},
        schedule_update(moved, date="2054-10-14"),
        schedule_update(retimed, time="17:30:00", template_version=str(uuid4())),
    ]
    operations = series.upkeep(document, patch)
    expected = sorted(
        member
        for member in row["values"]["schedules"]
        if member not in {removed["id"], moved["id"]}
    )
    assert operations == [
        {
            "operation": "update",
            "section": series.SECTION,
            "id": row["id"],
            "values": {"schedules": expected},
        }
    ]
    assert retimed["id"] in expected and kept["id"] in expected
    # The whole intent is a valid candidate.
    edit(version(document), *patch, *operations)


def test_upkeep_keeps_a_reminder_moved_to_another_rule_date():
    """Moving onto another date the rule gives keeps the membership."""
    document = series_document()
    dated = reminders(document)
    patch = [
        {"operation": "remove", "section": "schedules", "id": dated[MONDAYS[1]]["id"]},
        schedule_update(dated[MONDAYS[0]], date=MONDAYS[1]),
    ]
    [operation] = series.upkeep(document, patch)
    assert dated[MONDAYS[0]]["id"] in operation["values"]["schedules"]
    assert dated[MONDAYS[1]]["id"] not in operation["values"]["schedules"]


def test_upkeep_removes_an_emptied_series_and_leaves_the_rest_alone():
    """No reminder left removes the series; nothing changed means no operation."""
    document = series_document()
    row = document["sections"][series.SECTION][0]
    patch = [
        {"operation": "remove", "section": "schedules", "id": member}
        for member in row["values"]["schedules"]
    ]
    assert series.upkeep(document, patch) == [
        {"operation": "remove", "section": series.SECTION, "id": row["id"]}
    ]
    edit(version(document), *patch, *series.upkeep(document, patch))
    initial = document["sections"]["schedules"][0]
    unrelated = [schedule_update(initial, time="08:00:00")]
    assert series.upkeep(document, unrelated) == []
    assert series.upkeep(series_document(remembered=False), patch) == []
    # A series the patch already edits is the patch's own.
    named = [
        *patch,
        {"operation": "remove", "section": series.SECTION, "id": row["id"]},
    ]
    assert series.upkeep(document, named) == []


def test_upkeep_never_mutates_its_inputs():
    """The document and the patch are read, not changed."""
    document = series_document()
    patch = [
        {
            "operation": "remove",
            "section": "schedules",
            "id": only_series(document)["schedules"][0],
        }
    ]
    before = deepcopy((document, patch))
    series.upkeep(document, patch)
    assert (document, patch) == before

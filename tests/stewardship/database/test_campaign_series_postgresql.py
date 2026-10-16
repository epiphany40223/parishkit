"""Remembered reminder series on PostgreSQL (#469 slice 2b, migration 0075).

A ``schedule_series`` record remembers the rule some reminders came from.
These tests run it through the real request intake, configuration
installer and SQL projection guards: remembering a series keeps every
reminder's revision, an ordinary schedule change through the command line
(the page's own preview builder) keeps the series valid in the same signed
intent, and offline recovery keeps the series. The migration's closing
check is tested against the definitions it replaced.
"""

import re
from datetime import date
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from django.core import signing
from django.db import DatabaseError, IntegrityError, connection, transaction

from parishkit.config import ConfigError
from parishkit.stewardship import admin_changes
from parishkit.stewardship.accounts import integration_selection, request_patch
from parishkit.stewardship.accounts import users_upgrade as upgrade
from parishkit.stewardship.accounts.authority import parse_version
from parishkit.stewardship.accounts.configuration_installation import (
    coherent_configuration,
    install_request,
)
from parishkit.stewardship.accounts.configuration_requests import record_request
from parishkit.stewardship.accounts.configuration_schema import validator_for
from parishkit.stewardship.accounts.operator_recovery import recover_admin
from parishkit.stewardship.accounts.request_models import ConfigurationChangeRequest
from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.accounts.schedule_changes import (
    SERIES_UNMATCHED,
    preview_salt,
)
from parishkit.stewardship.accounts.series_schema import SCHEMA, SECTION
from parishkit.stewardship.accounts.users_schema import (
    CREDENTIAL_SCHEMA,
    REQUEST_SCHEMA,
)
from parishkit.stewardship.accounts.users_schema import SCHEMA as USERS_SCHEMA
from parishkit.stewardship.campaigns.models import ScheduleDefinition
from parishkit.stewardship.campaigns.recurrence import canonical_rule, expand

from ..campaign_factory import schedule
from ..content_factory import content
from .automation_builders import paired
from .campaign_builders import change, initialized
from .test_admin_schedule_cli_postgresql import (  # noqa: F401
    admin,
    confirm,
    one,
    preview,
)
from .test_recovery_postgresql import arguments as recovery_arguments
from .test_schedule_entry_postgresql import setup as with_saved_emails
from .test_users_upgrade_postgresql import arguments as upgrade_arguments
from .user_builders import convert

pytestmark = pytest.mark.django_db(transaction=True)

SCHEMA_FILES = Path(__file__).resolve().parents[3] / "src/parishkit/stewardship/schema"
MIGRATION = (SCHEMA_FILES / "migrations/0075_campaign_series.sql").read_text(
    encoding="utf-8"
)
# The fixture's reminder is on 2054-10-25; the rule repeats on its weekday.
LAST = "2054-10-25"
RULE = {
    "frequency": "weekly",
    "start": "2054-10-02",
    "end": "2054-10-31",
    "weekdays": [date.fromisoformat(LAST).weekday()],
}
FIRST = expand(RULE)["dates"][-2]


def active_schema():
    """The active configuration's validation schema."""
    return SystemConfiguration.objects.get().active_configuration.validation_schema


def reminder(store, day):
    """The id of the campaign's reminder on ``day``."""
    return next(
        row["id"]
        for row in store.active().document()["sections"]["schedules"]
        if row["values"]["kind"] == "reminder" and row["values"]["date"] == day
    )


def remembered(store):
    """The active configuration's series section."""
    return store.active().document()["sections"][SECTION]


def revisions():
    """Each reminder definition's current revision, by schedule id."""
    return {
        str(row.pk): row.current_revision_id
        for row in ScheduleDefinition.objects.filter(
            kind="reminder", current_revision__isnull=False
        )
    }


def series_deployment(store):
    """A users deployment whose campaign remembers two weekly reminders.

    Returns the campaign and the series record's id.
    """
    # Saved emails, so a series edit's new reminders can send the members'.
    campaign, emails = with_saved_emails(store)
    convert(store)
    assert active_schema() == "users-v10"
    added = schedule(
        str(campaign.pk),
        kind="reminder",
        date=FIRST,
        template_version=emails["reminder"]["id"],
        subject="reminder mail",
    )
    assert (
        change(
            store,
            store.active(),
            uuid4(),
            [{"operation": "add", "section": "schedules", **added}],
        ).state
        == "applied"
    )
    before = revisions()
    identifier = str(uuid4())
    result = change(
        store,
        store.active(),
        uuid4(),
        [
            {
                "operation": "add",
                "section": SECTION,
                "id": identifier,
                "values": {
                    "campaign_id": str(campaign.pk),
                    "rule": RULE,
                    "schedules": sorted([added["id"], reminder(store, LAST)]),
                },
            }
        ],
    )
    assert result.state == "applied"
    request = ConfigurationChangeRequest.objects.get(pk=result.request_id)
    assert request.request_schema == REQUEST_SCHEMA
    assert active_schema() == SCHEMA
    # Remembering a series replaces no reminder's revision or work.
    assert revisions() == before
    return campaign, identifier


def apply_preview(admin, secret, document):  # noqa: F811
    """Preview ``document``, confirm it and install the request.

    Returns the signed intent's patch.
    """
    store = admin.service.store
    campaign_id = store.active().document()["sections"]["campaigns"][0]["id"]
    code, reviewed = preview(admin, secret, document)
    assert code == 0, reviewed
    token = reviewed["result"]["preview"]["token"]
    patch = signing.loads(token, salt=preview_salt(campaign_id))["patch"]
    code, confirmed = confirm(admin, secret, token)
    assert code == 0, confirmed
    request = confirmed["result"]["request"]["request_id"]
    result = install_request(store, request_id=UUID(request), correlation_id=uuid4())
    assert result.state == "applied"
    return patch


def test_ordinary_schedule_changes_keep_the_series(admin, google):  # noqa: F811
    """Deleting, retiming and deleting again: the series follows its reminders."""
    store = admin.service.store
    _, identifier = series_deployment(store)
    _, secret, _ = paired(admin.service)
    first, last = reminder(store, FIRST), reminder(store, LAST)

    # Deleting one reminder removes it from its series in the same intent.
    patch = apply_preview(admin, secret, {"schedules": [{"id": first, "delete": True}]})
    assert {"operation": "remove", "section": "schedules", "id": first} in patch
    assert {
        "operation": "update",
        "section": SECTION,
        "id": identifier,
        "values": {"schedules": [last]},
    } in patch
    [row] = remembered(store)
    assert row["values"]["schedules"] == [last]

    # A new time keeps the reminder in its series; nothing else changes.
    patch = apply_preview(
        admin, secret, {"schedules": [{"id": last, "time": "10:30:00"}]}
    )
    assert [item["section"] for item in patch] == ["schedules"]
    assert remembered(store) == [row]

    # Deleting its last reminder removes the series; the schema stays.
    patch = apply_preview(admin, secret, {"schedules": [{"id": last, "delete": True}]})
    assert {"operation": "remove", "section": SECTION, "id": identifier} in patch
    assert remembered(store) == []
    assert active_schema() == SCHEMA


def remembering(store, **extra):
    """A repeat on the saved reminder's weekday, before it, with its email.

    The 2054-10-11 and 2054-10-18 reminders it adds fall after the
    fixture's initial invitation and clash with no saved schedule.
    """
    saved = next(
        row["values"]
        for row in store.active().document()["sections"]["schedules"]
        if row["id"] == reminder(store, LAST)
    )
    rule = RULE | {"start": "2054-10-10", "end": "2054-10-20"}
    entry = {
        "repeat": rule,
        "time": "10:00",
        "template_version": saved["template_version"],
    }
    return {"schedules": [entry | extra]}


def test_a_remembered_repeat_becomes_a_series(admin, google):  # noqa: F811
    """``"remember": true`` saves the rule with exactly the reminders it adds.

    The reminders are those the same repeat adds without it, the saved
    reminder keeps its revision, and ``schedule show`` lists the series.
    """
    store = admin.service.store
    # The repeat sends the saved reminder's email, as the page's forms need.
    campaign, _ = with_saved_emails(store)
    convert(store)
    _, secret, _ = paired(admin.service)
    # Without remember the review is the same, with no series.
    code, plain = preview(admin, secret, remembering(store))
    assert code == 0, plain
    assert plain["result"]["series"] == []
    before = revisions()

    code, reviewed = preview(admin, secret, remembering(store, remember=True))
    assert code == 0, reviewed
    result = reviewed["result"]
    added = sorted(change["id"] for change in result["changes"])
    assert [change["after"]["date"] for change in result["changes"]] == [
        "2054-10-11",
        "2054-10-18",
    ]
    strip = ("id", "after")
    assert [
        {key: value for key, value in change.items() if key not in strip}
        for change in result["changes"]
    ] == [
        {key: value for key, value in change.items() if key not in strip}
        for change in plain["result"]["changes"]
    ]
    [series] = result["series"]
    rule = canonical_rule(remembering(store)["schedules"][0]["repeat"])
    assert series["rule"] == rule and series["schedules"] == added
    code, confirmed = confirm(admin, secret, result["preview"]["token"])
    assert code == 0, confirmed
    request = confirmed["result"]["request"]["request_id"]
    installed = install_request(store, request_id=UUID(request), correlation_id=uuid4())
    assert installed.state == "applied"

    assert active_schema() == SCHEMA
    assert remembered(store) == [
        {
            "id": series["id"],
            "values": {
                "campaign_id": str(campaign.pk),
                "rule": rule,
                "schedules": added,
            },
        }
    ]
    after = revisions()
    assert {key: after[key] for key in before} == before
    assert set(after) - set(before) == set(added)
    code, shown = one(admin, "schedule", "show", secret=secret)
    assert code == 0, shown
    assert shown["result"]["series"] == [
        {"id": series["id"], "rule": rule, "schedules": added}
    ]


def test_a_series_that_cannot_be_matched_is_refused(
    admin,  # noqa: F811
    google,
    monkeypatch,
):
    """build_preview fails closed when an added form produced no add.

    parse_changes refuses an added entry with no values; this bypasses that
    check, as another caller might, with an empty entry before the repeat:
    the positions then no longer name the repeat's reminders.
    """
    store = admin.service.store
    with_saved_emails(store)
    convert(store)
    _, secret, _ = paired(admin.service)
    parse = admin_changes.parse_changes

    def shifted(text):
        """The real parse, with an empty added entry first."""
        parsed = parse(text)
        empty = {"comparison": False, admin_changes.LABEL: "new9"}
        return parsed | {
            "schedules": [empty, *parsed["schedules"]],
            "series": [
                item | {"added": [index + 1 for index in item["added"]]}
                for item in parsed["series"]
            ],
        }

    monkeypatch.setattr(admin_changes, "parse_changes", shifted)
    digest = store.active().digest
    code, document = preview(admin, secret, remembering(store, remember=True))
    assert code == 1 and document["error"]["code"] == "invalid", document
    [problem] = document["error"]["fields"]
    assert problem == {
        "field": "window",
        "code": "invalid",
        "message": SERIES_UNMATCHED,
    }
    assert (
        store.active().digest == digest
        and SECTION not in (store.active().document()["sections"])
    )


def test_other_changes_apply_on_a_series_configuration(admin, google):  # noqa: F811
    """Once remembered, the configuration stays campaign-series-v11, and a
    campaign setting and an added email still apply over it."""
    store = admin.service.store
    campaign, _ = with_saved_emails(store)
    convert(store)
    _, secret, _ = paired(admin.service)
    code, reviewed = preview(admin, secret, remembering(store, remember=True))
    assert code == 0, reviewed
    code, confirmed = confirm(admin, secret, reviewed["result"]["preview"]["token"])
    assert code == 0, confirmed
    request = UUID(confirmed["result"]["request"]["request_id"])
    assert install_request(store, request_id=request, correlation_id=uuid4()).state == (
        "applied"
    )
    assert active_schema() == SCHEMA
    before = remembered(store)
    email = content(str(campaign.pk), kind="email", slot="reminder", subject="later")
    result = change(
        store,
        store.active(),
        uuid4(),
        [
            {
                "operation": "update",
                "section": "campaigns",
                "id": str(campaign.pk),
                "values": {"name": "Renamed campaign"},
            },
            {"operation": "add", "section": "content", **email},
        ],
    )
    assert result.state == "applied"
    assert active_schema() == SCHEMA and remembered(store) == before
    sections = store.active().document()["sections"]
    assert sections["campaigns"][0]["values"]["name"] == "Renamed campaign"
    assert email["id"] in {row["id"] for row in sections["content"]}


def test_remembering_needs_the_users_form(admin, google):  # noqa: F811
    """Sign-in rules cannot hold a series: refused in plain words, not as a misfit."""
    store = admin.service.store
    with_saved_emails(store)
    _, secret, _ = paired(admin.service)
    digest = store.active().digest
    code, document = preview(admin, secret, remembering(store, remember=True))
    assert code == 1 and document["error"]["code"] == "invalid"
    [problem] = document["error"]["fields"]
    assert problem["field"] == "window"
    assert "run upgrade-users first" in problem["message"]
    assert store.active().digest == digest
    code, shown = one(admin, "schedule", "show", secret=secret)
    assert code == 0 and shown["result"]["series"] == []


def values_of(store, identifier):
    """The applied values of one schedule."""
    return next(
        row["values"]
        for row in store.active().document()["sections"]["schedules"]
        if row["id"] == identifier
    )


def test_this_and_following_cuts_the_series(admin, google):  # noqa: F811
    """A new time from the last reminder: the series keeps the earlier one.

    The earlier reminder keeps its revision; the later one is replaced by a
    reminder at the new time in a new series from its date, in one request.
    """
    store = admin.service.store
    _, identifier = series_deployment(store)
    _, secret, _ = paired(admin.service)
    first, last = reminder(store, FIRST), reminder(store, LAST)
    email = values_of(store, last)["template_version"]
    before = revisions()
    document = {
        "schedules": [
            {"series": identifier, "scope": "following", "from": last, "time": "10:30"}
        ]
    }
    code, reviewed = preview(admin, secret, document)
    assert code == 0, reviewed
    result = reviewed["result"]
    assert [(item["operation"], item["id"] == last) for item in result["changes"]] == [
        ("remove", True),
        ("add", False),
    ]
    added = result["changes"][1]["id"]
    cut = RULE | {"end": "2054-10-24"}
    assert result["series_changes"] == [
        {"id": identifier, "operation": "update", "rule": cut, "schedules": [first]}
    ]
    [created] = result["series"]
    assert created["rule"] == RULE | {"start": LAST}
    assert created["schedules"] == [added]

    # Confirming previews again, which mints the new ids afresh.
    patch = apply_preview(admin, secret, document)
    [added] = [
        item["id"]
        for item in patch
        if item["section"] == "schedules" and item["operation"] == "add"
    ]
    rows = {row["id"]: row["values"] for row in remembered(store)}
    kept = rows.pop(identifier)
    assert kept["rule"] == cut and kept["schedules"] == [first]
    [values] = rows.values()
    assert values["rule"] == RULE | {"start": LAST}
    assert values["schedules"] == [added]
    values = values_of(store, added)
    assert values["date"] == LAST and values["template_version"] == email
    assert values["time"].startswith("10:30")
    after = revisions()
    assert after[first] == before[first] and last not in after


def test_a_series_edit_beside_other_new_reminders(admin, google):  # noqa: F811
    """A series edit, a plain new reminder and a remembered repeat in one change.

    The edit's reminders come after the document's own, so each new series
    must hold exactly its own reminders (the shared builder's positions).
    """
    store = admin.service.store
    _, identifier = series_deployment(store)
    _, secret, _ = paired(admin.service)
    first, last = reminder(store, FIRST), reminder(store, LAST)
    email = values_of(store, last)["template_version"]
    alone = {"kind": "reminder", "date": "2054-10-14", "time": "10:00"}
    # A one-day repeat on the 11th: a one-reminder series of its own.
    remembered_repeat = remembering(store, remember=True)["schedules"][0]
    remembered_repeat["repeat"] = RULE | {"start": "2054-10-10", "end": "2054-10-12"}
    document = {
        "schedules": [
            alone | {"template_version": email},
            remembered_repeat,
            {"series": identifier, "scope": "following", "from": last, "time": "10:30"},
        ]
    }
    patch = apply_preview(admin, secret, document)
    adds = {
        item["id"]: item["values"]
        for item in patch
        if item["section"] == "schedules" and item["operation"] == "add"
    }
    assert sorted(values["date"] for values in adds.values()) == [
        "2054-10-11",
        "2054-10-14",
        LAST,
    ]
    by_date = {values["date"]: key for key, values in adds.items()}
    rows = {row["id"]: row["values"] for row in remembered(store)}
    kept = rows.pop(identifier)
    assert kept["schedules"] == [first]
    # Two new series: the remembered repeat's and the edit's, nothing else.
    assert sorted(values["schedules"] for values in rows.values()) == sorted(
        [[by_date["2054-10-11"]], [by_date[LAST]]]
    )
    assert by_date["2054-10-14"] not in {
        member for values in rows.values() for member in values["schedules"]
    }
    assert values_of(store, by_date[LAST])["time"].startswith("10:30")


def test_the_whole_series_keeps_unchanged_reminders(admin, google):  # noqa: F811
    """A rule that ends sooner removes only the dropped date's reminder.

    The series is replaced in place: same id, the new rule, and the kept
    reminder with its own revision.
    """
    store = admin.service.store
    _, identifier = series_deployment(store)
    _, secret, _ = paired(admin.service)
    first, last = reminder(store, FIRST), reminder(store, LAST)
    before = revisions()
    rule = RULE | {"start": FIRST, "end": "2054-10-20"}
    document = {
        "schedules": [{"series": identifier, "scope": "series", "repeat": rule}]
    }
    code, reviewed = preview(admin, secret, document)
    assert code == 0, reviewed
    result = reviewed["result"]
    assert [(item["operation"], item["id"]) for item in result["changes"]] == [
        ("remove", last)
    ]
    assert result["series"] == []
    assert result["series_changes"] == [
        {"id": identifier, "operation": "update", "rule": rule, "schedules": [first]}
    ]
    apply_preview(admin, secret, document)
    [row] = remembered(store)
    assert row["id"] == identifier and row["values"]["rule"] == rule
    assert revisions() == {first: before[first]}


def test_deleting_a_series_removes_its_unsent_reminders(admin, google):  # noqa: F811
    """Both reminders and the series go; the schema stays."""
    store = admin.service.store
    _, identifier = series_deployment(store)
    _, secret, _ = paired(admin.service)
    first, last = reminder(store, FIRST), reminder(store, LAST)
    document = {"schedules": [{"series": identifier, "delete": True}]}
    code, reviewed = preview(admin, secret, document)
    assert code == 0, reviewed
    assert sorted(item["id"] for item in reviewed["result"]["changes"]) == sorted(
        [first, last]
    )
    assert reviewed["result"]["series_changes"] == [
        {"id": identifier, "operation": "remove"}
    ]
    apply_preview(admin, secret, document)
    assert remembered(store) == [] and revisions() == {}
    assert active_schema() == SCHEMA


@pytest.mark.parametrize("problem", ["unknown", "named", "outside"])
def test_a_series_edit_that_cannot_be_made_is_refused(
    admin,  # noqa: F811
    google,
    problem,
):
    """An unknown series, a member also named alone, or a reminder outside it.

    Each is ``invalid`` on the entry, and nothing changes.
    """
    store = admin.service.store
    _, identifier = series_deployment(store)
    _, secret, _ = paired(admin.service)
    last = reminder(store, LAST)
    entry = {"series": identifier, "scope": "series", "time": "10:30"}
    rows = {
        "unknown": [entry | {"series": str(uuid4())}],
        "named": [{"id": last, "time": "11:00"}, entry],
        "outside": [
            {
                "series": identifier,
                "scope": "following",
                "from": str(uuid4()),
                "time": "10:30",
            }
        ],
    }[problem]
    digest = store.active().digest
    code, document = preview(admin, secret, {"schedules": rows})
    assert code == 1 and document["error"]["code"] == "invalid", document
    [field] = document["error"]["fields"]
    assert field["field"] == "schedules.new0" and field["code"] == "invalid"
    assert store.active().digest == digest


def test_recovery_keeps_the_series(admin, google):  # noqa: F811
    """Offline Admin-access recovery works over campaign-series-v11."""
    store = admin.service.store
    series_deployment(store)
    before = remembered(store)
    assert recover_admin(store, **recovery_arguments()).state == "applied"
    assert active_schema() == SCHEMA
    assert remembered(store) == before


def test_upgrade_users_is_a_no_op_over_series(tmp_path):
    """A configuration with series is already in the users form."""
    store, _, _ = initialized(tmp_path)
    series_deployment(store)
    digest = store.active().digest
    assert upgrade.upgrade_users(store, **upgrade_arguments()) is None
    assert store.active().digest == digest and active_schema() == SCHEMA
    # Only the conversion that made the users form was ever an upgrade.
    assert (
        ConfigurationChangeRequest.objects.filter(authority="operator_upgrade").count()
        == 1
    )


def test_a_credential_update_keeps_the_series_schema(tmp_path, monkeypatch):
    """The users credential format installs a campaign-series-v11 candidate.

    The new key's installation receipt is the key handoff's business
    (test_integration_selection_postgresql.py); here its check is stubbed so
    the request reaches activation, where SQL admits the v11 candidate.
    """
    monkeypatch.setattr(
        integration_selection, "validate_installation", lambda document: None
    )
    store, _, _ = initialized(tmp_path)
    series_deployment(store)
    before = remembered(store)
    integration = store.active().document()["sections"]["integrations"][0]["id"]
    receipt = record_request(
        base_digest=store.active().digest,
        patch=[
            {
                "operation": "update",
                "section": "integrations",
                "id": integration,
                "values": {"credential_fingerprint": "b" * 64},
            }
        ],
        actor_id=uuid4(),
        request_key=uuid4(),
        correlation_id=uuid4(),
        request_schema=CREDENTIAL_SCHEMA,
    )
    request = ConfigurationChangeRequest.objects.get(pk=receipt.request_id)
    assert request.request_schema == CREDENTIAL_SCHEMA
    result = install_request(store, request_id=request.pk, correlation_id=uuid4())
    assert result.state == "applied"
    assert active_schema() == SCHEMA and remembered(store) == before
    integrations = store.active().document()["sections"]["integrations"]
    assert integrations[0]["values"]["credential_fingerprint"] == "b" * 64


def history(store):
    """One configuration of each form, and an ordinary and an upgrade request.

    Returns ``(rules, users, series, ordinary, upgrade)``: the first
    sign-in-rules configuration, the first users-v10 one and the series one,
    then the request that remembered the series and the users upgrade.
    """
    series_deployment(store)
    chain = []
    version = SystemConfiguration.objects.get().active_configuration
    while version is not None:
        chain.append(version)
        version = version.predecessor
    first = {}
    for version in reversed(chain):
        first.setdefault(version.validation_schema, version.pk)
    rules = next(
        pk for schema, pk in first.items() if schema not in {USERS_SCHEMA, SCHEMA}
    )
    ordinary = ConfigurationChangeRequest.objects.get(
        candidate_version_id=first[SCHEMA]
    )
    upgraded = ConfigurationChangeRequest.objects.get(authority="operator_upgrade")
    return rules, first[USERS_SCHEMA], first[SCHEMA], ordinary.pk, upgraded.pk


def guard(cursor, predecessor, candidate, request):
    """Run the users-form guard on one activation of ``candidate``.

    A probe table with the activation's columns the guard reads carries the
    guard as its trigger, so each form pair is judged by the installed
    function itself, without the other activation checks.
    """
    cursor.execute(
        "INSERT INTO guard_probe VALUES (%s, %s, %s, %s)",
        [candidate, predecessor, request, uuid4()],
    )


@pytest.mark.parametrize(
    ("pair", "message"),
    [
        # A configuration with series never returns to sign-in rules,
        (("series", "rules", "ordinary"), "Users cannot return to sign-in rules"),
        # nor to users-v10: the schema sticks.
        (
            ("series", "users", "ordinary"),
            "Remembered reminder series keep their schema",
        ),
        # Only the offline upgrade leaves sign-in rules, never to series,
        (("rules", "series", "ordinary"), "Only the offline users upgrade converts"),
        # and that upgrade never starts from series.
        (
            ("series", "series", "upgrade"),
            "The users upgrade converts sign-in rules once",
        ),
    ],
)
def test_the_users_form_guard_refuses_leaving_or_entering_series(
    tmp_path, pair, message
):
    """SQL holds the forms as Python does, whatever a builder produced."""
    store, _, _ = initialized(tmp_path)
    rules, users, series, ordinary, upgraded = history(store)
    ids = {"rules": rules, "users": users, "series": series}
    requests = {"ordinary": ordinary, "upgrade": upgraded}
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(
            "CREATE TEMP TABLE guard_probe (configuration_id uuid, "
            "predecessor_id uuid, request_id uuid, correlation_id uuid)"
        )
        cursor.execute(
            "CREATE TRIGGER guard_probe BEFORE INSERT ON guard_probe FOR EACH ROW "
            "EXECUTE FUNCTION public.stewardship_users_form_v1()"
        )
        # Entering series from users-v10, and staying, are ordinary edits.
        guard(cursor, users, series, ordinary)
        guard(cursor, series, series, ordinary)
        predecessor, candidate, request = pair
        with pytest.raises(DatabaseError, match=message), transaction.atomic():
            guard(cursor, ids[predecessor], ids[candidate], requests[request])
        transaction.set_rollback(True)


def test_an_activation_dropping_the_series_section_is_refused(tmp_path, monkeypatch):
    """A builder that drops the emptied section still cannot leave v11.

    The series are removed first (the section stays, empty); then an
    ordinary edit whose builder is replaced by one dropping the section, so
    Python would let a users-v10 candidate through, is refused by SQL.
    """
    store, _, _ = initialized(tmp_path)
    _, identifier = series_deployment(store)
    removed = change(
        store,
        store.active(),
        uuid4(),
        [{"operation": "remove", "section": SECTION, "id": identifier}],
    )
    assert removed.state == "applied" and remembered(store) == []
    assert active_schema() == SCHEMA
    original = request_patch.BUILDERS[REQUEST_SCHEMA]

    def dropping(base, patch, *, candidate_id):
        """The ordinary edit, with the empty series section dropped."""
        result = original(base, patch, candidate_id=candidate_id)
        document = result.candidate.document()
        del document["sections"][SECTION]
        return request_patch.PatchedConfiguration(
            parse_version(document, validate_sections=validator_for(USERS_SCHEMA)),
            result.canonical_patch,
            result.payload_fingerprint,
        )

    monkeypatch.setattr(
        request_patch,
        "BUILDERS",
        dict(request_patch.BUILDERS) | {REQUEST_SCHEMA: dropping},
    )
    parish = store.active().document()["sections"]["parish"][0]["id"]
    version = store.active()
    with pytest.raises(IntegrityError, match="keep their schema"):
        change(
            store,
            version,
            uuid4(),
            [
                {
                    "operation": "update",
                    "section": "parish",
                    "id": parish,
                    "values": {"name": "Renamed Parish"},
                }
            ],
        )
    # As for a refused conversion, the deployment fails closed on v11.
    with pytest.raises(ConfigError):
        coherent_configuration(store)
    assert active_schema() == SCHEMA


# Each function 0075 replaces, with the frozen file holding its body before.
EARLIER = {
    "stewardship_campaign_complete_v1": "0047_portal_users.sql",
    "stewardship_campaign_projection_v1": "0047_portal_users.sql",
    "stewardship_content_complete_v1": "0047_portal_users.sql",
    "stewardship_content_projection_v1": "0047_portal_users.sql",
    "stewardship_ministry_complete_v1": "0047_portal_users.sql",
    "stewardship_ministry_projection_v1": "0047_portal_users.sql",
    "stewardship_policy_complete_v1": "0055_setup_users.sql",
    "stewardship_policy_projection_v1": "0055_setup_users.sql",
    "stewardship_users_form_v1": "0055_setup_users.sql",
}
CHECK = MIGRATION[MIGRATION.index("DO $check$") :]
# The Ministry activity functions as 0075 left them: #1267's 0091 replaced
# the first and dropped the second, so each check below first puts them back
# (rolled back with it) to see what 0075 installed.
AS_0075_LEFT_THEM = (
    "stewardship_ministry_complete_v1",
    "stewardship_ministry_projection_v1",
)


def definition(source, name):
    """One function's definition in ``source``, as CREATE OR REPLACE."""
    start = re.search(
        rf"^CREATE (?:OR REPLACE )?FUNCTION public\.{name}\(", source, re.M
    ).start()
    quote = re.compile(r"\bAS\s+(\$\w*\$)").search(source, start)
    end = source.index(quote[1] + ";", quote.end()) + len(quote[1]) + 1
    return re.sub(
        r"^CREATE (?:OR REPLACE )?FUNCTION",
        "CREATE OR REPLACE FUNCTION",
        source[start:end],
    )


def earlier_definition(name):
    """The function's body before 0075, as CREATE OR REPLACE."""
    source = (SCHEMA_FILES / "migrations" / EARLIER[name]).read_text(encoding="utf-8")
    text = definition(source, name)
    assert "campaign-series-v11" not in text
    return text


def as_0075_left_them(cursor):
    """Put back 0075's own bodies of ``AS_0075_LEFT_THEM``."""
    for name in AS_0075_LEFT_THEM:
        cursor.execute(definition(MIGRATION, name))


def check_refuses(statement, message):
    """Run ``statement`` and then 0075's closing check, which must refuse it."""
    with (
        pytest.raises(DatabaseError, match=f"Migration 0075: {message}"),
        transaction.atomic(),
        connection.cursor() as cursor,
    ):
        cursor.execute("SET LOCAL check_function_bodies = false")
        as_0075_left_them(cursor)
        cursor.execute(statement)
        cursor.execute(CHECK)


def test_the_closing_check_passes_on_the_installed_schema():
    """The check is the one the migration ran; it still holds afterwards."""
    assert sorted(EARLIER) == sorted(
        re.findall(r"^CREATE OR REPLACE FUNCTION public\.(\w+)\(", MIGRATION, re.M)
    )
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute("SET LOCAL check_function_bodies = false")
        as_0075_left_them(cursor)
        cursor.execute(CHECK)
        transaction.set_rollback(True)


@pytest.mark.parametrize("name", sorted(EARLIER))
def test_the_closing_check_refuses_each_earlier_definition(name):
    """With any one function still on its pre-0075 body, the migration refuses."""
    check_refuses(earlier_definition(name), "a replaced function is not installed")


def test_the_closing_check_refuses_a_guard_without_the_sticky_schema():
    """0075's own guard less its sticky-schema branch is not installed as declared."""
    own = definition(MIGRATION, "stewardship_users_form_v1")
    branch = own[
        own.index("    -- Remembered reminder series keep") : own.index(
            "    IF upgrade AND (candidate_schema"
        )
    ]
    assert "keep their schema" in branch
    check_refuses(own.replace(branch, ""), "a replaced function is not installed")


def test_the_closing_check_refuses_a_definer_or_the_earlier_constraint():
    """No replaced function runs as its owner, and the CHECK admits the name."""
    check_refuses(
        "ALTER FUNCTION public.stewardship_users_form_v1() SECURITY DEFINER",
        "a replaced function gained SECURITY DEFINER",
    )
    earlier = (SCHEMA_FILES / "migrations/0055_setup_users.sql").read_text(
        encoding="utf-8"
    )
    [constraint] = [
        line
        for line in earlier.splitlines()
        if 'ADD CONSTRAINT "configuration_validation_schema"' in line
    ]
    check_refuses(
        'ALTER TABLE "stewardship_configuration_version" '
        'DROP CONSTRAINT "configuration_validation_schema";' + constraint,
        "the configuration CHECK is not installed as declared",
    )


def test_the_migration_installs_over_the_earlier_definitions():
    """Run whole over the pre-0075 state, the frozen file installs and checks.

    Rolled back afterwards, so later tests see the installed schema.
    """
    statements = [earlier_definition(name) for name in sorted(EARLIER)]
    earlier = (SCHEMA_FILES / "migrations/0055_setup_users.sql").read_text(
        encoding="utf-8"
    )
    [constraint] = [
        line
        for line in earlier.splitlines()
        if 'ADD CONSTRAINT "configuration_validation_schema"' in line
    ]
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute("SET LOCAL check_function_bodies = false")
        cursor.execute(
            'ALTER TABLE "stewardship_configuration_version" '
            'DROP CONSTRAINT "configuration_validation_schema";' + constraint
        )
        for statement in statements:
            cursor.execute(statement)
        with pytest.raises(DatabaseError, match="Migration 0075"), transaction.atomic():
            cursor.execute(CHECK)
        cursor.execute(MIGRATION)
        cursor.execute(CHECK)
        transaction.set_rollback(True)

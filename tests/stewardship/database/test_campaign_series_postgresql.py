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
from parishkit.stewardship.accounts.schedule_changes import preview_salt
from parishkit.stewardship.accounts.series_schema import SCHEMA, SECTION
from parishkit.stewardship.accounts.users_schema import (
    CREDENTIAL_SCHEMA,
    REQUEST_SCHEMA,
)
from parishkit.stewardship.accounts.users_schema import SCHEMA as USERS_SCHEMA
from parishkit.stewardship.campaigns.models import ScheduleDefinition
from parishkit.stewardship.campaigns.recurrence import expand

from ..campaign_factory import schedule
from .automation_builders import paired
from .campaign_builders import change, initialized
from .test_admin_schedule_cli_postgresql import admin, confirm, preview  # noqa: F401
from .test_recovery_postgresql import arguments as recovery_arguments
from .test_schedule_views_postgresql import setup as campaign_with_reminder
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
        for row in ScheduleDefinition.objects.filter(kind="reminder")
    }


def series_deployment(store):
    """A users deployment whose campaign remembers two weekly reminders.

    Returns the campaign and the series record's id.
    """
    campaign, _ = campaign_with_reminder(store)
    convert(store)
    assert active_schema() == "users-v10"
    added = schedule(str(campaign.pk), kind="reminder", date=FIRST)
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


def check_refuses(statement, message):
    """Run ``statement`` and then 0075's closing check, which must refuse it."""
    with (
        pytest.raises(DatabaseError, match=f"Migration 0075: {message}"),
        transaction.atomic(),
        connection.cursor() as cursor,
    ):
        cursor.execute("SET LOCAL check_function_bodies = false")
        cursor.execute(statement)
        cursor.execute(CHECK)


def test_the_closing_check_passes_on_the_installed_schema():
    """The check is the one the migration ran; it still holds afterwards."""
    assert sorted(EARLIER) == sorted(
        re.findall(r"^CREATE OR REPLACE FUNCTION public\.(\w+)\(", MIGRATION, re.M)
    )
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(CHECK)


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

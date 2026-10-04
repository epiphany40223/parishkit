"""Durable Family engagement (#477): pure helpers, SQL/Python parity, grants."""

import re
from datetime import UTC, datetime, timedelta
from importlib import import_module
from pathlib import Path
from uuid import uuid4

import pytest

from parishkit.stewardship.campaigns.cleanup_types import CleanupCategory
from parishkit.stewardship.campaigns.credential_models import PRESENCE_SECTIONS
from parishkit.stewardship.campaigns.engagement import (
    FAMILY_CONSTRAINT,
    FIRST_SECTION,
    SECTION_RANK,
    engagement_mode,
    engagement_values,
)
from parishkit.stewardship.campaigns.engagement_grants import (
    ENGAGEMENT_UPDATE_COLUMNS,
)
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.runtime_grants import runtime_grants

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = ROOT / "src/parishkit/stewardship/schema"
MIGRATION = import_module(
    "parishkit.stewardship.campaigns.migrations.0002_family_engagement"
)
FROZEN = MIGRATION.FROZEN_SQL.read_text()
NOW = datetime(2026, 10, 3, 15, 0, tzinfo=UTC)


def test_sql_rank_lists_the_presence_sections_in_order():
    """The SQL rank function and the Python constant come from one step order."""
    match = re.search(r"array_position\(ARRAY\[([^\]]+)\], section\)", FROZEN)
    assert match is not None
    assert tuple(name.strip("'") for name in match[1].split(",")) == PRESENCE_SECTIONS
    assert {
        name: index + 1 for index, name in enumerate(PRESENCE_SECTIONS)
    } == SECTION_RANK
    assert FIRST_SECTION == "welcome"
    # The shape constraints name the same sections and the same first step.
    assert all(f"'{name}'::character varying" in FROZEN for name in PRESENCE_SECTIONS)
    assert "ARRAY[''::character varying, 'welcome'::character varying]" in FROZEN
    assert f'ADD CONSTRAINT "{FAMILY_CONSTRAINT}" FOREIGN KEY' in FROZEN


def test_engagement_mode_spells_the_response_vocabulary():
    assert engagement_mode("testing") == "test"
    assert engagement_mode("production") == "live"


def test_observation_values_derive_progress_from_a_later_step():
    family = uuid4()
    welcome = engagement_values(
        family_id=family,
        mode="live",
        rehearsal_epoch_id=None,
        seen_at=NOW,
        section="welcome",
        section_at=NOW,
    )
    assert welcome["progress_at"] is None and welcome["section"] == "welcome"
    census = engagement_values(
        family_id=family,
        mode="live",
        rehearsal_epoch_id=None,
        seen_at=NOW,
        section="census",
        section_at=NOW - timedelta(seconds=1),
    )
    assert census["progress_at"] == NOW - timedelta(seconds=1)
    link = engagement_values(
        family_id=family,
        mode="test",
        rehearsal_epoch_id=uuid4(),
        actor_id=family,
        seen_at=NOW,
        link_at=NOW,
    )
    assert link["section"] == "" and link["section_at"] is None
    assert link["link_at"] == NOW and link["form_at"] is None
    assert link["actor_id"] == family and link["mode"] == "test"


@pytest.mark.parametrize(
    "observation,message",
    [
        ({"mode": "testing", "link_at": NOW}, "live or test"),
        ({"section": "census"}, "come together"),
        ({"section": "unknown", "section_at": NOW}, "Unknown form section"),
        ({}, "some evidence"),
        ({"link_at": NOW + timedelta(seconds=1)}, "cannot follow"),
    ],
)
def test_observation_values_refuse_malformed_evidence(observation, message):
    values = {"family_id": uuid4(), "mode": "live", "rehearsal_epoch_id": None}
    values |= {"seen_at": NOW} | observation
    with pytest.raises(ValueError, match=message):
        engagement_values(**values)


def test_forward_migration_is_the_frozen_file_and_names_the_final_vocabulary():
    """The migration reads one frozen file whole; the baseline files agree."""
    assert MIGRATION.FROZEN_SQL == SCHEMA / "migrations" / "0002_family_engagement.sql"
    assert not hasattr(MIGRATION, "schema_sql")
    assert not hasattr(MIGRATION, "replaced_function")
    table = FROZEN.index('CREATE TABLE "stewardship_family_engagement"')
    category = FROZEN.index("ADD CONSTRAINT production_target_category")
    event = FROZEN.index("ADD CONSTRAINT operational_event_safe")
    relation = FROZEN.index(
        "CREATE OR REPLACE FUNCTION public.stewardship_cleanup_relation_v1("
    )
    inventory = FROZEN.index(
        "CREATE OR REPLACE FUNCTION public.stewardship_cleanup_inventory_v1("
    )
    assert table < category < event < relation < inventory < FROZEN.index("DO $$")
    assert FROZEN.count("CREATE OR REPLACE") == 2
    assert "('engagement'::varchar)::text" in FROZEN
    assert "'family_engagement_failed'" in FROZEN
    # The applied migration's list is frozen history: the 24 categories that
    # existed before it, then "engagement". A future category gets its own
    # migration; model-versus-state drift is makemigrations' and the baseline
    # fingerprint's job, not this test's.
    assert MIGRATION.CLEANUP_CATEGORIES == (
        "baselines",
        "session_data",
        "family_sessions",
        "ministry_requests",
        "occurrences",
        "occurrence_events",
        "outbox_events",
        "outbox_messages",
        "outbox_renders",
        "proposals",
        "rehearsal_credentials",
        "rehearsal_macs",
        "schedule_fulfillments",
        "source_pins",
        "submission_receipts",
        "submissions",
        "prior_inventory_targets",
        "daily_digest_recipients",
        "daily_digest_ready",
        "daily_digest_snapshots",
        "daily_digest_fact_pins",
        "recovery_replacements",
        "weekly_digest_recipients",
        "weekly_digest_snapshots",
        "engagement",
    )
    assert CleanupCategory.ENGAGEMENT.value == "engagement"
    # The fresh-install files already carry the final vocabulary.
    tables = (SCHEMA / "tables.sql").read_text()
    assert "('engagement'::varchar)::text" in tables
    assert "'family_engagement_failed'" in tables
    assert (
        "WHEN 'engagement' THEN 'stewardship_family_engagement'"
        in (SCHEMA / "cleanup.sql").read_text()
    )
    assert "SELECT 'engagement',e.id" in (SCHEMA / "production.sql").read_text()


def test_only_web_writes_engagement_and_never_deletes_it():
    table = "stewardship_family_engagement"
    tables, columns = runtime_grants(ServiceRole.WEB)
    assert tables[table] == {"SELECT", "INSERT"}
    assert columns[table] == {"UPDATE": set(ENGAGEMENT_UPDATE_COLUMNS)}
    assert {"family_id", "mode", "rehearsal_epoch_id", "created_at", "id"}.isdisjoint(
        ENGAGEMENT_UPDATE_COLUMNS
    )
    for role in (
        ServiceRole.WORKER,
        ServiceRole.SCHEDULER,
        ServiceRole.MAIL_DISPATCH,
        "download",
    ):
        tables, columns = runtime_grants(role)
        assert table not in tables and table not in columns, role

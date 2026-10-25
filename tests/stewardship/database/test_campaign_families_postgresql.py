"""Frozen migration 0175 and promotion: a campaign's Families rule (#1393).

0175 replaces ``stewardship_campaign_pointer_v1`` with 0166's body plus two
checks: an existing campaign's ``campaign_families`` value never changes
(absent, the legacy rule, staying absent) and a creating configuration may
carry one only in a canonical shape. It also replaces the source window
function (the rule joins a campaign's exact scope) and the Financial and
Additional information selections (a rule-loaded Family's "active" is its
``portal_eligible``). This tests the file against the OLD definitions, that
its DO block refuses each regression, that Python and SQL each refuse a
change, that promotion builds a rule campaign's Families from the rule while
a campaign without one keeps today's, and that a real worker refresh runs
for a campaign with or without a rule.
"""

from dataclasses import replace
from difflib import ndiff
from pathlib import Path
from uuid import uuid4

import pytest
from django.db import DatabaseError, connection, transaction

from parishkit.config import ConfigError
from parishkit.stewardship.campaigns import admission, campaign_families
from parishkit.stewardship.campaigns.credential_models import FamilyCampaign
from parishkit.stewardship.campaigns.lifecycle import Action
from parishkit.stewardship.campaigns.models import Campaign
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.source.corpus import normalize_core
from parishkit.stewardship.source.leases import acquire_source, release_source
from parishkit.stewardship.source.models import SourceCurrent, SourceMutationLease
from parishkit.stewardship.source.snapshot_models import SourceSnapshot
from parishkit.stewardship.source.snapshots import (
    begin_snapshot,
    finish_snapshot,
    promote_snapshot,
    stage_entities,
)

from ..campaign_factory import campaign as campaign_record
from ..campaign_families_fixture import parish
from ..test_campaign_families import ACTIVE, TODAY, advanced, selected
from .campaign_builders import (
    add_draft,
    change,
    command,
    draft_campaign,
    initialized,
)
from .credential_builders import keys
from .refresh_harness import Refreshes
from .source_builders import running_source_task
from .test_source_families_postgresql import reconcile
from .test_source_snapshots_postgresql import permit

pytestmark = pytest.mark.django_db(transaction=True)

MIGRATIONS = Path(__file__).resolve().parents[3] / (
    "src/parishkit/stewardship/schema/migrations"
)
FROZEN = (MIGRATIONS / "0175_campaign_families.sql").read_text(encoding="utf-8")
OLD = (MIGRATIONS / "0166_share_short_labels.sql").read_text(encoding="utf-8")
POINTER = "public.stewardship_campaign_pointer_v1()"
WINDOW = "public.stewardship_source_current_window_v1"
FINANCIAL = "public.stewardship_financial_report_v1"
INFORMATION = "public.stewardship_information_report_v1"
INFORMATION_V2 = "public.stewardship_information_report_v2"
# The newest definitions 0175 replaces in place: the reports' from their last
# frozen files; the window function had no frozen copy before 0175, so its old
# body is 0175's copy without the rule's lines (the comment and the IF).
OLD_FINANCIAL = (MIGRATIONS / "0110_financial_local_days.sql").read_text(
    encoding="utf-8"
)
OLD_INFORMATION = (MIGRATIONS / "0094_source_status_names.sql").read_text(
    encoding="utf-8"
)
OLD_INFORMATION_V2 = (MIGRATIONS / "0100_information_status_counts.sql").read_text(
    encoding="utf-8"
)
MARK = "The campaign''s Families cannot be changed"
ORGANIZATION = 12345


@pytest.fixture(autouse=True)
def source_singletons():
    """Restore only idle singleton seeds after the disposable database flush."""
    SourceMutationLease.objects.get_or_create(singleton=True)
    SourceCurrent.objects.get_or_create(singleton=True)


def statement(text, name=POINTER):
    """A function's last ``CREATE OR REPLACE`` statement in ``text``."""
    start = text.rindex(f"CREATE OR REPLACE FUNCTION {name}")
    return text[start : text.index("$$;", start) + len("$$;")]


def old_window():
    """The window function as it was before 0175: no rule in the scope."""
    new = statement(FROZEN, WINDOW)
    start = new.index("    -- The campaign's Families rule (#1393)")
    end = new.index("    END IF;\n", start) + len("    END IF;\n")
    return new[:start] + new[end:]


def installed(cursor):
    """The installed pointer function's body."""
    cursor.execute("SELECT prosrc FROM pg_proc WHERE oid=%s::regprocedure", [POINTER])
    return cursor.fetchone()[0]


def test_the_body_is_0166s_with_only_the_families_checks_added():
    """Nothing of 0166's body changes; only lines are added."""
    diff = list(ndiff(statement(OLD).splitlines(), statement(FROZEN).splitlines()))
    assert not [line for line in diff if line.startswith("- ")]
    added = "\n".join(line[2:] for line in diff if line.startswith("+ "))
    assert MARK in added and "Invalid campaign Families rule" in added


def test_the_file_replaces_the_old_definition():
    """Over 0166's body the file installs the new one and its check passes."""
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(statement(OLD))
        assert MARK not in installed(cursor)
        cursor.execute(FROZEN)
        assert MARK in installed(cursor)
        transaction.set_rollback(True)


def test_the_report_copies_change_only_the_active_expression():
    """Each report copy keeps every line of its newest definition but its
    ``active`` expression and comment, which now reads the rule."""
    for old, name in (
        (statement(OLD_FINANCIAL, FINANCIAL), FINANCIAL),
        (statement(OLD_INFORMATION, INFORMATION), INFORMATION),
        (statement(OLD_INFORMATION_V2, INFORMATION_V2), INFORMATION_V2),
    ):
        diff = list(ndiff(old.splitlines(), statement(FROZEN, name).splitlines()))
        removed = [line[2:].strip() for line in diff if line.startswith("- ")]
        only_active = all("active" in line or line.startswith("--") for line in removed)
        assert only_active, (name, removed)
        added = "\n".join(line[2:] for line in diff if line.startswith("+ "))
        assert "? 'campaign_families'" in added and "portal_eligible" in added


def test_migration_check_refuses_the_old_body_or_a_changed_function():
    """0175's DO block passes as installed and refuses each regression."""
    capture = FROZEN[FROZEN.index("SELECT set_config(") :]
    capture = capture[: capture.index("CREATE OR REPLACE FUNCTION")]
    check = FROZEN[FROZEN.index("DO $check$") :]
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(capture)
        cursor.execute(check)
        for change_sql, message in (
            (statement(OLD), "not installed as declared"),
            (f"ALTER FUNCTION {POINTER} SECURITY DEFINER", "not installed"),
            (f"ALTER FUNCTION {POINTER} RESET search_path", "not installed"),
            (
                "CREATE ROLE pk_i1393_probe NOLOGIN; "
                f"GRANT EXECUTE ON FUNCTION {POINTER} TO pk_i1393_probe",
                "grants changed",
            ),
            (old_window(), "source window function is not installed"),
            (f"ALTER FUNCTION {WINDOW}(uuid) SECURITY DEFINER", "source window"),
            (statement(OLD_FINANCIAL, FINANCIAL), "report selections"),
            (statement(OLD_INFORMATION, INFORMATION), "report selections"),
            (statement(OLD_INFORMATION_V2, INFORMATION_V2), "report selections"),
            (
                "CREATE ROLE pk_i1393_reads NOLOGIN; "
                f"GRANT EXECUTE ON FUNCTION {WINDOW}(uuid) TO pk_i1393_reads",
                "grants, owner or settings changed",
            ),
        ):
            with (
                pytest.raises(DatabaseError, match=f"Migration 0175.*{message}"),
                transaction.atomic(),
            ):
                cursor.execute(change_sql)
                cursor.execute(check)
        transaction.set_rollback(True)


def update(store, row, values):
    """Install one change of the campaign's values; the receipt or the error."""
    try:
        return change(
            store,
            store.active(),
            uuid4(),
            [
                {
                    "operation": "update",
                    "section": "campaigns",
                    "id": str(row.pk),
                    "values": values,
                }
            ],
        )
    except (ConfigError, DatabaseError) as error:
        return error


def applied(result):
    """Whether ``update`` installed its change."""
    return getattr(result, "state", None) == "applied"


def bypass_python(monkeypatch):
    """Let only SQL decide: skip the installer preflight's checks."""
    monkeypatch.setattr(admission, "validate_installation", lambda *a, **k: None)


@pytest.mark.parametrize("bypass", [False, True])
@pytest.mark.parametrize("live", [False, True])
def test_a_campaign_without_a_rule_can_never_gain_one(
    tmp_path, monkeypatch, bypass, live
):
    """The existing campaign keeps the legacy rule in every state (#1393's
    hard rule): Python refuses to add a rule, and so does SQL alone."""
    store, row, actor = draft_campaign(tmp_path)
    assert campaign_families.KEY not in row.active_configuration.values
    if live:
        command(row, actor, Action.ACTIVATE)
    if bypass:
        bypass_python(monkeypatch)
    result = update(store, row, {campaign_families.KEY: ACTIVE})
    assert not applied(result)
    if bypass:
        assert "The campaign's Families cannot be changed" in str(result)
    row.refresh_from_db()
    assert campaign_families.KEY not in row.active_configuration.values


@pytest.mark.parametrize("bypass", [False, True])
@pytest.mark.parametrize(
    "replacement", [{campaign_families.KEY: advanced([7])}, {"name": "Renamed"}]
)
def test_a_rule_is_fixed_once_the_campaign_exists(
    tmp_path, monkeypatch, bypass, replacement
):
    """A draft created with a rule keeps it; other settings still change."""
    record = campaign_record(campaign_families=ACTIVE)
    store, row, _ = draft_campaign(tmp_path, record)
    assert row.active_configuration.values[campaign_families.KEY] == ACTIVE
    if bypass:
        bypass_python(monkeypatch)
    result = update(store, row, replacement)
    assert applied(result) is ("name" in replacement)
    row.refresh_from_db()
    assert row.active_configuration.values[campaign_families.KEY] == ACTIVE


@pytest.mark.parametrize("malformed", [False, True])
def test_sql_refuses_a_malformed_rule_at_creation(tmp_path, monkeypatch, malformed):
    """Past Python's validation, the guard still checks the shape: the same
    creation installs with a canonical rule and is refused with a malformed
    one."""
    monkeypatch.setattr(campaign_families, "validate", lambda value: None)
    rule = advanced([7], ("Active", "Unknown"))
    if malformed:
        rule["member_statuses"].reverse()
    store, version, actor = initialized(tmp_path)
    try:
        result = add_draft(
            store, version, actor, campaign_record(campaign_families=rule)
        )[0]
    except DatabaseError as error:
        assert "Invalid campaign Families rule" in str(error)
        result = None
    assert applied(result) is not malformed
    assert Campaign.objects.exists() is not malformed


def stage(data, rule):
    """Stage ``data`` normalized under ``rule``, its load recording the rule."""
    data = replace(data, organization_id=ORGANIZATION)
    for family in data.families.values():
        if family.get("registeredOrganizationID") == 5:
            family["registeredOrganizationID"] = ORGANIZATION
    corpus = normalize_core(data, as_of=TODAY, families=rule)
    claim = acquire_source(**running_source_task(), phase="full")
    snapshot = begin_snapshot(claim, organization_id=ORGANIZATION, admit=permit)
    for kind, values in corpus.items():
        if values:
            stage_entities(snapshot.pk, claim, kind=kind, entities=values, admit=permit)
    applied_rule = campaign_families.evidence(rule, data.family_groups)
    finish_snapshot(
        snapshot.pk,
        claim,
        expected_counts={kind: len(rows) for kind, rows in corpus.items()},
        cursor={"load": {campaign_families.KEY: applied_rule}} if rule else {},
        admit=permit,
    )
    return snapshot, claim


@pytest.mark.parametrize("rule", [None, ACTIVE, advanced([9])])
def test_promotion_builds_the_rules_families(tmp_path, rule):
    """The campaign's Families are the rule's; a campaign without one gets
    exactly today's (legacy) Families with ParishSoft's active flag."""
    record = (
        campaign_record() if rule is None else campaign_record(campaign_families=rule)
    )
    _, campaign, _ = draft_campaign(tmp_path, record)
    ring = keys()
    snapshot, claim = stage(parish(), rule)

    def effects(value):
        """Reconcile the campaign's Families inside the promotion."""
        reconcile(value, claim, campaign, ring)
        return True

    with work_transaction():
        promote_snapshot(snapshot.pk, claim, admit=permit, reconcile=effects)
    release_source(claim)
    rows = {
        str(row.family_duid): row
        for row in FamilyCampaign.objects.filter(campaign=campaign)
    }
    portal, email = selected(rule)
    assert {key for key, row in rows.items() if row.portal_eligible} == portal
    assert {key for key, row in rows.items() if row.email_eligible} == email
    assert all(rows[key].code_ciphertext for key in portal)
    corpus = normalize_core(parish(), as_of=TODAY, families=rule)["family"]
    for key, row in rows.items():
        expected = corpus[key]["active"] or (rule is not None and key in portal)
        assert row.active is expected, key


@pytest.mark.parametrize("rule", [None, ACTIVE])
def test_a_real_refresh_runs_for_a_campaign_with_or_without_a_rule(
    tmp_path, monkeypatch, rule
):
    """The worker's real full refresh is admitted and promoted for a draft
    with a rule as for one without (Review A's blocker on #2134): the
    request's window, which Python writes with the rule, equals SQL's exact
    scope, and a legacy campaign's scope names no rule, as before 0175."""
    record = (
        campaign_record() if rule is None else campaign_record(campaign_families=rule)
    )
    refreshes = Refreshes(tmp_path, monkeypatch, row=record)
    with connection.cursor() as cursor:
        cursor.execute(f"SELECT {WINDOW}(%s)", [record["id"]])
        scope = cursor.fetchone()[0]
    assert ('"campaign_families"' in scope) is (rule is not None)
    load = refreshes.full.cursor["load"]
    assert (load.get(campaign_families.KEY) or {}).get("rule") == rule
    again = refreshes.again()
    assert again.state == "promoted"
    assert SourceSnapshot.objects.filter(kind="full", state="promoted").exists()
    assert SourceCurrent.objects.get().snapshot_id == again.pk

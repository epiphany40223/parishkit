"""Sign-in reads only users once the swap migration ran (#952 slice 4).

Migration 0052 replaces the Admin session admission, export authorization,
Ministry scope, role-derived leader scope and policy activation so that none
reads hosted-domain rules. Its DO block refuses an unconverted configuration,
so the offline upgrade converts first (``upgrade-users``) on the schema
``migrate --users-upgrade-base`` leaves, and Admin-access recovery must work
in every state of that upgrade (D2). The test database is a fresh install,
so the swap is already applied; a pending swap is made for the length of one
test by removing its ``django_migrations`` row and reinstalling the real
pre-swap bodies of the functions it replaces (``swap_pending``).
"""

import json
import re
import sys
from contextlib import contextmanager, nullcontext
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from django.db import DatabaseError, connection, transaction
from psycopg import sql as psycopg_sql

from parishkit.config import ConfigError
from parishkit.stewardship import operator_commands, runtime_database
from parishkit.stewardship.accounts import users_upgrade as upgrade
from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.accounts.users_schema import SCHEMA
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.runtime_database import USERS_SWAP
from parishkit.stewardship.runtime_grants import (
    CLOSURE_TABLES,
    PUBLICATION_WORKER_TABLES,
)
from parishkit.stewardship.source.source_status import NAME_NOT_RECORDED

from .campaign_builders import initialized
from .test_background_grants_postgresql import task_login
from .test_portal_identity_guards_postgresql import forge, refused
from .test_users_upgrade_postgresql import (
    ADMINS,
    _offline,
    active_users,
    arguments,
    identity,
    rules,
)

pytestmark = pytest.mark.django_db(transaction=True)


MIGRATIONS = Path(__file__).resolve().parents[3] / "src/parishkit/stewardship/schema"
SWAP = (MIGRATIONS / "migrations/0052_users_swap.sql").read_text(encoding="utf-8")
# The file's closing self-check, run on its own against chosen states.
CHECK = SWAP[SWAP.index("DO $check$") :]


def scalar(sql, params=()):
    """One value from the test's own (superuser) connection."""
    with connection.cursor() as cursor:
        cursor.execute(sql, params)
        return cursor.fetchone()[0]


def authorized(portal, admin_only=False):
    """What export authorization answers for one portal user."""
    return scalar(
        "SELECT stewardship_export_authorized_v1(%s, %s)", [portal.pk, admin_only]
    )


def ministry_scope(portal):
    """The Ministry report scope SQL derives for one portal user, or None."""
    return scalar("SELECT stewardship_ministry_scope_v1(%s)", [portal.pk])


def check_refuses(message):
    """Run the DO block in a savepoint, expecting it to refuse with ``message``."""
    with (
        pytest.raises(DatabaseError, match=message),
        transaction.atomic(),
        connection.cursor() as cursor,
    ):
        cursor.execute(CHECK)


def check_passes():
    """Run the DO block in a savepoint that is always rolled back."""
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute(CHECK)
        transaction.set_rollback(True)


def test_sign_in_reads_only_users_and_every_administrator_keeps_access(tmp_path):
    """Every Administrator, and the domain colleague, keeps access through the swap.

    Before the conversion and during it (converted, swap still pending) the
    pre-swap SQL runs, as `migrate --users-upgrade-base` leaves it: the
    hosted-domain colleague who signed in is admitted through the domain
    rule, then as the Staff user the conversion makes. After the swap the
    new SQL admits that user too. Both Administrators are Administrators in
    every state. The new bodies alone, on an unconverted configuration (a
    state the swap's check never commits), grant nothing from a domain rule.
    """
    store, _, _ = initialized(tmp_path, rules())
    portals = {email: identity(email) for email in (*ADMINS, "signed@example.org")}
    colleague = portals["signed@example.org"]

    def admitted(*, staff_scope):
        """Admin session, export and Ministry scope for every portal user."""
        for email, portal in portals.items():
            assert forge(portal.pk).principal_id == portal.pk, email
            assert authorized(portal) is True, email
            assert authorized(portal, admin_only=True) is (email in ADMINS), email
        assert json.loads(ministry_scope(colleague)) == {
            "capability": "ministry_report",
            "operational": staff_scope,
            "ministries": [],
        }

    # The new bodies: the domain rule grants nothing in SQL.
    with refused():
        forge(colleague.pk)
    assert authorized(colleague) is False
    assert ministry_scope(colleague) is None
    with swap_pending():
        assert all(reads_domain_rules(name) for name in REPLACED)
        # Before the conversion, on the pre-swap SQL: the domain rule
        # (Ministry leader and Staff) admits the colleague.
        admitted(staff_scope=True)
        assert upgrade.upgrade_users(store, **arguments()).state == "applied"
        assert active_users(store)["signed@example.org"] == ("signed", "staff")
        # Converted, swap still pending: the same SQL reads the users.
        admitted(staff_scope=True)
    # After the swap: the new SQL admits every user the same way.
    assert not any(reads_domain_rules(name) for name in REPLACED)
    admitted(staff_scope=True)


def test_the_swap_refuses_an_unconverted_configuration(tmp_path):
    """The DO block passes on a fresh install, in setup and once converted."""
    # A fresh install, before bootstrap: no configuration to convert.
    assert not SystemConfiguration.objects.exists()
    check_passes()
    store, _, _ = initialized(tmp_path, rules())
    check_refuses("still holds sign-in rules; run upgrade-users first")
    runtime = SystemConfiguration.objects.get()
    # A deployment still in initial setup is exempt exactly as startup
    # exempts it; nothing past setup passes as one.
    for state, passes in (
        (dict(mode="testing", restore_review_required=False), True),
        (dict(mode="production", restore_review_required=False), False),
        (dict(mode="testing", restore_review_required=True), False),
    ):
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute("SET LOCAL session_replication_role = replica")
                cursor.execute(
                    "UPDATE stewardship_configuration_version "
                    "SET validation_schema='bootstrap-policy-v1' WHERE id=%s",
                    [runtime.active_configuration_id],
                )
                cursor.execute(
                    "UPDATE stewardship_system_configuration SET mode=%s, "
                    "restore_review_required=%s, current_campaign_id=NULL",
                    [state["mode"], state["restore_review_required"]],
                )
                cursor.execute("SET LOCAL session_replication_role = origin")
            if passes:
                check_passes()
            else:
                check_refuses("still holds sign-in rules")
            transaction.set_rollback(True)
    assert upgrade.upgrade_users(store, **arguments()).state == "applied"
    assert SystemConfiguration.objects.get().active_configuration.validation_schema == (
        SCHEMA
    )
    check_passes()


# The three replaced functions whose earlier bodies are in no frozen
# migration, frozen as a test fixture from the baseline before the swap.
PRE_SWAP = Path(__file__).with_name("pre_users_swap_functions.sql")
REPLACED = (
    "stewardship_portal_session_admission_v1",
    "stewardship_export_authorized_v1",
    "stewardship_ministry_scope_v1",
    "stewardship_ministry_leader_scope_v1",
    "stewardship_policy_activation_v1",
)


def old_definitions():
    """Each replaced function's real definition before the swap, by name.

    The Ministry scope and the leader scope (SECURITY DEFINER in its own
    text) come from migration 0042, the latest frozen file before 0052 that
    defines them. The session guard, export authorization and policy
    activation come from the frozen fixture copied from the baseline before
    0052 replaced them; nothing between created or replaced any of the five.
    """
    sources = (
        MIGRATIONS / "migrations/0042_ministry_leaders_from_roles.sql"
    ).read_text(encoding="utf-8") + PRE_SWAP.read_text(encoding="utf-8")
    definitions = {}
    for name in REPLACED:
        match = re.search(
            rf"^CREATE (?:OR REPLACE )?FUNCTION public\.{name}\(.*?\$\$;$",
            sources,
            re.S | re.M,
        )
        definitions[name] = match.group(0).replace(
            "CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1
        )
    return definitions


@pytest.mark.parametrize("name", REPLACED)
def test_the_swap_check_refuses_each_old_definition(tmp_path, name):
    """With any one function still reading hosted domains, the swap refuses.

    The leader scope is also refused once it stops being SECURITY DEFINER,
    which a CREATE OR REPLACE without the attribute would do.
    """
    store, _, _ = initialized(tmp_path, rules())
    assert upgrade.upgrade_users(store, **arguments()).state == "applied"
    check_passes()
    old = old_definitions()[name]
    assert "domain_rule" in old
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute(old)
        check_refuses("a replaced function is not installed as declared")
        transaction.set_rollback(True)
    if name == "stewardship_ministry_leader_scope_v1":
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute(
                    "ALTER FUNCTION public.stewardship_ministry_leader_scope_v1(uuid) "
                    "SECURITY INVOKER"
                )
            check_refuses("a replaced function is not installed as declared")
            transaction.set_rollback(True)
    check_passes()


# Objects a later closure migration's frozen file creates with plain CREATE,
# which a full migration would otherwise fail to create again: #970's 0056,
# #995's 0057, #1024's 0058, #1018's 0059, #528's 0060 and 0061, #530's
# 0062, #463's 0063, #558's 0064, #529's 0065, #794's 0066, #1018's 0067,
# #806's 0068, #932's 0069, #1034's 0070, #1018's 0071 and 0072 and
# #806's 0077 follow 0055 (#993), so they are in the swap's closure too.
# Each is dropped while the swap is pending (with the trigger that uses it)
# and, unless the test's full migration made it again, installed again
# afterwards from its migration's frozen file. The tables 0059 to 0063 and
# 0071 create (runtime_grants.CLOSURE_TABLES) are dropped and installed
# again with their guard functions, as the base lacks them too. 0063 also alters the
# export request (``before_timeline_exports``), which is put back first, and
# 0064 re-points the two report export capture triggers
# (``before_report_local_days``), which are put back on the v1 captures.
# 0065 creates nothing; it adds a column and replaces the audit context
# check (``before_staff_entered``), so ``CLOSURE_ALTERED`` puts it back.
# 0066 creates nothing and only replaces a function in place. 0067 creates
# the two campaign comparison functions and alters no table. 0068 creates
# the Mail message page's per-address outcome read and alters no table.
# 0069 creates nothing and only replaces a function in place. 0070 creates
# the leader definition at given pointers and the change recorder, and
# re-points stewardship_ministry_leaders_v1 at the former
# (``before_leader_changes``), which is put back on 0042's body. 0071, like
# 0063, alters the export request (``before_comparison_exports``), which is
# put back as 0063 left it before 0063's own undo runs; its entries come
# after 0063's here so the frozen files install again in prefix order.
# 0072 creates nothing; it adds the daily digest ready row's comparison
# columns and replaces the audit context check and the two campaign
# comparison functions in place (``before_digest_comparison``), so
# ``CLOSURE_ALTERED`` puts it back, after 0067's file has made the two
# functions again. 0073 creates mail dispatch's one token read and alters
# no table; its column revokes are no-ops when run again.
# 0074 (#1062) creates nothing: it only replaces the ungranted branding
# asset insert guard in place, so a rerun of its file is harmless and
# nothing is registered for it. 0077
# creates the address follow-up table (in CLOSURE_TABLES) with its guard and
# position check and three definers, and adds a task type to the two task
# maps in place; the maps call nothing it creates, so they need no
# ``before_*``. 0078 (#530) creates the debug-off switch table (in
# CLOSURE_TABLES, since web sets and reads it) with its guard and two
# definers. Its widened operational event check is replaced again by a
# rerun, harmlessly. 0080 (#530) creates the large-change acceptance table
# (in CLOSURE_TABLES, which the worker reads) with its guard and its invoker
# lifetime function, and alters no table. 0081 (#806) creates the web-only
# bounce report definer, adds the refusal table's kind and evidence_note
# columns and replaces the refusal guard (``before_bounce_report``), so
# ``CLOSURE_ALTERED`` puts it back too. 0082 (#1062) creates nothing; it adds
# the branding bundle's mode column (``before_branding_bundle_mode``), so
# ``CLOSURE_ALTERED`` puts it back. 0083 (#477) creates nothing; it adds the
# daily digest ready row's funnel chart columns
# (``before_digest_funnel_charts``), which ``CLOSURE_ALTERED`` puts back.
# 0084 (#1018 part (a)) creates the campaign archive's answers read, and,
# like 0071, alters the export request (its kinds, guards and request
# authorization); the 0071 and 0063 undo statements already put all of that
# back as the base has it, so it needs no undo of its own.
# 0085 (#530) creates the example Families table (in CLOSURE_TABLES, which
# the worker writes and web reads) with its guard and its invoker
# visibility function, and alters no table.
# 0086 (#895) creates the worker-only snapshot statistics definer and alters
# nothing.
# 0087 (#528) creates the five
# publication tables (in CLOSURE_TABLES) with their guards and deferred
# checks and the worker's audit answer definer, and alters no table.
# 0088 (#1207) creates nothing: it replaces 0078's switch guard in place, which
# ``CLOSURE_REPLACED`` puts back after 0078's file has made the guard again.
# 0089 (#1201) creates nothing:
# it widens the operational event check and replaces the safe context and
# log writer functions in place. The reinstalled 0078 (CLOSURE_CREATED) and
# 0083 (CLOSURE_ALTERED) files bring back their older check and safe
# context, so ``CLOSURE_REPLACED`` runs 0089's file again afterwards (#1284).
# 0090 (#528) creates nothing: it replaces the derived guard (a base function)
# and two of 0087's guards in place, so once 0087's file installs again with its
# older guard bodies, 0090's file runs again too (``CLOSURE_RERUN``).
# 0091 (#1267) creates nothing:
# it drops the Ministry activity table and replaces, in place, functions
# that read it, two of which (0064's report and 0070's leader definition)
# are CLOSURE_CREATED and one (0057's leaders list) is in
# runtime_grants.CLOSURE_FUNCTIONS; their files bring back the older bodies,
# so ``CLOSURE_OLD_BODIES`` puts 0091's bodies back afterwards.
# 0092 (#1217) creates the two invoker functions for a Member's standing in
# ParishSoft and replaces the Ministry selections in place to call them;
# nothing reads the selections while the swap is pending, and its file
# installs them again.
# 0093 (#1219) creates nothing: it replaces those Ministry selections again,
# so once 0092's file installs again with its older bodies, 0093's file runs
# again too (``CLOSURE_RERUN``).
# 0094 (#1216) creates nothing: it replaces those selections once more, with
# the report selections 0056 and 0064 create, so once those files (and 0092's
# and 0093's) install again with their older bodies, ``CLOSURE_REPLACED``
# runs 0094's file again.
# 0095 (#1286) creates nothing: it replaces 0072's campaign comparison
# function in place, so once 0067's file (``CLOSURE_CREATED``) and 0072's
# (``CLOSURE_ALTERED``) install it again with the raw stored zone,
# ``CLOSURE_REPLACED`` runs 0095's file again.
# 0096 (#528) creates the review history table (in CLOSURE_TABLES) with its
# guard and deferred check, and replaces the derived guard again; since
# 0090's rerun puts 0090's derived guard back, ``CLOSURE_REPLACED_ONLY``
# then runs 0096's replacement statement alone (its table cannot be made
# twice).
# 0098 (#1229) drops 0078's count-only operator clear and creates its v2,
# which names the cleared switch. Its entry follows 0078's below, so once
# 0078's file installs again (making v1 again), 0098's file runs after it
# and drops v1 again.
# 0100 (#720) creates nothing: it replaces 0064's information selection in
# place again, after 0094, so once 0064's file (``CLOSURE_CREATED``) and
# 0094's (``CLOSURE_REPLACED``) install it again without the counts,
# ``CLOSURE_REPLACED`` runs 0100's file again.
# 0102 (#1353) creates nothing: it replaces the setup attempt's guards, the
# setup disposal check and the safe context in place. Only the safe context
# comes back older (0089's, through ``CLOSURE_REPLACED``), so
# ``CLOSURE_REPLACED_AGAIN`` runs 0102's file after it.
# 0106 (#1403) creates nothing a reinstall would make twice: it widens the
# event check and replaces the safe context and log writer in place again,
# after 0089 and 0104, and makes its partial index only when missing. The
# reruns above bring back 0089's event check and 0104's safe context, so
# ``CLOSURE_REPLACED_LAST`` runs 0106's file after them.
# 0108 (#528, ADM-09.03) adds the accepted base columns to 0096's review
# table (in CLOSURE_TABLES, so dropped whole while the swap is pending; no
# ``before_*`` undo is needed) and replaces the review guard, its effect
# check and the derived guard in place. Once 0096's and 0107's files make
# the table and guards again without them, ``CLOSURE_ALTERED`` runs 0108's
# file, after 0090's rerun has put 0090's derived guard back; its bodies
# keep the phrases ``CLOSURE_REPLACED`` (0107) and ``CLOSURE_REPLACED_ONLY``
# (0096) look for, so neither puts an older body back afterwards.
# 0112 (#528, ADM-09) creates nothing: it replaces the derived guard and
# 0087's confirmation guard in place, which 0087's (``CLOSURE_CREATED``),
# 0090's (``CLOSURE_RERUN``) and 0108's (``CLOSURE_ALTERED``) files bring back
# older, so ``CLOSURE_REPLACED`` runs 0112's file again afterwards. Its derived
# guard lacks 0113's phrase, so ``CLOSURE_REPLACED_ONLY`` then puts 0113's
# guard back last.
# 0103 (#1330) creates nothing: it replaces 0091's campaign pointer function
# in place. No closure file installs that function again, and 0091's
# ``CLOSURE_OLD_BODIES`` rerun touches only bodies still reading Ministry
# activity, which 0103's does not, so its body survives the swap and nothing
# is registered (the database conftest's schema guard checks this, #1489).
# 0109 (#1398) creates the discard table (in CLOSURE_TABLES) and the window,
# ended and discard functions, one of them granted to the scheduler
# (runtime_grants.CLOSURE_FUNCTIONS); ``closure_functions_absent`` makes it
# whole again once the table is gone. It also replaces the safe context, so
# ``CLOSURE_REPLACED_ONLY`` puts 0109's back after the reruns above.
# 0110 (#558) creates nothing: it replaces the baseline financial selection
# in place, which 0094's rerun (``CLOSURE_REPLACED``) brings back older, so
# ``CLOSURE_REPLACED`` runs 0110's file after it.
# 0113 (#1596) creates nothing: it replaces the derived guard in place (the
# corrected census handling, on 0112's body) and re-marks open rows. 0112's
# rerun (``CLOSURE_REPLACED``) puts 0112's guard back, so
# ``CLOSURE_REPLACED_ONLY``, which runs after it, runs 0113's replacement
# statement alone; the one-time correction does not run again.
# 0115 (#530) creates three tables (in CLOSURE_TABLES) and their guards
# (``CLOSURE_CREATED``), and replaces nothing.
CLOSURE_CREATED = {
    "public.stewardship_limitations_report_v1(uuid,jsonb)": (
        "stewardship_reports",
        "0013_retire_talents",
    ),
    "public.stewardship_admin_session_key_v1(text)": (
        "stewardship_accounts",
        "0017_admin_session_key_digests",
    ),
    "public.stewardship_portal_session_key_digest_v1()": (
        "stewardship_accounts",
        "0017_admin_session_key_digests",
    ),
    "public.stewardship_delivery_recipients_v1(uuid,uuid)": (
        "stewardship_jobs",
        "0022_mail_message_recipients",
    ),
    "public.stewardship_campaign_archive_guard_v1()": (
        "stewardship_reports",
        "0014_campaign_archives",
    ),
    "public.stewardship_campaign_archive_child_guard_v1()": (
        "stewardship_reports",
        "0014_campaign_archives",
    ),
    "public.stewardship_campaign_archive_child_insert_v1()": (
        "stewardship_reports",
        "0014_campaign_archives",
    ),
    "public.stewardship_proposal_resolution_guard_v1()": (
        "stewardship_responses",
        "0002_proposal_resolution",
    ),
    "public.stewardship_proposal_resolution_effect_v1()": (
        "stewardship_responses",
        "0002_proposal_resolution",
    ),
    "public.stewardship_ministry_roster_entry_guard_v1()": (
        "stewardship_workflows",
        "0002_roster_entries",
    ),
    "public.stewardship_ministry_roster_entry_effect_v1()": (
        "stewardship_workflows",
        "0002_roster_entries",
    ),
    "public.stewardship_backup_request_guard_v1()": (
        "stewardship_jobs",
        "0023_backup_request",
    ),
    "public.stewardship_timeline_export_capture_v1()": (
        "stewardship_reports",
        "0015_timeline_exports",
    ),
    "public.stewardship_timeline_export_binding_v1()": (
        "stewardship_reports",
        "0015_timeline_exports",
    ),
    **dict.fromkeys(
        (
            "public.stewardship_browser_day_start_v1(date,text)",
            "public.stewardship_information_report_v2(uuid,jsonb,integer,uuid,integer)",
            "public.stewardship_ministry_report_v3"
            "(uuid,jsonb,boolean,bigint[],integer,text,integer,integer)",
            "public.stewardship_ministry_report_v4"
            "(uuid,jsonb,uuid,integer,text,integer,integer)",
            "public.stewardship_information_export_capture_v2()",
            "public.stewardship_ministry_export_capture_v2()",
        ),
        ("stewardship_reports", "0016_report_local_days"),
    ),
    **dict.fromkeys(
        (
            "public.stewardship_campaign_comparison_v1(uuid,date,numeric[],uuid)",
            "public.stewardship_campaign_archive_renewal_v1(uuid,uuid,integer)",
        ),
        ("stewardship_reports", "0018_campaign_comparison"),
    ),
    "public.stewardship_delivery_recipient_outcomes_v1(uuid,uuid)": (
        "stewardship_jobs",
        "0024_mail_message_address_outcomes",
    ),
    **dict.fromkeys(
        (
            "public.stewardship_ministry_leaders_at_v1(uuid,uuid,uuid)",
            "public.stewardship_ministry_leader_changes_v1"
            "(uuid,uuid,uuid,uuid,uuid,uuid,uuid)",
        ),
        ("stewardship_accounts", "0018_leader_changes"),
    ),
    **dict.fromkeys(
        (
            "public.stewardship_comparison_export_capture_v1()",
            "public.stewardship_comparison_export_binding_v1()",
        ),
        ("stewardship_reports", "0020_comparison_exports"),
    ),
    "public.stewardship_dispatch_family_token_v1(uuid,uuid,uuid,bigint,uuid)": (
        "stewardship_jobs",
        "0025_dispatch_token_access",
    ),
    "public.stewardship_family_daily_resume_v1()": (
        "stewardship_jobs",
        "0026_daily_send_resume",
    ),
    **dict.fromkeys(
        (
            "public.stewardship_address_positions_v1(jsonb,integer)",
            "public.stewardship_address_retry_guard_v1()",
            "public.stewardship_address_retry_open_v1"
            "(uuid,uuid,bigint,uuid,text,text,integer)",
            "public.stewardship_address_retry_token_v1(uuid,uuid,uuid,bigint,uuid)",
            "public.stewardship_delivery_address_retries_v1(uuid,uuid)",
        ),
        ("stewardship_jobs", "0027_address_retry"),
    ),
    **dict.fromkeys(
        (
            "public.stewardship_debug_off_switch_guard_v1()",
            "public.stewardship_debug_logging_off_v1()",
        ),
        ("stewardship_jobs", "0028_debug_off_switch"),
    ),
    **dict.fromkeys(
        (
            "public.stewardship_source_drop_acceptance_guard_v1()",
            "public.stewardship_source_drop_acceptance_active_v1()",
        ),
        ("stewardship_source", "0008_drop_acceptance"),
    ),
    "public.stewardship_report_recipient_bounce_v1(uuid,uuid,text,uuid,text)": (
        "stewardship_jobs",
        "0030_bounce_report",
    ),
    # The campaign archive's answers read (0084, #1018). Its file also
    # re-adds export_report_known and replaces the export guards and the
    # request authorization; ``before_comparison_exports`` and
    # ``before_timeline_exports`` put those back as the base has them, and
    # this file, run again last, installs them as released.
    "public.stewardship_campaign_archive_answers_v1(uuid,timestamp with time zone)": (
        "stewardship_reports",
        "0023_campaign_archive_exports",
    ),
    **dict.fromkeys(
        (
            "public.stewardship_source_drop_example_guard_v1()",
            "public.stewardship_source_drop_example_attempt_v1()",
        ),
        ("stewardship_source", "0009_drop_examples"),
    ),
    "public.stewardship_source_snapshot_analyze_v1(uuid)": (
        "stewardship_source",
        "0010_snapshot_statistics",
    ),
    **dict.fromkeys(
        (
            "public.stewardship_publication_audited_v1(text,uuid,uuid,jsonb)",
            *(
                f"public.stewardship_publication_{name}_v1()"
                for name in (
                    "plan_guard",
                    "plan_effect",
                    "entity_guard",
                    "entity_effect",
                    "field_guard",
                    "confirmation_guard",
                    "confirmation_effect",
                    "outcome_guard",
                    "outcome_effect",
                )
            ),
        ),
        ("stewardship_responses", "0004_publication_plans"),
    ),
    **dict.fromkeys(
        (
            "public.stewardship_member_source_status_v1(uuid,text,bigint)",
            "public.stewardship_ministry_request_member_status_v1(uuid)",
        ),
        ("stewardship_reports", "0024_hide_gone_members"),
    ),
    **dict.fromkeys(
        (
            "public.stewardship_proposal_review_guard_v1()",
            "public.stewardship_proposal_review_effect_v1()",
        ),
        ("stewardship_responses", "0006_proposal_review"),
    ),
    "public.stewardship_debug_off_clear_v2()": (
        "stewardship_jobs",
        "0032_debug_off_clear_subject",
    ),
    # 0105 (#1404) creates the Campaign setup review table (in
    # CLOSURE_TABLES) with its guard and deferred audit check.
    **dict.fromkeys(
        (
            "public.stewardship_campaign_setup_review_guard_v1()",
            "public.stewardship_campaign_setup_review_effect_v1()",
        ),
        ("stewardship_accounts", "0025_campaign_setup_review"),
    ),
    # 0107 (#528, ADM-09.04) creates the edit guard on census changes; its
    # file also replaces 0096's review guard (CLOSURE_REPLACED below).
    "public.stewardship_proposal_edit_guard_v1()": (
        "stewardship_responses",
        "0008_proposal_review_edit",
    ),
    # 0109 (#1398) creates the discard table (in CLOSURE_TABLES) and these
    # functions; all of them go, so its file installs whole again.
    **dict.fromkeys(
        (
            "public.stewardship_export_window_v1()",
            "public.stewardship_export_ended_v1(uuid)",
            "public.stewardship_exact_export_ended_v1(uuid)",
            "public.stewardship_export_capture_cleared_v1(oid,jsonb,jsonb)",
            "public.stewardship_export_discard_v1(uuid,boolean)",
            "public.stewardship_exact_export_discard_v1(uuid,boolean)",
            "public.stewardship_export_discard_batch_v1(integer,boolean)",
            "public.stewardship_export_discard_due_v1(integer)",
        ),
        ("stewardship_reports", "0029_point_in_time_exports"),
    ),
    # 0115 (#530) creates the mail halt, clear signal and mailbox check
    # tables (in CLOSURE_TABLES) with their three guards; all go, so its
    # file installs whole again.
    **dict.fromkeys(
        (
            "public.stewardship_mail_halt_guard_v1()",
            "public.stewardship_mail_halt_clear_guard_v1()",
            "public.stewardship_mailbox_check_guard_v1()",
        ),
        ("stewardship_jobs", "0033_mail_halt_clear"),
    ),
}


# A closure migration whose frozen file only replaces functions another
# closure file creates, with that file's migration: when ``swap_pending``
# installs the creating file again, the replacing one runs again after it,
# so later tests keep the replaced bodies. Running it twice is harmless.
CLOSURE_RERUN = {
    ("stewardship_responses", "0005_publication_worker"): (
        "stewardship_responses",
        "0004_publication_plans",
    ),
    ("stewardship_reports", "0025_removed_ministry_gone_members"): (
        "stewardship_reports",
        "0024_hide_gone_members",
    ),
}


# Columns a later closure migration's frozen file adds to an existing table,
# with the migration that adds them. ``swap_pending`` drops each (with the
# ``before_*`` statements) and, unless the test's full migration added it
# again, installs it again afterwards from its migration's frozen file.
CLOSURE_ALTERED = {
    ("stewardship_submission", "entered_by_id"): (
        "stewardship_responses",
        "0003_staff_entered",
    ),
    ("stewardship_daily_digest_ready", "comparison"): (
        "stewardship_reports",
        "0021_digest_comparison",
    ),
    ("stewardship_recipient_refusal", "kind"): (
        "stewardship_jobs",
        "0030_bounce_report",
    ),
    ("stewardship_branding_bundle", "mode"): (
        "stewardship_accounts",
        "0021_branding_bundle_mode",
    ),
    ("stewardship_daily_digest_ready", "funnel_chart"): (
        "stewardship_reports",
        "0022_digest_funnel_charts",
    ),
    ("stewardship_proposal_review", "accepted_base"): (
        "stewardship_responses",
        "0009_conflict_resolution",
    ),
}


# Functions a later closure migration's frozen file replaces in place after
# an earlier one in CLOSURE_CREATED or CLOSURE_ALTERED made or replaced
# them, with that migration and a phrase only its body has. Installing the
# earlier file again (above) puts the earlier body back, so
# ``swap_pending`` runs the later file again unless the body already has
# the phrase. 0088 (#1207) replaces 0078's
# debug-off switch guard with the Testing-only web clear. 0089 (#1201)
# replaces the safe context that 0083's file brings back (and the event
# check 0078's file brings back) with its own, which admits 'retry_refusal'.
# 0094 (#1216) replaces 0056's and 0064's report selections; its file also
# puts back the Ministry selections 0092's and 0093's files ran again
# (``CLOSURE_RERUN``) with older bodies, as it runs after them. 0095 (#1286)
# replaces the comparison function 0067's and 0072's files bring back.
# 0100 (#720) replaces the information selection 0094 also replaces, so its
# entry comes after 0094's: 0094's file, run again for the limitations
# report, puts that selection back without the counts first.
# 0101 (#528) replaces the task-type maps 0077's file brings back.
# 0107 (#528, ADM-09.04) replaces the review guard 0096's file brings back
# with the one that also admits an edited value.
# 0110 (#558) replaces the baseline financial selection that 0094's file,
# run again above for the limitations report, puts back without the
# browser-zone days, so its entry comes after 0094's.
CLOSURE_REPLACED = {
    "public.stewardship_debug_off_switch_guard_v1()": (
        ("stewardship_jobs", "0031_debug_allow"),
        "clears the debug-off switch in Production",
    ),
    "public.stewardship_safe_context_v1(text,jsonb)": (
        ("stewardship_audit", "0003_retry_refusal_log"),
        "'retry_refusal'",
    ),
    **dict.fromkeys(
        (
            "public.stewardship_limitations_report_v1(uuid,jsonb)",
            "public.stewardship_ministry_report_v3"
            "(uuid,jsonb,boolean,bigint[],integer,text,integer,integer)",
        ),
        (("stewardship_reports", "0026_source_status_names"), NAME_NOT_RECORDED),
    ),
    "public.stewardship_campaign_comparison_v1(uuid,date,numeric[],uuid)": (
        ("stewardship_reports", "0027_canonical_timezone"),
        "stewardship_timezone_name_v1(selected.timezone)",
    ),
    "public.stewardship_information_report_v2(uuid,jsonb,integer,uuid,integer)": (
        ("stewardship_reports", "0028_information_status_counts"),
        "'counts',(SELECT jsonb_build_object(",
    ),
    "public.stewardship_financial_report_v1(uuid,jsonb,integer,integer)": (
        ("stewardship_reports", "0030_financial_local_days"),
        "CASE WHEN zoned THEN",
    ),
    **dict.fromkeys(
        (
            "public.stewardship_task_type_login_v1(text)",
            "public.stewardship_task_type_creators_v1(text)",
        ),
        (("stewardship_responses", "0007_publication_tasks"), "census_publication"),
    ),
    "public.stewardship_proposal_review_guard_v1()": (
        ("stewardship_responses", "0008_proposal_review_edit"),
        "OR decided=edited",
    ),
    "public.stewardship_response_derived_guard_v1()": (
        ("stewardship_responses", "0010_upstream_at_check"),
        "f.outcome='already_in_parishsoft'",
    ),
    "public.stewardship_publication_confirmation_guard_v1()": (
        ("stewardship_responses", "0010_upstream_at_check"),
        "p.version<>f.proposal_version+1",
    ),
}


# Functions a ``CLOSURE_REPLACED`` file brings back with an older body that a
# still later closure file replaces again, with that file and its phrase;
# run after ``CLOSURE_REPLACED`` the same way. 0102 (#1353) widens 0089's
# safe context, which 0089's rerun (for its event check) puts back, and
# 0104 (#1402) widens 0102's again with the setup sign-in age, so 0104's
# file, whose body holds both, is the one that runs.
CLOSURE_REPLACED_AGAIN = {
    "public.stewardship_safe_context_v1(text,jsonb)": (
        ("stewardship_accounts", "0024_setup_sign_in_hour"),
        "'sign_in_age_seconds']",
    ),
}


# Run after ``CLOSURE_REPLACED_AGAIN`` the same way: 0106 (#1403) widens
# 0104's safe context with the refusal schema, and its file also puts back
# the event check and log writer that 0089's rerun made older. 0111 (#1499)
# replaces 0104's setup credential guard, which 0104's rerun above brings
# back, so 0111's file runs again too (its index and constraint statements
# are idempotent).
CLOSURE_REPLACED_LAST = {
    "public.stewardship_safe_context_v1(text,jsonb)": (
        ("stewardship_audit", "0004_admin_input_refusal_log"),
        "WHEN 'refusal' THEN",
    ),
    "public.stewardship_setup_secret_guard_v1()": (
        ("stewardship_accounts", "0026_setup_slack_off"),
        "IF attempt.state='collecting' THEN",
    ),
}


# Closure migrations whose frozen files replace, in place, functions that a
# reinstalled earlier file (``CLOSURE_CREATED``, ``CLOSURE_ALTERED`` or
# runtime_grants.CLOSURE_FUNCTIONS) brings back with an older body, with
# the text that marks such an older body.
CLOSURE_OLD_BODIES = {
    ("stewardship_accounts", "0022_drop_ministry_activity"): (
        "stewardship_ministry_activity"
    ),
}


def rerun_old_bodies(cursor, migrations):
    """Replace again each older body a ``CLOSURE_OLD_BODIES`` file replaced.

    ``migrations`` is the loader's ``disk_migrations``. Only a function that
    exists and still holds the older body's marker is replaced, from its
    statement in the file (with the file's ``SECURITY DEFINER`` alter for
    it), so this also works while ``swap_pending`` has dropped some of them
    and ``closure_functions_absent`` puts its own back first.
    """
    for key, marker in CLOSURE_OLD_BODIES.items():
        module = sys.modules[migrations[key].__module__]
        text = Path(module.FROZEN_SQL).read_text(encoding="utf-8")
        names = re.findall(r"^CREATE OR REPLACE FUNCTION public\.(\w+)\(", text, re.M)
        for name in names:
            cursor.execute(
                "SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace "
                "WHERE n.nspname='public' AND p.proname=%s AND strpos(p.prosrc, %s)>0",
                [name, marker],
            )
            if cursor.fetchone() is None:
                continue
            start = text.index(f"CREATE OR REPLACE FUNCTION public.{name}(")
            quote = re.compile(r"\bAS\s+(\$\w*\$)").search(text, start)
            end = text.index(quote[1] + ";", quote.end()) + len(quote[1]) + 1
            cursor.execute(text[start:end])
            for alter in re.findall(
                rf"^ALTER FUNCTION public\.{name}\([^)]*\) SECURITY DEFINER;$",
                text,
                re.M,
            ):
                cursor.execute(alter)


# Functions a later closure file replaces in place after a rerun above has
# put an earlier body back, when that file also creates tables and so cannot
# run whole twice: only its replacement statement runs again, unless the body
# already has the phrase. 0096 (#528) replaced the derived guard after 0090,
# and 0113 (#1596) replaces it last, after 0108's and 0112's reruns; its body
# keeps 0096's, 0108's and 0112's rules, so only the latest replacement runs.
CLOSURE_REPLACED_ONLY = {
    "stewardship_response_derived_guard_v1": (
        ("stewardship_responses", "0011_census_handling_registry"),
        "OR expected_submitted IN ('null'::jsonb",
    ),
    # 0109 (#1398) widens 0106's safe context with the export report and
    # format keys; the reruns above bring back 0106's, and 0109's discard
    # table cannot be made twice, so its replacement statement runs alone.
    "stewardship_safe_context_v1": (
        ("stewardship_reports", "0029_point_in_time_exports"),
        "'report','format']",
    ),
    # 0109 also lets each capture trigger, the export history trigger and
    # the request guard admit the discard; the reruns of the files that made
    # them (0037, 0063, 0064, 0071, 0084) bring back their earlier bodies.
    **{
        name: (("stewardship_reports", "0029_point_in_time_exports"), phrase)
        for name, phrase in (
            *(
                (f"stewardship_{kind}_export_capture_v{version}", "capture_cleared")
                for kind, version in (
                    ("directory", 1),
                    ("financial", 1),
                    ("information", 2),
                    ("ministry", 2),
                    ("family_test_names", 1),
                    ("timeline", 1),
                    ("comparison", 1),
                )
            ),
            ("stewardship_export_immutable_v1", "stewardship_export_discard d"),
            (
                "stewardship_export_request_guard_v1",
                "snapshot.document,directory.document",
            ),
        )
    },
}


def _replaced(text, name):
    """One ``CREATE OR REPLACE FUNCTION`` statement of a frozen file, whole.

    Its body ends at the dollar quote that opened it, plain ``$$`` or a
    tagged one such as the safe context's ``$_$``.
    """
    start = text.index(f"CREATE OR REPLACE FUNCTION public.{name}(")
    quote = re.compile(r"\bAS\s+(\$\w*\$)").search(text, start)
    return text[start : text.index(quote[1] + ";", quote.end()) + len(quote[1]) + 1]


def before_timeline_exports():
    """The statements that put the export request back as it was before 0063.

    Unlike the other closure files, #463's 0063 alters an existing table:
    it adds ``timeline_snapshot_id`` and a ``family_timeline`` arm to
    ``export_report_known``, and re-creates two guards and the request
    authorization with a timeline branch. Once its snapshot table is
    dropped (which drops the column's key), these restore 0049's check,
    drop the column and restore 0037's guards and the authorization
    without the branch, so the base matches production's and 0063's
    frozen file (or the full migration) installs again on it.
    """
    files = MIGRATIONS / "migrations"
    known = (files / "0049_family_test_names_formats.sql").read_text(encoding="utf-8")
    guards = (files / "0037_family_test_names.sql").read_text(encoding="utf-8")
    timeline = (files / "0063_timeline_exports.sql").read_text(encoding="utf-8")
    branch = (
        "        WHEN request.report='family_timeline' THEN\n"
        "            -- One Family's full timeline is the page's Administrator view\n"
        "            -- (migration 0063), for the requester as for anyone else.\n"
        "            stewardship_export_authorized_v1(user_uuid,true)\n"
    )
    authorized = _replaced(timeline, "stewardship_export_request_authorized_v1")
    assert authorized.count(branch) == 1
    return [
        *(
            line
            for line in known.splitlines()
            if line.startswith("ALTER TABLE public.stewardship_export_request")
        ),
        "ALTER TABLE public.stewardship_export_request "
        "DROP COLUMN timeline_snapshot_id",
        _replaced(guards, "stewardship_export_request_guard_v1"),
        _replaced(guards, "stewardship_export_publication_guard_v1"),
        authorized.replace(branch, ""),
    ]


def before_comparison_exports():
    """The statements that put the export request's check back as 0063 left it.

    #1018's 0071, like 0063, alters the export request: it adds
    ``comparison_snapshot_id`` and a ``campaign_comparison`` arm to
    ``export_report_known`` and re-creates the two guards with a comparison
    branch (the request authorization is unchanged). Once its snapshot
    table is dropped (which drops the column's key), these restore 0063's
    check and drop the column. The guards are left to
    ``before_timeline_exports``, which runs next and puts 0037's back: 0063's
    could not be compiled here, as the timeline table is already dropped.
    0071's frozen file (or the full migration) then installs again.
    """
    timeline = (MIGRATIONS / "migrations/0063_timeline_exports.sql").read_text(
        encoding="utf-8"
    )
    return [
        *(
            line
            for line in timeline.splitlines()
            if line.startswith("ALTER TABLE public.stewardship_export_request")
        ),
        "ALTER TABLE public.stewardship_export_request "
        "DROP COLUMN comparison_snapshot_id",
    ]


def before_bounce_report():
    """The statements that put the refusal table and its guard before 0081.

    #806's 0081 adds ``kind`` and ``evidence_note`` to
    ``stewardship_recipient_refusal`` (with the check over them, which goes
    with them) and replaces its guard with 0079's body plus the reported
    bounce branch. These drop the columns and put 0079's body back, as on
    production's base; its definer is dropped with the other
    ``CLOSURE_CREATED`` objects, and 0081's frozen file (or the full
    migration) installs all of it again.
    """
    text = (MIGRATIONS / "migrations/0079_retry_refusal.sql").read_text(
        encoding="utf-8"
    )
    return [
        *(
            "ALTER TABLE public.stewardship_recipient_refusal DROP COLUMN " + column
            for column in ("kind", "evidence_note")
        ),
        _replaced(text, "stewardship_recipient_refusal_guard_v1"),
    ]


def before_digest_comparison():
    """The statements that put the digest ready row back as 0071 left it.

    #1018's 0072 adds three comparison columns to
    ``stewardship_daily_digest_ready`` (with a check over them, which goes
    with them) and replaces ``stewardship_safe_context_v1`` with 0065's body
    plus the chart timeout words. These drop the columns and put 0065's body
    back. The two comparison functions it also replaces are dropped with the
    other ``CLOSURE_CREATED`` objects and made again from 0067's file, which
    0072's file (``CLOSURE_ALTERED``) then replaces again.
    """
    text = (MIGRATIONS / "migrations/0065_staff_entered.sql").read_text(
        encoding="utf-8"
    )
    start = text.index("CREATE OR REPLACE FUNCTION public.stewardship_safe_context_v1(")
    return [
        *(
            "ALTER TABLE public.stewardship_daily_digest_ready DROP COLUMN " + column
            for column in (
                "comparison",
                "comparison_families_chart",
                "comparison_pledges_chart",
            )
        ),
        text[start : text.index("$_$;", start) + len("$_$;")],
    ]


def before_digest_funnel_charts():
    """The statements that put the digest ready row back as 0075 left it.

    #477's 0083 adds the funnel and activity chart columns (with the check
    over them, which goes with them), and replaces 0072's comparison chart
    check and ``stewardship_safe_context_v1``. Those two need no undo here:
    ``before_digest_comparison``, which runs next, drops the comparison
    columns (and so any check over them) and puts 0065's body back.
    """
    return [
        "ALTER TABLE public.stewardship_daily_digest_ready DROP COLUMN " + column
        for column in ("funnel_chart", "activity_chart")
    ]


def before_report_local_days():
    """The statements that put the report export captures back before 0064.

    #558's 0064 re-points the information and Ministry export snapshots'
    capture triggers at its v2 capture functions. Dropping those functions
    (``CLOSURE_CREATED``) drops the triggers with them, so these create
    them again on the v1 captures, as on production's base; 0064's frozen
    file (or the full migration) re-points them when it installs again.
    """
    text = (MIGRATIONS / "migrations/0064_report_local_days.sql").read_text(
        encoding="utf-8"
    )
    statements = [
        text[start : text.index(";", start) + 1]
        for start in (
            text.index(f"CREATE OR REPLACE TRIGGER {name}_export_capture ")
            for name in ("information", "ministry")
        )
    ]
    assert all(s.count("_export_capture_v2()") == 1 for s in statements)
    return [
        s.replace("_export_capture_v2()", "_export_capture_v1()") for s in statements
    ]


def before_staff_entered():
    """The statements that put submissions and the context check before 0065.

    #529's 0065 adds ``stewardship_submission.entered_by_id`` and replaces
    ``stewardship_safe_context_v1`` with 0059's body plus two sort tokens.
    These drop the column and put 0059's body back, as on production's
    base, so 0065's frozen file (or the full migration) installs again.
    """
    text = (MIGRATIONS / "migrations/0059_campaign_archives.sql").read_text(
        encoding="utf-8"
    )
    start = text.index("CREATE OR REPLACE FUNCTION public.stewardship_safe_context_v1(")
    return [
        "ALTER TABLE public.stewardship_submission DROP COLUMN entered_by_id",
        text[start : text.index("$_$;", start) + len("$_$;")],
    ]


def before_branding_bundle_mode():
    """The statement that puts branding bundles back as they were before 0082.

    #1062's 0082 adds only ``stewardship_branding_bundle.mode`` (and its
    check, which goes with the column), so dropping it is the production
    base; 0082's frozen file (or the full migration) adds it again.
    """
    return ["ALTER TABLE public.stewardship_branding_bundle DROP COLUMN mode"]


def before_leader_changes():
    """The statements that put the leader definition back as it was before 0070.

    #1034's 0070 turns ``stewardship_ministry_leaders_v1`` into a wrapper
    over its new ``stewardship_ministry_leaders_at_v1``. Dropping that
    (``CLOSURE_CREATED``) leaves the wrapper calling nothing, so this puts
    0042's body back, as on production's base; 0070's frozen file (or the
    full migration) records the leaders with it and replaces it again.
    """
    text = (MIGRATIONS / "migrations/0042_ministry_leaders_from_roles.sql").read_text(
        encoding="utf-8"
    )
    start = text.index("CREATE FUNCTION public.stewardship_ministry_leaders_v1()")
    body = text[start : text.index("END $$;", start) + len("END $$;")]
    return [body.replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1)]


@contextmanager
def swap_pending():
    """Put the database in the state `migrate --users-upgrade-base` leaves.

    The ``django_migrations`` rows of the swap and of every migration that
    depends on it are removed and the five functions
    it replaces get their real earlier bodies back (``old_definitions``),
    so sign-in, export authorization and recovery run on the SQL a
    production database has between the base migration and the full one.
    Afterwards the swap's own statements (everything before its closing
    check) and its row are put back: a transactional test's flush touches
    neither function bodies nor that table, so later tests would otherwise
    see this state. Each ``CLOSURE_CREATED`` object and ``CLOSURE_TABLES``
    table is dropped meanwhile, the export request is put back as it was
    before 0071 (``before_comparison_exports``) and then before 0063
    (``before_timeline_exports``), the report export capture
    triggers as they were before 0064 (``before_report_local_days``) and
    submissions and the context check as they were before 0065
    (``before_staff_entered``, each ``CLOSURE_ALTERED`` column) and the
    leader definition as it was before 0070 (``before_leader_changes``), the
    digest ready row as it was before 0072 (``before_digest_comparison``),
    and the refusal table and its guard as they were before 0081
    (``before_bounce_report``), and branding bundles as they were before 0082
    (``before_branding_bundle_mode``), and the digest ready row's funnel
    charts as they were before 0083 (``before_digest_funnel_charts``).
    """
    from django.db.migrations.loader import MigrationLoader

    # The swap and every later migration that depends on it are pending
    # then (a later one, such as 0053's Name lookup, keeps its objects:
    # nothing here reads them).
    loader = MigrationLoader(connection)
    closure = sorted(runtime_database.users_swap_closure(loader.graph))
    assert set(CLOSURE_CREATED.values()) <= set(closure)
    assert set(CLOSURE_ALTERED.values()) <= set(closure)
    assert {key for key, _ in CLOSURE_REPLACED.values()} <= set(closure)
    assert {key for key, _ in CLOSURE_REPLACED_AGAIN.values()} <= set(closure)
    assert {key for key, _ in CLOSURE_REPLACED_LAST.values()} <= set(closure)
    assert set(CLOSURE_RERUN) | set(CLOSURE_RERUN.values()) <= set(closure)
    assert set(CLOSURE_OLD_BODIES) <= set(closure)
    assert {key for key, _ in CLOSURE_REPLACED_ONLY.values()} <= set(closure)
    removed = []
    with transaction.atomic(), connection.cursor() as cursor:
        for app, name in closure:
            cursor.execute(
                "DELETE FROM django_migrations WHERE app=%s AND name=%s "
                "RETURNING applied",
                [app, name],
            )
            removed.extend((app, name, applied) for (applied,) in cursor.fetchall())
        assert USERS_SWAP in {(app, name) for app, name, _ in removed}
        for definition in old_definitions().values():
            cursor.execute(definition)
        for table in CLOSURE_TABLES:
            cursor.execute(f"DROP TABLE public.{table} CASCADE")
        for function in CLOSURE_CREATED:
            cursor.execute(f"DROP FUNCTION {function} CASCADE")
        for statement in (
            *before_bounce_report(),
            *before_digest_funnel_charts(),
            *before_digest_comparison(),
            *before_comparison_exports(),
            *before_timeline_exports(),
            *before_report_local_days(),
            *before_staff_entered(),
            *before_leader_changes(),
            *before_branding_bundle_mode(),
        ):
            cursor.execute(statement)
    try:
        yield
    finally:
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute("RESET SESSION AUTHORIZATION")
            cursor.execute(SWAP[: SWAP.index("DO $check$")])
            # A frozen file makes all its objects again, the tables with
            # their guard functions, so each runs at most once.
            installed = set()
            for function, key in CLOSURE_CREATED.items():
                cursor.execute("SELECT to_regprocedure(%s)", [function])
                if cursor.fetchone()[0] is None:
                    module = sys.modules[loader.disk_migrations[key].__module__]
                    cursor.execute(Path(module.FROZEN_SQL).read_text(encoding="utf-8"))
                    installed.add(key)
            for key, created_by in CLOSURE_RERUN.items():
                if created_by in installed:
                    module = sys.modules[loader.disk_migrations[key].__module__]
                    cursor.execute(Path(module.FROZEN_SQL).read_text(encoding="utf-8"))
            # After the created objects: a frozen file run again above (0059)
            # replaces the context check with its older body, which the
            # later file (0065) replaces again here.
            for (table, column), key in CLOSURE_ALTERED.items():
                cursor.execute(
                    "SELECT 1 FROM pg_attribute WHERE attrelid=%s::regclass "
                    "AND attname=%s AND NOT attisdropped",
                    [f"public.{table}", column],
                )
                if cursor.fetchone() is None:
                    module = sys.modules[loader.disk_migrations[key].__module__]
                    cursor.execute(Path(module.FROZEN_SQL).read_text(encoding="utf-8"))
            # After both: a created function's earlier body, made again
            # above, gets the later file's replacement back.
            for function, (key, phrase) in (
                *CLOSURE_REPLACED.items(),
                *CLOSURE_REPLACED_AGAIN.items(),
                *CLOSURE_REPLACED_LAST.items(),
            ):
                cursor.execute(
                    "SELECT prosrc LIKE %s FROM pg_proc WHERE oid=to_regprocedure(%s)",
                    [f"%{phrase}%", function],
                )
                if not cursor.fetchone()[0]:
                    module = sys.modules[loader.disk_migrations[key].__module__]
                    cursor.execute(Path(module.FROZEN_SQL).read_text(encoding="utf-8"))
            rerun_old_bodies(cursor, loader.disk_migrations)
            for function, (key, phrase) in CLOSURE_REPLACED_ONLY.items():
                cursor.execute(
                    "SELECT prosrc LIKE %s FROM pg_proc WHERE proname=%s",
                    [f"%{phrase}%", function],
                )
                if not cursor.fetchone()[0]:
                    module = sys.modules[loader.disk_migrations[key].__module__]
                    text = Path(module.FROZEN_SQL).read_text(encoding="utf-8")
                    cursor.execute(_replaced(text, function))
            for app, name, applied in removed:
                # A test that ran the full migration recorded the row again.
                cursor.execute(
                    "INSERT INTO django_migrations (app, name, applied) "
                    "SELECT %s, %s, %s WHERE NOT EXISTS (SELECT 1 FROM "
                    "django_migrations WHERE app=%s AND name=%s)",
                    [app, name, applied, app, name],
                )


@contextmanager
def closure_functions_absent():
    """Drop each routine the swap's closure creates, as the base lacks them.

    ``swap_pending`` keeps them (nothing there reads them), but
    database-grants on the users upgrade's base must not name them (#952).
    One that ``swap_pending`` already dropped (it is in ``CLOSURE_CREATED``
    too, such as #1024's recipient read) stays dropped. Afterwards each is
    installed again from its migration's frozen file, unless a full
    migration in the test already did.
    """
    from django.db.migrations.loader import MigrationLoader

    from parishkit.stewardship.runtime_grants import CLOSURE_FUNCTIONS

    migrations = MigrationLoader(connection).disk_migrations
    with connection.cursor() as cursor:
        for function in CLOSURE_FUNCTIONS:
            cursor.execute(f"DROP FUNCTION IF EXISTS public.{function}")
        # Those migrations' frozen files may also create routines outside the
        # registry (0070's leader copy, #1034); the base lacks them too, and
        # each file must install whole again below.
        for key in set(CLOSURE_FUNCTIONS.values()):
            module = sys.modules[migrations[key].__module__]
            text = Path(module.FROZEN_SQL).read_text(encoding="utf-8")
            for name in re.findall(r"^CREATE FUNCTION public\.(\w+)\(", text, re.M):
                cursor.execute(f"DROP FUNCTION IF EXISTS public.{name}")
    try:
        yield
    finally:
        with connection.cursor() as cursor:
            cursor.execute("RESET SESSION AUTHORIZATION")
            for function, key in CLOSURE_FUNCTIONS.items():
                cursor.execute("SELECT to_regprocedure(%s)", [f"public.{function}"])
                if cursor.fetchone()[0] is None:
                    module = sys.modules[migrations[key].__module__]
                    cursor.execute(Path(module.FROZEN_SQL).read_text(encoding="utf-8"))
            rerun_old_bodies(cursor, migrations)


@contextmanager
def provisioned_logins(monkeypatch):
    """Every runtime login, empty, and the real database-grants to fill them.

    Yields a function that runs ``provision_grants`` on this test's own
    connection as the operator would. Its operator, marker and role checks
    are stubbed (the Docker provisioning test covers them); the grants
    themselves, the writer-guard check and the existing-grant checks run
    for real. The logins are dropped afterwards.
    """
    from parishkit.stewardship import database_provisioning as provisioning
    from parishkit.stewardship.runtime_identities import database_identities

    logins = [
        (login, role)
        for _, login, role, _ in database_identities()
        if role is not ServiceRole.MIGRATION
    ]
    with connection.cursor() as cursor:
        for login, role in logins:
            name = psycopg_sql.Identifier(login)
            cursor.execute(
                psycopg_sql.SQL("CREATE ROLE {} LOGIN NOINHERIT").format(name)
            )
            cursor.execute(
                psycopg_sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(name)
            )
            if role is ServiceRole.BACKUP_WORKER:
                cursor.execute(
                    psycopg_sql.SQL("GRANT pg_read_all_data TO {}").format(name)
                )
    for name in ("_password", "_admit_operator", "role_limit"):
        monkeypatch.setattr(provisioning, name, lambda *a, **k: None)
    monkeypatch.setattr(provisioning, "_check_role", lambda *a, **k: True)
    connection.ensure_connection()
    monkeypatch.setattr(
        provisioning, "_connection", lambda *a: nullcontext(connection.connection)
    )
    configuration = SimpleNamespace(postgres=SimpleNamespace(password_file=None))

    def grants():
        """Run database-grants as the operator; its result."""
        with connection.cursor() as cursor:
            cursor.execute("RESET SESSION AUTHORIZATION")
        return provisioning.provision_grants(configuration, uuid4())

    try:
        yield grants
    finally:
        with connection.cursor() as cursor:
            cursor.execute("RESET SESSION AUTHORIZATION")
            for login, _ in logins:
                name = psycopg_sql.Identifier(login)
                cursor.execute(psycopg_sql.SQL("DROP OWNED BY {}").format(name))
                cursor.execute(psycopg_sql.SQL("DROP ROLE {}").format(name))


def test_database_grants_wait_for_the_swap_closures_functions(tmp_path, monkeypatch):
    """The users upgrade's order: base grants, conversion, migration, grants.

    On the base (the swap's closure pending, the routines and tables it
    adds absent) database-grants succeeds without them; the conversion runs
    as the admin-recovery login with exactly the grants it gave; the full
    migration then installs the swap, the Name lookup and the archive
    tables, and the grants again give web the lookup (#952), the tables
    and the digest ready row's comparison column (#1018). Mail dispatch
    still holds the earlier release's ciphertext reads on the base, which
    database-grants admits there; the full migration's 0073 revokes them
    (#389 L1), and after it the same reads would be refused as excess.
    """
    from django.core.management import call_command

    from parishkit.stewardship.runtime_grants import (
        CLOSURE_COLUMNS,
        CLOSURE_FUNCTIONS,
        DISPATCH_TOKEN_FUNCTION,
        MEMBER_NAMES_FOR_FUNCTION,
    )

    lookup = f"public.{MEMBER_NAMES_FOR_FUNCTION}"
    store, _, _ = initialized(tmp_path, rules())
    deployment = str(SystemConfiguration.objects.get().pk)
    configuration = offline_commands(monkeypatch, store)
    with (
        provisioned_logins(monkeypatch) as grants,
        swap_pending(),
        closure_functions_absent(),
    ):
        assert scalar("SELECT to_regprocedure(%s)", [lookup]) is None
        for table in CLOSURE_TABLES:
            assert scalar("SELECT to_regclass(%s)", [f"public.{table}"]) is None
        for table, column in CLOSURE_COLUMNS:
            assert not scalar(
                "SELECT count(*) FROM pg_attribute WHERE attrelid=%s::regclass "
                "AND attname=%s AND NOT attisdropped",
                [f"public.{table}", column],
            )
        with connection.cursor() as cursor:
            cursor.execute(EARLIER_MAIL_TOKEN_READS)
        assert grants() == {"database_grants_provisioned": True}
        assert mail_reads_ciphertext()
        with connection.cursor() as cursor:
            cursor.execute("SET SESSION AUTHORIZATION pk_stewardship_admin_recovery")
        result = operator_commands.upgrade_users_command(
            configuration,
            deployment_id=deployment,
            preview=False,
            operation_id=str(uuid4()),
            operator_name="Test operator",
            reason="Users revamp",
            confirmed_administrators=",".join(ADMINS),
        )
        assert result["users_upgrade_applied"] is True
        with connection.cursor() as cursor:
            cursor.execute("RESET SESSION AUTHORIZATION")
        call_command("migrate", interactive=False, verbosity=0)
        runtime_database.require_current_schema()
        with connection.cursor() as cursor:
            cursor.execute("SELECT app, name FROM django_migrations")
            assert set(CLOSURE_FUNCTIONS.values()) <= set(cursor.fetchall())
        assert grants() == {"database_grants_provisioned": True}
        assert scalar(
            "SELECT has_function_privilege('pk_stewardship_web', %s, 'EXECUTE')",
            [lookup],
        )
        # Web inserts into each closure table (the large-change acceptances,
        # 0080, since System health's button, ADM-13 PR 5b), except the
        # address follow-ups (0077, #806), which only mail dispatch reads and
        # updates.
        writers = {
            "stewardship_outbox_address_retry": (
                "pk_stewardship_mail_dispatch",
                "UPDATE",
            ),
            # A refused change's example Families (0085, #530): the worker
            # records them; web only reads them.
            "stewardship_source_drop_example": ("pk_stewardship_worker", "INSERT"),
            # The publication plans and outcomes (0087, #528), which the
            # worker writes; web inserts only the confirmation.
            **dict.fromkeys(
                PUBLICATION_WORKER_TABLES, ("pk_stewardship_worker", "INSERT")
            ),
            # Point-in-time exports' discard record (0109, #1398): only its
            # owner-run functions write it; fact compaction reads it.
            "stewardship_export_discard": ("pk_stewardship_worker", "SELECT"),
            # Clear the halt's halt records (0115, #530): mail dispatch
            # records them; web only reads them.
            "stewardship_mail_halt": ("pk_stewardship_mail_dispatch", "INSERT"),
        }
        for table in CLOSURE_TABLES:
            login, privilege = writers.get(table, ("pk_stewardship_web", "INSERT"))
            assert scalar(
                "SELECT has_table_privilege(%s, %s, %s)",
                [login, f"public.{table}", privilege],
            ), table
        for table, column in CLOSURE_COLUMNS:
            assert scalar(
                "SELECT has_column_privilege('pk_stewardship_web', %s, %s, 'SELECT')",
                [f"public.{table}", column],
            ), column
        assert not mail_reads_ciphertext()
        assert scalar(
            "SELECT has_function_privilege('pk_stewardship_mail_dispatch', %s, "
            "'EXECUTE')",
            [f"public.{DISPATCH_TOKEN_FUNCTION}"],
        )
        with connection.cursor() as cursor:
            cursor.execute(EARLIER_MAIL_TOKEN_READS)
        with pytest.raises(ConfigError, match="column grants exceed"):
            grants()


# The earlier release's mail-dispatch reads of both token ciphertexts, which
# migration 0073 revokes (#389 L1).
EARLIER_MAIL_TOKEN_READS = (
    "GRANT SELECT (ciphertext) ON public.stewardship_family_token "
    "TO pk_stewardship_mail_dispatch; "
    "GRANT SELECT (token_ciphertext) ON public.stewardship_rehearsal_credential "
    "TO pk_stewardship_mail_dispatch"
)


def mail_reads_ciphertext():
    """Whether mail dispatch may read both token ciphertext columns."""
    return scalar(
        "SELECT has_column_privilege('pk_stewardship_mail_dispatch', "
        "'public.stewardship_family_token', 'ciphertext', 'SELECT') "
        "AND has_column_privilege('pk_stewardship_mail_dispatch', "
        "'public.stewardship_rehearsal_credential', 'token_ciphertext', 'SELECT')"
    )


def catalog(cursor):
    """Every routine and relation in ``public``, by oid, with how to name it.

    Routines (functions and procedures, every overload) are named by their
    full ``regprocedure`` signature; relations (tables, views, sequences
    and the rest) by kind and name.
    """
    cursor.execute(
        "SELECT oid, oid::regprocedure::text FROM pg_proc "
        "WHERE pronamespace='public'::regnamespace"
    )
    routines = dict(cursor.fetchall())
    cursor.execute(
        "SELECT oid, relkind::text || ' ' || relname FROM pg_class "
        "WHERE relnamespace='public'::regnamespace"
    )
    return routines, dict(cursor.fetchall())


def test_closure_functions_list_every_granted_routine_the_swap_closure_adds(
    tmp_path,
):
    """No grant names an object only the users swap's closure creates, unlisted.

    A scratch database is migrated as ``migrate --users-upgrade-base`` would
    (every app to its target before the swap), then through each closure
    migration in order, and the catalog is diffed after each step, so
    whatever SQL spelling a migration uses (quoting, an unqualified name,
    IF NOT EXISTS, an overload, a view or a procedure) the objects it adds
    are what PostgreSQL reports. A granted routine among them must be in
    CLOSURE_FUNCTIONS, and a granted table or view in CLOSURE_TABLES, with
    the migration that adds it, so database-grants waits for it on the base
    (#952, #1018). A granted column the base's table lacks must be in
    CLOSURE_COLUMNS, with a closure migration, and every one listed there
    must be absent from the base. Registry names are resolved by the
    database too, so type spellings need not match.
    """
    import psycopg
    from django.db.migrations.loader import MigrationLoader

    from parishkit.stewardship.operator_commands import users_upgrade_targets
    from parishkit.stewardship.runtime_grants import (
        CLOSURE_COLUMNS,
        CLOSURE_FUNCTIONS,
        MEMBER_NAMES_FOR_FUNCTION,
        runtime_functions,
        runtime_grants,
    )
    from parishkit.stewardship.runtime_identities import database_identities

    from .test_upgrade_parity_postgresql import (
        ROOT,
        SETTINGS,
        database_parameters,
        drop_database,
        migrate,
    )

    loader = MigrationLoader(None, ignore_no_migrations=True)
    closure = runtime_database.users_swap_closure(loader.graph)
    # The closure in an order that applies each migration after its parents.
    steps = []
    for leaf in sorted(loader.graph.leaf_nodes()):
        steps += [
            key
            for key in loader.graph.forwards_plan(leaf)
            if key in closure and key not in steps
        ]
    assert USERS_SWAP in steps and set(CLOSURE_FUNCTIONS.values()) <= closure
    settings_directory = tmp_path / "settings"
    settings_directory.mkdir()
    (settings_directory / "upgrade_parity_settings.py").write_text(SETTINGS)
    source = ROOT / "src"
    scratch = f"{connection.settings_dict['NAME']}_closure"
    with psycopg.connect(
        **database_parameters(connection.settings_dict["NAME"]), autocommit=True
    ) as maintenance:
        drop_database(maintenance, scratch)
        maintenance.execute(f'CREATE DATABASE "{scratch}"')
        try:
            for target in users_upgrade_targets(loader):
                migrate(source, settings_directory, scratch, "base", target)
            added_routines, added_relations = {}, {}
            with psycopg.connect(**database_parameters(scratch)) as database:
                routines, relations = catalog(database.cursor())
                cursor = database.cursor()
                cursor.execute(
                    "SELECT c.relname, a.attname FROM pg_attribute a "
                    "JOIN pg_class c ON c.oid=a.attrelid "
                    "JOIN pg_namespace n ON n.oid=c.relnamespace "
                    "WHERE n.nspname='public' AND a.attnum>0 AND NOT a.attisdropped"
                )
                base_columns = set(cursor.fetchall())
                database.rollback()
                base_tables = {table for table, _ in base_columns}
                assert not set(CLOSURE_COLUMNS) & base_columns
                assert set(CLOSURE_COLUMNS.values()) <= closure
                for key in steps:
                    migrate(source, settings_directory, scratch, "closure", key)
                    after_routines, after_relations = catalog(database.cursor())
                    database.rollback()
                    for oid in after_routines.keys() - routines.keys():
                        added_routines[oid] = (after_routines[oid], key)
                    for oid in after_relations.keys() - relations.keys():
                        added_relations[oid] = (after_relations[oid], key)
                    routines, relations = after_routines, after_relations

                def resolve(kind, name):
                    """The oid the registry's name resolves to, or None."""
                    cursor = database.cursor()
                    cursor.execute(f"SELECT {kind}(%s)::oid", [f"public.{name}"])
                    return cursor.fetchone()[0]

                lookup = resolve("to_regprocedure", MEMBER_NAMES_FOR_FUNCTION)
                assert lookup in added_routines
                for _, _, role, target in database_identities():
                    if role is ServiceRole.MIGRATION:
                        continue
                    for function in runtime_functions(role, target=target):
                        oid = resolve("to_regprocedure", function)
                        assert oid is not None, function
                        key = added_routines.get(oid, (None, None))[1]
                        assert CLOSURE_FUNCTIONS.get(function) == key, function
                    tables, columns = runtime_grants(role, target=target)
                    for table, privileges in columns.items():
                        if table not in base_tables:
                            continue
                        for names in privileges.values():
                            for column in names:
                                pair = (table, column)
                                assert (
                                    pair in base_columns or pair in CLOSURE_COLUMNS
                                ), pair
                    for table in tables.keys() | columns.keys():
                        oid = resolve("to_regclass", table)
                        key = added_relations.get(oid, (None, None))[1]
                        assert CLOSURE_TABLES.get(table) == key, table
        finally:
            drop_database(maintenance, scratch)


def reads_domain_rules(name):
    """Whether the installed body of one replaced function names domain rules."""
    return "stewardship_domain_rule" in scalar(
        "SELECT prosrc FROM pg_proc WHERE oid=%s::regproc", [f"public.{name}"]
    )


def test_only_the_swap_closure_may_be_pending_for_recovery():
    """The relaxed check admits exactly the swap pending, nothing else."""
    runtime_database.require_current_schema()
    with swap_pending():
        with pytest.raises(ConfigError, match="do not match"):
            runtime_database.require_current_schema()
        runtime_database.require_current_schema(users_swap_pending=True)
    # Any other pending migration still refuses, relaxed or not: a leaf of
    # another app is outside the swap's closure.
    from django.db.migrations.loader import MigrationLoader

    other = next(
        key
        for key in sorted(MigrationLoader(connection).graph.leaf_nodes())
        if key[0] != USERS_SWAP[0]
    )
    with connection.cursor() as cursor:
        cursor.execute(
            "DELETE FROM django_migrations WHERE app=%s AND name=%s RETURNING applied",
            list(other),
        )
        (applied,) = cursor.fetchone()
    try:
        with pytest.raises(ConfigError, match="do not match"):
            runtime_database.require_current_schema(users_swap_pending=True)
    finally:
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO django_migrations (app, name, applied) "
                "VALUES (%s, %s, %s)",
                [*other, applied],
            )


def offline_commands(monkeypatch, store):
    """Run the operator commands in this process, admitting the real schema state.

    Process admission, the interlock and the capacity and TEMP checks are
    stubbed (covered elsewhere); require_current_schema is not, so the
    commands' own choice of what may be pending is what is tested.
    """
    configuration = SimpleNamespace(
        service_role=ServiceRole.ADMIN_RECOVERY, paths={"authority": store.root}
    )
    monkeypatch.setattr(
        operator_commands,
        "admit_offline_service",
        lambda configuration: ServiceRole.ADMIN_RECOVERY,
    )
    monkeypatch.setattr(
        operator_commands, "configure_operator_database", lambda configuration: None
    )
    monkeypatch.setattr(operator_commands, "StartupLease", lambda *a, **k: _offline())
    monkeypatch.setattr(
        operator_commands, "RuntimeLayout", lambda _: SimpleNamespace(interlock=None)
    )
    for name in (
        "require_no_temporary_authority",
        "require_capacity",
        "require_role_capacity",
    ):
        monkeypatch.setattr(runtime_database, name, lambda *a, **k: None)
    return configuration


def recover(configuration, deployment, email):
    """Recover Administrator access for ``email`` through the real command."""
    return operator_commands.recover_admin_command(
        configuration,
        deployment_id=deployment,
        operation_id=str(uuid4()),
        target_email=email,
        confirmed_email=email,
        operator_name="Test operator",
        reason="Lost administration access",
    )


def test_recovery_works_before_during_and_after_the_conversion(tmp_path, monkeypatch):
    """D2: recovery and the upgrade run with the swap pending, and after it.

    With only the swap pending and the pre-swap SQL installed, as the
    upgrade's base migration leaves the database, recover-admin adds an
    Administrator rule, which opens an Admin session at once; upgrade-users
    then converts with that Administrator confirmed too; recovery still runs
    on the converted configuration before the swap, and every Administrator
    so far opens an Admin session through the pre-swap guard. Once the swap
    is applied recovery still runs, and every Administrator recovered on the
    way opens an Admin session through the swapped guard.
    """
    store, _, _ = initialized(tmp_path, rules())
    deployment = str(SystemConfiguration.objects.get().pk)
    configuration = offline_commands(monkeypatch, store)

    portals = {}

    def sessions(*emails):
        """Each address is an Administrator user who can open an Admin session."""
        users = active_users(store)
        for email in emails:
            assert users[email][1] == "administrator", email
            if email not in portals:
                portals[email] = identity(email, signed_in=False)
            assert forge(portals[email].pk).principal_id == portals[email].pk, email

    with swap_pending():
        with base_recovery_login(monkeypatch):
            # Before the conversion, with only the swap pending.
            preview = operator_commands.preview_recovery_command(
                configuration,
                deployment_id=deployment,
                target_email="early@example.org",
            )
            assert preview["recovery_preview"]
            assert recover(configuration, deployment, "early@example.org")[
                "recovery_applied"
            ]
        # The just-recovered Administrator signs in on the pre-swap SQL.
        early = portals["early@example.org"] = identity(
            "early@example.org", signed_in=False
        )
        assert forge(early.pk).principal_id == early.pk
        with base_recovery_login(monkeypatch):
            result = operator_commands.upgrade_users_command(
                configuration,
                deployment_id=deployment,
                preview=False,
                operation_id=str(uuid4()),
                operator_name="Test operator",
                reason="Users revamp",
                confirmed_administrators=",".join(
                    sorted([*ADMINS, "early@example.org"])
                ),
            )
            assert result["users_upgrade_applied"] is True
            # Converted, swap still pending: recovery writes a users form.
            assert recover(configuration, deployment, "middle@example.org")[
                "recovery_applied"
            ]
        # The pre-swap SQL admits every Administrator of the users form.
        sessions(*ADMINS, "early@example.org", "middle@example.org")
    # The swap applied (the full migration ran): recovery still runs.
    with task_login(ServiceRole.ADMIN_RECOVERY):
        assert recover(configuration, deployment, "late@example.org")[
            "recovery_applied"
        ]
        # A converted configuration is left alone by a repeat.
        assert operator_commands.upgrade_users_command(
            configuration,
            deployment_id=deployment,
            preview=False,
            operation_id=str(uuid4()),
            operator_name="Test operator",
            reason="Users revamp",
            confirmed_administrators="admin@example.org",
        ) == {"users_upgrade_applied": False, "already_users": True}
    sessions(*ADMINS, "early@example.org", "middle@example.org", "late@example.org")


def backup_command(monkeypatch, run_backup):
    """Run `backup` in this process with ``run_backup`` standing in for the set.

    Process admission, the interlock, the identity check and the off-site
    copy are stubbed (covered elsewhere); the schema check is the real one.
    """
    from contextlib import nullcontext

    from parishkit.stewardship import (
        backup,
        backup_boundaries,
        backup_commands,
        runtime_paths,
        startup_interlock,
    )

    monkeypatch.setattr(backup_commands, "configure_logging", lambda: None)
    monkeypatch.setattr(backup_commands, "load_deployment", lambda path: None)
    monkeypatch.setattr(backup_commands, "_admit_backup_identity", lambda: None)
    monkeypatch.setattr(backup_commands, "_copy_offsite", lambda c: {"state": "x"})
    monkeypatch.setattr(backup_boundaries, "admit_backup_service", lambda c: None)
    monkeypatch.setattr(
        operator_commands, "configure_operator_database", lambda c: None
    )
    monkeypatch.setattr(
        runtime_paths, "RuntimeLayout", lambda c: SimpleNamespace(interlock=None)
    )
    monkeypatch.setattr(
        startup_interlock, "StartupLease", lambda path, offline: nullcontext()
    )
    monkeypatch.setattr(backup, "run_backup", run_backup)


@contextmanager
def base_recovery_login(monkeypatch):
    """The admin-recovery login as database-grants leaves it on the base.

    A definer a closure migration creates (runtime_grants.CLOSURE_FUNCTIONS,
    such as #530's debug-off switch read and clear) is granted only once it
    exists (``pending_functions``), so the login's fixture grants skip each
    one the database lacks; otherwise this is ``task_login``.
    """
    from . import test_background_grants_postgresql as background

    real = background.runtime_functions

    def base_functions(service, **kwargs):
        """The login's definers without the closure ones not created yet."""
        return frozenset(
            function
            for function in real(service, **kwargs)
            if scalar("SELECT to_regprocedure(%s)", [f"public.{function}"])
        )

    with monkeypatch.context() as patch:
        patch.setattr(background, "runtime_functions", base_functions)
        with task_login(ServiceRole.ADMIN_RECOVERY):
            yield


@contextmanager
def base_backup_login(monkeypatch):
    """The backup login as database-grants leaves it on the users upgrade's base.

    A table a closure migration creates (runtime_grants.CLOSURE_TABLES, such
    as #530's backup requests) is granted only once it exists
    (``pending_tables``), so the login's fixture grants skip each one the
    database lacks; otherwise this is ``backup_login``.
    """
    from . import test_background_grants_postgresql as background
    from .test_backup_postgresql import backup_login

    real = background.runtime_grants

    def base_grants(service, **kwargs):
        """The login's grants without the closure tables not created yet."""
        tables, columns = real(service, **kwargs)
        absent = {
            table
            for table in CLOSURE_TABLES
            if scalar("SELECT to_regclass(%s)", [f"public.{table}"]) is None
        }
        return (
            {name: value for name, value in tables.items() if name not in absent},
            {name: value for name, value in columns.items() if name not in absent},
        )

    with monkeypatch.context() as patch:
        patch.setattr(background, "runtime_grants", base_grants)
        with backup_login():
            yield


def test_the_backup_admits_a_database_with_only_the_swap_pending(
    tmp_path, monkeypatch, capsys
):
    """A repeated deploy's step-1 backup runs between the two migrations (#952).

    The schema check is the real one: with only the swap pending the backup
    records its set, whose manifest lists every applied migration but the
    swap, so it restores exactly that state.
    """
    from parishkit.stewardship.cli import main

    from .test_backup_postgresql import FACTS

    initialized(tmp_path, rules())
    listed = []

    def run_backup(configuration, *, record, migrations, recipient):
        """Record one run and keep the migration list the manifest would carry."""
        listed.extend(migrations)
        record(started_at=scalar("SELECT statement_timestamp()"), **FACTS)
        return {
            "database": {"plaintext_bytes": 1},
            "files": {"plaintext_bytes": 1},
            "recipient_fingerprint": FACTS["recipient_fingerprint"],
        }

    backup_command(monkeypatch, run_backup)
    with swap_pending(), base_backup_login(monkeypatch):
        assert main(["backup", "--config", "unused"]) == 0
    assert json.loads(capsys.readouterr().out)["backup_recorded"] is True
    assert listed and USERS_SWAP not in set(map(tuple, listed))


@contextmanager
def restored_copy():
    """A restore of the test database as it is now, as a second database.

    pg_dump and pg_restore are not on the test runner (and the CI runner's
    cannot read a PostgreSQL 18 dump; test_restore_compare_postgresql.py),
    so the server copies the database instead (CREATE DATABASE ... TEMPLATE):
    every table row, function body and grant comes back as a restore brings
    them back. While the context is open the test's connection uses the
    copy; afterwards it is back on the test database and the copy is gone.
    """
    import psycopg

    settings = connection.settings_dict
    original = settings["NAME"]
    copy = f"{original}_restored"
    server = {
        "host": settings["HOST"],
        "port": settings["PORT"],
        "user": settings["USER"],
        "password": settings["PASSWORD"],
        "dbname": "postgres",
        "autocommit": True,
    }
    # A template copy needs no other session on its source.
    connection.close()
    with psycopg.connect(**server) as admin:
        admin.execute(f'DROP DATABASE IF EXISTS "{copy}"')
        admin.execute(f'CREATE DATABASE "{copy}" TEMPLATE "{original}"')
    settings["NAME"] = copy
    try:
        yield
    finally:
        connection.close()
        settings["NAME"] = original
        with psycopg.connect(**server) as admin:
            admin.execute(f'DROP DATABASE "{copy}" WITH (FORCE)')


def test_a_swap_pending_backup_restores_into_the_same_state(
    tmp_path, monkeypatch, capsys
):
    """Back up with only the swap pending, restore, and recover Admin access.

    The set's manifest lists what the database had applied; restore-check,
    against this image's real migration files, calls it swap_pending (exit
    4), not a mismatch. The restored database is in the same state: only the
    swap pending (the services' check refuses it, the recovery commands'
    admits it) and the pre-swap function bodies installed. recover-admin
    runs there, and the recovered Administrator opens an Admin session
    through the restored pre-swap guard.
    """
    from parishkit.stewardship import restore_check
    from parishkit.stewardship.cli import main

    from .test_backup_postgresql import FACTS

    store, _, _ = initialized(tmp_path, rules())
    deployment = str(SystemConfiguration.objects.get().pk)
    manifest = tmp_path / "manifest.json"

    def run_backup(configuration, *, record, migrations, recipient):
        """Write the set's manifest as the backup does, and record the run."""
        manifest.write_text(
            json.dumps(
                {
                    "version": 1,
                    "application_version": FACTS["application_version"],
                    "image": "ghcr.io/example/stewardship@sha256:" + "c" * 64,
                    "migrations": sorted([app, name] for app, name in migrations),
                }
            )
        )
        record(started_at=scalar("SELECT statement_timestamp()"), **FACTS)
        return {
            "database": {"plaintext_bytes": 1},
            "files": {"plaintext_bytes": 1},
            "recipient_fingerprint": FACTS["recipient_fingerprint"],
        }

    backup_command(monkeypatch, run_backup)
    with swap_pending():
        with base_backup_login(monkeypatch):
            assert main(["backup", "--config", "unused"]) == 0
        assert json.loads(capsys.readouterr().out)["backup_recorded"] is True
        # The restore: a copy of the database the set was taken from.
        with restored_copy():
            code, report = restore_check.check(manifest)
            assert (code, report["result"]) == (
                restore_check.SWAP_PENDING,
                "swap_pending",
            )
            assert report["not_in_backup"] == sorted(
                map(list, restore_check.image_swap_closure())
            )
            assert report["unknown_to_image"] == []
            # The same pending state as before the backup.
            with pytest.raises(ConfigError, match="do not match"):
                runtime_database.require_current_schema()
            runtime_database.require_current_schema(users_swap_pending=True)
            assert (
                scalar(
                    "SELECT count(*) FROM django_migrations WHERE app=%s AND name=%s",
                    list(USERS_SWAP),
                )
                == 0
            )
            assert all(reads_domain_rules(name) for name in REPLACED)
            # Admin-access recovery works on the restored database.
            configuration = offline_commands(monkeypatch, store)
            with base_recovery_login(monkeypatch):
                assert recover(configuration, deployment, "restored@example.org")[
                    "recovery_applied"
                ]
            portal = identity("restored@example.org", signed_in=False)
            assert forge(portal.pk).principal_id == portal.pk
    # The test database itself is back with the swap applied.
    runtime_database.require_current_schema()


@contextmanager
def base_partly_applied():
    """Swap pending and one migration outside its closure pending too.

    The state a `migrate --users-upgrade-base` that stopped partway (a
    failure, a time limit) leaves: the newest migration of another app is
    not yet recorded. Its row is put back afterwards.
    """
    from django.db.migrations.loader import MigrationLoader

    other = next(
        key
        for key in sorted(MigrationLoader(connection).graph.leaf_nodes())
        if key[0] != USERS_SWAP[0]
    )
    with swap_pending():
        with connection.cursor() as cursor:
            cursor.execute(
                "DELETE FROM django_migrations WHERE app=%s AND name=%s "
                "RETURNING applied",
                list(other),
            )
            (applied,) = cursor.fetchone()
        try:
            yield
        finally:
            with connection.cursor() as cursor:
                cursor.execute("RESET SESSION AUTHORIZATION")
                cursor.execute(
                    "INSERT INTO django_migrations (app, name, applied) "
                    "VALUES (%s, %s, %s)",
                    [*other, applied],
                )


def test_a_resumed_users_upgrade_reuses_the_earlier_runs_backup(
    tmp_path, monkeypatch, capsys
):
    """A base migration that stopped partway: the repeat reuses the backup.

    With migrations outside the swap pending, `backup` refuses as before;
    `backup --users-upgrade-resume` reports the newest recorded backup (the
    earlier run's step 1, within migrate's 24-hour window) instead of
    taking one, with that set's own off-site state. Without such a backup it
    still refuses; with only the swap pending it backs up as usual.
    """
    from datetime import timedelta

    from parishkit.stewardship.cli import main
    from parishkit.stewardship.jobs.backup_models import BackupUpload

    from .test_backup_postgresql import FACTS, recorded

    initialized(tmp_path, rules())
    taken = []

    def run_backup(configuration, *, record, migrations, recipient):
        """Record one fresh run; the reuse path never reaches this."""
        taken.append(sorted(migrations))
        record(started_at=scalar("SELECT statement_timestamp()"), **FACTS)
        return {
            "database": {"plaintext_bytes": 1},
            "files": {"plaintext_bytes": 1},
            "recipient_fingerprint": FACTS["recipient_fingerprint"],
        }

    def backup(*flags):
        """Run the command as the backup login; its exit code and JSON."""
        with base_backup_login(monkeypatch):
            code = main(["backup", "--config", "unused", *flags])
        out = capsys.readouterr().out
        return code, json.loads(out) if out else None

    backup_command(monkeypatch, run_backup)
    resume = "--users-upgrade-resume"
    with base_partly_applied():
        # No recent backup: nothing to reuse, so it refuses either way.
        recorded(age=timedelta(hours=25), manifest_digest="d" * 64)
        assert backup(resume) == (2, None)
        # The earlier run's step-1 backup, copied off-site.
        recorded(manifest_digest="e" * 64)
        assert backup() == (2, None)
        code, report = backup(resume)
        assert code == 0
        assert report["backup_recorded"] is False
        assert report["backup_reused"] is True
        assert report["reused_backup"]["manifest_digest"] == "e" * 64
        assert report["pending_base_migrations"] == 1
        # No off-site destination in this configuration and no copy row.
        assert report["offsite"] == {"state": "not_configured"}
        with transaction.atomic():
            BackupUpload.objects.create(
                state="uploaded",
                set_name="20261010T000000Z",
                manifest_digest="e" * 64,
                folder_id="f" * 20,
            )
        assert backup(resume)[1]["offsite"] == {"state": "uploaded"}
        assert taken == []
    with swap_pending():
        # Only the swap pending: a fresh backup, flag or not.
        code, report = backup(resume)
        assert (code, report["backup_recorded"]) == (0, True)
        assert "backup_reused" not in report
    assert len(taken) == 1

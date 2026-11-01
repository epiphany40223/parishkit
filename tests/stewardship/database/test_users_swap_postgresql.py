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


LEADER_SCOPE = "public.stewardship_ministry_leader_scope_v1(uuid)"


def as_swap_left_it(cursor):
    """Put 0052's leader scope body back where 0179's stands (#2166).

    0179 has since replaced the body with its ``?|`` written
    ``OPERATOR(pg_catalog.?|)``, which 0052's check (matching 0052's text)
    refuses. Only that body is swapped back, keeping the function's current
    rights, so a test that made it SECURITY INVOKER or installed an older
    body still sees what it set up. Callers roll the change back.
    """
    cursor.execute(
        "SELECT prosecdef FROM pg_proc WHERE oid=to_regprocedure(%s) "
        "AND strpos(prosrc, 'OPERATOR(pg_catalog.?|)')>0",
        [LEADER_SCOPE],
    )
    row = cursor.fetchone()
    if row is not None:
        cursor.execute(_replaced(SWAP, "stewardship_ministry_leader_scope_v1"))
        if not row[0]:
            cursor.execute(f"ALTER FUNCTION {LEADER_SCOPE} SECURITY INVOKER")


def check_refuses(message):
    """Run the DO block in a savepoint, expecting it to refuse with ``message``."""
    with (
        pytest.raises(DatabaseError, match=message),
        transaction.atomic(),
        connection.cursor() as cursor,
    ):
        as_swap_left_it(cursor)
        cursor.execute(CHECK)


def check_passes():
    """Run the DO block in a savepoint that is always rolled back."""
    with transaction.atomic():
        with connection.cursor() as cursor:
            as_swap_left_it(cursor)
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
# guard lacks 0118's phrase, so ``CLOSURE_REPLACED_ONLY`` then puts 0118's
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
# ``CLOSURE_REPLACED_ONLY``, which runs after it, runs the latest
# replacement statement alone (0118's, below); the one-time correction does
# not run again.
# 0115 (#530) creates three tables (in CLOSURE_TABLES) and their guards
# (``CLOSURE_CREATED``), and replaces nothing.
# 0116 (#1562) creates Sent mail's two recipient reads (in
# runtime_grants.CLOSURE_FUNCTIONS), which go, and an index on the outbox
# renderings, which stays: its file makes the index only when missing, so it
# installs whole again.
# 0117 (#765) creates only the pg_trgm extension and six indexes, each only
# when missing, and replaces nothing; the swap drops none of them, so
# nothing is registered.
# 0118 (#1603) creates nothing either: it replaces the derived guard in place
# (gender By hand, on 0113's body) and re-marks open gender rows. Its body
# keeps 0113's rules, so ``CLOSURE_REPLACED_ONLY`` runs 0118's replacement
# statement alone, in place of 0113's.
# 0119 (#1591) creates nothing: it replaces the baseline packet selection
# and 0064's report selection in place, which 0094's rerun
# (``CLOSURE_REPLACED``) brings back older, so ``CLOSURE_REPLACED`` runs
# 0119's file after it; since 0140 (#1718) replaced both again, 0140's.
# 0121 (#1658) creates nothing: it replaces the
# occurrence guard (0005's, before the closure), the digest slot check and
# the weekly page proof (both the baseline's) in place. No closure file
# installs any of them again, so their bodies survive the swap and nothing
# is registered.
# 0122 (#1620) creates nothing: it replaces the safe context in place again,
# after 0109, with the automatic view key, so ``CLOSURE_REPLACED_ONLY`` now
# names 0122's replacement, whose body keeps 0109's keys.
# 0123 (#1550) creates nothing: it drops 0013's fresh-principal check (before
# the closure, so no closure file makes it again; IF EXISTS lets the file run
# again) and replaces the baseline's portal session guard in place, which no
# closure file installs again, so nothing is registered.
# 0124 (#1762) creates nothing: it rewords guards in place. Four of them
# (0114's) and 0104's setup credential audit come back older from closure
# files, so ``CLOSURE_REPLACED_ONLY`` runs 0124's replacements of those;
# the rest (baseline guards no closure file installs again) survive.
# 0132 (#1689, provisional number) creates the refresh check and start
# tables (in CLOSURE_TABLES) with their two guards and replaces nothing; all
# go, so its file installs whole again.
# 0125 (#1763) adds a request column (IF NOT EXISTS, so its file runs again)
# and a definer check granted to web and three installers
# (runtime_grants.CLOSURE_FUNCTIONS, so ``closure_functions_absent`` drops it
# and installs the file whole again), drops 0114's ended-instant check (IF
# EXISTS; 0114 is no longer in CLOSURE_FUNCTIONS, so nothing makes it
# again) and replaces three bodies (0124's intake check, 0114's state guard,
# the baseline's request guard) that no closure file installs again, so
# nothing else is registered.
# 0133 (#1737, provisional number) creates nothing: it replaces 0060's
# resolution guard and 0132's refresh check guard, which their files bring
# back older (``CLOSURE_REPLACED`` runs 0133's file again), and the derived
# guard last (``CLOSURE_REPLACED_ONLY``).
# 0137 (#1738, provisional number) creates the census refresh source and
# replaces 0132's refresh check guard. The source goes, so its file installs
# whole again after 0132's, putting its guard body back over 0132's.
# 0138 (#1749, provisional number) creates the address key, widens 0132's
# outcome check, adds a column to 0060's resolution table and replaces the
# census refresh source and the resolution, refresh check and derived
# guards. The key goes and both tables are made again, so its file installs
# whole again after 0060's, 0132's and 0137's, putting its bodies back over
# theirs; the derived guard's later reruns are undone by
# ``CLOSURE_REPLACED_ONLY``.
# 0142 (#1557) creates the participation Summary capture table (in
# CLOSURE_TABLES) with its guard and the request table's deferred check
# (``CLOSURE_CREATED``), and replaces 0109's discard in place, which 0109's
# file brings back older, so ``CLOSURE_OLD_BODIES`` puts 0142's back.
# 0143 (#1705, provisional number) creates nothing: it adds what a Family
# form showed (``reviewed_view``, ``shown_items`` and the check pairing
# them) to the form baseline and the Submission. While the swap is pending
# ``before_reviewed_form_shown_items`` drops the four columns (their checks
# go with them), and ``CLOSURE_ALTERED`` runs the file again afterwards.
# 0146 (#1840) creates nothing: it replaces 0139's
# resolution guard, 0060's resolution effect check and 0138's derived guard
# in place, which the reruns of 0060's, 0133's and 0138's files bring back
# older, so ``CLOSURE_REPLACED`` runs its file again after theirs and
# ``CLOSURE_REPLACED_ONLY`` puts its derived guard back last.
# 0147 (#1840) creates nothing: it alters 0129's
# resolution table (in CLOSURE_TABLES, so made again by 0129's file) and
# replaces 0129's three guards in place; every statement can run again, so
# ``CLOSURE_REPLACED`` runs its file again after 0129's.
# 0148 (#1840) creates nothing: it replaces 0061's
# roster entry guard and 0131's request and revision guards in place, so
# ``CLOSURE_REPLACED`` runs its file again after theirs.
# 0149 (#1840) creates nothing: it widens 0127's pledge
# review state check and replaces 0135's pledge review guard in place, which
# the reruns of 0127's and 0135's files bring back older, so
# ``CLOSURE_REPLACED`` runs its file again after theirs.
# 0150 and 0151 (#1840) create nothing: each replaces
# one of 0126's guards in place (the information revision guard and the
# limitation review guard), which 0126's rerun brings back older, so
# ``CLOSURE_REPLACED`` runs each file again after it.
# 0152 (#1866) creates only the audit actor index, only when missing, and
# replaces nothing; the swap drops none of it, so nothing is registered.
# 0157 (#1867) creates the two person identity reads (in
# runtime_grants.CLOSURE_FUNCTIONS), which go, and replaces nothing, so its
# file installs whole again.
# 0158 (#1889) creates nothing: it replaces six guards in place on 0130's,
# 0146's, 0148's, 0149's, 0150's and 0151's bodies, which the reruns of
# those files (``CLOSURE_REPLACED``) bring back older, so
# ``CLOSURE_REPLACED_LAST`` runs its file again after them.
# 0159 (#1890, provisional number) creates nothing: it replaces seven
# functions in place (0158's resolution guard, 0153's derived guard, 0124's
# publication confirmation guard, 0154's Ministry request guard, 0138's
# refresh check guard, 0136's pledge stage selection and 0135's pledge
# refresh check guard), each copied whole from that latest body, which the
# reruns above bring back; so ``CLOSURE_REPLACED_ONLY`` puts each 0159 body
# back last, by its replacement statement alone.
# 0160 (#1895, provisional number) creates nothing: it replaces 0159's
# derived guard and 0153's submission effects check in place, so
# ``CLOSURE_REPLACED_ONLY`` puts each 0160 body back last, after 0159's.
# 0163 (#1909) creates nothing: it replaces 0160's derived guard in place,
# so ``CLOSURE_REPLACED_ONLY`` puts its body back last, after 0160's.
# 0166 (#1569) creates nothing: it replaces 0103's campaign pointer function
# in place, as 0103 did 0091's. No closure file installs that function
# again, so its body survives the swap and nothing is registered.
# 0169 (#1594) creates one definer, granted to web
# (runtime_grants.CLOSURE_FUNCTIONS), and replaces nothing.
# 0170 (#2008) creates nothing: it replaces 0160's submission effects check
# and 0163's derived guard in place, so ``CLOSURE_REPLACED_ONLY`` puts each
# body back last, after 0160's and 0163's.
# 0172 (#1932) creates one definer, granted to the general worker
# (runtime_grants.CLOSURE_FUNCTIONS), and replaces nothing.
# 0175 (#1393) creates nothing: it replaces 0166's campaign pointer function
# in place, as 0166 did 0103's, and the baseline's source window function;
# no closure file installs either again, so their bodies survive the swap.
# It also replaces 0110's financial and 0094's and 0100's information
# selections, which those files' reruns bring back older, so
# ``CLOSURE_REPLACED_ONLY`` puts 0175's bodies back last.
# 0176 (#269) creates one definer, granted to the Google Workspace installer
# (runtime_grants.CLOSURE_FUNCTIONS), and drops and adds the access check's
# failure kind CHECK again; its file installs whole again (the CHECK is
# still there, so dropping and adding it again is harmless).
# 0177 (#2163) creates nothing: it replaces 0174's setup load organization
# check in place as SECURITY DEFINER, which 0174's whole rerun brings back
# as its caller's, so ``CLOSURE_REPLACED_ONLY`` puts 0177's body back last.
# 0179 (#2166) creates nothing: it replaces fourteen definer functions in
# place with their latest bodies, operators qualified, which the reruns and
# restores above bring back older; ``CLOSURE_REPLACED_ONLY`` puts each 0179
# body back last (with its SECURITY DEFINER alter), 0177's among them.
# 0181 (#2177) adds the reviewer's share methods to 0127's pledge reviews
# and replaces 0158's pledge review guard and 0155's pledge item selection
# in place; the swap drops the table (runtime_grants.CLOSURE_TABLES), and
# 0127's reinstall makes it again without the column and with older bodies.
# Its file must run whole again only after 0149's rerun (which also widens
# 0127's state check) and 0158's, both triggered by the guard's phrases,
# which 0181's body holds too: so ``CLOSURE_REPLACED_LAST`` runs it, keyed
# by the item selection (0127's reinstall puts back a body without its
# rule), after 0158's entry.
# 0182 (#2196) creates nothing: it replaces 0175's financial selection in
# place, which 0110's rerun and 0175's restore bring back older, so its
# ``CLOSURE_REPLACED_ONLY`` entry takes the place of 0175's and restores
# 0182's body instead.
# 0183 (#2201) creates the setup parish data check table (in CLOSURE_TABLES)
# with its three functions (below), adds the readiness record's check column
# (runtime_grants.CLOSURE_COLUMNS; ``before_setup_source_check`` drops it
# and restores the staged load's NOT NULL while the swap is pending) and
# replaces the readiness and attempt guards and the installers' readiness
# check in place. Its file, whole, puts all of it back, last.
# 0184 (#2202) creates one definer, granted to the web and general worker
# logins (runtime_grants.CLOSURE_FUNCTIONS); it replaces nothing.
# 0185 (#2202, part 2) creates two granted definers
# (runtime_grants.CLOSURE_FUNCTIONS) and the trigger that records a fact
# generation's pledge Edits, adds columns its file adds again only if
# missing (so it may run whole again), and replaces in place functions
# that 0095's, 0156's and 0161's reinstalled files bring back older, so
# ``CLOSURE_REPLACED_ONLY`` restores 0185's bodies last.
# 0187 (#2228) creates nothing: it widens 0106's event check in place,
# which 0106's rerun (``CLOSURE_REPLACED_LAST``) brings back older, so
# ``CLOSURE_CONSTRAINTS`` runs 0187's file again after it.
# 0189 (#2293) creates two plain reads (``CLOSURE_CREATED``: neither is
# granted nor a definer, but the full migration creates them again) and
# replaces 0159's refresh check guard in place, which the reruns above
# bring back older, so ``CLOSURE_REPLACED_ONLY`` restores 0189's guard
# instead of 0159's.
# 0190 (#2295) and 0191 (#2300) create nothing: each replaces 0173's
# delivery resolution guard in place, so ``CLOSURE_REPLACED_ONLY`` puts
# 0191's body back last.
# 0192 (#2298) creates nothing: it replaces the baseline's fact set guard,
# which no closure file installs again, and the exact export request and
# report email preparation guards, which 0185's (and 0156's and 0161's)
# reruns bring back older, so its ``CLOSURE_REPLACED_ONLY`` entries restore
# 0192's bodies instead, the preparation guard's in 0185's entry's place.
# 0193 (#235) creates nothing: it replaces 0176's backup destination
# definer and the operational log writer in place, which 0176's whole rerun
# (``CLOSURE_CREATED``) and 0106's (``CLOSURE_REPLACED_LAST``) bring back
# without the uploader's login, so ``CLOSURE_REPLACED_ONLY`` puts 0193's
# bodies back last.
# 0195 (#235, contract) creates and replaces nothing and alters no table:
# it only revokes the backup login's two table INSERTs (tolerated on the
# base through runtime_grants.CLOSURE_TABLE_REVOKES), and its revokes are
# no-ops when run again, so nothing is registered for it.
# 0196 (#2331) creates one plain read (``CLOSURE_CREATED``: neither granted
# nor a definer; its file, whole, puts it back) and replaces in place the
# report snapshot guard and the fact runtime binding, which 0185's entries
# restore older, and the source pin guard, which 0161's rerun brings back
# older, so its ``CLOSURE_REPLACED_ONLY`` entries restore 0196's bodies,
# the first two in 0185's entries' places.
# 0197 (#2335) creates nothing: it replaces 0194's Financial and both
# Additional information selections in place, so its ``CLOSURE_REPLACED_ONLY``
# entries restore 0197's bodies instead, in the place of those 0175 began.
# The columns 0185 (#2202) adds, by table.
SERIES_IN_FORCE_COLUMNS = (
    ("stewardship_daily_fact_set", "pledge_edits"),
    ("stewardship_daily_fact_set", "review_watermark"),
    ("stewardship_fact_demand", "requested_review_watermark"),
    ("stewardship_exact_export_request", "review_watermark"),
    ("stewardship_report_email_preparation", "review_watermark"),
)
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
    **dict.fromkeys(
        (
            "public.stewardship_delivery_ids_for_address_v1(text,uuid)",
            "public.stewardship_delivery_page_recipients_v1(uuid[],uuid)",
        ),
        ("stewardship_jobs", "0034_sent_mail_recipients"),
    ),
    **dict.fromkeys(
        (
            "public.stewardship_address_people_v1(text,uuid)",
            "public.stewardship_family_users_v1(bigint,uuid)",
        ),
        ("stewardship_accounts", "0031_person_identity_reads"),
    ),
    # 0120 (#1592) creates the Ministry leader email tables (in
    # CLOSURE_TABLES) and these functions, and replaces the four outbox
    # guards and the two task-type maps in place with bodies that keep every
    # earlier rule, so its file installs whole again, after 0077's file has
    # put the older task maps back.
    **dict.fromkeys(
        (
            "public.stewardship_leader_email_facts_v1(jsonb)",
            "public.stewardship_leader_emails_v1(text,uuid)",
            "public.stewardship_leader_email_request_guard_v1()",
            "public.stewardship_leader_email_cohort_immutable_v1()",
            "public.stewardship_leader_email_recipient_immutable_v1()",
            "public.stewardship_leader_email_cohort_binding_v1()",
            "public.stewardship_leader_email_render_v1(jsonb,uuid,uuid,text,jsonb)",
            "public.stewardship_leader_email_recipient_binding_v1()",
            "public.stewardship_leader_email_prepare_write_v1(text,jsonb)",
            "public.stewardship_leader_email_prepare_receipt_v1()",
            "public.stewardship_leader_email_dispatch_live_v1(uuid)",
            "public.stewardship_leader_email_dispatch_write_v1(text,jsonb,jsonb)",
        ),
        ("stewardship_jobs", "0035_leader_emails"),
    ),
    **dict.fromkeys(
        (
            "public.stewardship_refresh_check_guard_v1()",
            "public.stewardship_refresh_check_start_guard_v1()",
        ),
        ("stewardship_workflows", "0004_refresh_checks"),
    ),
    # 0126 (#1685, provisional number) creates the Limitations review table
    # (in CLOSURE_TABLES) with its guard, deferred audit check, answer and
    # in-force reads and the report's v2 selection; all of them go, and the
    # column it adds to the information revisions goes too
    # (``before_information_limitation_review``), so its file installs whole
    # again, the revision guard's replacement included.
    **dict.fromkeys(
        (
            "public.stewardship_limitation_review_guard_v1()",
            "public.stewardship_limitation_review_effect_v1()",
            "public.stewardship_limitation_answer_v1(jsonb,text,text)",
            "public.stewardship_limitation_stated_v1(jsonb,text,text)",
            "public.stewardship_limitation_in_force_v1(uuid)",
            "public.stewardship_limitations_report_v2(uuid,jsonb)",
        ),
        ("stewardship_responses", "0013_information_limitation_review"),
    ),
    # 0127 (#1686, provisional number) creates the pledge review table (in
    # CLOSURE_TABLES) with these functions; all go, so its file installs
    # whole again.
    **dict.fromkeys(
        (
            "public.stewardship_pledge_answer_v1(jsonb,numeric)",
            "public.stewardship_pledge_review_states_v1(uuid,uuid)",
            "public.stewardship_pledge_review_guard_v1()",
            "public.stewardship_pledge_review_effect_v1()",
        ),
        ("stewardship_reports", "0032_pledge_review"),
    ),
    # 0129 (#1710) creates the census addition tables (in CLOSURE_TABLES)
    # with their guards and deferred audit checks, and replaces nothing, so
    # its file installs whole again.
    **dict.fromkeys(
        (
            "public.stewardship_census_addition_guard_v1()",
            "public.stewardship_census_addition_effect_v1()",
            "public.stewardship_census_addition_resolution_guard_v1()",
            "public.stewardship_census_addition_resolution_effect_v1()",
        ),
        ("stewardship_responses", "0015_census_additions"),
    ),
    # 0134 (#1704, provisional number) creates the pledge refresh check table
    # (in CLOSURE_TABLES) with its guard and the upcoming pledge selection,
    # and replaces 0127's pledge review guard, which 0127's own reinstall
    # puts back first; all go, so its file installs whole again after 0127's.
    **dict.fromkeys(
        (
            "public.stewardship_pledge_upcoming_v1(uuid,uuid,bigint)",
            "public.stewardship_pledge_refresh_items_v1(uuid,uuid)",
            "public.stewardship_pledge_refresh_check_guard_v1()",
        ),
        ("stewardship_reports", "0033_pledge_refresh_checks"),
    ),
    # 0135 (#1747, provisional number) creates the pledge open check table
    # (in CLOSURE_TABLES) with its guard, the stage selection and the
    # worker's v2 selection; it alters 0134's check outcomes and 0127's
    # review table (both made again first, being closure tables) and
    # replaces both earlier guards, which their own reinstalls put back
    # first; all go, so its file installs whole again after 0134's.
    **dict.fromkeys(
        (
            "public.stewardship_pledge_stages_v1(uuid,uuid)",
            "public.stewardship_pledge_refresh_items_v2(uuid,uuid,uuid)",
            "public.stewardship_pledge_open_check_guard_v1()",
        ),
        ("stewardship_reports", "0034_pledge_conflicts"),
    ),
    "public.stewardship_refresh_census_source_v1(uuid,uuid,text,text,text)": (
        "stewardship_workflows",
        "0005_census_refresh_sources",
    ),
    "public.stewardship_refresh_address_key_v1(jsonb)": (
        "stewardship_responses",
        "0019_published_refresh_conflicts",
    ),
    # 0141 (#1670, provisional number) creates the added Member link table
    # (in CLOSURE_TABLES), its guard and the definer candidates function;
    # all go, so its file installs whole again (and replaces the two
    # Ministry selections; see CLOSURE_REPLACED_ONLY).
    **dict.fromkeys(
        (
            "public.stewardship_added_member_candidates_v1(uuid,uuid)",
            "public.stewardship_added_member_link_guard_v1()",
        ),
        ("stewardship_responses", "0021_added_member_links"),
    ),
    # 0142 (#1557) creates the participation Summary capture table (in
    # CLOSURE_TABLES) with its guard and the request table's deferred check;
    # all go, so its file installs whole again, after 0109's, whose discard
    # it replaces (``CLOSURE_OLD_BODIES`` puts it back after 0109's file
    # runs again on its own).
    **dict.fromkeys(
        (
            "public.stewardship_participation_export_capture_v1()",
            "public.stewardship_participation_export_summary_v1()",
        ),
        ("stewardship_reports", "0036_participation_summary"),
    ),
    # 0145 (#1562) creates only Sent mail's Member-name read.
    "public.stewardship_delivery_page_member_names_v1(uuid[],uuid)": (
        "stewardship_jobs",
        "0036_page_member_names",
    ),
    # 0156 (#1786) creates the report email tables (in
    # CLOSURE_TABLES) and these functions, and replaces the two task-type
    # maps with 0120's bodies plus report_email_prepare, so its file installs
    # whole again after 0120's.
    **dict.fromkeys(
        (
            "public.stewardship_report_next_send_v1(jsonb,date,date,date)",
            "public.stewardship_report_slot_due_v1(jsonb,date,date,text,text)",
            "public.stewardship_report_due_v1(uuid,text)",
            "public.stewardship_report_email_scope_v1(uuid,uuid,uuid,text,uuid)",
            "public.stewardship_report_email_live_v1(uuid,uuid,bigint,uuid)",
            "public.stewardship_report_email_page_v1(jsonb,jsonb)",
            "public.stewardship_report_email_predecessors_v1(uuid,text,uuid)",
            "public.stewardship_report_email_replacement_v1(jsonb)",
            "public.stewardship_report_email_slots_v1(uuid,text)",
            "public.stewardship_report_email_inputs_v1("
            "jsonb,jsonb,timestamp with time zone,date,date,text)",
            "public.stewardship_report_email_cohort_v1(jsonb,uuid)",
            "public.stewardship_report_email_current_v1(uuid,text,text,text)",
            "public.stewardship_report_email_label_v1(text,text,text)",
            "public.stewardship_report_email_skipped_v1(uuid)",
            "public.stewardship_report_email_requests_v1("
            "uuid,uuid,timestamp with time zone)",
            "public.stewardship_report_email_preparation_guard_v1()",
            "public.stewardship_report_email_source_commit_v1()",
            "public.stewardship_report_email_snapshot_guard_v1()",
            "public.stewardship_report_email_snapshot_commit_v1()",
            "public.stewardship_report_email_recipient_guard_v1()",
            "public.stewardship_report_email_render_v1(jsonb,uuid,text,text,text)",
            "public.stewardship_report_email_write_admitted_v1(text,jsonb,jsonb)",
            "public.stewardship_report_email_mail_write_v1(uuid,text,jsonb,jsonb)",
            "public.stewardship_report_email_mail_commit_v1()",
            "public.stewardship_report_email_reason_v1(text)",
            "public.stewardship_report_email_completion_v1(jsonb)",
            "public.stewardship_report_email_settle_v1()",
            "public.stewardship_report_email_dispatch_live_v1(uuid,boolean)",
            "public.stewardship_report_email_seed_v1(uuid,uuid)",
            "public.stewardship_report_email_dispatch_write_v1(text,jsonb,jsonb)",
            "public.stewardship_report_email_manual_guard_v1()",
            "public.stewardship_report_email_manual_allocate_v1()",
            "public.stewardship_report_email_immutable_v1()",
            "public.stewardship_report_email_pin_retained_v1()",
        ),
        ("stewardship_reports", "0040_report_pipeline"),
    ),
    # 0161 (#1786) creates these two and replaces many base
    # and closure functions and views in place (``before_report_conversion``
    # puts those back while the swap is pending), so its file installs
    # whole again after 0156's.
    **dict.fromkeys(
        (
            "public.stewardship_digest_work_open_v1()",
            "public.stewardship_report_slots_v1(uuid,timestamp with time zone)",
        ),
        ("stewardship_reports", "0041_report_conversion"),
    ),
    # 0162 (#1835) creates the coverage comparison, adds a
    # column to 0156's recipient table (in CLOSURE_TABLES) and replaces
    # 0156's recipient guard and 0161's completion view. All three files'
    # objects are dropped, so 0156's file runs again first, then 0161's
    # (their entries are above, in that order), and then 0162's, whole,
    # puts back the column, guard, view and function.
    "public.stewardship_report_email_prior_v1(uuid,text)": (
        "stewardship_reports",
        "0042_report_recovery_coverage",
    ),
    # 0164 (#1872) creates the coverage decision table (in
    # CLOSURE_TABLES) with its guard and replaces 0162's comparison; after
    # 0171's file runs again (its entry is above), 0164's, whole, puts back
    # the table, guard and its comparison. 0165's waiting read is in
    # runtime_grants.CLOSURE_FUNCTIONS, which installs it again on its own.
    "public.stewardship_report_email_coverage_decision_guard_v1()": (
        "stewardship_reports",
        "0043_report_coverage_decisions",
    ),
    # 0167 (#1925) creates only this Administrator-only read and replaces
    # nothing, so its file installs whole again.
    "public.stewardship_address_leader_scope_v1(text,uuid)": (
        "stewardship_accounts",
        "0033_address_leader_scope",
    ),
    # 0169 (#1594): the Ministry requests Leaders column's definer.
    "public.stewardship_ministry_leader_coverage_v1(text,uuid)": (
        "stewardship_reports",
        "0045_ministry_leader_coverage",
    ),
    # 0172 (#1932): the Family campaign statistics definer.
    "public.stewardship_family_campaign_analyze_v1(uuid)": (
        "stewardship_campaigns",
        "0018_family_statistics",
    ),
    # 0173 (#2036) creates the report email test table (in CLOSURE_TABLES)
    # with these functions, its triggers on the outbox and task tables, and
    # replaces six of 0161's functions in place. After 0161's file runs again
    # (its entry is above), 0173's, whole, puts all of it back.
    **dict.fromkeys(
        (
            "public.stewardship_report_email_test_mutable_v1()",
            "public.stewardship_report_email_test_settled_v1(uuid)",
            "public.stewardship_report_email_test_guard_v1()",
            "public.stewardship_report_email_test_settle_v1()",
            "public.stewardship_report_email_test_live_v1(uuid)",
            "public.stewardship_report_email_test_render_v1(jsonb,uuid)",
            "public.stewardship_report_email_test_write_v1(text,jsonb)",
            "public.stewardship_report_email_test_dispatch_live_v1(uuid)",
            "public.stewardship_report_email_test_dispatch_write_v1(text,jsonb,jsonb)",
        ),
        ("stewardship_reports", "0047_report_email_test"),
    ),
    # 0174 (#1554) creates the setup organization lookup table (in
    # CLOSURE_TABLES) with these functions and two triggers on the setup
    # attempt, and replaces the setup credential guard and the safe context
    # in place (see their CLOSURE_REPLACED_ONLY entries). Its file, whole,
    # puts all of it back.
    **dict.fromkeys(
        (
            "public.stewardship_setup_parishsoft_lookup_mutable_v1()",
            "public.stewardship_setup_lookup_list_valid_v1(jsonb)",
            "public.stewardship_setup_owner_live_v1(uuid)",
            "public.stewardship_setup_parishsoft_lookup_guard_v1()",
            "public.stewardship_setup_scrub_lookups_v1()",
            "public.stewardship_setup_load_organization_v1()",
        ),
        ("stewardship_accounts", "0035_parishsoft_organization_choice"),
    ),
    # 0176 (#269): the Test access re-check's backup destination definer.
    "public.stewardship_backup_destination_v1()": (
        "stewardship_jobs",
        "0038_probe_settings_changed",
    ),
    # 0178 (#2123) creates the review wizard's run table (in CLOSURE_TABLES)
    # and its invoker-rights guard; its file, whole, puts both back.
    "public.stewardship_review_run_guard_v1()": (
        "stewardship_reports",
        "0048_review_run",
    ),
    # 0183 (#2201): the setup parish data check's guards, its end-of-setup
    # discard and the setup attempt's freeze check.
    **dict.fromkeys(
        (
            "public.stewardship_setup_source_check_mutable_v1()",
            "public.stewardship_setup_source_check_guard_v1()",
            "public.stewardship_setup_discard_checks_v1()",
            "public.stewardship_setup_freeze_check_v1()",
        ),
        ("stewardship_accounts", "0038_setup_source_check"),
    ),
    # 0184 (#2202): the statistics capture's pledge Edits in force.
    "public.stewardship_pledge_edits_in_force_v1(uuid)": (
        "stewardship_reports",
        "0051_statistics_in_force",
    ),
    # 0185 (#2202): the fact generations' pledge review watermark and
    # Edits, and the trigger that records the Edits.
    **dict.fromkeys(
        (
            "public.stewardship_pledge_review_watermark_v1(uuid)",
            "public.stewardship_fact_pledge_edits_v1(uuid)",
            "public.stewardship_fact_pledge_edits_record_v1()",
        ),
        ("stewardship_reports", "0052_series_in_force"),
    ),
    # 0189 (#2293): the Ministry roster date and the roster read as of a
    # date; its file, whole, puts both back with its refresh check guard.
    **dict.fromkeys(
        (
            "public.stewardship_ministry_roster_date_v1(uuid,uuid)",
            "public.stewardship_response_ministry_held_v1(uuid,uuid,text,integer,date)",
        ),
        ("stewardship_responses", "0033_commitment_roster_date"),
    ),
    # 0194 (#2320) creates the name series, last name and two-surname
    # name helpers; its file's other statements replace in place, so
    # running it whole again is safe. It comes last: it replaces the
    # Additional information selection v2, which 0064's file (above)
    # makes again, and records the grants of what it replaces first.
    **dict.fromkeys(
        (
            "public.stewardship_name_series_v1(text[])",
            "public.stewardship_family_last_name_v1(jsonb)",
            "public.stewardship_family_heads_name_v2(text,boolean,text[])",
        ),
        ("stewardship_reports", "0054_mixed_surname_names"),
    ),
    # 0196 (#2331): the report email's open requests without the Ignored
    # ones, each with its stage; its file, whole, puts it back with its
    # replaced guards.
    "public.stewardship_report_email_requests_v2(uuid,uuid,timestamp with time zone)": (
        "stewardship_reports",
        "0055_report_email_population",
    ),
}


# The CLOSURE_TABLES entries that are views: the report email completion
# proof (0156, #1786).
CLOSURE_VIEWS = {"stewardship_report_email_completion_ready"}


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
    # 0171 (#1834) drops the digest write functions and triggers that
    # ``before_retire_digests`` makes again and narrows the definition check
    # 0161's file widens, so it runs again after 0161's.
    ("stewardship_reports", "0046_retire_digests"): (
        "stewardship_reports",
        "0041_report_conversion",
    ),
}


# Columns a later closure migration's frozen file adds to an existing table,
# with the migration that adds them. ``swap_pending`` drops each (with the
# ``before_*`` statements) and, unless the test's full migration added it
# again, installs it again afterwards from its migration's frozen file.
CLOSURE_ALTERED = {
    # 0185 (#2202): the fact identity's pledge review watermark and Edits.
    **dict.fromkeys(
        SERIES_IN_FORCE_COLUMNS, ("stewardship_reports", "0052_series_in_force")
    ),
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
    # 0131 (#1684, provisional number) adds five Ministry request and
    # revision columns; one entry, so its file runs once
    # (``before_ministry_review_pipeline`` drops them all).
    ("stewardship_ministry_request", "reviewed_ministry_duid"): (
        "stewardship_workflows",
        "0003_ministry_review_pipeline",
    ),
    # 0136 (#1753, provisional number) adds the responses' upcoming pledge
    # with its INSERT guard and replaces 0135's stage selection in place,
    # which 0135's reinstall (``CLOSURE_CREATED``) puts back first; its file
    # then runs whole again (``before_submission_upcoming_pledge`` drops the
    # column and the guard).
    ("stewardship_submission", "upcoming_pledge"): (
        "stewardship_responses",
        "0018_submission_upcoming_pledge",
    ),
    ("stewardship_family_form_baseline", "reviewed_view"): (
        "stewardship_responses",
        "0022_reviewed_form_shown_items",
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
# 0107 (#528, ADM-09.04) replaced the review guard 0096's file brings back
# with the one that also admits an edited value, and 0108's file (run by
# ``CLOSURE_ALTERED``) with the accepted base; 0128 (#1683) replaces it last,
# for the review pipeline, and 0130 (#1719) after it, for an edited
# address; neither creates anything, so 0130's file (the latest body) runs
# again whenever the guard lacks its phrase.
# 0110 (#558) replaces the baseline financial selection that 0094's file,
# run again above for the limitations report, puts back without the
# browser-zone days, so its entry comes after 0094's.
# 0119 (#1591) replaces the packet and report selections that 0094's file
# puts back without the requesting Member's consent, so their entry comes
# after 0094's, whose rerun the limitations report alone now triggers.
# 0140 (#1718, provisional number) replaces both again, whole, listing a
# request under the Ministry its Edit carried forward, so its file (which
# holds 0119's consent too) is the one that runs.
CLOSURE_REPLACED = {
    "public.stewardship_debug_off_switch_guard_v1()": (
        ("stewardship_jobs", "0031_debug_allow"),
        "clears the debug-off switch in Production",
    ),
    "public.stewardship_safe_context_v1(text,jsonb)": (
        ("stewardship_audit", "0003_retry_refusal_log"),
        "'retry_refusal'",
    ),
    "public.stewardship_limitations_report_v1(uuid,jsonb)": (
        ("stewardship_reports", "0026_source_status_names"),
        NAME_NOT_RECORDED,
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
            "public.stewardship_ministry_packet_v1"
            "(uuid,bigint[],boolean,boolean,bigint[])",
            "public.stewardship_ministry_report_v3"
            "(uuid,jsonb,boolean,bigint[],integer,text,integer,integer)",
        ),
        (
            ("stewardship_reports", "0035_edited_ministry_contacts"),
            "coalesce(r.reviewed_ministry_duid,r.ministry_duid) AS ministry_duid",
        ),
    ),
    **dict.fromkeys(
        (
            "public.stewardship_task_type_login_v1(text)",
            "public.stewardship_task_type_creators_v1(text)",
        ),
        (("stewardship_responses", "0007_publication_tasks"), "census_publication"),
    ),
    "public.stewardship_proposal_review_guard_v1()": (
        ("stewardship_responses", "0016_census_address_guard"),
        "PERFORM public.stewardship_response_address_guard_v1(NEW.admin_value);",
    ),
    "public.stewardship_response_derived_guard_v1()": (
        ("stewardship_responses", "0010_upstream_at_check"),
        "f.outcome='already_in_parishsoft'",
    ),
    "public.stewardship_publication_confirmation_guard_v1()": (
        ("stewardship_responses", "0010_upstream_at_check"),
        "p.version<>f.proposal_version+1",
    ),
    # 0133 (#1737) replaces 0060's resolution guard and 0132's refresh
    # check guard, which those files bring back; it creates nothing, so its
    # file runs again whole. 0139 (#1773, provisional number) replaced the
    # resolution guard on 0138's body (which keeps 0133's), and 0146 (#1840,
    # below) replaces it last on 0139's.
    "public.stewardship_refresh_check_guard_v1()": (
        ("stewardship_responses", "0017_entered_incorrectly_fixes"),
        "k.item_version=change.version-1",
    ),
    # 0146 (#1840) replaces the resolution guard (0139's
    # body), the resolution effect check (0060's) and the derived guard
    # (0138's). The reruns of 0060's and 0138's files (CLOSURE_CREATED) and
    # of 0133's (the entry above, which must come first) bring back older
    # bodies; 0146 creates nothing, so its file runs again whole, after
    # them. The derived guard's later reruns are undone by
    # ``CLOSURE_REPLACED_ONLY``.
    "public.stewardship_proposal_resolution_guard_v1()": (
        ("stewardship_responses", "0023_census_terminal_undo"),
        "undo:=NEW.action='reopened' AND NOT replaced",
    ),
    "public.stewardship_proposal_resolution_effect_v1()": (
        ("stewardship_responses", "0023_census_terminal_undo"),
        "AND NOT (proposal.decision='approved' AND proposal.execution='pending'))",
    ),
    # 0147 (#1840) alters 0129's resolution table and
    # replaces its three guards, which 0129's reinstall (CLOSURE_CREATED)
    # brings back without the undo; it creates nothing, and each statement
    # can run again, so its file runs again whole.
    "public.stewardship_census_addition_resolution_guard_v1()": (
        ("stewardship_responses", "0024_census_addition_undo"),
        "Census addition step does not follow its latest step",
    ),
    # 0148 (#1840) replaces 0061's roster entry guard
    # (CLOSURE_CREATED) and 0131's request and revision guards (its file
    # runs again through CLOSURE_ALTERED), which those reruns bring back
    # older; it creates nothing, so its file runs again whole.
    "public.stewardship_ministry_roster_entry_guard_v1()": (
        ("stewardship_workflows", "0007_ministry_terminal_undo"),
        "Clearing Entered in ParishSoft needs its Back to review with a note",
    ),
    "public.stewardship_ministry_revision_guard_v1()": (
        ("stewardship_workflows", "0007_ministry_terminal_undo"),
        "undo := reopen AND NEW.state='resolved';",
    ),
    "public.stewardship_ministry_request_guard_v1()": (
        ("stewardship_workflows", "0007_ministry_terminal_undo"),
        "NEW.resolution_source_id := NULL;",
    ),
    # 0149 (#1840) replaces 0135's pledge review guard
    # and widens 0127's state check, both of which 0127's and 0135's reruns
    # (CLOSURE_CREATED) bring back older; it creates nothing, so its file
    # runs again whole.
    "public.stewardship_pledge_review_guard_v1()": (
        ("stewardship_reports", "0038_pledge_terminal_undo"),
        "item.stage IN ('entered_by_hand','entered_incorrectly','confirmed')",
    ),
    # 0150 (#1840) replaces 0126's information revision
    # guard, which 0126's file (CLOSURE_CREATED) brings back; it creates
    # nothing, so its file runs again whole.
    "public.stewardship_information_revision_guard_v1()": (
        ("stewardship_responses", "0025_information_handled_undo"),
        "Undoing a handled request needs a note",
    ),
    # 0151 (#1840) replaces 0126's limitation review
    # guard, which 0126's file (CLOSURE_CREATED) makes again; it creates
    # nothing, so its file runs again whole.
    "public.stewardship_limitation_review_guard_v1()": (
        ("stewardship_responses", "0026_limitation_handled_undo"),
        "WHEN 'handled' THEN NEW.decision='accepted'",
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
    # 0158 (#1889) drops the Ignored-reopen note from six guards whose files
    # (0130, 0146, 0148 to 0151) ``CLOSURE_REPLACED`` reruns above; it
    # creates nothing, so its file runs again whole. Each new body names
    # the issue.
    **dict.fromkeys(
        (
            "public.stewardship_proposal_review_guard_v1()",
            "public.stewardship_proposal_resolution_guard_v1()",
            "public.stewardship_ministry_revision_guard_v1()",
            "public.stewardship_pledge_review_guard_v1()",
            "public.stewardship_information_revision_guard_v1()",
            "public.stewardship_limitation_review_guard_v1()",
        ),
        (("stewardship_responses", "0028_ignored_note_optional"), "(#1889)"),
    ),
    # 0181 (#2177), after 0158's reruns: see the note above CLOSURE_CREATED.
    "public.stewardship_pledge_review_states_v1(uuid,uuid)": (
        ("stewardship_reports", "0049_pledge_share_methods"),
        "coalesce(v.reviewed_shares,b.answer->'shares')",
    ),
}


# Run after ``CLOSURE_REPLACED_LAST``: closure migrations whose frozen
# files replace a check constraint in place, by (table, constraint), with
# the text that marks the later definition. 0187 (#2228) widens the
# operational event check that 0106's rerun above puts back older; its
# file only re-creates that check, so it runs again whole.
CLOSURE_CONSTRAINTS = {
    ("stewardship_operational_log", "operational_event_safe"): (
        ("stewardship_audit", "0008_consumer_readmission_log"),
        "'consumer_readmission'",
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
    # 0142 (#1557) replaces 0109's discard, which 0109's file (its routines
    # in CLOSURE_CREATED and runtime_grants.CLOSURE_FUNCTIONS) brings back
    # without the participation capture; only that older body goes straight
    # from the capture loop to the pins.
    ("stewardship_reports", "0036_participation_summary"): (
        "END LOOP;\n    DELETE FROM stewardship_fact_pin"
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
# 0113 (#1596), 0118 (#1603), 0133 (#1737) and 0138 (#1749) replace it last,
# after 0108's and 0112's reruns; 0138's body keeps 0096's, 0108's, 0112's,
# 0113's, 0118's and 0133's rules, so only the latest replacement runs.
CLOSURE_REPLACED_ONLY = {
    # 0141 (#1670) replaces both Ministry selections last, reading added
    # Member links; 0140's rerun (CLOSURE_REPLACED) can put 0140's bodies
    # back, and 0141 creates a table, so only its replacements run again.
    **dict.fromkeys(
        ("stewardship_ministry_packet_v1", "stewardship_ministry_report_v3"),
        (
            ("stewardship_responses", "0021_added_member_links"),
            "bool_or(v.cannot_serve) AS cannot_serve",
        ),
    ),
    # 0131 (#1684) replaces the follow-up selection last; 0094's rerun
    # (above) puts back its older body, and 0131 adds columns, so only its
    # replacement statement runs again.
    "stewardship_ministry_followup_v1": (
        ("stewardship_workflows", "0003_ministry_review_pipeline"),
        "AS reviewed_ministry_name",
    ),
    # 0138 (#1749) replaced it on 0133's body (0133 replaced 0118's for
    # Entered incorrectly's two fixes), for the published Conflict; 0146
    # (#1840) replaced it on 0138's body, for Back to review undoing a
    # terminal step; 0153 (#1705) replaces it last, on 0146's body, for
    # the reviewed Family form.
    "stewardship_response_derived_guard_v1": (
        ("stewardship_responses", "0027_reviewed_census_resubmission"),
        "carries_intent := FOUND AND NOT response.reviewed_view",
    ),
    # 0154 (#1705) replaces 0148's Ministry request guard and 0131's
    # Ministry completeness check, which the reruns of 0131's file
    # (CLOSURE_ALTERED) and 0148's (CLOSURE_REPLACED) put back; only its
    # replacements run again. 0180 (#2173) replaces the completeness check
    # last, on 0154's body, so its replacement is the one that runs.
    "stewardship_ministry_request_guard_v1": (
        ("stewardship_workflows", "0008_reviewed_ministry_resubmission"),
        "AND later.reviewed_view",
    ),
    # 0180 (#2173) also replaces 0158's Ministry revision guard, which the
    # reruns above (0131's, 0148's and 0158's files) put back older; it
    # creates nothing, and only its replacements run again.
    **dict.fromkeys(
        (
            "stewardship_ministry_submission_effects_v1",
            "stewardship_ministry_revision_guard_v1",
        ),
        (("stewardship_workflows", "0009_added_undo_to_review"), "(#2173)"),
    ),
    # 0155 (#1705) replaces 0127's pledge item selection, which 0127's
    # rerun puts back.
    "stewardship_pledge_review_states_v1": (
        ("stewardship_reports", "0039_reviewed_pledge_resubmission"),
        "AND NOT (x.reviewed_view AND EXISTS (",
    ),
    # 0138 (#1749) replaces the refresh check guard last, on 0137's body
    # (0133's with #1738's fields); the rerun of 0133's file
    # (CLOSURE_REPLACED, for its refresh check guard) puts 0133's body back over
    # it, and 0138 creates the address key, so only its replacement
    # statement runs again.
    "stewardship_refresh_check_guard_v1": (
        ("stewardship_responses", "0019_published_refresh_conflicts"),
        "change.field='new_member'",
    ),
    # 0109 (#1398) widens 0106's safe context with the export report and
    # format keys, and 0122 (#1620) widens 0109's with the automatic view
    # key; the reruns above bring back 0106's, so 0122's replacement, whose
    # body holds both, runs alone (0109's discard table cannot be made twice).
    # 0174 (#1554) widens 0122's with the organization choice's four keys,
    # so its replacement, whose body holds every earlier key, runs alone.
    "stewardship_safe_context_v1": (
        ("stewardship_accounts", "0035_parishsoft_organization_choice"),
        "'organizations_unknown']",
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
    # 0114 (#1547) drops the sign-in age from guards that closure files
    # (0062, 0078, 0080, 0087, 0088, 0090, 0104, and 0111/0112 once merged)
    # bring back older; its bodies all carry the issue number. It also
    # creates the ended-automation check, so it cannot run whole twice.
    # 0174 (#1554) replaces 0114's body again, admitting a ParishSoft key
    # staged with no organization; its body keeps 0114's rules.
    "stewardship_setup_secret_guard_v1": (
        ("stewardship_accounts", "0035_parishsoft_organization_choice"),
        "(#1554)",
    ),
    # 0177 (#2163) replaces 0174's setup load organization check with the
    # same checks as SECURITY DEFINER (named in its replacement statement,
    # so that statement alone restores the rights); 0174's whole rerun
    # brings back the caller's-rights body, which lacks 0177's comment.
    "stewardship_setup_load_organization_v1": (
        ("stewardship_accounts", "0036_setup_load_definer"),
        "SECURITY DEFINER (#2163)",
    ),
    # 0124 (#1762) rewords four of 0114's guards again, and 0104's setup
    # credential audit (which 0104's rerun above brings back); each body is
    # its latest earlier one with only the words changed, so its replacement
    # runs alone, last, when the body lacks its new words.
    **{
        name: (("stewardship_accounts", "0029_guard_wording"), phrase)
        for name, phrase in (
            (
                "stewardship_backup_request_guard_v1",
                "A backup request requires a live Administrator session",
            ),
            (
                "stewardship_debug_off_switch_guard_v1",
                "The debug-off switch requires a live Administrator session",
            ),
            (
                "stewardship_source_drop_acceptance_guard_v1",
                "Accepting a large ParishSoft change requires a live "
                "Administrator session",
            ),
            (
                "stewardship_publication_confirmation_guard_v1",
                "Publishing to ParishSoft requires a live Administrator session",
            ),
            (
                "stewardship_setup_secret_audit_v1",
                "(the instant never changes in place, #1550)",
            ),
        )
    },
    # 0144 (#1799) replaces 0109's timeline capture and request guard and
    # 0084's request authorization, which the reruns above bring back
    # (these keys' later values win, so 0109's capture and guard entries
    # above become 0144's); its bodies keep 0109's and 0084's rules.
    **{
        name: (("stewardship_reports", "0037_staff_timeline_export"), phrase)
        for name, phrase in (
            ("stewardship_timeline_export_capture_v1", "(#1799)"),
            ("stewardship_export_request_guard_v1", "since #1799"),
            ("stewardship_export_request_authorized_v1", "and Staff (#1799)"),
        )
    },
    # 0159 (#1890) replaces seven functions last, each on its latest body
    # (these keys' later values win over the 0153, 0154, 0138 and 0124
    # entries above), for strict phase order.
    **{
        name: (("stewardship_responses", "0029_strict_phase_order"), phrase)
        for name, phrase in (
            (
                "stewardship_proposal_resolution_guard_v1",
                "OR proposal.execution<>'pending'))",
            ),
            (
                "stewardship_response_derived_guard_v1",
                "OR (NEW.execution='resolved_upstream' AND OLD.execution<>'queued')",
            ),
            (
                "stewardship_publication_confirmation_guard_v1",
                "OR p.execution NOT IN ('published','resolved_upstream')",
            ),
            (
                "stewardship_ministry_request_guard_v1",
                "A refresh does not resolve a Ministry request",
            ),
            (
                "stewardship_refresh_check_guard_v1",
                "AND NOT coalesce((SELECT e.entered",
            ),
            (
                "stewardship_pledge_stages_v1",
                "CASE WHEN k.outcome='confirmed' AND i.stage='entered_by_hand'",
            ),
            (
                "stewardship_pledge_refresh_check_guard_v1",
                "IF item.review_id IS NULL AND EXISTS (SELECT 1",
            ),
        )
    },
    # 0160 (#1895) replaces two functions last, each on its latest body
    # (the derived guard's later value wins over the 0159 entry above),
    # so an unchanged resubmission keeps a change ParishSoft already holds.
    **{
        name: (("stewardship_responses", "0030_resubmission_phase_order"), phrase)
        for name, phrase in (
            (
                "stewardship_response_derived_guard_v1",
                "OR OLD.id=(SELECT newest.id FROM (",
            ),
            (
                "stewardship_submission_effects_v1",
                "IF NOT NEW.reviewed_view AND prior.entity_kind IN",
            ),
        )
    },
    # 0163 (#1909) replaces the derived guard last, on 0160's body (its
    # value wins over the 0160 entry above), so a response not drawn as the
    # reviewed form finds a change a reviewed form showed.
    **{
        "stewardship_response_derived_guard_v1": (
            ("stewardship_responses", "0031_edit_replacement"),
            "AND shown->>'stream'='census' AND shown->>'item'=candidate.id::text",
        )
    },
    # 0170 (#2008) replaces the submission effects check last, on 0160's
    # body, and the derived guard last, on 0163's (each value wins over the
    # entries above), so a reviewed form's change the parish added, left as
    # shown, makes no change, and edited back to ParishSoft's value makes
    # one.
    **{
        "stewardship_submission_effects_v1": (
            ("stewardship_responses", "0032_parish_added_shown"),
            "AND shown->>'stream'='census' AND shown->>'shown'='parish'",
        ),
        "stewardship_response_derived_guard_v1": (
            ("stewardship_responses", "0032_parish_added_shown"),
            "AND addition.family_id=response.family_id",
        ),
    },
    # 0175 (#1393) replaces the Financial and both Additional information
    # selections last, on 0110's, 0094's and 0100's bodies, so a rule-loaded
    # Family's badge follows the rule; those files' reruns put the older
    # bodies back, so only its replacement statements run again.
    **dict.fromkeys(
        (
            "stewardship_financial_report_v1",
            "stewardship_information_report_v1",
            "stewardship_information_report_v2",
        ),
        (
            ("stewardship_campaigns", "0019_campaign_families"),
            "WHEN p.canonical::jsonb ? 'campaign_families'",
        ),
    ),
    # 0179 (#2166) replaces fourteen definer functions last, each its latest
    # body with the jsonb and containment operators qualified (so 0177's
    # entry above is overridden by the setup load check's). Every 0179 body,
    # and no earlier one, names pg_catalog's operators.
    **dict.fromkeys(
        re.findall(
            r"^CREATE OR REPLACE FUNCTION public\.(\w+)\(",
            (MIGRATIONS / "migrations/0179_definer_operators.sql").read_text(
                encoding="utf-8"
            ),
            re.M,
        ),
        (("stewardship_accounts", "0037_definer_operators"), "OPERATOR(pg_catalog."),
    ),
    # 0182 (#2196) replaces the Financial selection on 0175's body: this
    # key's value replaces the 0175 entry above (in that entry's place), so
    # the restore installs 0182's body and the report reads the values in
    # force.
    "stewardship_financial_report_v1": (
        ("stewardship_reports", "0050_financial_in_force"),
        "CROSS JOIN LATERAL stewardship_pledge_review_states_v1(x.id,NULL) k",
    ),
    # 0185 (#2202) replaces, with the pledge review watermark and the
    # generation's Edits, functions that 0095's, 0156's and 0161's files
    # (run again above) bring back older; only its replacements run again.
    **{
        name: (("stewardship_reports", "0052_series_in_force"), phrase)
        for name, phrase in (
            ("stewardship_fact_disposable", "newer.review_watermark"),
            ("stewardship_fact_runtime_binding_v1", "requested_review_watermark"),
            ("stewardship_report_email_preparation_guard_v1", "review_watermark"),
            ("stewardship_report_email_snapshot_guard_v1", "review_watermark"),
            ("stewardship_campaign_comparison_v1", "selected.pledge_edits"),
        )
    },
    # 0189 (#2293) replaces the refresh check guard last, on 0159's body
    # (this key's value replaces the 0159 entry above), so a Ministry check
    # reads the roster as of the Ministry roster date, after the reruns
    # that bring back older bodies.
    **{
        "stewardship_refresh_check_guard_v1": (
            ("stewardship_responses", "0033_commitment_roster_date"),
            "roster := stewardship_response_ministry_held_v1(NEW.snapshot_id,family,",
        )
    },
    # 0190 (#2295) and 0191 (#2300) create nothing: each replaces the
    # delivery resolution guard in place, which 0173's whole rerun
    # (CLOSURE_CREATED) brings back without the notices or the Ministry
    # leader emails, so only the latest replacement, 0191's (with its
    # SECURITY DEFINER alter), runs again: its body carries 0190's too.
    "stewardship_delivery_resolution_guard_v1": (
        ("stewardship_jobs", "0040_settle_leader_emails"),
        "A Ministry leader email (#2300)",
    ),
    # 0192 (#2298) replaces 0185's preparation guard: this key's value
    # replaces 0185's entry above (in its place), so the restore installs
    # 0192's body, held at the report end.
    "stewardship_report_email_preparation_guard_v1": (
        ("stewardship_reports", "0053_late_report_totals"),
        "s.campaign_sequence<=NEW.submission_watermark",
    ),
    "stewardship_exact_request_guard_v1": (
        ("stewardship_reports", "0053_late_report_totals"),
        "r.campaign_sequence<=NEW.submission_watermark",
    ),
    # 0193 (#235) creates nothing: it replaces 0176's backup destination
    # definer and the operational log writer in place, which 0176's whole
    # rerun (CLOSURE_CREATED) and 0106's (CLOSURE_REPLACED_LAST) bring back
    # without the uploader's login, so only its replacements run again (the
    # definer's with its SECURITY DEFINER alter).
    **dict.fromkeys(
        (
            "stewardship_backup_destination_v1",
            "stewardship_operational_log_writer_v1",
        ),
        (
            ("stewardship_jobs", "0041_backup_uploader_preparation"),
            "pk_stewardship_backup_uploader",
        ),
    ),
    # 0194 (#2320) replaces both directory selections last, on 0094's
    # bodies, so they build the shown Family name with the shared function;
    # 0094's rerun (CLOSURE_REPLACED) puts the inline name back, so only
    # 0194's replacements run again. It also replaces the Financial and both
    # Additional information selections last, on 0182's and 0175's bodies
    # (these keys' values replace those entries above, in their place), so
    # they name a Family with a blank lastName by its heads and search the
    # Family surname. The name functions it also replaces come from 0045,
    # which no closure reruns.
    **dict.fromkeys(
        ("stewardship_directory_report_v1", "stewardship_directory_report_v2"),
        (
            ("stewardship_reports", "0054_mixed_surname_names"),
            "stewardship_family_heads_name_v2(family_name,surname_recorded,n.parts)",
        ),
    ),
    **dict.fromkeys(
        (
            "stewardship_financial_report_v1",
            "stewardship_information_report_v1",
            "stewardship_information_report_v2",
        ),
        (
            ("stewardship_reports", "0054_mixed_surname_names"),
            "stewardship_family_heads_name_v2(r.surname,r.surname_recorded,n.parts)",
        ),
    ),
    # 0196 (#2331) replaces 0185's snapshot guard and runtime binding (these
    # keys' values replace 0185's entries above, in their places) and
    # 0161's source pin guard, so the restore installs 0196's bodies: a
    # report email counts today's campaign Families.
    **{
        name: (("stewardship_reports", "0055_report_email_population"), phrase)
        for name, phrase in (
            ("stewardship_report_email_snapshot_guard_v1", "requests_v2("),
            (
                "stewardship_fact_runtime_binding_v1",
                "NEW.population_scope NOT IN ('current','historical')",
            ),
            (
                "stewardship_source_pin_guard",
                "WHERE t.task_type='report_email_prepare' AND p.phase='capture'",
            ),
        )
    },
    # 0197 (#2335) replaces the same three selections last, on 0194's
    # bodies (these keys' values replace 0194's entries, in their place), so
    # each Family's status reads its portal_eligible under every rule.
    **dict.fromkeys(
        (
            "stewardship_financial_report_v1",
            "stewardship_information_report_v1",
            "stewardship_information_report_v2",
        ),
        (
            ("stewardship_reports", "0055_financial_eligibility"),
            "THEN (p.canonical::jsonb->'portal_eligible')::boolean END AS active,",
        ),
    ),
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


def before_reviewed_form_shown_items():
    """The statements that put baselines and submissions back as before 0143.

    #1705's 0143 adds only ``reviewed_view`` and ``shown_items`` to both
    tables, with a check on each that goes with the columns, so dropping
    them is the production base; 0143's frozen file (or the full migration)
    adds them again.
    """
    return [
        f"ALTER TABLE public.{table} DROP COLUMN {column}"
        for table in ("stewardship_family_form_baseline", "stewardship_submission")
        for column in ("shown_items", "reviewed_view")
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


def before_submission_upcoming_pledge():
    """The statements that put responses back as they were before 0136.

    #1753's 0136 adds ``stewardship_submission.upcoming_pledge`` with its
    INSERT guard and replaces 0135's stage selection, which ``swap_pending``
    already dropped (``CLOSURE_CREATED``); dropping the guard function drops
    its trigger. 0136's frozen file (or the full migration) installs all of
    it again.
    """
    return [
        "DROP FUNCTION public.stewardship_submission_pledge_guard_v1() CASCADE",
        "ALTER TABLE public.stewardship_submission DROP COLUMN upcoming_pledge",
    ]


PRE_SERIES_IN_FORCE = Path(__file__).with_name("pre_series_in_force_functions.sql")


def before_series_in_force():
    """The statements that put the fact identity back as it was before 0185.

    #2202's 0185 adds the pledge review watermark and Edits columns (its
    unique exact-input key and report email check go with them), and
    replaces base functions that read them; their earlier bodies come from
    ``PRE_SERIES_IN_FORCE``. Its routines are in ``CLOSURE_CREATED``, so
    ``swap_pending`` already dropped them (and the recording trigger); the
    functions of 0095's, 0156's and 0161's files are dropped or put back
    older there too. 0185's frozen file (or the full migration) installs
    all of it again.
    """
    return [
        *(
            # 0156's report email table is a closure table, dropped already.
            f"ALTER TABLE IF EXISTS public.{table} DROP COLUMN IF EXISTS {column}"
            for table, column in SERIES_IN_FORCE_COLUMNS
        ),
        "ALTER TABLE public.stewardship_daily_fact_set ADD CONSTRAINT "
        "daily_fact_exact_inputs UNIQUE (campaign_id, population_scope, "
        "source_generation, submission_watermark, timezone_configuration_id, "
        "through_date)",
        PRE_SERIES_IN_FORCE.read_text(encoding="utf-8"),
    ]


# 0161 (#1786) replaces, in place, functions and views of the
# base and of earlier closure files so that they read 0156's report email
# tables and functions, which ``swap_pending`` drops; 0156's table drops take
# many of those views with them. While the swap is pending each gets the
# definition it had before 0156, from the frozen copy ``PRE_CONVERSION``
# (``before_report_conversion``), as a database at the users upgrade's base
# has them, and every view a drop took is made again. Afterwards 0161's file
# runs whole again after 0156's (its two new functions are in
# ``CLOSURE_CREATED``), and any view still missing is made again from its
# current definition (``restore_views``).
REPORT_CONVERSION = MIGRATIONS / "migrations/0161_report_conversion.sql"
REPORT_PIPELINE = MIGRATIONS / "migrations/0156_report_pipeline.sql"
PRE_CONVERSION = Path(__file__).with_name("pre_report_conversion_definitions.sql")
# 0171 (#1834) drops the digest write functions and triggers, which the
# base's gate bodies (put back by ``before_report_conversion``) still call.
# While the swap is pending they are made again from their frozen baseline
# copy (``before_retire_digests``); 0171's file runs again afterwards
# (``CLOSURE_RERUN``).
PRE_RETIREMENT = Path(__file__).with_name("pre_retire_digests_definitions.sql")


def schema_sources():
    """Each SQL text a fresh install applies, in order.

    The baseline's schema files (migration 0001) come first, then every
    frozen migration file in prefix order.
    """
    import importlib

    initial = importlib.import_module(
        "parishkit.stewardship.accounts.migrations.0001_initial_schema"
    )
    files = sorted((MIGRATIONS / "migrations").glob("*.sql"))
    return [("0001", initial.schema_sql())] + [
        (path.name, path.read_text(encoding="utf-8")) for path in files
    ]


def _statement(text, start, *, view):
    """The whole CREATE statement that begins at ``start``.

    A function ends at the first ``;`` after its dollar-quoted body (which
    a trailing LANGUAGE clause may follow); a view, which has no body
    quote, at the first ``;`` that ends a line.
    """
    if view:
        return text[start : re.compile(r";[ \t]*$", re.M).search(text, start).end()]
    quote = re.compile(r"\bAS\s+(\$\w*\$)").search(text, start)
    close = text.index(quote[1], quote.end()) + len(quote[1])
    return text[start : text.index(";", close) + 1]


def latest_definitions(names, sources):
    """Each named function or view's latest definition among ``sources``.

    ``names`` maps a name to ``FUNCTION`` or ``VIEW``; one no source
    defines (0156's own, which ``swap_pending`` drops and the file makes
    again) is left out. The result keeps the order in which the
    definitions appear, so a view comes after the views it reads. A
    baseline file may leave out the ``public.`` qualifier.
    """
    found = {}
    for name, kind in names.items():
        pattern = re.compile(
            rf"^CREATE (?:OR REPLACE )?{kind} (?:public\.)?{name}\b", re.M
        )
        hits = [
            (index, match.start(), text)
            for index, (label, text) in enumerate(sources)
            for match in pattern.finditer(text)
        ]
        if not hits:
            continue
        index, start, text = hits[-1]
        found[name] = (
            (index, start),
            kind,
            _statement(text, start, view=kind == "VIEW").replace(
                f"CREATE {kind}", f"CREATE OR REPLACE {kind}", 1
            ),
        )
    return {
        name: (kind, statement)
        for name, (_, kind, statement) in sorted(
            found.items(), key=lambda item: item[1][0]
        )
    }


def public_views(cursor):
    """The names of the views in the public schema."""
    cursor.execute("SELECT viewname FROM pg_views WHERE schemaname='public'")
    return {name for (name,) in cursor.fetchall()}


def create_views(cursor, definitions):
    """Create each view, retrying those whose inputs a later one makes.

    Each attempt runs in a savepoint; a pass that makes no view fails the
    test with the first refusal, rather than looping.
    """
    pending = dict(definitions)
    while pending:
        made, refusal = [], None
        for name, statement in pending.items():
            try:
                with transaction.atomic():
                    cursor.execute(statement)
                made.append(name)
            except DatabaseError as error:
                refusal = refusal or error
        if not made:
            raise refusal
        for name in made:
            del pending[name]


def before_report_conversion(cursor, views_before):
    """Put back what 0161 replaced as it was before 0156; remake lost views.

    Functions keep their grants (CREATE OR REPLACE) and their SECURITY
    DEFINER, which 0161's file repeats for the three the baseline alters.
    """
    text = REPORT_CONVERSION.read_text(encoding="utf-8")
    names = {
        name: kind
        for kind, name in re.findall(
            r"^CREATE OR REPLACE (FUNCTION|VIEW) public\.(\w+)\b", text, re.M
        )
    }
    definitions = latest_definitions(
        names, [("frozen", PRE_CONVERSION.read_text(encoding="utf-8"))]
    )
    cursor.execute(
        "SELECT proname FROM pg_proc WHERE pronamespace='public'::regnamespace "
        "AND prosecdef AND proname=ANY(%s)",
        [list(definitions)],
    )
    definers = {name for (name,) in cursor.fetchall()}
    views = {
        name: statement
        for name, (kind, statement) in definitions.items()
        if kind == "VIEW"
    }
    for name in views:
        cursor.execute(f"DROP VIEW IF EXISTS public.{name} CASCADE")
    create_views(cursor, views)
    # As a fresh install does: a body is checked when it runs, so a SQL
    # function may name what a later statement makes.
    cursor.execute("SET LOCAL check_function_bodies = false")
    for name, (kind, statement) in definitions.items():
        if kind == "FUNCTION":
            cursor.execute(statement)
            if name in definers:
                cursor.execute(f"ALTER FUNCTION public.{name} SECURITY DEFINER")
    cursor.execute("SET LOCAL check_function_bodies = true")
    # The views 0156 and 0161 create stay absent, as on the base.
    created = {
        name
        for path in (REPORT_CONVERSION, REPORT_PIPELINE)
        for name in re.findall(
            r"^CREATE VIEW public\.(\w+)\b", path.read_text(encoding="utf-8"), re.M
        )
    }
    restore_views(cursor, views_before - created, sources=schema_sources())


def before_retire_digests(cursor):
    """Make again the digest write functions and triggers 0171 drops.

    The copy is the baseline's own, run as a fresh install runs it: bodies
    are checked when they run, since the copy's functions call each other.
    """
    cursor.execute("SET LOCAL check_function_bodies = false")
    cursor.execute(PRE_RETIREMENT.read_text(encoding="utf-8"))
    cursor.execute("SET LOCAL check_function_bodies = true")


def restore_views(cursor, views_before, *, sources):
    """Make again each view of ``views_before`` now missing, from ``sources``."""
    missing = views_before - public_views(cursor)
    definitions = latest_definitions(dict.fromkeys(missing, "VIEW"), sources)
    create_views(
        cursor, {name: statement for name, (_, statement) in definitions.items()}
    )


def before_ministry_review_pipeline():
    """The statements that put Ministry requests back as they were before 0131.

    #1684's 0131 adds the Edit's Ministry, the parish-added mark and the
    request an addition replaces to ``stewardship_ministry_request``, and
    the Edit's Ministry and the decision note to its revisions; dropping
    the columns drops their checks and foreign key with them. The guards and
    selection it replaced keep their 0131 bodies, which nothing on the
    users upgrade's base calls; 0131's file (or the full migration) adds
    the columns again.
    """
    return [
        'ALTER TABLE "stewardship_ministry_request" '
        'DROP COLUMN "reviewed_ministry_duid", DROP COLUMN "parish_added", '
        'DROP COLUMN "added_in_place_of_id"',
        'ALTER TABLE "stewardship_ministry_revision" '
        'DROP COLUMN "reviewed_ministry_duid", DROP COLUMN "decision_note"',
    ]


def before_information_limitation_review():
    """The statement that puts the information revisions back as before 0126.

    #1685's 0126 adds only ``stewardship_information_revision``'s review
    decision (and its check, which goes with the column) to a table the base
    has; its new table and functions are ``CLOSURE_TABLES`` and
    ``CLOSURE_CREATED``. The revision guard it replaced keeps its 0126 body,
    which nothing on the users upgrade's base calls; 0126's file (or the full
    migration) adds the column again.
    """
    return [
        'ALTER TABLE "stewardship_information_revision" DROP COLUMN "review_decision"'
    ]


def before_setup_source_check():
    """The statements that put the setup readiness record back before 0183.

    #2201's 0183 adds ``source_check_id`` (its foreign key goes with the
    check table, its check and index with the column) and lets
    ``source_result_id`` be null, as no readiness record made since binds a
    staged load. These drop the column and restore the NOT NULL, as on
    production's base, so 0183's frozen file (or the full migration) installs
    again; the functions it replaced keep their 0183 bodies, which its file
    replaces again.
    """
    return [
        "ALTER TABLE public.stewardship_setup_readiness_binding "
        "DROP COLUMN source_check_id",
        "ALTER TABLE public.stewardship_setup_readiness_binding "
        "ALTER COLUMN source_result_id SET NOT NULL",
    ]


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
    charts as they were before 0083 (``before_digest_funnel_charts``), and
    form baselines and submissions as they were before 0143
    (``before_reviewed_form_shown_items``), and the functions and views 0161
    replaced as they were before 0156 (``before_report_conversion``), and
    the digest write functions and triggers 0171 drops
    (``before_retire_digests``).
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
    assert {key for key, _ in CLOSURE_CONSTRAINTS.values()} <= set(closure)
    assert set(CLOSURE_RERUN) | set(CLOSURE_RERUN.values()) <= set(closure)
    assert set(CLOSURE_OLD_BODIES) <= set(closure)
    assert {key for key, _ in CLOSURE_REPLACED_ONLY.values()} <= set(closure)
    removed = []
    with transaction.atomic(), connection.cursor() as cursor:
        views_before = public_views(cursor)
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
            # A view a dropped table's CASCADE took with it is gone already.
            if table in CLOSURE_VIEWS:
                cursor.execute(f"DROP VIEW IF EXISTS public.{table} CASCADE")
            else:
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
            *before_ministry_review_pipeline(),
            *before_information_limitation_review(),
            *before_submission_upcoming_pledge(),
            *before_reviewed_form_shown_items(),
            *before_setup_source_check(),
            *before_series_in_force(),
        ):
            cursor.execute(statement)
        before_report_conversion(cursor, views_before)
        before_retire_digests(cursor)
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
            # After the function reruns, one of which (0106's) re-creates an
            # older check: the later file that widens it runs again.
            for (table, name), (key, phrase) in CLOSURE_CONSTRAINTS.items():
                cursor.execute(
                    "SELECT strpos(pg_get_constraintdef(oid), %s)>0 "
                    "FROM pg_constraint WHERE conrelid=%s::regclass AND conname=%s",
                    [phrase, f"public.{table}", name],
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
                    # A header without SECURITY DEFINER (0179's baseline
                    # guards) needs the file's alter again, as it ran it.
                    for alter in re.findall(
                        rf"^ALTER FUNCTION public\.{function}\([^)]*\) "
                        "SECURITY DEFINER;$",
                        text,
                        re.M,
                    ):
                        cursor.execute(alter)
            restore_views(cursor, views_before, sources=schema_sources())
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
                # A trigger function's triggers go first (0185's recording
                # trigger, #2202); the file makes them again.
                cursor.execute(
                    "SELECT format('DROP TRIGGER %%I ON %%s', t.tgname, "
                    "t.tgrelid::regclass) FROM pg_trigger t "
                    "JOIN pg_proc p ON p.oid=t.tgfoid WHERE NOT t.tgisinternal "
                    "AND p.pronamespace='public'::regnamespace AND p.proname=%s",
                    [name],
                )
                for (statement,) in cursor.fetchall():
                    cursor.execute(statement)
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
    # The inspection login (#2148) is cluster-wide and has its own Docker
    # provisioning test; this shared cluster never gets it. The off-site
    # uploader's creation and password check (#235) read its password file,
    # which this stand-in configuration has none of; the login itself is
    # made above like every other, and its own tests cover that step.
    for name in ("ensure_inspect_login", "grant_inspect", "_ensure_uploader"):
        monkeypatch.setattr(provisioning, name, lambda *a: None)
    connection.ensure_connection()
    monkeypatch.setattr(
        provisioning, "_connection", lambda *a: nullcontext(connection.connection)
    )
    configuration = SimpleNamespace(
        postgres=SimpleNamespace(password_file=None, name="stewardship_tests")
    )

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
    (#389 L1), and after it the same reads would be refused as excess. The
    backup login's earlier table INSERTs on its run row and the off-site
    copy outcomes are the same case for 0195 (#235, contract): admitted on
    the base, revoked by the full migration, refused as excess after it.
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
            cursor.execute(EARLIER_BACKUP_INSERTS)
        assert grants() == {"database_grants_provisioned": True}
        assert mail_reads_ciphertext()
        assert backup_inserts() == (True, True, True)
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
            # Ministry leader emails (0120, #1592): web records the request;
            # the worker writes the cohort and recipient rows.
            **dict.fromkeys(
                (
                    "stewardship_leader_email_cohort",
                    "stewardship_leader_email_recipient",
                ),
                ("pk_stewardship_worker", "INSERT"),
            ),
            # Refresh checks (0132, #1689): the worker records them in
            # promotion and reads the start row; web only reads both.
            "stewardship_refresh_check": ("pk_stewardship_worker", "INSERT"),
            "stewardship_refresh_check_start": ("pk_stewardship_worker", "SELECT"),
            # Pledge refresh checks (0134, #1704): the worker records them
            # in promotion; web only reads them.
            "stewardship_pledge_refresh_check": ("pk_stewardship_worker", "INSERT"),
            # To review pledges' checks (0135, #1747), the same way.
            "stewardship_pledge_open_check": ("pk_stewardship_worker", "INSERT"),
            # Added Member links (0141, #1670): the worker records them in
            # promotion; web only reads them.
            "stewardship_added_member_link": ("pk_stewardship_worker", "INSERT"),
            # The report email pipeline (0156, #1786): the scheduler
            # allocates preparations, the worker writes snapshots and
            # recipients, and web reads the completion proof.
            "stewardship_report_email_preparation": (
                "pk_stewardship_scheduler",
                "INSERT",
            ),
            **dict.fromkeys(
                (
                    "stewardship_report_email_snapshot",
                    "stewardship_report_email_recipient",
                ),
                ("pk_stewardship_worker", "INSERT"),
            ),
            "stewardship_report_email_completion_ready": (
                "pk_stewardship_web",
                "SELECT",
            ),
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
        # Only the run row's other columns (the registry's column grant).
        assert backup_inserts() == (False, False, False)
        assert scalar(
            "SELECT has_column_privilege('pk_stewardship_backup_worker', "
            "'public.stewardship_backup_run', 'started_at', 'INSERT')"
        )
        with connection.cursor() as cursor:
            cursor.execute(EARLIER_BACKUP_INSERTS)
        with pytest.raises(ConfigError, match="grants exceed initial provisioning"):
            grants()
        with connection.cursor() as cursor:
            cursor.execute(
                EARLIER_BACKUP_INSERTS.replace("GRANT", "REVOKE").replace(
                    " TO ", " FROM "
                )
            )
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


# The earlier release's backup-login table INSERTs on its run row and the
# off-site copy outcomes, which migration 0195 revokes (#235, contract).
EARLIER_BACKUP_INSERTS = (
    "GRANT INSERT ON public.stewardship_backup_run TO pk_stewardship_backup_worker; "
    "GRANT INSERT ON public.stewardship_backup_upload TO pk_stewardship_backup_worker"
)


def backup_inserts():
    """The backup login's table INSERTs and its INSERT on completed_at."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT has_table_privilege('pk_stewardship_backup_worker', "
            "'public.stewardship_backup_run', 'INSERT'), "
            "has_table_privilege('pk_stewardship_backup_worker', "
            "'public.stewardship_backup_upload', 'INSERT'), "
            "has_column_privilege('pk_stewardship_backup_worker', "
            "'public.stewardship_backup_run', 'completed_at', 'INSERT')"
        )
        return cursor.fetchone()


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
        # Whether it was copied is the uploader's answer (#235, contract).
        assert "offsite" not in report
        assert taken == []
    with swap_pending():
        # Only the swap pending: a fresh backup, flag or not.
        code, report = backup(resume)
        assert (code, report["backup_recorded"]) == (0, True)
        assert "backup_reused" not in report
    assert len(taken) == 1

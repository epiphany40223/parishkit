"""Frozen forward-migration SQL files stay frozen and in step with the baseline.

A released forward migration installs ``schema/migrations/NNNN_*.sql`` whole.
Its text must never change afterwards (a Production database already ran it),
so each file's digest is pinned here. The four-digit prefixes form one
repository-wide sequence, so the files sort in apply order even though Django
numbers migrations per app. When such a file re-creates a function the
fresh-install baseline also defines, the latest migration's copy must be the
baseline's current body: a fresh install runs the baseline and then the
migrations, so the two texts are what a fresh install and an upgraded database
each end up with. A later change to a replaced function needs a new numbered
migration file, which then becomes "latest" here. A function an earlier
migration file created (plain ``CREATE FUNCTION``) is migration-owned: every
install path runs the migration that replaces it, so no baseline copy exists
or is compared. A view a frozen file replaces (``CREATE OR REPLACE VIEW``) is
checked the same way against its baseline ``CREATE VIEW``. Constraints have no
text check here; the upgrade-parity database test catches their drift.
"""

import hashlib
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = ROOT / "src/parishkit/stewardship/schema"
MIGRATIONS = SCHEMA / "migrations"
# A baseline statement making a function SECURITY DEFINER: its name and its
# identity arguments (pg_get_function_identity_arguments, as pg_dump writes
# them). Shared by the attribute test and its self-test.
DEFINER_ALTER = re.compile(
    r"^ALTER FUNCTION public\.(\w+)\(([^)]*)\) SECURITY DEFINER;", re.M
)

# Every frozen file and its sha256. Add a line when adding a migration file;
# never change an existing line.
FROZEN = {
    "0002_family_engagement.sql": (
        "d029ac9aa4a5e7d6be742e4497e612c24bbffa93d6f3ce51d7d443a60c8d050a"
    ),
    "0003_refresh_tick_times.sql": (
        "906afe7be0ad16ada790a814adfa0ea83893c76956c75eef17900f4e6d286c02"
    ),
    "0004_automation_sessions.sql": (
        "e0b01ea24cad2ca148a774d51b81828331bdcefde857adf34a8e3897ec5fdf2b"
    ),
    "0005_occurrence_prepare_ahead.sql": (
        "12bc2e9c5c21af7da9ff2d3867f3421d9be5a0b63a6c58383931c764304e2987"
    ),
    "0006_due_work_context.sql": (
        "f9ac6212009daec6ccaeb2bdfa6df7aa07ac44ff27487d519dbba3dc75147e13"
    ),
    "0007_system_health_records.sql": (
        "cbea01108beda94c69f14b5d76c0817ab3a6a50df3bc552c6b1cf1fcc74a0943"
    ),
    "0008_log_detail.sql": (
        "14fc3b72cfaf129d5a8799bd66abaaefcae1f4a5b1a1be49abc0aa657edb1dd8"
    ),
    "0009_unchanged_snapshots.sql": (
        "206f09701bc2f4b034ec5a180831d6bc3bafcc2ef3d6a4d0647c4c1e659e0e4d"
    ),
    "0010_task_type_index.sql": (
        "5326954fb557cff286c211caceb1e283c1b340fcbdbcfe4d5daa5037be6c66e1"
    ),
    "0011_refresh_schedule.sql": (
        "5701c8cad983e9c1c1d7343f2e603f1a50fe4139cc35af0d4c04096ad9eb2b1b"
    ),
    "0012_daily_send_count.sql": (
        "5516ebaa6625d6e25e263a8541c40bf7c1ca670d36e3204a722bec6dc81793fa"
    ),
    "0013_automation_fresh_guards.sql": (
        "08b82fa53698ba8ac6787cd2b8ca28ff5ef2f40825c71758198b44faadef97d6"
    ),
    "0014_slot_decisions.sql": (
        "3b97fdb22903dd045f1067661626bee9d2cb8be820f01fd34d5fd771113b895f"
    ),
    "0015_reminder_workgroup_setting.sql": (
        "76b33d8e4ce41bf45c2289ad70846bf15686dc4f1b42398127e91d930046bec1"
    ),
    "0016_log_events.sql": (
        "89d6ab97ae5f64e948de393a58076f2fe7440086de42d231a173b99ef383a2ef"
    ),
    "0017_restore_review.sql": (
        "a57fd0f1b954b3eb4fb181a7f8a1eacdc4417d4332898ed7d98d1a12ce241c19"
    ),
    "0018_download_audit_context.sql": (
        "28b934306c516ad2ace52b16038aa5e7bd2f9c6ce9ffa30f2b3cb5e3d5567433"
    ),
    "0019_web_health.sql": (
        "9a7d1a330ccf8b19f0d57a1abcb3ada39d997cf392d6ea83931211c3cbdd7c1e"
    ),
    "0020_directory_reach.sql": (
        "cb505df5c8d4cd58da53244b805d8d01d0d9394883915ac9aea784189ca44f45"
    ),
    "0021_slim_directory_capture.sql": (
        "40e9df61f1c6c6b35b6781510a5eda34173dc570aae85951d1e59048c2945266"
    ),
    "0022_directory_member_search.sql": (
        "e81f7e08b1daf3ff046da8f877040a1b2afe9758f55a827a1d8e03db5c026a66"
    ),
    "0023_read_guard_kills.sql": (
        "13b03cb17f7cb3f6e188470818815e172f654e3efdaf4584b229e5fde51cec6a"
    ),
    "0024_task_event_retention.sql": (
        "7e09c66ee0b85584955fb902ec21094051f94008f9bf0d82f57738d58a25e6d0"
    ),
    "0025_scheduler_link_preparation.sql": (
        "223644da6fefccaae630af459e1cd7717967501786ac8b311f2d5ad26583b4d8"
    ),
    "0026_chair_decisions_plan.sql": (
        "6a5b866a6b188331cbe9266ddb9aeb7aaa9d259b7ab5aeceb0561ba32ee6cbbf"
    ),
    "0027_recovery_session_grants.sql": (
        "942522d8d481a4cd9511fccbb667279ae0ad220f48d5633f3b897f13c00a7436"
    ),
    "0028_drop_initial_invitation_state.sql": (
        "f6c6745536014cf67ccffdbaadd42282e331f98b1358afad5843747831259111"
    ),
    "0029_log_writer_allowlist.sql": (
        "2f09aae27a7e3f92336c55fd72594f0740f08e0575947571955f8b999aab7398"
    ),
    "0030_portal_user_insert_guard.sql": (
        "8b854b725381710c2b31061116b6956d1391f1f0bfaf9540143bddf1ada11afd"
    ),
    "0031_critical_ack_guard.sql": (
        "088a1368d806ba1718794986bfeb6d580992ebc203f412d0daf076c42e91af34"
    ),
    "0032_load_budget_timeout.sql": (
        "d6d55496b9a36cebe8c6e795effbe55787d5c47c47d6ffeff5d1a49404871513"
    ),
    "0033_retention_stall_wording.sql": (
        "70188504c3ba33124f3ab0a8f495c946b3b568181da1fb0f71401e6f7fcc1e3a"
    ),
    "0034_setup_without_campaign.sql": (
        "721b40fdf8d0b3a49b3d77d330d833334b2b3df647bb20590dc8428658ac0309"
    ),
    "0035_workgroup_recovery_skip.sql": (
        "caf487471ad981bf360e12c72ca6848607f9dd61492f866130b67123d2123e9a"
    ),
    "0036_go_live_log_events.sql": (
        "fcf83cf10b12666ae7656546774f97bd44a0e4899dd4871236af2ac96c0e241e"
    ),
    "0037_family_test_names.sql": (
        "2a84ed1ee20997311c3a11068e1ceb9b2f4469660e8e3035f04fcc841009d054"
    ),
    "0038_admin_command_failure_log.sql": (
        "9e237ca17059679a4f0ff8742a4ce6cd63d5f45fe8d20c3dc51159ac3f8a7bbe"
    ),
    "0039_response_list_sort_audit.sql": (
        "f819e94675c5a67e5c9a2c32661583d3a1bef0cf7e7b2967f180a71993b9e283"
    ),
    "0040_ministry_report_actor_scope.sql": (
        "21ccd2a08485c2e464614639d39fb396a62f90e6dd7e00bb88d44d93d1eb9ab5"
    ),
    "0041_task_type_creators.sql": (
        "c94558d9c9a56dac0303d38859935922d0e0c7e4ceadab366e5f370bc41fde72"
    ),
    "0042_ministry_leaders_from_roles.sql": (
        "71d3037323f029197f1d8d595f0e16a0bd4069612e2c43c25965ca64817fd612"
    ),
    "0043_alert_local_time.sql": (
        "b30abe5ec194cd8071485a3225eb21d96fe1c571706ba9e3601273e95524aae0"
    ),
    "0044_corpus_evidence_from_snapshot.sql": (
        "17f7b2eaee06e950f8e091f41cb270b8fc68a2332780f0efe8da259e9cba068f"
    ),
    "0045_report_duid_sorts.sql": (
        "dc2b43df327068290e94e5891f6667a3c1804fa7b48989b2386b00b2f096c1d4"
    ),
    "0046_restore_begin_keeps_review.sql": (
        "251f9db9d1a23e5ac9326e9f1034c9bf3c4b299b68b32c036634fa681c988e62"
    ),
    "0047_portal_users.sql": (
        "e90737d9b5386d9edf85de132185fc536f6334161b7a95ce6e96125f0c69eb97"
    ),
    "0048_restore_holds_after_release.sql": (
        "8f58646d41dac78b9cc4c996a05187d636c57249e7d5c0ce9c3072c7b3f6ed6c"
    ),
    "0049_family_test_names_formats.sql": (
        "255a01e671064c1811c778b8a04c810d6ec47adca6d629e3f519e17ecef212f7"
    ),
    "0050_directory_response_filters.sql": (
        "768e09939df9cf7458121d997bf255c4ecb655995f0c22c181e6d6f589c6fd1b"
    ),
    "0051_users_requests.sql": (
        "6f524ab96f1ecb5a68e37a2c790a7735567af4bb91c251f9cf79eccd6b7cc858"
    ),
    "0052_users_swap.sql": (
        "188e28a1640c83a8621ff08d2141624eb605dd72adcd494cc85ef6c9b5448a25"
    ),
    "0053_user_name_lookup.sql": (
        "8d830c804f525ae5753343dde91e3bc69d08081c3cd716c47d57e220b099dcf2"
    ),
    "0054_leaders_without_user.sql": (
        "3ffad84560e252d0f0cfa6c7aac0fced64a6c7c14537af2ef2b596779c000076"
    ),
    "0055_setup_users.sql": (
        "aa178a20f0feb29f3428f28dda8c106bf6d1d7d9e615c26e62d9e18ed9fe9cc3"
    ),
    "0056_retire_talents.sql": (
        "c6a94fcef747669bca87da2f38767cb7c38927645fc6530b93be277036816118"
    ),
    "0057_admin_session_key_digests.sql": (
        "d6e3df87eb59fcae5f3e99d79de1d4830c32fa4270dc0bbc89e0979ed678578f"
    ),
    "0058_mail_message_recipients.sql": (
        "79dcb6c6775b88134195e00f3ad773c04cdd93466387ed996bad65f313be6b50"
    ),
    "0059_campaign_archives.sql": (
        "9b5886ac3f5fd6d1140c31bb2ddf89548915381a15c8ab7b24d094f4e74e1e3a"
    ),
    "0060_census_resolution.sql": (
        "8495f4ab2534f79a3b509858f31a0c0168170a9d868a53cf1ea7baa1443b257b"
    ),
    "0061_roster_entries.sql": (
        "0f1dc48e722831ef2541a82a1fe0d689769e5b0bd5229f296aaae0e785e74ecf"
    ),
    "0062_backup_request.sql": (
        "2eafb3ccd65ad1aef3d1e7925f7dfb8b4e17c5a5f47022e96e80a74b8b7e47a4"
    ),
    "0063_timeline_exports.sql": (
        "c99c24710b46e3de1fccebd3c4f4a921315cb2f4b4ba53f31bf44c119b8dbf46"
    ),
    "0064_report_local_days.sql": (
        "85da90b9c2df3eebaba5ea9bd2ce048fb9fadbe70d6bf8d0113c0fe2a11c1511"
    ),
    "0065_staff_entered.sql": (
        "0c9ab0d7e4a2f102ec7c5e498e57bf34d9eecf302e5a2cf6974dec6a35ff258e"
    ),
}


def function_bodies(text, *, replace):
    """Map each function name to its definition text in one SQL file.

    Definitions run from ``CREATE [OR REPLACE] FUNCTION public.name(`` to the
    closing dollar quote (``$$`` or a tagged ``$_$``) followed by ``;``; the
    text kept starts after the keyword, so the two forms compare equal.
    """
    keyword = "CREATE OR REPLACE FUNCTION" if replace else "CREATE FUNCTION"
    bodies = {}
    for match in re.finditer(rf"^{re.escape(keyword)} public\.(\w+)\(", text, re.M):
        # The header ends at "AS" and the body's opening dollar quote, which
        # pg_dump tags ($_$) when the body itself contains a dollar sign; the
        # same tag followed by ";" ends the definition, whether written as
        # "END $$;" or on its own line.
        quote = re.compile(r"\bAS\s+(\$\w*\$)").search(text, match.end())
        body = quote.end()
        end = text.index(quote[1] + ";", body) + len(quote[1]) + 1
        bodies[match[1]] = text[match.start() + len(keyword) : end]
    return bodies


def test_function_bodies_end_at_the_tag_that_opened_them():
    """A pg_dump-tagged body ($_$) containing "$$;" is read to its own end."""
    text = (
        "CREATE FUNCTION public.tagged() RETURNS text\n"
        "    LANGUAGE sql\n"
        "    AS $_$ SELECT '$$;' || '^a$' $_$;\n"
        "CREATE FUNCTION public.plain() RETURNS void\n"
        "    LANGUAGE plpgsql\n"
        "    AS $$\nBEGIN\nEND $$;\n"
    )
    bodies = function_bodies(text, replace=False)
    assert bodies["tagged"].endswith("|| '^a$' $_$;")
    assert bodies["plain"].endswith("END $$;")
    replaced = function_bodies(
        text.replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION"), replace=True
    )
    assert replaced == bodies


def test_every_frozen_migration_file_is_pinned_and_unchanged():
    """Each frozen file is pinned, unchanged, well-formed and part of the image.

    These checks run on every file even while the numbering has gaps (a pull
    request whose file number follows others not yet merged), so a missing
    pin or image entry is never hidden behind the sequence check below.
    """
    files = sorted(path.name for path in MIGRATIONS.glob("*.sql"))
    assert files == sorted(FROZEN), "pin every schema/migrations/*.sql digest"
    for name, digest in FROZEN.items():
        text = (MIGRATIONS / name).read_text(encoding="utf-8")
        assert hashlib.sha256(text.encode()).hexdigest() == digest, name
        assert (
            text.startswith("--") and "SET LOCAL check_function_bodies = false;" in text
        )
        # Unqualified names resolve in public, whatever the migrating role's
        # own search_path is.
        assert "SET LOCAL search_path = public;" in text, name
        # Every frozen file is part of the image.
        for ignore in (
            ROOT / ".dockerignore",
            ROOT / "deploy/stewardship/Dockerfile.dockerignore",
        ):
            assert (
                f"!src/parishkit/stewardship/schema/migrations/{name}"
                in ignore.read_text()
            )


def test_frozen_files_form_one_consecutive_sequence():
    """The baseline is 0001; frozen files continue from 0002 without gaps."""
    prefixes = [name[:4] for name in sorted(FROZEN)]
    assert all(prefix.isdigit() for prefix in prefixes), sorted(FROZEN)
    assert [int(prefix) for prefix in prefixes] == list(range(2, 2 + len(prefixes))), (
        "frozen files form one consecutive repository-wide sequence from 0002"
    )


# A temporary object in a frozen file: CREATE [GLOBAL|LOCAL] TEMP[ORARY]
# across any whitespace, or anything qualified with pg_temp.
TEMP_OBJECT = re.compile(r"\bCREATE\s+((GLOBAL|LOCAL)\s+)?TEMP|\bpg_temp\.", re.I)


def test_no_frozen_file_needs_the_temp_privilege():
    """The deployed migration login cannot create temporary tables.

    PostgreSQL test databases run as a superuser, so only the compose jobs
    would otherwise notice; keep checks in settings or the DO block itself.
    TEMP_OBJECT has no bare INTO TEMP pattern: frozen files have INTO
    template lines.
    """
    for path in MIGRATIONS.glob("*.sql"):
        assert not TEMP_OBJECT.search(path.read_text(encoding="utf-8")), path.name


@pytest.mark.parametrize(
    "text, found",
    [
        ("CREATE TEMPORARY TABLE x AS SELECT 1;", True),
        ("create\n  local\ttemp table x (a int);", True),
        ("CREATE GLOBAL TEMPORARY TABLE x (a int);", True),
        ("SELECT * FROM pg_temp.x;", True),
        ("SELECT body INTO template FROM t;", False),
        ("SET search_path TO pg_catalog,public,pg_temp AS $$", False),
    ],
)
def test_the_temp_privilege_scan_finds_only_temporary_objects(text, found):
    """The scan above catches spaced or qualified forms, not look-alikes."""
    assert bool(TEMP_OBJECT.search(text)) is found


def test_latest_migration_copy_of_each_replaced_function_equals_the_baseline():
    """The fresh-install files already carry what the latest migration installs."""
    baseline = {}
    for path in SCHEMA.glob("*.sql"):
        baseline.update(
            function_bodies(path.read_text(encoding="utf-8"), replace=False)
        )
    latest = {}
    migration_owned = set()
    for path in sorted(MIGRATIONS.glob("*.sql")):
        text = path.read_text(encoding="utf-8")
        migration_owned |= function_bodies(text, replace=False).keys()
        latest.update(function_bodies(text, replace=True))
    assert latest, "no replaced functions found"
    for name, body in latest.items():
        if name in migration_owned:
            # Created by a frozen file, so every install path runs the
            # migration that replaces it; there is no baseline copy to match.
            continue
        assert name in baseline, (
            f"{name} is replaced but never created: a new function uses plain "
            "CREATE FUNCTION"
        )
        assert body == baseline[name], f"{name}: latest migration differs from baseline"


def view_bodies(text, *, replace):
    """Map each view name to its definition text in one SQL file.

    A definition runs from ``CREATE [OR REPLACE] VIEW public.name AS`` to the
    first line ending in ``;``; the text kept starts after the keyword, so the
    two forms compare equal.
    """
    keyword = "CREATE OR REPLACE VIEW" if replace else "CREATE VIEW"
    bodies = {}
    for match in re.finditer(rf"^{re.escape(keyword)} public\.(\w+) AS$", text, re.M):
        end = text.index(";\n", match.end()) + 1
        bodies[match[1]] = text[match.start() + len(keyword) : end]
    return bodies


def test_latest_migration_copy_of_each_replaced_view_equals_the_baseline():
    """A replaced view's latest frozen text is the fresh-install baseline's."""
    baseline = {}
    for path in SCHEMA.glob("*.sql"):
        baseline.update(view_bodies(path.read_text(encoding="utf-8"), replace=False))
    latest = {}
    for path in sorted(MIGRATIONS.glob("*.sql")):
        latest.update(view_bodies(path.read_text(encoding="utf-8"), replace=True))
    assert "stewardship_delivery_family_recovery" in latest
    for name, body in latest.items():
        assert name in baseline, f"{name} is replaced but has no baseline view"
        assert body == baseline[name], f"{name}: latest migration differs from baseline"


def test_frozen_file_migrations_apply_in_file_order():
    """Each frozen-file migration depends on the one before it, so the database
    applies the files in prefix order whatever their apps' own numbering.

    Django's loader reads the migration modules from disk without a database;
    a module that installs a frozen file names it in ``FROZEN_SQL``.
    """
    import sys

    from django.db.migrations.loader import MigrationLoader

    loader = MigrationLoader(None, ignore_no_migrations=True)
    frozen = {}
    for key, migration in loader.disk_migrations.items():
        module = sys.modules[migration.__module__]
        if hasattr(module, "FROZEN_SQL"):
            frozen[Path(module.FROZEN_SQL).name] = key
    assert sorted(frozen) == sorted(FROZEN), "every frozen file has one migration"
    ordered = [frozen[name] for name in sorted(frozen)]
    for previous, current in zip(ordered, ordered[1:], strict=False):
        # forwards_plan lists every migration that must apply before current.
        assert previous in loader.graph.forwards_plan(current), (
            f"{current} must depend on {previous}, directly or transitively"
        )


def test_replaced_functions_keep_the_baselines_altered_attributes():
    """A replaced function is altered again as the baseline alters it.

    ``CREATE OR REPLACE FUNCTION`` keeps a function's owner and grants but
    resets every attribute the command does not name. The baseline makes
    some guards ``SECURITY DEFINER`` with a separate ``ALTER FUNCTION``, so a
    frozen file that replaces one must repeat that statement, or the guard
    would silently start running with its caller's rights (BG-12 found
    this for the occurrence guard). The baseline names each function with
    its identity arguments (``pg_get_function_identity_arguments``, as
    pg_dump writes them), and the frozen file must name the same ones.
    """
    altered = {}
    for path in SCHEMA.glob("*.sql"):
        for name, arguments in DEFINER_ALTER.findall(path.read_text(encoding="utf-8")):
            altered.setdefault(name, set()).add(arguments)
    assert "" in altered["stewardship_occurrence_guard_v1"]
    for path in sorted(MIGRATIONS.glob("*.sql")):
        text = path.read_text(encoding="utf-8")
        for name in function_bodies(text, replace=True).keys() & altered.keys():
            created = text.index(f"CREATE OR REPLACE FUNCTION public.{name}(")
            for arguments in altered[name]:
                statement = (
                    f"ALTER FUNCTION public.{name}({arguments}) SECURITY DEFINER;"
                )
                assert statement in text, f"{path.name} must restore {name}'s rights"
                assert text.index(statement) > created


def test_altered_attribute_scan_reads_identity_arguments():
    """A function with arguments is matched with its full identity."""
    text = (
        "ALTER FUNCTION public.example_v1(uuid, text) SECURITY DEFINER;\n"
        "ALTER FUNCTION public.other_v1() SECURITY DEFINER;\n"
    )
    assert DEFINER_ALTER.findall(text) == [
        ("example_v1", "uuid, text"),
        ("other_v1", ""),
    ]


# The three pre-swap bodies the users-swap database tests reinstall
# (tests/stewardship/database/pre_users_swap_functions.sql), pinned to the
# fresh-install baseline at e930e9fc0, the release before migration 0052:
# the sha256 of each `CREATE FUNCTION ... $$;` statement there, as
# `git show e930e9fc0:src/parishkit/stewardship/schema/functions.sql` (and
# `exports.sql` for the export authorization) prints it. The fixture writes
# CREATE OR REPLACE, which is turned back before hashing.
PRE_SWAP_FIXTURE = ROOT / "tests/stewardship/database/pre_users_swap_functions.sql"
PRE_SWAP_BASELINE = {
    "stewardship_portal_session_admission_v1": (
        "65a6e2dbbe2dd28d9c24208f97683b86133ea6b89098559302e553032543a8dd"
    ),
    "stewardship_export_authorized_v1": (
        "a73beaf9151606f5cc2662975f8a47ce6904e6bbe80135305943967293213cde"
    ),
    "stewardship_policy_activation_v1": (
        "e13d2121a8b1dd5906267e28c64b466fe8fa66fefe41d030d16fa44a38f5f1f8"
    ),
}


def test_the_pre_swap_fixture_is_the_baseline_before_the_swap():
    """The reinstalled bodies are byte for byte the e930e9fc0 baseline's.

    The swap tests prove recovery and sign-in on the SQL a Production
    database runs between `migrate --users-upgrade-base` and the full
    migration only while the fixture holds exactly those bodies, so an edit
    to it (or a fixture that drops one) fails here.
    """
    text = PRE_SWAP_FIXTURE.read_text(encoding="utf-8")
    found = {}
    for name in PRE_SWAP_BASELINE:
        [statement] = re.findall(
            rf"^CREATE OR REPLACE FUNCTION public\.{name}\(.*?\$\$;$",
            text,
            re.S | re.M,
        )
        found[name] = hashlib.sha256(
            statement.replace("CREATE OR REPLACE", "CREATE", 1).encode()
        ).hexdigest()
    assert found == PRE_SWAP_BASELINE
    assert text.count("CREATE OR REPLACE FUNCTION") == len(PRE_SWAP_BASELINE)


# The pre-0055 bodies the setup-users database tests reinstall
# (tests/stewardship/database/pre_setup_users_functions.sql), copied byte for
# byte from the fresh-install baseline at c55a0b92d, the head migration 0055
# (#993) was written on. Pinned so an edit to the fixture fails here.
PRE_SETUP_USERS_FIXTURE = (
    ROOT / "tests/stewardship/database/pre_setup_users_functions.sql"
)
PRE_SETUP_USERS_DIGEST = (
    "3c0c2a8aad5d5c2e98681c04c3f79103721793041a393d787805e9f0ccbc30d2"
)


def test_the_pre_setup_users_fixture_is_unchanged():
    """The setup-users tests prove 0055's check on exactly the earlier bodies."""
    digest = hashlib.sha256(PRE_SETUP_USERS_FIXTURE.read_bytes()).hexdigest()
    assert digest == PRE_SETUP_USERS_DIGEST

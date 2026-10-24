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
    "0066_financial_staff_entered.sql": (
        "6b13a5dc9fa5f012710b9ac7e44ce36a98ac9cf5a999d5b9c64b02152943d9fe"
    ),
    "0067_campaign_comparison.sql": (
        "7470b2e51d474af17f85e91f44ed495be29480d658d70eb7b34a08a15da0b2d8"
    ),
    "0068_mail_message_address_outcomes.sql": (
        "6f76e80ef26948ae028524217b023adcc5faa32f1142ae620760524c77d56333"
    ),
    "0069_member_names_last_first.sql": (
        "fab50a02a867f282e4d8704f879a67082252e5eeb105d41989a9f98c591862f0"
    ),
    "0070_ministry_leader_changes.sql": (
        "a8eb331a607d66ecb320782b46cfcf06451f47ea9eaab7fd34229359ca69f3d8"
    ),
    "0071_comparison_exports.sql": (
        "458439a4ce633a15ec9bf32b5a38ffced7ee0fcda75f9bdd3503cf88bd8e8c58"
    ),
    "0072_digest_comparison.sql": (
        "fb33ae24438f59b7c0f745d71722ebc07526807ae89395dbeeea4a44c9774f71"
    ),
    "0073_dispatch_token_access.sql": (
        "9cc43abb774ed0250b6a5234cc482bf76c2fce9a8423d21f1fb4ea9d6664b6fc"
    ),
    "0074_wide_menu_logo.sql": (
        "24c1b91821f318c072e9fcc1b09ae3aafba7eddab6dabac251a5f330043c62f3"
    ),
    "0075_campaign_series.sql": (
        "290b90ba8ecdec8fd0a17dd190dbea510bde7487904f09dc45e1840ca69eb2c2"
    ),
    "0076_daily_send_resume.sql": (
        "5b6ad32b9ca4f581963092013c7889959d3336909d1eb5cb634756c9583f174d"
    ),
    "0077_address_retry.sql": (
        "b1af088fb6a234352c69cdc33edb913c864c9fa13d0d00641834b8d7c85d59f9"
    ),
    "0078_debug_off_switch.sql": (
        "507ef08c4445c1cd74a3668fe0b550ac537052e92ba2b473a8fa499f9b608cbf"
    ),
    "0079_retry_refusal.sql": (
        "55f1eed335f0616c34610a2e8f8dd3291596f6131ae91e29087bd492f9034375"
    ),
    "0080_drop_acceptance.sql": (
        "556e938e39369cca5f02cb25fece5e4f9b110271431d40d4ce8420562c04a997"
    ),
    "0081_bounce_report.sql": (
        "623c61127b2d65b4a649815c84a4e757f2ef33201e445b543e5c1bba7328284a"
    ),
    "0082_branding_bundle_mode.sql": (
        "1743245bc1291cde5f96e54c3b83c34346aa73a47db56ce11f6eaf89694fb9b0"
    ),
    "0083_digest_funnel_charts.sql": (
        "ce82e08e8f9dcf45b9576ce760522af9c2ad6c9ee94119f5feb6eebc1ebe56e4"
    ),
    "0084_campaign_archive_exports.sql": (
        "66b7ce60794446eeda1d2c61627da569489c4908a05bc2a1457cb2bd3738a8d8"
    ),
    "0085_drop_examples.sql": (
        "77666b75f3cbd17203b7ece92aec07bc81fb0ab8c50032e77cae48defb11743a"
    ),
    "0086_snapshot_statistics.sql": (
        "fd11700fc4872b5847afec7124f265b5950daca3a74f05e6ac87c912871f62c1"
    ),
    "0087_publication_plans.sql": (
        "d46783894e0389933f9e1dd6a41785fb8d07c965f2b19fc7ac5670177535d15a"
    ),
    "0088_debug_allow.sql": (
        "a7ac3d137c80b4314e82d8bc7c8e19f96db67c78d8bff7edc3823d100bc6b52b"
    ),
    "0089_retry_refusal_log.sql": (
        "07c432248d8d28e730df2f0c07378faa44c3b98def736eeb15f36410f0475828"
    ),
    "0090_publication_worker.sql": (
        "67a990b0fee1f8c98619ad6ba9d5a7eaab7c8bade7a2ca874ad4206170c0a956"
    ),
    "0091_drop_ministry_activity.sql": (
        "68403af02afe572c5e81765439d08f387668fe85d33b24fdcd63d7bc245a76bc"
    ),
    "0092_hide_gone_members.sql": (
        "55c18819e14938c233345cccbc3e3c0dc8ab3f3a7651a641d252610f3f2fc29d"
    ),
    "0093_removed_ministry_gone_members.sql": (
        "9c9b4ebeb51da19ac20ee77465b6de325d70f4269913e47e1cfdb87194360050"
    ),
    "0094_source_status_names.sql": (
        "fb0671d5cf8fdbc80871d2cd41073b214bf84514005e31ad05ab4c1caea10e54"
    ),
    "0095_canonical_timezone.sql": (
        "6a0abe41bb3cf3e1e44020e55ba662924a634680f424e1e4e0fbe4c358b24998"
    ),
    "0096_proposal_review.sql": (
        "c64ebee3ace428eb7b6f9d22f2fcf78c1f754d254bea347ea36973163fbe855a"
    ),
    "0097_resume_sender_check.sql": (
        "f2a0334f44759a9f87e7637282847a200f4cd9dcf2f5c217ffa564f94fde5285"
    ),
    "0098_debug_off_clear_subject.sql": (
        "5e77e47bdb4c074839c5938fa739fd1b249c12a9c7d517ab4c9dd1d0ca512a3d"
    ),
    "0099_workgroup_rename_keep.sql": (
        "d6c82585d1876698dcea878a3648f23a0633cf7775e85a31b0a0d73d1d9a7adb"
    ),
    "0100_information_status_counts.sql": (
        "1c57a030f6130f596f6b5c1ee9fe2c9c540e5676ee69b2055faa401e5828c4f6"
    ),
    "0101_publication_tasks.sql": (
        "6d1e92b1571c696103b9e72bf302ce67f4f161bb8ab86d44d95618a9bd9f4b2f"
    ),
    "0102_setup_load_void.sql": (
        "ff6a6644235b6efc39d9a95622e86ee89b8e67b20f26e55b2d44a29840a88d34"
    ),
    "0103_any_creation_timezone.sql": (
        "939867f48bd7966bf19fad3cf6b96339e806de9752625ec1532e40c30de106a1"
    ),
    "0104_setup_sign_in_hour.sql": (
        "7248a23e6fdf74141c68528d089b02ba4f112e5d9736f7f24fd0234306a1a378"
    ),
    "0105_campaign_setup_review.sql": (
        "c49eb82e252a0588ec584b530ed228011d134b7f29bc26650b5871993f80c0b5"
    ),
    "0106_admin_input_refusal_log.sql": (
        "a7eda23b477757f1155947c7c8fbef02d1d5dc5c7d6c5cc55c481cf034c82de4"
    ),
    "0107_proposal_review_edit.sql": (
        "460a43df40c847fa725ac7699ba4a97a4941b3e4f1352a792b97d6d0a6fe823c"
    ),
    "0108_conflict_resolution.sql": (
        "13074c17577c9158b464f03462c7b69c482f62fb56d35cde2591afdd93a13d59"
    ),
    "0109_point_in_time_exports.sql": (
        "dfdb910d2ccd4aeea4787222a85e3080ef6b272c37ccddf3eb6329163c96cdee"
    ),
    "0110_financial_local_days.sql": (
        "f89490777c7145a1b0213dc2976d4c1c2bc14289fe08926c502667f0ca9cb5d8"
    ),
    "0111_setup_slack_off.sql": (
        "ca795eb61592697af8dac8172add7e213d1c183319004227b96e57ed94798b86"
    ),
    "0112_upstream_at_check.sql": (
        "2ad93429256bc20dfc47c6f676981b13a993835c6e83ec70dbf8977a74776ffd"
    ),
    "0113_census_handling_registry.sql": (
        "a6c031014875eea7c98aa66a488e9f38e417ad8c3dbf38992622590e852cda1c"
    ),
    "0114_no_sign_in_age.sql": (
        "4948755ee2960e59f422a4253d637afc8f9e02b1d161e6608ea90bc977c8f9c0"
    ),
    "0115_mail_halt_clear.sql": (
        "517dcb53a4790e92c58addd1f0cd65809f1c7dfdaef405f6a178332e93e63198"
    ),
    "0116_sent_mail_recipients.sql": (
        "42f1fe9ef354c7239f22c98a56262eb1a40ffb2cc63410edf9e8a2faf8de7d6a"
    ),
    "0117_log_search_indexes.sql": (
        "6041159c2c6b3e86cd23bbcbd3979c335662ae0371cd8a1904b364c319b05b81"
    ),
    "0118_gender_by_hand.sql": (
        "5b2cc690bc1f6fdade66a22618290b959145989cd4d650675bda755d4df34ea8"
    ),
    "0119_leader_request_contacts.sql": (
        "777fe95c886554e65f131c169ce9bd9d1ad72a6b0b54d0c474547372c30241ee"
    ),
    "0120_leader_emails.sql": (
        "982e9bc23b68c7a5e953122b8f6eda0310b66b5f62340d475314480166f0468a"
    ),
    "0121_digest_skip_dates.sql": (
        "cc88c09f4be01751997f1144be6ac2ade39bc3a760f90dc99baf09da17ced446"
    ),
    "0122_automatic_report_views.sql": (
        "b686ede20806893936ee416bd3829cc65adfa6e6857f4b7ad02233177b4744ae"
    ),
    "0123_sign_in_leftovers.sql": (
        "8894706e2b79c2e72ecfba500995bfc6c4c281363feb5b235a084678bac22d8c"
    ),
    "0124_guard_wording.sql": (
        "cbe60936ca8d92a473280cbb173b2c0c379b295135eef83b114750c7b2ce035c"
    ),
    "0125_secret_request_session.sql": (
        "aaa27b6bb22e3e3df0723f659195396fd944b2137cc10111a130f7cc5ee982fd"
    ),
    "0126_information_limitation_review.sql": (
        "4acebc765834c122f5a007e690658b6a99280b9e8a56f066250841e19a7eb669"
    ),
    "0127_pledge_review.sql": (
        "bb2729437fdec314ee1c79d3fef4e3c8ffa0a4f372ded37b21b1bc68f54afeb4"
    ),
    "0128_census_pipeline_review.sql": (
        "a586f0a0100242e02ee493f9baaf8fb8116efea5525dc6329cb35072239be7c1"
    ),
    "0129_census_additions.sql": (
        "a63622c0286872d9fea2ed9fd2cbc8dc09ea338a4aea3089f3cd550762ea44c8"
    ),
    "0130_census_address_guard.sql": (
        "3c1ede0cb9790113bffc6d57d71da1aa6371defc3ff28b754bd8acf6d04a0f9e"
    ),
    "0131_ministry_review_pipeline.sql": (
        "a2c82ffe3c9e967b4d0c0d1599cbcc93c8658fa97235cfe9c37246c67372926b"
    ),
    "0132_refresh_checks.sql": (
        "850b796b28d7cd472fee463fc6e119b73c33150ca920c899d1d69d400f84ed9d"
    ),
    "0133_entered_incorrectly_fixes.sql": (
        "a482e1594c86f1c27820c4901171c055f1b3490615f92da97b76d4eca904e312"
    ),
    "0134_pledge_refresh_checks.sql": (
        "18c6c6c4053ce6470a3db4586f486bfd76716417e34903a695e7aff871eed912"
    ),
    "0135_pledge_conflicts.sql": (
        "958caff9e59ffe8464f3a71a2fe21582e8372b3371b4f2a46186a0a019f94ab7"
    ),
    "0136_submission_upcoming_pledge.sql": (
        "b66fcf15d06818dc6781b5a5813df647282dbe1f1dfb30ff4edf922696bd64cf"
    ),
    "0137_census_refresh_sources.sql": (
        "5e855dbbfaffb607d16f4c9d5ff25bf443c021a2cc853a5c9f30ee2f5a1a94f3"
    ),
    "0138_published_refresh_conflicts.sql": (
        "9e24ed6ab1b4a65d1c9081a976b562a2b7720e4fe383ef115548207d4df5759f"
    ),
    "0139_new_member_id_correction.sql": (
        "6a6a5744da0c33c346213a5a178e55100141f073ac76cba98c04072b0200c8ae"
    ),
    "0140_edited_ministry_contacts.sql": (
        "fca7ffa4f76a74ccc422264b303b79a4af5c859f55017abf6f3267fcba4e223c"
    ),
    "0141_added_member_links.sql": (
        "01bf1e8b13a9f293d3c3713984a6b8e3cfab64288b22784688e10ff02d917820"
    ),
    "0142_participation_export_summary.sql": (
        "e8eecd8555cfd8f4cfee650b6974b92551ab1f52d592b3ae0b513445d5fce073"
    ),
    "0143_reviewed_form_shown_items.sql": (
        "5071598637b62aed4cce02362f86a7e67cb1273996387308a95969aa064f0a66"
    ),
    "0144_staff_timeline_export.sql": (
        "adfde07b256d584b99de8ae7cdc2910cd982bf4bc1d505404e7b4fc957b522b6"
    ),
    "0145_page_member_names.sql": (
        "9391e8c99018c8b7c5f7c996d4530e89d4d533864876cb2d5dc859321f1cc3b9"
    ),
    "0158_ignored_note_optional.sql": (
        "2ad8ec09cab596393c02b32175b1510a163068a4953ba72ff1b5388744fe063a"
    ),
    "0159_strict_phase_order.sql": (
        "6e22e18665523982160c73cbba0802e1458b39d55c6b1e7d184c50a56fb19579"
    ),
    "0160_resubmission_phase_order.sql": (
        "59c093bb0fef3623bcb4e927fe4643ac77dc594afba2e16ea553963833b7cee7"
    ),
    "0163_edit_replacement.sql": (
        "afd603b45720460dde761376f034f255cc49633d3d83bdbf348d03e7a6849ed6"
    ),
    "0146_census_terminal_undo.sql": (
        "4bdbbda58daa0aecda124bb36bab4157c2898d27a5d86392e5833d0c7684c456"
    ),
    "0147_census_addition_undo.sql": (
        "eb1adbd6ae93bf45a258b9ce780327fdda4136654c416224c13aada41711cade"
    ),
    "0148_ministry_terminal_undo.sql": (
        "86e61414d56fc3c6679fd92da0b5cdb59e50babcbe744e6223075157e4025837"
    ),
    "0149_pledge_terminal_undo.sql": (
        "f6a3f9f033a7762572b632638ef81267c485c7a89c4bbee6a4b1ab6bd27f8b64"
    ),
    "0150_information_handled_undo.sql": (
        "be6227c224f3b1e0308a77c0dc2b4d5f77f63f1fc86e333c57074eea49a7ab26"
    ),
    "0151_limitation_handled_undo.sql": (
        "576d22f2d53cb5093794e599ef8f8a0384a187dcd9ee2c8d8505b9291d5d020e"
    ),
    "0152_audit_actor_time.sql": (
        "1f7ac38ef615a918afcc7a6ff87d743484b013b5af31b5174b743c87868b96fb"
    ),
    "0153_reviewed_census_resubmission.sql": (
        "a6f73dc9bd0bffa4d2e8527ba33e45cf3da1c4029e7d6aa1def2f4106f4d92b9"
    ),
    "0154_reviewed_ministry_resubmission.sql": (
        "ebfd4ab090cde6e8676a42dd4ec73ada1b4c116b7bc533c994efdf52da81435d"
    ),
    "0155_reviewed_pledge_resubmission.sql": (
        "c58fed6f43b79cd5bf5c6b8979df949d68cd461d7be04c816afc3a38055427f3"
    ),
    "0156_report_pipeline.sql": (
        "dc04b332bc79d4ebeb52f4349e426744b79a83aac233fe9eea833ede7ee43a69"
    ),
    "0157_person_identity_reads.sql": (
        "85466fd212f85c2efe5d184d2485fad66a5022df25df531b235020ecdd85cb37"
    ),
    "0161_report_conversion.sql": (
        "3e723f78dcd1ac826e1d7b31309f34f513d6d9e31c23e713e279b41fc9f8a016"
    ),
    "0162_report_recovery_coverage.sql": (
        "4d10bc449e224f7434df25cb695981a534bb2c469c1f319e5b5f67137f8e0d5f"
    ),
    "0164_report_coverage_decisions.sql": (
        "17a536b18846e11bfe8ad6c7e5c5549b998d7d4bb38a2cfcb2eb3ed3fd6ae620"
    ),
    "0165_report_waiting_read.sql": (
        "0ae340a4e116bf517217d3ac8ce9ae58e8d707d689c213a0199b8db9966bcc31"
    ),
    "0166_share_short_labels.sql": (
        "e43201c7e55cdf8ce46508cf48c6e56490cf7d2b807dcb9cab9537fdfa1f7409"
    ),
    "0167_address_leader_scope.sql": (
        "46866190315b2caf7787d75760e1931e703a16aed03666d2b38b1f21961d1297"
    ),
    "0168_setup_test_binding.sql": (
        "592ca793da55098a9a7b7b7ca864795480f8c57943a1bceda465be6643f7dd9e"
    ),
    "0169_ministry_leader_coverage.sql": (
        "a155a572cc3c9190a6a31ba8a8879cc4fe3e4019e009c620c91fb7b71b8d7264"
    ),
    "0170_parish_added_shown.sql": (
        "464837acb882063ecf8374fe318cb9a87ead789a59dbb9b7a1672e6e5e555d73"
    ),
    "0171_retire_digests.sql": (
        "b008823233c276bf35c938237f85cb0f709c2023e70b5b7911fb48cc9c07d79a"
    ),
    "0172_family_campaign_statistics.sql": (
        "8b71ec22ab4f5432cc938a0b9cb285b3ecfd9a5b042fa6948b60350f55e3396d"
    ),
    "0173_report_email_test.sql": (
        "f112d010b3feb86f79adcc3e48d38781f7cf66970eaf3104719fd9c416bedd35"
    ),
    "0174_parishsoft_organization_choice.sql": (
        "133e6978d1421afd54db67cefe5a373f7648e16d95c4d33288dda84a57253acd"
    ),
}


# A frozen file's ``DROP FUNCTION IF EXISTS public.name(``.
DROPPED_FUNCTION = re.compile(r"^DROP FUNCTION IF EXISTS public\.(\w+)\(", re.M)


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
    dropped = set()
    for path in sorted(MIGRATIONS.glob("*.sql")):
        text = path.read_text(encoding="utf-8")
        migration_owned |= function_bodies(text, replace=False).keys()
        replaced = function_bodies(text, replace=True)
        latest.update(replaced)
        # A later file that drops a function (#1267's 0091 drops the Ministry
        # activity projection) leaves no baseline copy to match.
        dropped -= replaced.keys()
        dropped |= set(DROPPED_FUNCTION.findall(text))
    assert latest, "no replaced functions found"
    for name, body in latest.items():
        if name in migration_owned:
            # Created by a frozen file, so every install path runs the
            # migration that replaces it; there is no baseline copy to match.
            continue
        if name in dropped and name not in baseline:
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


# The views whose final definition reads a table or view that only a frozen
# file creates (#1786's 0161 reads the report email records of 0156): the
# baseline cannot create them in that form, since a view is checked when it
# is created, so it keeps the last definition it can install and the frozen
# file replaces it on every install path. The upgrade-parity test compares
# the results.
MIGRATION_FINAL_VIEWS = {
    "0161_report_conversion.sql": frozenset(
        {
            "stewardship_admin_digest_work_row",
            "stewardship_schedule_work_row",
            "stewardship_delivery_digest_message",
            "stewardship_delivery_digest_recovery",
            "stewardship_delivery_closed_coverage",
            "stewardship_delivery_closed_coverage_summary",
            "stewardship_withdrawal_inventory",
        }
    ),
}


def test_latest_migration_copy_of_each_replaced_view_equals_the_baseline():
    """A replaced view's latest frozen text is the fresh-install baseline's.

    Except a view in ``MIGRATION_FINAL_VIEWS``, whose latest text must name
    an object a frozen file creates (else it belongs in the baseline).
    """
    baseline, created = {}, set()
    for path in SCHEMA.glob("*.sql"):
        baseline.update(view_bodies(path.read_text(encoding="utf-8"), replace=False))
    latest, final = {}, {}
    for path in sorted(MIGRATIONS.glob("*.sql")):
        text = path.read_text(encoding="utf-8")
        replaced = view_bodies(text, replace=True)
        latest.update(replaced)
        for name in replaced:
            final[name] = name in MIGRATION_FINAL_VIEWS.get(path.name, ())
        created |= set(view_bodies(text, replace=False))
    assert "stewardship_delivery_family_recovery" in latest
    for name, body in latest.items():
        # A view a frozen file created is replaced freely, as a migration-
        # owned function is: every install path runs the migrations.
        if name in created and name not in baseline:
            continue
        assert name in baseline, f"{name} is replaced but has no baseline view"
        if final[name]:
            assert "stewardship_report_email" in body, f"{name} belongs in the baseline"
            continue
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


# The pre-0102 bodies the setup load void tests reinstall
# (tests/stewardship/database/pre_setup_load_void_functions.sql), copied byte
# for byte from the fresh-install baseline at 1a585debf, the head migration
# 0102 (#1353) was written on. Pinned so an edit to the fixture fails here.
PRE_SETUP_LOAD_VOID_FIXTURE = (
    ROOT / "tests/stewardship/database/pre_setup_load_void_functions.sql"
)
PRE_SETUP_LOAD_VOID_DIGEST = (
    "2ee01d4987ea6dd0a8343704090c27c18e574e616773806f737683adb5457a45"
)


def test_the_pre_setup_load_void_fixture_is_unchanged():
    """The setup load void tests prove 0102's check on exactly the earlier bodies."""
    digest = hashlib.sha256(PRE_SETUP_LOAD_VOID_FIXTURE.read_bytes()).hexdigest()
    assert digest == PRE_SETUP_LOAD_VOID_DIGEST

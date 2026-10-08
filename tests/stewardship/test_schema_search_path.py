"""Which schema functions may run without a fixed ``search_path`` (#389 L9).

A function with no ``SET search_path`` resolves unqualified names through
its caller's path. That is safe only for SECURITY INVOKER functions whose
callers cannot plant objects on that path, and admission refuses every
login CREATE on any schema and TEMP (``credential_database.admit_grants``,
``runtime_database.require_no_temporary_authority``). The functions below
predate the rule that every function sets one; they are all invokers
(mostly row guards) and were left as they are rather than altered in a
migration. Every new function signature, and every definer, must set
``search_path``: this list only ever shrinks.

Functions are keyed by full signature (name and argument types), so an
overload of a listed function is a new function. A schema file pins a
function when, after that file runs, the function has ``SET search_path``
from its ``CREATE`` (written before or after the body) or from a later
``ALTER FUNCTION`` in the same file. A function is a definer when any
``CREATE`` or ``ALTER FUNCTION`` makes it one, which is the documented
pattern for frozen migrations because ``CREATE OR REPLACE`` drops the
attribute. A listed function that gains a path in a migration leaves the
list only once its baseline file pins it too.
"""

import re
from pathlib import Path

import pytest

SCHEMA = Path(__file__).parents[2] / "src/parishkit/stewardship/schema"
COMMENT = re.compile(r"--[^\n]*")
STATEMENT = re.compile(
    r"\b(?:(CREATE\s+(?:OR\s+REPLACE\s+)?FUNCTION)|ALTER\s+FUNCTION)"
    r"\s+(?:public\.)?(\w+)\s*\(",
    re.I,
)
DOLLAR = re.compile(r"\$(\w*)\$")
SETS_PATH = re.compile(r"\bSET\s+search_path\b", re.I)
RESETS_PATH = re.compile(r"\bRESET\s+(?:search_path|ALL)\b", re.I)
# Leading words of multi-word types, which are not parameter names.
TYPE_STARTS = frozenset({"bit", "character", "double", "interval", "time", "timestamp"})
ALIASES = {
    "bool": "boolean",
    "int": "integer",
    "int4": "integer",
    "int8": "bigint",
    "timestamp with time zone": "timestamptz",
    "varchar": "character varying",
}
# The reviewed invoker functions without a fixed search_path, by signature.
UNPINNED = frozenset(
    {
        "stewardship_activation_catchup_mutable_v1()",
        "stewardship_address_grant_immutable_v1()",
        "stewardship_address_rule_immutable_v1()",
        "stewardship_admin_revocation_immutable_v1()",
        "stewardship_applied_integration_immutable_v1()",
        "stewardship_assignment_overlay_mutable_v1()",
        "stewardship_audit_context_immutable_v1()",
        "stewardship_audit_event_immutable_v1()",
        "stewardship_auth_incident_mutable_v1()",
        "stewardship_automation_notice_ack_immutable_v1()",
        "stewardship_automation_notice_immutable_v1()",
        "stewardship_backup_run_immutable_v1()",
        "stewardship_branding_asset_immutable_v1()",
        "stewardship_branding_asset_insert_v1()",
        "stewardship_branding_bundle_mutable_v1()",
        "stewardship_campaign_artwork_v1()",
        "stewardship_campaign_boundary_mutable_v1()",
        "stewardship_campaign_config_abort_immutable_v1()",
        "stewardship_campaign_config_intent_immutable_v1()",
        "stewardship_campaign_configuration_immutable_v1()",
        "stewardship_campaign_control_immutable_v1()",
        "stewardship_campaign_credentials_mutable_v1()",
        "stewardship_campaign_mail_test_mutable_v1()",
        "stewardship_campaign_mutable_v1()",
        "stewardship_campaign_transition_immutable_v1()",
        "stewardship_campaign_work_gate_mutable_v1()",
        "stewardship_catchup_checkpoint_immutable_v1()",
        "stewardship_catchup_failure_immutable_v1()",
        "stewardship_chair_reconciliation_immutable_v1()",
        "stewardship_chair_review_mutable_v1()",
        "stewardship_chair_seed_evidence_immutable_v1()",
        "stewardship_chair_seed_intent_immutable_v1()",
        "stewardship_cleanup_inventory_v1(uuid)",
        "stewardship_config_activation_immutable_v1()",
        "stewardship_config_checkpoint_immutable_v1()",
        "stewardship_config_request_immutable_v1()",
        "stewardship_configuration_version_immutable_v1()",
        "stewardship_content_version_immutable_v1()",
        "stewardship_credential_consumer_ack_immutable_v1()",
        "stewardship_credential_deployment_mutable_v1()",
        "stewardship_credential_key_state_mutable_v1()",
        "stewardship_critical_event_ack_immutable_v1()",
        "stewardship_daily_fact_set_mutable_v1()",
        "stewardship_domain_rule_immutable_v1()",
        "stewardship_due_work_critical_v1(text, timestamptz, timestamptz, integer, timestamptz)",  # noqa: E501
        "stewardship_due_work_since_v1(text, timestamptz, timestamptz, timestamptz, text, timestamptz, timestamptz)",  # noqa: E501
        "stewardship_fact_compaction_guard()",
        "stewardship_fact_compaction_immutable_v1()",
        "stewardship_fact_compaction_pair_guard()",
        "stewardship_fact_day_guard()",
        "stewardship_fact_delete_pair_guard()",
        "stewardship_fact_demand_guard()",
        "stewardship_fact_demand_mutable_v1()",
        "stewardship_fact_disposable(uuid)",
        "stewardship_fact_pin_mutable_v1()",
        "stewardship_fact_pointer_mutable_v1()",
        "stewardship_fact_reference_guard()",
        "stewardship_fact_set_guard()",
        "stewardship_fact_source_pin_guard()",
        "stewardship_family_campaign_mutable_v1()",
        "stewardship_family_code_mac_immutable_v1()",
        "stewardship_family_eligibility_immutable_v1()",
        "stewardship_family_session_mutable_v1()",
        "stewardship_family_token_generation_mutable_v1()",
        "stewardship_family_token_mutable_v1()",
        "stewardship_hosted_file_mutable_v1()",
        "stewardship_limiter_health_mutable_v1()",
        "stewardship_ministry_activity_immutable_v1()",
        "stewardship_ministry_assignment_immutable_v1()",
        "stewardship_occurrence_transition_immutable_v1()",
        "stewardship_operational_log_immutable_v1()",
        "stewardship_operational_log_writer_v1()",
        "stewardship_ops_cohort_immutable_v1()",
        "stewardship_ops_incident_mutable_v1()",
        "stewardship_ops_log_receipt_immutable_v1()",
        "stewardship_ops_notice_immutable_v1()",
        "stewardship_ops_recipient_immutable_v1()",
        "stewardship_parish_branding_v1()",
        "stewardship_parish_immutable_v1()",
        "stewardship_policy_epoch_immutable_v1()",
        "stewardship_policy_security_ack_immutable_v1()",
        "stewardship_policy_security_event_immutable_v1()",
        "stewardship_portal_session_mutable_v1()",
        "stewardship_portal_user_mutable_v1()",
        "stewardship_postclose_resolution_immutable_v1()",
        "stewardship_production_cancellation_immutable_v1()",
        "stewardship_provider_context_immutable_v1()",
        "stewardship_provider_context_insert_v1()",
        "stewardship_public_credential_handoff_immutable_v1()",
        "stewardship_public_handoff_insert_v1()",
        "stewardship_refresh_attempt_guard_v1()",
        "stewardship_refresh_command_guard_v1()",
        "stewardship_refresh_fallback_guard_v1()",
        "stewardship_refresh_snapshot_completion_v1()",
        "stewardship_rehearsal_credential_mutable_v1()",
        "stewardship_rehearsal_epoch_mutable_v1()",
        "stewardship_rehearsal_reservation_immutable_v1()",
        "stewardship_restore_delivery_hold_mutable_v1()",
        "stewardship_restore_hold_resolution_immutable_v1()",
        "stewardship_runtime_transition_immutable_v1()",
        "stewardship_schedule_definition_mutable_v1()",
        "stewardship_schedule_fulfillment_immutable_v1()",
        "stewardship_schedule_occurrence_mutable_v1()",
        "stewardship_schedule_revision_immutable_v1()",
        "stewardship_schedule_selection_immutable_v1()",
        "stewardship_secret_checkpoint_immutable_v1()",
        "stewardship_secret_request_mutable_v1()",
        "stewardship_security_cohort_immutable_v1()",
        "stewardship_security_recipient_immutable_v1()",
        "stewardship_setup_attempt_mutable_v1()",
        "stewardship_setup_completion_immutable_v1()",
        "stewardship_setup_config_abort_immutable_v1()",
        "stewardship_setup_config_intent_immutable_v1()",
        "stewardship_setup_credential_install_immutable_v1()",
        "stewardship_setup_draft_section_mutable_v1()",
        "stewardship_setup_mail_delivery_mutable_v1()",
        "stewardship_setup_mail_exchange_mutable_v1()",
        "stewardship_setup_prepared_immutable_v1()",
        "stewardship_setup_readiness_binding_immutable_v1()",
        "stewardship_setup_sealed_credential_mutable_v1()",
        "stewardship_setup_slack_delivery_mutable_v1()",
        "stewardship_setup_source_exchange_mutable_v1()",
        "stewardship_setup_source_result_immutable_v1()",
        "stewardship_source_compaction_immutable_v1()",
        "stewardship_source_corpus_evidence(uuid)",
        "stewardship_source_current_guard()",
        "stewardship_source_current_mutable_v1()",
        "stewardship_source_lease_mutable_v1()",
        "stewardship_source_lease_owner_guard()",
        "stewardship_source_pin_guard()",
        "stewardship_source_pin_mutable_v1()",
        "stewardship_source_promotion_pair_guard()",
        "stewardship_source_refresh_attempt_immutable_v1()",
        "stewardship_source_refresh_command_immutable_v1()",
        "stewardship_source_refresh_fallback_immutable_v1()",
        "stewardship_source_refresh_request_immutable_v1()",
        "stewardship_source_refresh_tick_immutable_v1()",
        "stewardship_source_snapshot_guard()",
        "stewardship_source_snapshot_mutable_v1()",
        "stewardship_system_configuration_mutable_v1()",
        "stewardship_task_event_immutable_v1()",
        "stewardship_task_run_mutable_v1()",
    }
)


def _signature(name, args):
    """``name(type, ...)`` for a declared argument list, the way PostgreSQL
    identifies a function: parameter names, defaults and OUT arguments do
    not count, and common type aliases share one spelling."""
    types = []
    for arg in re.split(r",(?![^()]*\))", args):
        words = re.split(r"\s(?:DEFAULT\b|=)", arg, maxsplit=1, flags=re.I)[0].split()
        mode = words[0].upper() if words else ""
        if mode in {"IN", "OUT", "INOUT", "VARIADIC"}:
            words.pop(0)
        if mode == "OUT":
            continue
        if len(words) > 1 and words[0].lower() not in TYPE_STARTS:
            words = words[1:]  # the parameter name
        if words:
            spelled = " ".join(words).lower()
            types.append(ALIASES.get(spelled, spelled))
    return f"{name}({', '.join(types)})"


def _statements(text):
    """Each ``CREATE`` or ``ALTER FUNCTION`` in ``text``, in order, as
    ``(created, signature, attributes)``.

    A ``CREATE``'s attributes are everything outside its argument list and
    body, so ``SET search_path`` written after the closing dollar quote
    counts. The scan resumes after each body, so SQL inside a body is not
    read as a statement.
    """
    text = COMMENT.sub("", text)
    position = 0
    while match := STATEMENT.search(text, position):
        depth, end = 1, match.end()
        while depth:  # the balanced argument list
            depth += {"(": 1, ")": -1}.get(text[end], 0)
            end += 1
        signature = _signature(match[2], text[match.end() : end - 1])
        if match[1]:
            body = DOLLAR.search(text, end)
            closing = text.index(body.group(), body.end()) + len(body.group())
            position = text.index(";", closing)
            attributes = text[end : body.start()] + text[closing:position]
        else:
            position = text.index(";", end)
            attributes = text[end:position]
        yield bool(match[1]), signature, attributes


def _search_paths(files):
    """``(unpinned, definers)`` over ``(name, text)`` schema files.

    ``unpinned`` holds each signature that some file creates, or resets,
    and leaves without a fixed path; ``definers`` holds each signature that
    any statement makes SECURITY DEFINER.

    Every ``ALTER FUNCTION`` must name a signature some ``CREATE`` produced.
    Otherwise a type spelled differently there (``int2`` for ``smallint``,
    say) would file a definer under a signature no ``CREATE`` pins or
    leaves unpinned, and the definer test would pass without it; the scan
    fails closed instead.
    """
    unpinned, definers, creates, alters = set(), set(), set(), set()
    for _, text in files:
        pinned = {}  # signature -> path fixed after this file so far
        for created, signature, attributes in _statements(text):
            (creates if created else alters).add(signature)
            if SETS_PATH.search(attributes):
                pinned[signature] = True
            elif created or RESETS_PATH.search(attributes):
                pinned[signature] = False
            if re.search(r"\bSECURITY\s+DEFINER\b", attributes, re.I):
                definers.add(signature)
        unpinned |= {signature for signature, fixed in pinned.items() if not fixed}
    assert alters <= creates, f"ALTER FUNCTION of no CREATE: {sorted(alters - creates)}"
    return unpinned, definers


def _schema_files():
    """Every baseline and migration schema file, as ``(name, text)``."""
    for path in sorted([*SCHEMA.glob("*.sql"), *SCHEMA.glob("migrations/*.sql")]):
        yield path.name, path.read_text(encoding="utf-8")


def test_no_new_function_lacks_a_fixed_search_path():
    """A function without ``SET search_path`` must be on the reviewed list."""
    missing, _ = _search_paths(_schema_files())
    # A function that gained one leaves the list, so it cannot regress.
    assert missing == UNPINNED, {
        "new without search_path": sorted(missing - UNPINNED),
        "now set, drop from the list": sorted(UNPINNED - missing),
    }


def test_every_definer_sets_its_search_path():
    """A SECURITY DEFINER body runs as its owner, so it never trusts the path."""
    unpinned, definers = _search_paths(_schema_files())
    assert definers, "the scan found no definers"
    assert not sorted(definers & unpinned)


def test_scan_reads_alter_function_trailing_attributes_and_overloads():
    """The scan sees a definer made by ``ALTER FUNCTION``, a path set after
    the body or by ``ALTER``, a reset, and overloads as distinct functions."""
    unpinned, definers = _search_paths(
        [
            (
                "a.sql",
                """
                CREATE FUNCTION public.f(p_id uuid, p_n integer DEFAULT 0)
                RETURNS void LANGUAGE sql AS $$ SELECT 1; $$;
                -- ALTER FUNCTION public.ignored() SECURITY DEFINER;
                ALTER FUNCTION public.f(uuid, int4) SECURITY DEFINER;
                CREATE OR REPLACE FUNCTION f(p_id uuid) RETURNS void
                LANGUAGE plpgsql AS $body$ BEGIN
                    EXECUTE 'ALTER FUNCTION g() SECURITY DEFINER';
                END $body$ SET search_path TO pg_catalog;
                CREATE FUNCTION public.g(OUT total bigint) RETURNS bigint
                LANGUAGE sql AS $$ SELECT 1::bigint; $$;
                ALTER FUNCTION public.g() SET search_path TO pg_catalog;
                CREATE FUNCTION public.h(at timestamp with time zone)
                RETURNS void SET search_path TO pg_catalog
                LANGUAGE sql AS $$ SELECT 1; $$;
                ALTER FUNCTION public.h(timestamptz) RESET search_path;
                """,
            )
        ]
    )
    assert unpinned == {"f(uuid, integer)", "h(timestamptz)"}
    assert definers == {"f(uuid, integer)"}


def test_scan_refuses_an_alter_of_a_signature_no_create_produced():
    """A differently spelled ``ALTER`` fails the scan instead of hiding."""
    with pytest.raises(AssertionError, match=r"no CREATE: \['f\(int2\)'\]"):
        _search_paths(
            [
                (
                    "a.sql",
                    """
                    CREATE FUNCTION public.f(p_n smallint) RETURNS void
                    LANGUAGE sql AS $$ SELECT 1; $$;
                    ALTER FUNCTION public.f(int2) SECURITY DEFINER;
                    """,
                )
            ]
        )

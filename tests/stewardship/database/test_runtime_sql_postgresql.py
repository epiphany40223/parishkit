"""Operational prerequisite: callers cannot redirect durable trigger effects."""

from datetime import timedelta
from importlib import import_module
from uuid import uuid4

import pytest
from django.db import connection
from django.utils import timezone

from parishkit.stewardship.accounts.authority import AuthorityStore
from parishkit.stewardship.accounts.configuration_installation import (
    install_request,
    prepare_initial_configuration,
)
from parishkit.stewardship.accounts.configuration_requests import record_request
from parishkit.stewardship.accounts.configuration_schema import validate_sections
from parishkit.stewardship.accounts.secret_requests import stage_secret_request

from ..configuration_factory import configuration_version
from ..test_request_patch import parish_patch

pytestmark = pytest.mark.django_db(transaction=True)


def test_legacy_emitters_and_helpers_have_trusted_search_paths():
    """Every named legacy function pins catalog/public before temporary objects."""
    migration = import_module(
        "parishkit.stewardship.accounts.migrations.0033_trusted_trigger_paths"
    )
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT p.proname,p.proconfig FROM pg_proc p "
            "JOIN pg_namespace n ON n.oid=p.pronamespace "
            "WHERE n.nspname='public' AND p.proname=ANY(%s)",
            [list(migration.FUNCTIONS)],
        )
        rows = dict(cursor.fetchall())
    assert rows.keys() == set(migration.FUNCTIONS)
    assert all(
        "search_path=pg_catalog, public, pg_temp" in options
        for options in rows.values()
    )


@pytest.mark.parametrize(
    "module,operation,names",
    [
        (
            "0006_runtime_guards",
            "restore_predecessors",
            [
                "stewardship_runtime_guard_v1",
                "stewardship_activation_guard_v1",
                "stewardship_activation_effects_v1",
                "stewardship_campaign_pointer_v1",
                "stewardship_campaign_runtime_v1",
                "stewardship_campaign_activate_v1",
            ],
        ),
        (
            "0016_exceptional_abort_guards",
            "restore_checkpoint",
            [
                "stewardship_request_checkpoint_v2",
            ],
        ),
    ],
)
def test_partial_downgrade_helpers_preserve_trusted_function_paths(
    module, operation, names
):
    """Historical CREATE OR REPLACE must not erase a still-applied security pin."""
    migration = import_module("parishkit.stewardship.campaigns.migrations." + module)
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT pg_get_functiondef(p.oid) FROM pg_proc p JOIN pg_namespace n "
            "ON n.oid=p.pronamespace WHERE n.nspname='public' AND p.proname=ANY(%s)",
            [names],
        )
        originals = [row[0] for row in cursor.fetchall()]
    assert len(originals) == len(names)
    try:
        with connection.schema_editor() as editor:
            getattr(migration, operation)(None, editor)
        test_legacy_emitters_and_helpers_have_trusted_search_paths()
    finally:
        with connection.cursor() as cursor:
            for definition in originals:
                cursor.execute(definition)


def test_actual_emitters_ignore_temporary_audit_shadow(tmp_path):
    """Bootstrap, request checkpoints, activation and secrets reach real audit.

    The privileged disposable test deliberately supplies a temporary shadow,
    even though production runtime roles will additionally lack TEMP authority.
    This proves emitter safety independently of that defense in depth.
    """
    store = AuthorityStore(tmp_path, validate_sections)
    root, actor = configuration_version(), uuid4()
    with connection.cursor() as cursor:
        cursor.execute(
            "CREATE TEMP TABLE stewardship_audit_event "
            "(LIKE public.stewardship_audit_event INCLUDING DEFAULTS)"
        )
        cursor.execute("SET search_path=pg_temp,public")
    try:
        prepare_initial_configuration(
            store,
            root,
            testing_recipient="test@example.org",
            actor_id=actor,
            correlation_id=uuid4(),
        )
        request = record_request(
            base_digest=root.digest,
            patch=parish_patch(root, name="Updated parish"),
            actor_id=actor,
            request_key=uuid4(),
            correlation_id=uuid4(),
        )
        assert (
            install_request(
                store, request_id=request.request_id, correlation_id=uuid4()
            ).state
            == "applied"
        )
        stage_secret_request(
            request_id=uuid4(),
            target="parishsoft",
            staging_reference=uuid4(),
            actor_id=actor,
            reauthenticated_at=timezone.now() - timedelta(minutes=1),
            expires_at=timezone.now() + timedelta(minutes=10),
            expected_fingerprint="a" * 64,
            correlation_id=uuid4(),
        )
        with connection.cursor() as cursor:
            cursor.execute("SELECT count(*) FROM pg_temp.stewardship_audit_event")
            assert cursor.fetchone()[0] == 0
            cursor.execute(
                "SELECT event_type,count(*) FROM public.stewardship_audit_event "
                "GROUP BY event_type"
            )
            events = dict(cursor.fetchall())
        assert events["configuration_activated"] == 2
        assert events["config_request_staged"] == 1
        assert events["config_request_applied"] == 1
        assert events["secret_request_staged"] == 1
    finally:
        with connection.cursor() as cursor:
            cursor.execute("RESET search_path")
            cursor.execute("DROP TABLE pg_temp.stewardship_audit_event")

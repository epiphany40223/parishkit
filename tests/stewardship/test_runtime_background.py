"""Compiled background startup rejects missing dependencies before serving work."""

from dataclasses import replace
from threading import Event
from types import SimpleNamespace

import pytest

from parishkit.config import ConfigError
from parishkit.stewardship import runtime_background as background
from parishkit.stewardship.deployment import ServiceRole

from .test_runtime_topology import configuration_at


@pytest.mark.parametrize("entry", ["configuration", "process"])
def test_fresh_process_admission_precedes_django_model_imports(entry):
    """Pytest's initialized app registry cannot conceal a cold-start import bug."""
    import os
    import subprocess
    import sys

    script = """
import sys
from dataclasses import replace
from threading import Event
from types import SimpleNamespace
from django.conf import settings
from parishkit.stewardship.deployment import ServiceRole, load_deployment
from parishkit.stewardship import runtime_background, runtime_process
from parishkit.stewardship import service_boundaries
assert not settings.configured
configuration = replace(load_deployment(environ={}), service_role=ServiceRole.WORKER)
def stop(*args, **kwargs):
    raise RuntimeError('reached-admission')
try:
    if sys.argv[1] == 'configuration':
        service_boundaries.admit_online_service = stop
        runtime_background.configure_background(
            configuration, stop=Event(), heartbeat=lambda: None)
    else:
        runtime_background.configure_background = stop
        runtime_process.serve_background(
            configuration, SimpleNamespace(check=lambda: None))
except RuntimeError as error:
    assert str(error) == 'reached-admission'
else:
    raise AssertionError('Admission was not reached')
assert not settings.configured
"""
    environment = dict(os.environ)
    environment.pop("DJANGO_SETTINGS_MODULE", None)
    result = subprocess.run(
        [sys.executable, "-c", script, entry],
        env=environment,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr


@pytest.fixture
def admitted_configuration(tmp_path, monkeypatch):
    """Use real purpose-key files; substitute only kernel/SQL process admission."""
    from parishkit.stewardship.accounts.cryptography import (
        CodeMacKeyring,
        GeneralKeyring,
        Key,
        TokenPrivateKeyring,
    )
    from parishkit.stewardship.accounts.key_files import (
        serialize_keyring,
        write_private,
    )

    configuration = configuration_at(tmp_path)
    rings = SimpleNamespace(
        general=GeneralKeyring([Key("g1", "active", b"g" * 32)]),
        mac=CodeMacKeyring([Key("m1", "active", b"m" * 32)]),
        public=TokenPrivateKeyring([Key("t1", "active", b"t" * 32)]).public(),
    )
    secrets = {}
    for name, ring in (
        ("general_encryption", rings.general),
        ("family_code_mac", rings.mac),
        ("token_public", rings.public),
    ):
        secrets[name] = tmp_path / name
        write_private(secrets[name], serialize_keyring(ring))
    secrets["parishsoft"] = tmp_path / "parishsoft"
    write_private(secrets["parishsoft"], b"synthetic-private-api-key")
    write_private(configuration.valkey.password_file, b"synthetic-broker-key")
    calls = []
    monkeypatch.setattr(
        "parishkit.stewardship.service_boundaries.admit_online_service",
        lambda config: calls.append("mounts") or config.service_role,
    )
    for target, label in (
        ("runtime_web.admit_lifecycle_mounts", "lifecycle"),
        ("operator_commands.configure_operator_database", "django"),
        ("runtime_grants.admit_runtime_database", "grants"),
        ("accounts.configuration_installation.coherent_configuration", "coherence"),
    ):
        monkeypatch.setattr(
            "parishkit.stewardship." + target,
            lambda *args, label=label: calls.append(label),
        )
    return replace(
        configuration, service_role=ServiceRole.WORKER, secrets=secrets
    ), calls


@pytest.mark.parametrize("role", [ServiceRole.WORKER, ServiceRole.SCHEDULER])
def test_background_assembly_binds_exact_keys_role_and_closed_registry(
    admitted_configuration, role
):
    """Build a real lazy broker without provider sockets or ambient configuration."""
    from parishkit.stewardship.accounts.key_files import file_fingerprint

    configuration, calls = admitted_configuration
    if role is ServiceRole.SCHEDULER:
        configuration = replace(
            configuration,
            service_role=role,
            secrets={"token_public": configuration.secrets["token_public"]},
        )
    stop, pulse = Event(), lambda: None
    runtime = background.configure_background(configuration, stop=stop, heartbeat=pulse)
    try:
        assert calls == ["mounts", "lifecycle", "django", "grants", "coherence"]
        assert runtime.broker.service is role and runtime.broker.stop is stop
        expected = {
            "report_facts",
            "family_mail_prepare",
            "report_export",
            "report_export_cleanup",
            "campaign_boundary",
            "activation_catchup",
            "production_cleanup",
            "source_refresh",
            "setup_finalize",
            "branding_cleanup",
            "setup_source_load",
            "setup_source_cleanup",
        }
        if role is ServiceRole.SCHEDULER:
            expected.add("setup_mail_test")
            expected.add("campaign_mail_test")
            expected.add("outbox_delivery")
        assert set(runtime.handlers) == expected
        assert runtime.handlers["source_refresh"].pulse is pulse
        assert set(runtime.receipts) == set(configuration.secrets)
        if role is ServiceRole.WORKER:
            assert runtime.receipts["parishsoft"] == file_fingerprint(
                b"synthetic-private-api-key"
            )
        assert "synthetic" not in repr(runtime)
    finally:
        runtime.broker.app.close()


@pytest.mark.parametrize("failure", ["missing_key", "malformed_key", "bad_broker"])
def test_incomplete_background_credentials_fail_before_database_or_broker(
    admitted_configuration, failure
):
    """Invalid local material never starts a process with reduced source effects."""
    from parishkit.stewardship.accounts.key_files import write_private

    configuration, calls = admitted_configuration
    if failure == "missing_key":
        configuration = replace(
            configuration,
            secrets={
                key: value
                for key, value in configuration.secrets.items()
                if key != "family_code_mac"
            },
        )
    else:
        path = (
            configuration.secrets["parishsoft"]
            if failure == "malformed_key"
            else configuration.valkey.password_file
        )
        write_private(
            path, b"bad\nprivate\nvalue" if failure == "malformed_key" else "é".encode()
        )
    with pytest.raises((ConfigError, ValueError)):
        background.configure_background(
            configuration, stop=Event(), heartbeat=lambda: None
        )
    assert "django" not in calls


@pytest.mark.parametrize("role", [ServiceRole.WEB, ServiceRole.CONFIG_INSTALLER])
def test_background_assembly_does_not_accept_other_profiles(tmp_path, role):
    """A role name cannot bypass its separately owned online startup boundary."""
    configuration = replace(configuration_at(tmp_path), service_role=role)
    with pytest.raises(ConfigError):
        background.configure_background(
            configuration, stop=Event(), heartbeat=lambda: None
        )


def test_scheduler_registry_is_metadata_only():
    """Both compiled types refuse even direct provider/file execution attempts."""
    handlers = background.scheduler_handlers()
    assert set(handlers) == {
        "report_facts",
        "family_mail_prepare",
        "report_export",
        "report_export_cleanup",
        "campaign_boundary",
        "activation_catchup",
        "production_cleanup",
        "source_refresh",
        "branding_cleanup",
        "setup_source_load",
        "setup_source_cleanup",
        "setup_mail_test",
    }
    for handler in handlers.values():
        with pytest.raises(PermissionError):
            handler.execute(None)


@pytest.mark.parametrize(
    "bootstrap,installed", [(True, False), (False, False), (False, True)]
)
def test_mail_runtime_requires_working_key_except_for_coherent_bootstrap(
    admitted_configuration, monkeypatch, bootstrap, installed
):
    """The setup relay does not weaken configured-service credential requirements."""
    from parishkit.stewardship.accounts.cryptography import Key, TokenPrivateKeyring
    from parishkit.stewardship.accounts.key_files import (
        serialize_keyring,
        write_private,
    )

    configuration, _ = admitted_configuration
    private = configuration.secrets["token_public"].with_name("token_private")
    write_private(
        private,
        serialize_keyring(TokenPrivateKeyring([Key("t1", "active", b"t" * 32)])),
    )
    secrets = {
        "token_private": private,
        "token_public": configuration.secrets["token_public"],
    }
    if installed:
        secrets["google_workspace"] = private.with_name("google_workspace")
        write_private(secrets["google_workspace"], b"synthetic-installed-workspace")
    configuration = replace(
        configuration, service_role=ServiceRole.MAIL_DISPATCH, secrets=secrets
    )
    active = SimpleNamespace(
        mode="testing",
        restore_review_required=False,
        current_campaign_id=None,
        active_configuration=SimpleNamespace(
            validation_schema=(
                "bootstrap-policy-v1" if bootstrap else "campaign-content-v5"
            )
        ),
    )
    monkeypatch.setattr(
        background,
        "mail_authority",
        lambda _: active,
    )
    if not bootstrap and not installed:
        with pytest.raises(ConfigError, match="installed Workspace"):
            background.configure_background(
                configuration, stop=Event(), heartbeat=lambda: None
            )
        return
    runtime = background.configure_background(
        configuration, stop=Event(), heartbeat=lambda: None
    )
    try:
        assert set(runtime.handlers) == (
            {"setup_mail_test", "campaign_mail_test", "outbox_delivery"}
            if installed
            else {"setup_mail_test"}
        )
        assert runtime.broker.service is ServiceRole.MAIL_DISPATCH
        assert set(runtime.receipts) == set(secrets)
    finally:
        runtime.broker.app.close()


def test_mail_runtime_rejects_mismatched_public_private_key_inventories(
    admitted_configuration,
):
    """The mail service cannot acknowledge an unrelated link-decryption key."""
    from parishkit.stewardship.accounts.cryptography import Key, TokenPrivateKeyring
    from parishkit.stewardship.accounts.key_files import (
        serialize_keyring,
        write_private,
    )

    configuration, calls = admitted_configuration
    private = configuration.secrets["token_public"].with_name("token_private")
    write_private(
        private,
        serialize_keyring(TokenPrivateKeyring([Key("t1", "active", b"x" * 32)])),
    )
    configuration = replace(
        configuration,
        service_role=ServiceRole.MAIL_DISPATCH,
        secrets={
            "token_private": private,
            "token_public": configuration.secrets["token_public"],
        },
    )
    with pytest.raises(ConfigError, match="inventories differ"):
        background.configure_background(
            configuration, stop=Event(), heartbeat=lambda: None
        )
    assert "django" not in calls


@pytest.mark.parametrize(
    "change",
    [None, "missing_yaml", "missing_sql", "pointer", "digest", "document", "race"],
)
def test_mail_authority_requires_exact_document_and_stable_pointer(monkeypatch, change):
    """A read-only consumer cannot accept stale or differently projected authority."""
    from uuid import uuid4

    from parishkit.stewardship.accounts.runtime_models import SystemConfiguration

    identifier, digest = uuid4(), "a" * 64
    document = {"synthetic": "public configuration"}
    selected = SimpleNamespace(
        version_id=identifier, digest=digest, document=lambda: document
    )
    projection = SimpleNamespace(digest=digest, canonical_document=document)
    runtime = SimpleNamespace(
        active_configuration_id=identifier, active_configuration=projection
    )
    reference = (identifier, digest)
    if change == "missing_yaml":
        selected = None
    elif change == "missing_sql":
        runtime = None
    elif change == "pointer":
        runtime.active_configuration_id = uuid4()
    elif change == "digest":
        projection.digest = "b" * 64
    elif change == "document":
        projection.canonical_document = {}
    elif change == "race":
        reference = (uuid4(), digest)
    store = SimpleNamespace(
        active=lambda: selected, manifest_reference=lambda: reference
    )
    monkeypatch.setattr(
        SystemConfiguration.objects,
        "select_related",
        lambda *args: SimpleNamespace(first=lambda: runtime),
    )
    if change is None:
        assert background.mail_authority(store) is runtime
    else:
        with pytest.raises(ConfigError, match="requires recovery"):
            background.mail_authority(store)


@pytest.mark.parametrize("bootstrap", [True, False])
def test_only_bootstrap_worker_can_omit_installed_source_key(
    admitted_configuration, monkeypatch, bootstrap
):
    """Initial staged loads need no working key; configured refresh never weakens."""
    configuration, _ = admitted_configuration
    configuration = replace(
        configuration,
        secrets={
            key: value
            for key, value in configuration.secrets.items()
            if key != "parishsoft"
        },
    )
    active = SimpleNamespace(
        mode="testing",
        restore_review_required=False,
        current_campaign_id=None,
        active_configuration=SimpleNamespace(
            validation_schema="bootstrap-policy-v1"
            if bootstrap
            else "campaign-content-v5"
        ),
    )
    monkeypatch.setattr(
        "parishkit.stewardship.accounts.configuration_installation.coherent_configuration",
        lambda _: active,
    )
    if not bootstrap:
        with pytest.raises(ConfigError, match="installed ParishSoft"):
            background.configure_background(
                configuration, stop=Event(), heartbeat=lambda: None
            )
        return
    runtime = background.configure_background(
        configuration, stop=Event(), heartbeat=lambda: None
    )
    try:
        assert set(runtime.handlers) == {
            "report_facts",
            "family_mail_prepare",
            "report_export",
            "report_export_cleanup",
            "campaign_boundary",
            "activation_catchup",
            "production_cleanup",
            "setup_source_load",
            "branding_cleanup",
            "setup_source_cleanup",
        }
        assert "parishsoft" not in runtime.receipts
    finally:
        runtime.broker.app.close()


def test_bound_registry_rechecks_authority_before_domain_admission(monkeypatch):
    """Prior startup admission cannot replace a current configuration check."""
    calls = []
    original = background.scheduler_handlers()["source_refresh"]
    original = replace(original, admit=lambda *args: calls.append("domain") or True)
    store, pulse = object(), lambda: None
    monkeypatch.setattr(
        background, "matching_authority", lambda value: calls.append(value)
    )
    handler = background.bind_authority(
        {"source_refresh": original}, store, heartbeat=pulse
    )["source_refresh"]
    assert handler.admit("hint", None)
    assert calls == [store, "domain"] and handler.pulse is pulse
    assert original.pulse is None


@pytest.mark.parametrize("mode", ["testing", "production"])
def test_source_refresh_uses_durable_suppression_owner_in_each_mode(monkeypatch, mode):
    """Testing cannot silently forget live refusals; only creation is mode-limited."""
    calls, expected = [], object()

    def suppressions(value):
        """Represent the separately tested durable promotion owner."""
        calls.append(value)
        return expected

    monkeypatch.setattr(
        "parishkit.stewardship.jobs.recipient_suppressions.source_suppressions",
        suppressions,
    )
    scope = SimpleNamespace(runtime=SimpleNamespace(mode=mode))
    assert background.source_refusal_suppressions(scope) is expected
    assert calls == [scope]

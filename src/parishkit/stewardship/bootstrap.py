"""Offline-only initial provisioning, resumable without adopting unrelated keys.

The command owner must admit the dedicated kernel mount boundary before invoking
these primitives. Each target's initial candidate journal stays in that target's
private storage, never in YAML, PostgreSQL, logs or another installer's mount.
It precedes credential publication so interruption cannot lose the exact bytes
needed to distinguish a matching retry from an unrelated existing credential.
"""

import base64
import json
import os
import secrets
from dataclasses import dataclass
from uuid import UUID, uuid5

from parishkit.config import ConfigError

from .accounts.authority import AuthorityStore
from .accounts.bootstrap_schema import bootstrap_version
from .accounts.configuration_schema import validate_sections
from .accounts.cryptography import Key, TokenPrivateKeyring
from .accounts.key_files import (
    KEYRINGS,
    parse_keyring,
    read_private,
    serialize_keyring,
    write_private,
)
from .accounts.metrics_credentials import MetricsCredential
from .accounts.policy_schema import normalized_email
from .deployment import SECRET_NAMES, ServiceRole
from .deployment_documents import deployment_document
from .runtime_paths import RuntimeLayout, private_directory
from .startup_interlock import StartupLease

INITIAL_TARGETS = (
    "django_signing",
    "general_encryption",
    "family_code_mac",
    "token_private",
    "token_public",
    "metrics",
)
HANDOFF_TARGETS = tuple(sorted(SECRET_NAMES - {"handoff_private"}))


@dataclass(frozen=True)
class BootstrapIdentity:
    """Explicit initial identity; no generated default can silently select a tenant."""

    deployment_id: UUID
    admin_email: str

    def __post_init__(self):
        if not isinstance(self.deployment_id, UUID):
            raise ConfigError("Bootstrap requires an explicit deployment UUID.")
        object.__setattr__(self, "admin_email", normalized_email(self.admin_email))

    def document(self):
        """Only non-secret operator-confirmed data belongs in the lifecycle marker."""
        return {
            "version": 1,
            "deployment": str(self.deployment_id),
            "admin": self.admin_email,
        }


def _json(value):
    """Stable bounded records use the existing private atomic-file boundary."""
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def _read_json(path):
    """Malformed private journals never include submitted contents in an error."""
    try:
        return json.loads(read_private(path))
    except (ValueError, UnicodeError, RecursionError):
        raise ConfigError("Bootstrap journal is invalid.") from None


def _key_id(identity, target):
    """Key labels bind initial candidates to deployment and purpose, not key bytes."""
    return "initial-" + uuid5(identity.deployment_id, "bootstrap-key:" + target).hex


def _candidate(path, identity, target, generate, *, existing=False):
    """Commit the owned initial candidate before selecting its consumer file.

    A preexisting file without its bootstrap journal is never adopted. A journal
    can survive a crash before publication; a committed consumer file must match
    it byte-for-byte. Online rotation is deliberately not a bootstrap retry.
    """
    journal = path.parent / ".bootstrap-candidate"
    expected = {
        "version": 1,
        "deployment": str(identity.deployment_id),
        "target": target,
    }
    if journal.exists():
        record = _read_json(journal)
        if not isinstance(record, dict) or set(record) != {*expected, "candidate"}:
            raise ConfigError("Bootstrap journal has an invalid shape.")
        if any(record[name] != value for name, value in expected.items()):
            raise ConfigError("Bootstrap journal belongs to another initialization.")
        try:
            value = base64.b64decode(record["candidate"], validate=True)
        except (ValueError, TypeError):
            raise ConfigError("Bootstrap candidate is invalid.") from None
    else:
        if existing or path.exists():
            raise ConfigError("Bootstrap cannot adopt an existing credential.")
        value = generate()
        write_private(
            journal,
            _json(expected | {"candidate": base64.b64encode(value).decode("ascii")}),
        )
    if (existing or path.exists()) and read_private(path) != value:
        raise ConfigError("Existing credential does not match bootstrap.")
    return value


def _publish(path, value, lease):
    """Never replace a differing existing credential, even after partial setup."""
    lease.check()
    if path.exists():
        if read_private(path) != value:
            raise ConfigError("Existing credential does not match bootstrap.")
    else:
        write_private(path, value)


def _ring(identity, target):
    """Every generated secret purpose has independent random 256-bit material."""
    return serialize_keyring(
        KEYRINGS[target](
            [Key(_key_id(identity, target), "active", secrets.token_bytes(32))]
        )
    )


def _marker(layout, identity, *, existing=False):
    """Refuse unrelated or completed setup rather than offering a repair bypass."""
    path = layout.deployment_directory / "bootstrap.json"
    expected = identity.document()
    if path.exists():
        record = _read_json(path)
        if record == expected | {"state": "materialized"}:
            raise ConfigError("This deployment has already been initialized.")
        if existing and record == expected | {"state": "retiring_journals"}:
            return path
        if record != expected | {"state": "provisioning"}:
            raise ConfigError("Bootstrap identity does not match existing state.")
    else:
        if existing:
            raise ConfigError("Bootstrap provisioning must precede materialization.")
        write_private(path, _json(expected | {"state": "provisioning"}))
    return path


def _layout(configuration):
    """The caller cannot invoke initial provisioning as an ordinary online role."""
    if configuration.service_role is not ServiceRole.BOOTSTRAP:
        raise ConfigError("Bootstrap requires its explicit offline profile.")
    layout = RuntimeLayout(configuration).validate()
    for path in [
        layout.deployment_directory,
        configuration.paths["authority"],
        *(layout.credential_directory(name) for name in INITIAL_TARGETS),
        *(layout.handoff(name).parent for name in HANDOFF_TARGETS),
    ]:
        private_directory(path)
    return layout


def _generate(identity, target, candidates):
    """Generate only a missing owned candidate, with public material derived last."""
    if target == "metrics":
        return MetricsCredential.generate().serialize()
    if target == "token_public":
        private = parse_keyring(candidates["token_private"], "token_private")
        return serialize_keyring(private.public())
    return _ring(identity, target)


def _candidates(layout, identity, *, existing=False):
    """Admit the entire initial key set before publishing any consumer file."""
    candidates, result = {}, {}
    for target in INITIAL_TARGETS:
        path = layout.credential(target)
        value = _candidate(
            path,
            identity,
            target,
            lambda name=target: _generate(identity, name, candidates),
            existing=existing,
        )
        if target == "metrics":
            MetricsCredential.parse(value)
        else:
            ring = parse_keyring(value, target)
            key_target = "token_private" if target == "token_public" else target
            if list(ring.keys) != [_key_id(identity, key_target)]:
                raise ConfigError("Initial keyring identity does not match bootstrap.")
        candidates[target], result[path] = value, value
    if candidates["token_public"] != serialize_keyring(
        parse_keyring(candidates["token_private"], "token_private").public()
    ):
        raise ConfigError("Initial public/private keyrings do not match.")
    for target in HANDOFF_TARGETS:
        name, path = "handoff-" + target, layout.handoff(target)
        value = _candidate(
            path,
            identity,
            name,
            lambda key=name: serialize_keyring(
                TokenPrivateKeyring(
                    [Key(_key_id(identity, key), "active", secrets.token_bytes(32))]
                )
            ),
            existing=existing,
        )
        ring = parse_keyring(value, "token_private")
        if list(ring.keys) != [_key_id(identity, name)]:
            raise ConfigError("Handoff identity does not match bootstrap.")
        result[path] = value
    return result


def provision_initial_files(configuration, identity):
    """Pre-migration phase has no application-table dependency or provider calls."""
    layout = _layout(configuration)
    version = bootstrap_version(identity.deployment_id, identity.admin_email)
    with StartupLease(layout.interlock, offline=True) as lease:
        _marker(layout, identity)
        store = AuthorityStore(configuration.paths["authority"], validate_sections)
        selected = store.active()
        if selected is not None and selected != version:
            raise ConfigError("Existing authority does not match bootstrap.")
        # Validate inputs before creating candidates; these remain operator-owned.
        oauth = configuration.secrets.get("google_oauth")
        password = configuration.postgres.password_file
        if oauth is None or password is None:
            raise ConfigError(
                "Bootstrap requires operator-provided OAuth and database files."
            )
        read_private(oauth)
        read_private(password)
        deployment_file = layout.deployment_directory / "deployment.yaml"
        _publish(deployment_file, _json(deployment_document(configuration)), lease)
        for path, value in _candidates(layout, identity).items():
            _publish(path, value, lease)
        lease.check()
        store.write_version(version)
        store.select(version)
        return version


def materialize_initial_files(configuration, identity):
    """Post-migration phase imports only the exact root into empty/matching state."""
    from .accounts.configuration_installation import (
        coherent_configuration,
        prepare_initial_configuration,
    )

    layout = _layout(configuration)
    version = bootstrap_version(identity.deployment_id, identity.admin_email)
    with StartupLease(layout.interlock, offline=True) as lease:
        marker = _marker(layout, identity, existing=True)
        store = AuthorityStore(configuration.paths["authority"], validate_sections)
        if store.active() != version:
            raise ConfigError("Bootstrap requires its exact initial YAML authority.")
        if _read_json(marker)["state"] == "retiring_journals":
            runtime = coherent_configuration(store)
            if runtime.pk != identity.deployment_id:
                raise ConfigError("Bootstrap database identity has changed.")
        else:
            # Revalidate before materialization; this phase creates no missing keys.
            _candidates(layout, identity, existing=True)
            lease.check()
            prepare_initial_configuration(
                store,
                version,
                deployment_id=identity.deployment_id,
                testing_recipient=identity.admin_email,
                actor_id=uuid5(identity.deployment_id, "bootstrap-operator-v1"),
                correlation_id=uuid5(identity.deployment_id, "bootstrap-import-v1"),
            )
            lease.check()
            write_private(
                marker, _json(identity.document() | {"state": "retiring_journals"})
            )
        _retire_journals(layout, identity, lease)
        lease.check()
        write_private(marker, _json(identity.document() | {"state": "materialized"}))
        return version


def _retire_journals(layout, identity, lease):
    """Remove only exact owned initialization copies after durable DB activation.

    The non-secret retiring marker permits a crash retry after some copies have
    already been removed. This path never generates, adopts, replaces or deletes
    the selected credential files. Ordinary installers cannot inherit stale
    bootstrap secret copies after initialization reports completion.
    """
    targets = [(name, layout.credential(name)) for name in INITIAL_TARGETS]
    targets += [("handoff-" + name, layout.handoff(name)) for name in HANDOFF_TARGETS]
    for target, path in targets:
        lease.check()
        journal = path.parent / ".bootstrap-candidate"
        if not journal.exists():
            continue
        record = _read_json(journal)
        if (
            type(record) is not dict
            or set(record) != {"version", "deployment", "target", "candidate"}
            or record["version"] != 1
            or record["deployment"] != str(identity.deployment_id)
            or record["target"] != target
        ):
            raise ConfigError("Bootstrap cleanup found an unrelated journal.")
        journal.unlink()
        descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

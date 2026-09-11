"""Operator-only creation of fresh runtime storage, never adoption or repair.

This helper has no SQL connection, provider credential input, Docker control or
application startup authority. Run it as the deployment filesystem owner before
mounting the narrow service profiles. A private immutable intent binds resumable
creation to the exact metadata/image/paths; generated passwords are never replaced.
"""

import fcntl
import json
import os
import re
import secrets
import stat
from dataclasses import replace
from pathlib import Path

from parishkit.config import ConfigError

from .accounts.authority import _sync_directory
from .accounts.key_files import read_private, write_private
from .bootstrap import HANDOFF_TARGETS
from .deployment_documents import deployment_document
from .runtime_identities import database_identities
from .runtime_paths import RuntimeLayout, explicit_path, private_directory
from .runtime_topology import render_runtime, resolve_database_files
from .runtime_valkey import web_acl
from .startup_interlock import MARKER

MAX_DOCUMENT = 1024 * 1024


def _admit_inventory(root, directories, files):
    """On every retry refuse anything outside this intent's closed creation set."""
    allowed_directories = {root, *directories}
    for target in tuple(allowed_directories) + tuple(files):
        for parent in target.parents:
            if parent == root or any(base in parent.parents for base in directories):
                allowed_directories.add(parent)
    for directory in allowed_directories:
        explicit_path(directory)
        if not directory.exists():
            continue
        private_directory(directory)
        for entry in directory.iterdir():
            explicit_path(entry)
            if entry.is_dir() and entry in allowed_directories:
                continue
            if not entry.is_file() or entry not in files:
                raise ConfigError("Runtime provisioning contains unplanned storage.")


def _write_intent(descriptor, intent):
    """Complete a locked initial marker, including short writes, then sync it."""
    os.lseek(descriptor, 0, os.SEEK_SET)
    remaining = memoryview(intent)
    while remaining:
        count = os.write(descriptor, remaining)
        if count == 0:
            raise OSError("Provisioning intent write made no progress.")
        remaining = remaining[count:]
    os.fsync(descriptor)


def _json(value):
    """Use stable non-secret metadata for exact resumable intent comparisons."""
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _mkdir(path):
    """Create missing private ancestors; never chmod/chown an existing directory."""
    path = explicit_path(path)
    absent, parent = [], path
    while not parent.exists():
        absent.append(parent)
        parent = parent.parent
    if not parent.is_dir():
        raise ConfigError("Runtime storage parent is not a directory.")
    for directory in reversed(absent):
        directory.mkdir(mode=0o700)
        private_directory(directory)
        _sync_directory(directory.parent)
    private_directory(path)


def _retain(path, value):
    """Write a missing planned file or require byte-identical prior completion."""
    if path.exists():
        if read_private(path, maximum=MAX_DOCUMENT) != value:
            raise ConfigError(
                "Existing runtime artifact differs from provisioning intent."
            )
    else:
        write_private(path, value, maximum=MAX_DOCUMENT)


def _password(path):
    """Retry never changes a generated password, even after later SQL provisioning."""
    if not path.exists():
        write_private(path, secrets.token_urlsafe(32).encode("ascii"))
    value = read_private(path)
    if re.fullmatch(rb"[A-Za-z0-9_-]{43}", value) is None:
        raise ConfigError("Existing provisioning password has an invalid shape.")
    return value


def provisioning_plan(configuration, *, image, checkout=None, bind_source_root=None):
    """Resolve every target before creating any filesystem or runtime authority."""
    if configuration.runtime_budget.replicas != 1:
        raise ConfigError("Operational provisioning requires one web container.")
    configuration = replace(
        configuration,
        valkey=replace(
            configuration.valkey,
            password_file=configuration.valkey.password_file
            or configuration.paths["credentials"] / "valkey" / "web",
        ),
    )
    configuration = resolve_database_files(configuration)
    layout = RuntimeLayout(configuration).validate()
    compose, documents = render_runtime(configuration, image=image, checkout=checkout)
    if bind_source_root is not None:
        source_root = explicit_path(bind_source_root)
        for service in compose["services"].values():
            for mount in service["volumes"]:
                source = Path(mount["source"])
                if source.is_relative_to(configuration.paths.root):
                    mount["source"] = str(
                        source_root / source.relative_to(configuration.paths.root)
                    )
    directories = {
        *configuration.paths.values.values(),
        layout.deployment_directory,
        layout.service_directory,
        *(layout.credential_directory(target) for target in HANDOFF_TARGETS),
        *(layout.handoff(target).parent for target in HANDOFF_TARGETS),
        configuration.paths["caddy"] / "data",
        configuration.paths["caddy"] / "config",
        configuration.paths["cache"] / "static",
    }
    passwords = {
        layout.database_password(name)
        for name in ("operator", *(entry[0] for entry in database_identities()))
    }
    passwords.add(configuration.valkey.password_file)
    acl = configuration.paths["credentials"] / "valkey" / "server.acl"
    directories.update(path.parent for path in passwords | {acl, layout.interlock})
    documents = {
        path: document.encode() if isinstance(document, str) else _json(document)
        for path, document in documents.items()
    }
    compose_path = layout.service_directory / "compose.json"
    documents[compose_path] = _json(compose)
    intent = _json(
        {
            "version": 1,
            "deployment": deployment_document(configuration),
            "image": image,
            "checkout": str(checkout) if checkout is not None else None,
            "bind_source_root": str(bind_source_root)
            if bind_source_root is not None
            else None,
        }
    )
    if len(intent) > MAX_DOCUMENT or any(
        len(value) > MAX_DOCUMENT for value in documents.values()
    ):
        raise ConfigError("Runtime provisioning documents exceed the safe size bound.")
    return configuration, directories, passwords, documents, acl, intent


def provision_runtime(configuration, *, image, checkout=None, bind_source_root=None):
    """Create a fresh root or resume only its exact interrupted provisioning intent.

    Existing roots and external target directories must be empty owner-only
    storage on first invocation. OAuth/provider credentials are supplied by the
    operator afterward; bootstrap separately generates application keyrings.
    Completed provisioning is not an upgrade or configuration-edit operation.
    """
    configuration, directories, passwords, documents, acl, intent = provisioning_plan(
        configuration, image=image, checkout=checkout, bind_source_root=bind_source_root
    )
    root = explicit_path(configuration.paths.root)
    pending = root / ".stewardship-provisioning.json"
    completed = root / ".stewardship-provisioned.json"
    if completed.exists():
        raise ConfigError(
            "Runtime storage is already provisioned; use the upgrade workflow."
        )
    if not pending.exists():
        # Check all existing destinations before creating even the ownership
        # marker. This is not permission to adopt a populated directory.
        for directory in {root, *directories}:
            explicit_path(directory)
            if directory.exists():
                private_directory(directory)
                if any(directory.iterdir()):
                    raise ConfigError("Initial runtime storage must be empty.")
        if not root.exists():
            root.mkdir(mode=0o700)
        private_directory(root)
    private_directory(root)
    descriptor = os.open(
        pending, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600
    )
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_uid != os.geteuid()
            or metadata.st_nlink != 1
        ):
            raise ConfigError("Provisioning intent metadata is invalid.")
        if completed.exists():
            raise ConfigError("Runtime provisioning already completed.")
        if os.fstat(descriptor).st_ino != pending.stat().st_ino:
            raise ConfigError("Provisioning intent changed during admission.")
        existing = os.read(descriptor, MAX_DOCUMENT + 1)
        if existing != intent:
            if not intent.startswith(existing):
                raise ConfigError(
                    "Interrupted provisioning belongs to different deployment inputs."
                )
            # A crash may leave only an empty/prefix marker. No other artifact
            # may exist until the complete intent is durable, so finishing this
            # prefix never reinterprets earlier provisioning side effects.
            for directory in {root, *directories}:
                if directory.exists():
                    private_directory(directory)
                    if any(entry != pending for entry in directory.iterdir()):
                        raise ConfigError(
                            "Incomplete provisioning intent has unrelated storage."
                        )
            _write_intent(descriptor, intent)
            _sync_directory(root)
        _admit_inventory(
            root,
            directories,
            {
                *passwords,
                *documents,
                acl,
                RuntimeLayout(configuration).interlock,
                pending,
            },
        )
        for directory in sorted(
            directories, key=lambda value: (len(value.parts), str(value))
        ):
            _mkdir(directory)
        for path in sorted(passwords):
            _password(path)
        _retain(acl, web_acl(_password(configuration.valkey.password_file)))
        _retain(RuntimeLayout(configuration).interlock, MARKER)
        for path, value in documents.items():
            _retain(path, value)
        _retain(completed, intent)
    finally:
        os.close(descriptor)
    return {
        "runtime_storage_provisioned": True,
        "services_started": False,
        "oauth_credentials_required": True,
        "application_bootstrap_required": True,
        "static_collection_required": True,
    }

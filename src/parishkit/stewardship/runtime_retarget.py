"""Point a provisioned deployment at a newer approved application image.

Provisioning is create-only: a completed runtime root refuses another run. An
upgrade changes exactly one input, the immutable application image. This
command re-renders every generated document from the recorded inputs with the
new image, rewrites those that differ and updates the completion marker; it
refuses a changed deployment input, a missing once-generated password, an
unfinished provisioning, or an image the profile does not admit. It does not
create new SQL logins or storage a later release introduces, and creates a
new password only for an identity in the closed ``RETARGET_CREATES`` list
(#235) whose file is absent. The other exception is the generated documents'
own owner-only ``pg-hba`` directory beside them (#2111), which it creates when
absent. It runs offline, holding the startup interlock
exclusively, so no online service can observe a half-written document; it
starts nothing and connects to nothing. Schema migrations, grants and service
restarts remain the operator's separate, documented upgrade steps.
"""

import json
import os
import secrets
import stat

from parishkit.config import ConfigError

from .accounts.authority import _sync_directory
from .accounts.key_files import read_private, write_private
from .runtime_identities import database_identities
from .runtime_paths import (
    PROVISIONING_RECORD,
    RuntimeLayout,
    explicit_path,
    private_directory,
)
from .runtime_provisioning import MAX_DOCUMENT, provisioning_plan
from .startup_interlock import StartupLease

TOPOLOGIES = ("compose.json", "compose-initial.json", "compose-slack.json")
# The only database identities whose missing password file retarget creates
# (#235): a login an existing deployment gains with a release, deployed
# through the upgrade rather than a reinstall. Closed on purpose: a later
# identity that needs the same path is added here by its own reviewed
# change, never by being new. An identity is acted on only when the running
# release has it (database_identities), so a release without it does
# nothing.
RETARGET_CREATES = frozenset({"backup-uploader"})


def read_provisioning_record(path):
    """The completed provisioning intent, admitted as an owner-only JSON file.

    The backup reads it too, to name the image a set was taken under.
    """
    metadata = os.lstat(path)
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or metadata.st_uid != os.geteuid()
        or metadata.st_nlink != 1
    ):
        raise ConfigError("The provisioning record is not a private file.")
    value = json.loads(read_private(path, maximum=MAX_DOCUMENT))
    if type(value) is not dict or set(value) != {
        "version",
        "deployment",
        "image",
        "checkout",
        "bind_source_root",
    }:
        raise ConfigError("The provisioning record has an unknown shape.")
    return value


def _rederived(document):
    """The recorded deployment document as the running release would write it.

    A release may add a defaulted field (a new runtime path, say) that the
    provisioning release never recorded. Loading the recorded document back
    through the current loader fills in exactly those defaults, so the
    comparison sees the operator's inputs, not the older release's shape.
    """
    from .deployment import load_deployment
    from .deployment_documents import deployment_document

    # In memory: retarget runs with a read-only root and no writable /tmp.
    return deployment_document(load_deployment(document=document, environ={}))


def _plan(configuration, recorded, *, image):
    """The provisioning plan for the recorded inputs with the given image."""
    checkout, source_root = recorded["checkout"], recorded["bind_source_root"]
    return provisioning_plan(
        configuration,
        image=image,
        checkout=None if checkout is None else explicit_path(checkout),
        bind_source_root=None if source_root is None else explicit_path(source_root),
    )


def recorded_plan(configuration, *, image=None):
    """The recorded record and the plan for its inputs with ``image``.

    ``image`` defaults to the recorded one. Refused unless provisioning is
    complete and the operator's current inputs match the recorded ones in
    everything but the image, so what is planned is what provisioning (or
    the last retarget) wrote. The plan is ``provisioning_plan``'s tuple.
    """
    root = explicit_path(configuration.paths.root)
    private_directory(root)
    completed = root / PROVISIONING_RECORD
    if not completed.exists():
        raise ConfigError("Provisioning is unfinished; resume it before upgrading.")
    recorded = read_provisioning_record(completed)
    # The plan re-derives every document from the operator's current inputs
    # with the new image; only the image may differ from what was recorded.
    plan = _plan(
        configuration, recorded, image=recorded["image"] if image is None else image
    )
    proposed = json.loads(plan[5])
    expected = dict(recorded, deployment=_rederived(recorded["deployment"]))
    if {key: value for key, value in proposed.items() if key != "image"} != {
        key: value for key, value in expected.items() if key != "image"
    }:
        raise ConfigError(
            "Deployment inputs differ from provisioning; only the image may change."
        )
    return recorded, plan


def _absent_passwords(layout):
    """The ``RETARGET_CREATES`` password files of this release that are absent.

    Absent means nothing at all at the path (``lstat``): a dangling link or
    any other entry is not absent, so it is left to the password read loop,
    which refuses it as it refuses any unusable password file.
    """
    absent = {}
    for name, *_ in database_identities():
        if name not in RETARGET_CREATES:
            continue
        path = layout.database_password(name)
        try:
            os.lstat(path)
        except FileNotFoundError:
            absent[path] = name
    return absent


def _discard(*paths):
    """Remove this call's own files after a failure; return a cleanup error.

    Every path is attempted even when an earlier one fails, so as little as
    possible is left behind; the first error is returned for chaining.
    """
    failure = None
    for each in paths:
        try:
            os.unlink(each)
        except FileNotFoundError:
            pass
        except OSError as error:
            failure = failure or error
    return failure


def _create_password(path):
    """Generate one password as provisioning does, all-or-nothing and owner-only.

    The value is written and flushed under a temporary name in the same
    directory and only then linked to ``path``: ``os.link`` fails if
    anything appeared there after the absence check and never follows a
    link at the target, so the password either appears whole or not at all.
    The temporary name is created with ``O_EXCL`` and ``O_NOFOLLOW`` and set
    to 0600 explicitly, so the umask cannot change its mode. On any failure
    this call's own files are removed (the target only if this call linked
    it); a cleanup failure is chained to the refusal rather than hiding it.
    The file is then read back through the usual admission. Its directory
    must already be real owner-only storage, as provisioning left it.
    """
    private_directory(path.parent)
    value = secrets.token_urlsafe(32).encode("ascii")
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(8)}.creating")
    try:
        descriptor = os.open(
            temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
        )
    except OSError:
        raise ConfigError(
            "A new database password file could not be created."
        ) from None
    linked = False
    try:
        try:
            os.fchmod(descriptor, 0o600)
            if os.write(descriptor, value) != len(value):
                raise OSError("short write")
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        os.link(temporary, path, follow_symlinks=False)
        linked = True
        os.unlink(temporary)
        _sync_directory(path.parent)
    except OSError as error:
        # Only this call's own inode is removed: the target is unlinked only
        # after this call's link put it there (the interlock is held
        # exclusively, so nothing else writes the directory meanwhile).
        failure = _discard(temporary, *((path,) if linked else ()))
        verb = "created" if isinstance(error, FileExistsError) else "written"
        raise ConfigError(
            f"A new database password file could not be {verb}."
        ) from failure
    read_private(path, maximum=MAX_DOCUMENT)


def retarget_image(configuration, *, image):
    """Re-render the generated documents of a completed deployment for an image.

    Returns what changed. Every generated document, the three topologies, the
    per-service configurations and the ingress document, is re-derived from
    the recorded inputs by the code that is running, so a release whose
    renderer changed reaches the deployment through the same command as one
    that only changed the image; the passwords and broker ACL are generated
    once and kept. Only the deployment inputs may not differ. Documents that
    already match are left alone, so an interrupted retarget is finished by
    running the same command again and undone by asking for the recorded
    image, and a repeat is no change.

    The one password it creates is that of a ``RETARGET_CREATES`` identity
    the running release has, when its file is absent: that is found first,
    before the loop below reads every password file (which would refuse the
    missing one) and before the no-change return, so a retarget to the
    recorded image (the lost-file recovery) still creates it. Every other
    missing password still refuses.
    """
    recorded, plan = recorded_plan(configuration, image=image)
    configuration, _, passwords, documents, acl, intent = plan
    root = explicit_path(configuration.paths.root)
    completed = root / PROVISIONING_RECORD
    layout = RuntimeLayout(configuration)
    created = _absent_passwords(layout)
    changed = recorded["image"] != image
    # A document the running release renders but the provisioned release did
    # not is simply absent: it is written like any other differing document.
    stale = {
        path
        for path, value in documents.items()
        if not path.exists() or read_private(path, maximum=MAX_DOCUMENT) != value
    }
    for path in (*passwords, acl):
        # Presence and privacy only: the values are generated once and kept.
        if path not in created:
            read_private(path, maximum=MAX_DOCUMENT)
    if not stale and not changed and not created:
        return {
            "image_changed": False,
            "documents_changed": 0,
            "services_started": False,
        }
    with StartupLease(layout.interlock, offline=True):
        # A deployment provisioned before the source rules (#2111) lacks
        # their directory; it is the one directory this command creates. An
        # existing one must be a real owner-only directory (lstat: no link).
        rules_created = not os.path.lexists(layout.hba_directory)
        private_directory(layout.hba_directory, create=True)
        if rules_created:
            _sync_directory(layout.service_directory)
        for path in sorted(created):
            _create_password(path)
        for path in sorted(stale):
            write_private(path, documents[path], maximum=MAX_DOCUMENT)
        _sync_directory(layout.service_directory)
        _sync_directory(layout.hba_directory)
        # The record changes last: until it does, rerunning this command with
        # the same image rewrites the topologies instead of reporting no change.
        if changed:
            write_private(completed, intent, maximum=MAX_DOCUMENT)
            _sync_directory(root)
    result = {
        "image_changed": changed,
        "documents_changed": len(stale),
        "services_started": False,
    }
    if created:
        # Named only when there are any, so every other retarget reports
        # exactly what it did before #235.
        result["passwords_created"] = sorted(created.values())
    return result

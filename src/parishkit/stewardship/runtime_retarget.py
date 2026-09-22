"""Point a provisioned deployment at a newer approved application image.

Provisioning is create-only: a completed runtime root refuses another run. An
upgrade changes exactly one input, the immutable application image, and the
only artifacts that name it are the three rendered Compose topologies and the
completion marker. This command re-renders those from the recorded inputs with
the new image and refuses anything else: a changed deployment input, a changed
generated document or password, an unfinished provisioning, or an image the
profile does not admit. It runs offline, holding the startup interlock
exclusively, so no online service can observe a half-written topology; it
starts nothing and connects to nothing. Schema migrations, grants and service
restarts remain the operator's separate, documented upgrade steps.
"""

import json
import os
import stat

from parishkit.config import ConfigError

from .accounts.authority import _sync_directory
from .accounts.key_files import read_private, write_private
from .runtime_paths import RuntimeLayout, explicit_path, private_directory
from .runtime_provisioning import MAX_DOCUMENT, provisioning_plan
from .startup_interlock import StartupLease

TOPOLOGIES = ("compose.json", "compose-initial.json", "compose-slack.json")


def _recorded(path):
    """The completed provisioning intent, admitted as an owner-only JSON file."""
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


def _plan(configuration, recorded, *, image):
    """The provisioning plan for the recorded inputs with the given image."""
    checkout, source_root = recorded["checkout"], recorded["bind_source_root"]
    return provisioning_plan(
        configuration,
        image=image,
        checkout=None if checkout is None else explicit_path(checkout),
        bind_source_root=None if source_root is None else explicit_path(source_root),
    )


def _other_image(current, rendered):
    """The one application image a topology names that its rendering does not.

    Infrastructure images are the same in both, so the difference is the
    application image the topology was rendered with, if it was rendered at
    all; a malformed or mixed topology has no such image.
    """
    try:
        known = {s["image"] for s in json.loads(rendered)["services"].values()}
        named = {s["image"] for s in json.loads(current)["services"].values()}
    except (ValueError, KeyError, TypeError, AttributeError):
        return None
    other = named - known
    return other.pop() if len(other) == 1 else None


def retarget_image(configuration, *, image):
    """Re-render only the image-bearing artifacts of a completed deployment.

    Returns what changed. A topology on disk may name the recorded image or
    the requested one, the two states an interrupted retarget can leave, and
    is brought to the requested one; anything else is a hand edit and is
    refused. So running the same command again finishes an interrupted
    retarget, and running it with the recorded image undoes one.
    """
    root = explicit_path(configuration.paths.root)
    private_directory(root)
    completed = root / ".stewardship-provisioned.json"
    if not completed.exists():
        raise ConfigError("Provisioning is unfinished; resume it before upgrading.")
    recorded = _recorded(completed)
    # The plan re-derives every document from the operator's current inputs
    # with the new image; only the image may differ from what was recorded.
    configuration, _, passwords, documents, acl, intent = _plan(
        configuration, recorded, image=image
    )
    proposed = json.loads(intent)
    if {key: value for key, value in proposed.items() if key != "image"} != {
        key: value for key, value in recorded.items() if key != "image"
    }:
        raise ConfigError(
            "Deployment inputs differ from provisioning; only the image may change."
        )
    layout = RuntimeLayout(configuration)
    topologies = {layout.service_directory / name for name in TOPOLOGIES}
    changed = recorded["image"] != image
    stale, renderings = set(), {}
    for path, value in documents.items():
        current = read_private(path, maximum=MAX_DOCUMENT)
        if current == value:
            continue
        if path not in topologies:
            raise ConfigError("A generated document differs; this is not an upgrade.")
        # A topology may lag behind (interrupted retarget) or run ahead of the
        # record (undoing one): it is admitted only if it is exactly the same
        # inputs rendered with the recorded image, or with the one image it
        # names instead. Anything else is a hand edit.
        other = recorded["image"] if changed else _other_image(current, value)
        if other is None:
            raise ConfigError("A generated document differs; this is not an upgrade.")
        if other not in renderings:
            renderings[other] = _plan(configuration, recorded, image=other)[3]
        if current != renderings[other][path]:
            raise ConfigError("A generated document differs; this is not an upgrade.")
        stale.add(path)
    for path in (*passwords, acl):
        # Presence and privacy only: the values are generated once and kept.
        read_private(path, maximum=MAX_DOCUMENT)
    if not stale and not changed:
        return {"image_changed": False, "services_started": False}
    with StartupLease(layout.interlock, offline=True):
        for path in sorted(stale):
            write_private(path, documents[path], maximum=MAX_DOCUMENT)
        _sync_directory(layout.service_directory)
        # The record changes last: until it does, rerunning this command with
        # the same image rewrites the topologies instead of reporting no change.
        if changed:
            write_private(completed, intent, maximum=MAX_DOCUMENT)
            _sync_directory(root)
    return {"image_changed": changed, "services_started": False}

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


def retarget_image(configuration, *, image):
    """Re-render only the image-bearing artifacts of a completed deployment.

    Returns what changed. An image equal to the recorded one changes nothing,
    so an interrupted retarget is finished by running the same command again.
    """
    root = explicit_path(configuration.paths.root)
    private_directory(root)
    completed = root / ".stewardship-provisioned.json"
    if not completed.exists():
        raise ConfigError("Provisioning is unfinished; resume it before upgrading.")
    recorded = _recorded(completed)
    checkout, source_root = recorded["checkout"], recorded["bind_source_root"]
    # The plan re-derives every document from the operator's current inputs
    # with the new image; only the image may differ from what was recorded.
    configuration, _, passwords, documents, acl, intent = provisioning_plan(
        configuration,
        image=image,
        checkout=None if checkout is None else explicit_path(checkout),
        bind_source_root=None if source_root is None else explicit_path(source_root),
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
    for path, value in documents.items():
        if path in topologies:
            continue
        if read_private(path, maximum=MAX_DOCUMENT) != value:
            raise ConfigError("A generated document differs; this is not an upgrade.")
    for path in (*passwords, acl):
        # Presence and privacy only: the values are generated once and kept.
        read_private(path, maximum=MAX_DOCUMENT)
    if recorded["image"] == image:
        return {"image_changed": False, "services_started": False}
    with StartupLease(layout.interlock, offline=True):
        for path in sorted(topologies):
            write_private(path, documents[path], maximum=MAX_DOCUMENT)
        _sync_directory(layout.service_directory)
        # The record changes last: until it does, rerunning this command with
        # the same image rewrites the topologies instead of reporting no change.
        write_private(completed, intent, maximum=MAX_DOCUMENT)
        _sync_directory(root)
    return {"image_changed": True, "services_started": False}

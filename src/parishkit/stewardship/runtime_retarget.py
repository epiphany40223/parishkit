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
import tempfile
from pathlib import Path

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


def _rederived(document):
    """The recorded deployment document as the running release would write it.

    A release may add a defaulted field (a new runtime path, say) that the
    provisioning release never recorded. Loading the recorded document back
    through the current loader fills in exactly those defaults, so the
    comparison sees the operator's inputs, not the older release's shape.
    """
    from .deployment import load_deployment
    from .deployment_documents import deployment_document

    with tempfile.NamedTemporaryFile("w", suffix=".json", encoding="utf-8") as handle:
        json.dump(document, handle)
        handle.flush()
        return deployment_document(load_deployment(Path(handle.name), environ={}))


def _plan(configuration, recorded, *, image):
    """The provisioning plan for the recorded inputs with the given image."""
    checkout, source_root = recorded["checkout"], recorded["bind_source_root"]
    return provisioning_plan(
        configuration,
        image=image,
        checkout=None if checkout is None else explicit_path(checkout),
        bind_source_root=None if source_root is None else explicit_path(source_root),
    )


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
    expected = dict(recorded, deployment=_rederived(recorded["deployment"]))
    if {key: value for key, value in proposed.items() if key != "image"} != {
        key: value for key, value in expected.items() if key != "image"
    }:
        raise ConfigError(
            "Deployment inputs differ from provisioning; only the image may change."
        )
    layout = RuntimeLayout(configuration)
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
        read_private(path, maximum=MAX_DOCUMENT)
    if not stale and not changed:
        return {
            "image_changed": False,
            "documents_changed": 0,
            "services_started": False,
        }
    with StartupLease(layout.interlock, offline=True):
        for path in sorted(stale):
            write_private(path, documents[path], maximum=MAX_DOCUMENT)
        _sync_directory(layout.service_directory)
        # The record changes last: until it does, rerunning this command with
        # the same image rewrites the topologies instead of reporting no change.
        if changed:
            write_private(completed, intent, maximum=MAX_DOCUMENT)
            _sync_directory(root)
    return {
        "image_changed": changed,
        "documents_changed": len(stale),
        "services_started": False,
    }

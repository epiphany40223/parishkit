"""An in-memory Google Drive for off-site backup tests; no network, no Google."""

import hashlib
import json
from itertools import count

from parishkit.stewardship.backup import DUMP, FILES, MANIFEST
from parishkit.stewardship.backup_drive import FOLDER_MIME, TAG_VALUE, DriveFailure


def write_sealed_set(directory, content=b"sealed"):
    """Fill one local set directory with two sealed files and a matching manifest.

    The off-site copy checks each sealed file against the SHA-256 the
    manifest recorded, so a fake set needs a manifest that names them.
    """
    manifest = {"version": 1}
    for kind, name in (("database", DUMP), ("files", FILES)):
        data = content + b" " + name.encode()
        (directory / name).write_bytes(data)
        manifest[kind] = {
            "file": name,
            "sealed_sha256": hashlib.sha256(data).hexdigest(),
        }
    (directory / MANIFEST).write_text(json.dumps(manifest), encoding="utf-8")
    return directory


class FakeDrive:
    """Duck-types :class:`DriveClient`: folders, tagged sets, uploads and trash.

    ``fail`` maps an operation name ("folder", "upload", "create_folder",
    "children", "trash") to a failure kind raised once per listed call, so
    tests can script transient and permanent errors.
    """

    def __init__(self, root="rootfolder0123", *, can_add=True, tag=TAG_VALUE):
        # ``tag`` is the deployment tag this client writes and lists; set it
        # to another value to act as a second deployment sharing the folder.
        self.tag = tag
        self.ids = count(1)
        self.items = {root: {"name": "root", "mime": FOLDER_MIME, "parent": None}}
        self.root = root
        self.can_add = can_add
        self.fail = {}
        self.calls = []
        self.corrupt = False

    def _maybe_fail(self, operation):
        """Raise the next scripted failure for one operation, if any."""
        self.calls.append(operation)
        queued = self.fail.get(operation)
        if queued:
            raise DriveFailure(queued.pop(0))

    def _new(self, **item):
        """Store one item and return its synthetic Drive ID."""
        identifier = f"id{next(self.ids):012d}"
        self.items[identifier] = {"trashed": False, **item}
        return identifier

    def folder(self, folder_id):
        self._maybe_fail("folder")
        item = self.items.get(folder_id)
        if item is None:
            raise DriveFailure("not_found")
        if item["mime"] != FOLDER_MIME:
            raise DriveFailure("not_folder")
        if not self.can_add:
            raise DriveFailure("permission")
        return {"id": folder_id}

    def children(self, parent, *, tagged=False, folders=None):
        self._maybe_fail("children")
        return [
            {
                "id": identifier,
                "name": item["name"],
                "mimeType": item["mime"],
                "size": item.get("size"),
                "md5Checksum": item.get("md5"),
            }
            for identifier, item in self.items.items()
            if item.get("parent") == parent
            and not item["trashed"]
            and (not tagged or item.get("tag") == self.tag)
            and (folders is not True or item["mime"] == FOLDER_MIME)
        ]

    def create_folder(self, name, parent):
        self._maybe_fail("create_folder")
        return self._new(name=name, mime=FOLDER_MIME, parent=parent, tag=self.tag)

    def upload(
        self, path, name, parent, *, content_type="application/octet-stream", **_
    ):
        self._maybe_fail("upload")
        data = path.read_bytes()
        digest = hashlib.md5(data, usedforsecurity=False).hexdigest()
        if self.corrupt:
            digest = "0" * 32
        identifier = self._new(
            name=name, mime=content_type, parent=parent, size=str(len(data)), md5=digest
        )
        return {"id": identifier, "size": str(len(data)), "md5Checksum": digest}

    def trash(self, file_id):
        self._maybe_fail("trash")
        self.items[file_id]["trashed"] = True

    def sets(self, tag=None):
        """Names of the live set folders tagged ``tag`` (default: this client's)."""
        tag = self.tag if tag is None else tag
        return sorted(
            item["name"]
            for item in self.items.values()
            if item.get("parent") == self.root
            and item.get("tag") == tag
            and not item["trashed"]
        )

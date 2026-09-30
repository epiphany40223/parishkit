"""Scripted batched Family helpers for #284 tests; no network, no credentials."""

import json
import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

from parishkit.stewardship.family_delivery import FamilyDeliveryMail
from parishkit.stewardship.family_delivery_process import FamilyMailSession

SCRIPT = Path(__file__).with_name("fake_gmail_helper.py")
# A child process imports only from the repository's own roots: the package
# (src) and, for scripts that import the test fakes, the repository root.
# Passing the parent's whole sys.path could let a stray module shadow them.
ROOT = Path(__file__).resolve().parents[2]
CHILD_PATH = os.pathsep.join([str(ROOT / "src"), str(ROOT)])
SETTINGS = {
    "delegated_email": "mail@example.org",
    "sender": "office@example.org",
    "reply_to": "reply@example.org",
}
LIMIT = ["reply", 550, "5.4.5 Daily user sending limit exceeded."]


def mail(*recipients):
    """One synthetic Family message with its own identity."""
    return FamilyDeliveryMail(
        uuid4(),
        SETTINGS["sender"],
        SETTINGS["reply_to"],
        recipients or ("a@example.org", "b@example.org"),
        "Campaign",
        "<p>Synthetic private code</p>",
        "Synthetic private code",
    )


def message_id(value):
    """The Message-ID Gmail sees for ``value``, a mail or semantic key."""
    key = getattr(value, "semantic_key", value)
    return f"<stewardship-{key.hex}@parishkit.invalid>"


class FakeGmailHelpers:
    """Spawn real helper processes that talk to one scripted fake Gmail."""

    def __init__(self, directory):
        """Keep the fault queue and event log in ``directory``."""
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.script()

    def script(self, *faults):
        """Replace the fault queue: each fault is ``(stage, action)``."""
        (self.directory / "faults.json").write_text(
            json.dumps([{"stage": stage, "action": action} for stage, action in faults])
        )

    def spawn(self):
        """Start one fake helper as the production spawn does, minus isolation."""
        return subprocess.Popen(
            [sys.executable, str(SCRIPT), str(self.directory)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            env={"PYTHONPATH": CHILD_PATH},
        )

    def session(self, **kwargs):
        """A parent session whose helpers are these fakes."""
        return FamilyMailSession(spawn=self.spawn, **kwargs)

    def events(self, name=None):
        """Every recorded event, or those named ``name``."""
        path = self.directory / "events.jsonl"
        if not path.exists():
            return []
        values = [json.loads(line) for line in path.read_text().splitlines()]
        return [value for value in values if name is None or value["event"] == name]

    def count(self, name):
        """How many ``name`` events were recorded."""
        return len(self.events(name))

    def data(self, value):
        """How many times Gmail received DATA for one message."""
        return sum(
            1 for event in self.events("data") if event["key"] == message_id(value)
        )

    def accepted(self, value):
        """How many times Gmail accepted one message."""
        return sum(
            1 for event in self.events("accepted") if event["key"] == message_id(value)
        )

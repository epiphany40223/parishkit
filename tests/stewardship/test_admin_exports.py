"""The export lifecycle commands' documents and plumbing (ADM-11 PR 8b).

Pure tests: the golden documents of ``export status``, the four changes and
``export download``, built through the projections the commands use, with
an exact allowlist of member names; the closed vocabularies kept equal to
the services'; the catalog entries; and a download's chunks reaching
standard output exactly, with its document on standard error. The commands
against a real database are in
database/test_admin_export_cli_postgresql.py.
"""

import hashlib
import io
import json
from types import SimpleNamespace
from uuid import UUID

import pytest

from parishkit.stewardship import admin_cli, admin_exports
from parishkit.stewardship.admin_cli import write_stream

from .test_admin_reads import FORBIDDEN, members
from .test_admin_reports import PREAMBLE, streaming

EXPORT = UUID("00000000-0000-4000-8000-000000000031")
CAMPAIGN = UUID("00000000-0000-4000-8000-000000000032")
KEY = UUID("00000000-0000-4000-8000-000000000033")
CREATED = "2054-10-05T14:00:00+00:00"
EXPIRES = "2054-10-06T14:00:00+00:00"
DIGEST = hashlib.sha256(b"date,scope\r\n").hexdigest()


def status(state, expires_at=None):
    """``export_state``'s status for the export, in ``state``."""
    return {
        "id": str(EXPORT),
        "campaign_id": str(CAMPAIGN),
        "report": "participation",
        "format": "csv",
        "state": state,
        "created_at": CREATED,
        "expires_at": expires_at,
    }


PUBLICATION = SimpleNamespace(size=12, sha256=DIGEST, row_count=3)


def queued():
    """An export the worker has not run yet: no file, Cancel offered."""
    return admin_exports.export_model(status("queued"), None, mutable=True)


def ready():
    """A published export: its file's name, type, size, digest and rows."""
    return admin_exports.export_model(
        status("ready", EXPIRES), PUBLICATION, mutable=True
    )


def change():
    """A created export, as ``export create`` reports it."""
    return admin_exports.ExportChange(
        created=True, request_key=KEY, export=queued().to_document()
    )


def download():
    """The document of a streamed file."""
    return admin_exports.ExportDownload(
        id=EXPORT,
        file_name="participation.csv",
        content_type="text/csv",
        size=12,
        sha256=DIGEST,
        count=3,
    )


QUEUED = {
    "id": str(EXPORT),
    "campaign_id": str(CAMPAIGN),
    "report": "participation",
    "format": "csv",
    "state": "queued",
    "created_at": CREATED,
    "expires_at": None,
    "changes_available": True,
    "can_cancel": True,
    "file_name": None,
    "content_type": None,
    "size": None,
    "sha256": None,
    "count": None,
}
GOLDEN = {
    "export status": (queued, QUEUED),
    "export status (ready)": (
        ready,
        QUEUED
        | {
            "state": "ready",
            "expires_at": EXPIRES,
            "can_cancel": False,
            "file_name": "participation.csv",
            "content_type": "text/csv",
            "size": 12,
            "sha256": DIGEST,
            "count": 3,
        },
    ),
    "export create": (
        change,
        {"created": True, "request_key": str(KEY), "export": QUEUED},
    ),
    "export download": (
        download,
        {
            "id": str(EXPORT),
            "file_name": "participation.csv",
            "content_type": "text/csv",
            "size": 12,
            "sha256": DIGEST,
            "count": 3,
        },
    ),
}
STATUS_MEMBERS = set(QUEUED)
ALLOWED = {
    "export status": STATUS_MEMBERS,
    "export status (ready)": STATUS_MEMBERS,
    "export create": {"created", "request_key", "export"} | STATUS_MEMBERS,
    "export download": {"id", "file_name", "content_type", "size", "sha256", "count"},
}


@pytest.mark.parametrize("command", sorted(GOLDEN))
def test_each_document_is_its_golden_projection(command):
    """The exact document, from the projection the command uses."""
    build, expected = GOLDEN[command]
    assert build().to_document() == expected


@pytest.mark.parametrize("command", sorted(GOLDEN))
def test_each_documents_members_are_exactly_its_allowlist(command):
    """Members at any depth equal the allowlist; none is personal data."""
    document = GOLDEN[command][0]().to_document()
    assert set(members(document)) == ALLOWED[command], command
    for name in ALLOWED[command]:
        assert FORBIDDEN.search(name) is None, (command, name)
    assert "@" not in json.dumps(document)


def test_every_export_command_has_a_golden_document():
    """Each 8b command's catalog fields are its model's."""
    entries = {entry["name"]: entry for entry in admin_cli.catalog()}
    exports = {spec.name for spec in admin_cli.COMMANDS if spec.name[:7] == "export "}
    assert exports == {
        "export create",
        "export status",
        "export cancel",
        "export retry",
        "export download",
        # The Family-level exports (PR 8e) print the same change document.
        "export financial",
        "export information",
        "export ministry",
        "export ministry-packet",
        "export directory",
        "export postal",
        "export exact create",
        "export exact status",
        "export exact cancel",
        "export exact retry",
        "export family-timeline",
        # The Campaign comparison's download (#1018, slice 6).
        "export comparison",
        # The campaign archive workbook (#1018 part (a)).
        "export archive",
        # The Census changes download (#463).
        "export census",
        # The Limitations download (#463).
        "export limitations",
        # The Roster changes to enter download (#463).
        "export roster",
        # The Pledges to enter download (#1733).
        "export pledges",
    }
    from parishkit.stewardship import (
        admin_census_export,
        admin_exact_exports,
        admin_limitations_export,
        admin_pledges,
        admin_roster_export,
    )

    models = {
        "export status": admin_exports.ExportStatus,
        "export download": admin_exports.ExportDownload,
        "export exact status": admin_exact_exports.ExactStatus,
        "export exact create": admin_exact_exports.ExactChange,
        "export exact cancel": admin_exact_exports.ExactChange,
        "export exact retry": admin_exact_exports.ExactChange,
        "export census": admin_census_export.CensusExport,
        "export limitations": admin_limitations_export.LimitationsExport,
        "export roster": admin_roster_export.RosterExport,
        "export pledges": admin_pledges.PledgeExport,
    }
    for name in exports:
        model = models.get(name, admin_exports.ExportChange)
        assert entries[name]["result_fields"] == list(model.field_names()), name
        assert entries[name]["pr"] == 8


@pytest.mark.parametrize(
    "state,terminal",
    [
        ("queued", False),
        ("running", False),
        ("retry_wait", False),
        ("abandoned", False),
        ("failed", True),
        ("succeeded", True),
        ("ready", True),
        ("expired", True),
        ("cancelled", True),
    ],
)
def test_a_watch_stops_once_the_worker_cannot_change_the_export(state, terminal):
    """Active task states keep a watch going; every other state ends it."""
    model = admin_exports.export_model(status(state), None, mutable=True)
    assert model.terminal is terminal


def test_a_busy_campaign_offers_no_cancel():
    """While other campaign work holds it, the page's buttons are off (temporary)."""
    model = admin_exports.export_model(status("queued"), None, mutable=False)
    document = model.to_document()
    assert not document["changes_available"] and not document["can_cancel"]


def test_the_vocabularies_are_the_services():
    """Spelled out before Django is set up; kept equal to the code they mirror."""
    from parishkit.stewardship.jobs.models import NONTERMINAL_STATES
    from parishkit.stewardship.reports.export_views import CONTENT_TYPES

    assert set(NONTERMINAL_STATES) == admin_exports.ACTIVE_STATES
    assert set(admin_cli.EXPORT_FORMATS) == set(CONTENT_TYPES)
    # The page offers Cancel while the export may still run.
    assert {"queued", "running", "retry_wait"} == admin_exports.CANCELLABLE_STATES


def test_the_download_lifetime_is_the_deployments():
    """The guard lives as long as a web download, within the guard's maximum."""
    from parishkit.stewardship.runtime_budget import RuntimeBudget

    limits = admin_cli.download_limits()
    assert limits.interactive_seconds == RuntimeBudget().download_seconds
    assert limits.drain_seconds == RuntimeBudget().drain_seconds


def test_a_downloads_chunks_reach_standard_output_as_they_are_written(monkeypatch):
    """Chunks written by the handler alone on standard output, in order.

    ``export download`` writes inside its read guard, so the command line
    writes nothing more after it; the document goes to standard error.
    """

    def handler(args, preamble, runtime, context):
        """Two chunks of a binary file, then its description."""
        for chunk in (b"\x89PNG\r\n", b"\x00\xff"):
            write_stream(context["stdout"], chunk)
        return {"size": 8}

    code, out, err = streaming(monkeypatch, handler)
    assert code == 0 and out == b"\x89PNG\r\n\x00\xff"
    [document] = [json.loads(line) for line in err]
    assert document["ok"] and document["result"] == {"size": 8}


@pytest.mark.parametrize(
    "argv",
    [
        ["export", "download", str(EXPORT)],
        ["export", "download", "not-a-uuid", "--stream"],
    ],
)
def test_a_downloads_usage_error_goes_to_standard_error(argv):
    """Without --stream, or with a bad id: a usage error, never in the file."""
    out, err = io.StringIO(), io.StringIO()
    code = admin_cli.main(
        [*argv, "--config", "web.yaml", "--session-stdin"],
        stdin=io.BytesIO(PREAMBLE),
        stdout=out,
        stderr=err,
    )
    assert code == 2 and out.getvalue() == ""
    assert json.loads(err.getvalue())["error"]["code"] == "usage"

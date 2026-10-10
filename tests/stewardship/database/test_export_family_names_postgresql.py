"""Export files name every Family one way, even once their source is compacted.

The Financial and Additional information exports name each Family as the
page does, surname then heads (#932), from the snapshot their capture was
read from. Once compaction has marked that snapshot, whether or not it has
deleted the snapshot's rows yet, a late render keeps the captured surname for
every row and says so in its report details.
"""

from dataclasses import asdict
from uuid import uuid4

import pytest

from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.reports.export_models import ExportRequest
from parishkit.stewardship.reports.export_tasks import load_document
from parishkit.stewardship.reports.information import InformationQuery
from parishkit.stewardship.reports.information_exports import create_information_export
from parishkit.stewardship.source import compaction
from parishkit.stewardship.source.snapshot_names import (
    FAMILY_NAMES_DETAIL,
    FAMILY_NAMES_FULL,
    FAMILY_NAMES_SURNAME,
)
from parishkit.stewardship.source.version_models import SnapshotFamily

from ..test_financial_answers import CHECK, OPTIONS
from .response_builders import activate_response_service, response_source
from .test_background_grants_postgresql import task_login
from .test_directory_exports_postgresql import _compact
from .test_financial_exports_postgresql import capture
from .test_financial_report_postgresql import pledge
from .test_financial_source_postgresql import financial_source
from .test_policy_postgresql import user
from .test_response_http_postgresql import load_form
from .test_runtime_auth_grants_postgresql import web_login
from .test_source_families_postgresql import prepare, promote
from .test_weekly_observation_postgresql import respond

pytestmark = pytest.mark.django_db(transaction=True)


def _render(request_id):
    """Build the file's document as the real worker does, under its login.

    Returns the Family column (found by its heading) and the report details.
    """
    with task_login(ServiceRole.WORKER, exact=True, reconnect=True):
        request = ExportRequest.objects.select_related(
            "configuration__parish", "information_snapshot", "financial_snapshot"
        ).get(pk=request_id)
        document = load_document(request)
    column = document.headings.index("Family")
    return [row[column] for row in document.rows], dict(document.metadata)


def _compact_capture(harness, monkeypatch, captured, *, reclaimed):
    """Promote newer data, then compact the capture's snapshot.

    ``reclaimed`` False stops after the mark, as a render racing the
    reclaimer would find it: the snapshot is compacted but all its Family
    rows still exist.
    """
    snapshot, claim = prepare(response_source())
    promote(snapshot, claim, harness.campaign, harness.rings)
    with monkeypatch.context() as patched:
        if not reclaimed:
            patched.setattr(compaction, "_drain", lambda *args, **kwargs: None)
        _compact(monkeypatch, captured)
    remaining = SnapshotFamily.objects.filter(snapshot_id=captured).exists()
    assert remaining is not reclaimed


@pytest.mark.parametrize("reclaimed", [False, True], ids=["marked", "reclaimed"])
def test_financial_export_after_compaction_uses_surnames(
    response_service, monkeypatch, reclaimed
):
    """A Financial file names every Family by surname once its source is compacted."""
    harness = response_service
    financial_source(harness, modules=["financial"], options=map(asdict, OPTIONS))
    harness = activate_response_service(harness)
    with web_login():
        pledge(harness, load_form(harness), shares={CHECK: ""})
    # The answer pins the snapshot it was given for as long as it stands, so
    # the export is captured from a later refresh, which nothing pins.
    financial_source(harness, modules=["financial"], options=map(asdict, OPTIONS))
    request = capture(harness, user("admin@example.org").pk)
    captured = request.financial_snapshot.source_id
    names, details = _render(request.pk)
    assert names[0].startswith("Example, ")
    assert details[FAMILY_NAMES_DETAIL] == FAMILY_NAMES_FULL
    _compact_capture(harness, monkeypatch, captured, reclaimed=reclaimed)
    names, details = _render(request.pk)
    assert names and all(name == "Example" for name in names)
    assert details[FAMILY_NAMES_DETAIL] == FAMILY_NAMES_SURNAME


@pytest.mark.parametrize("reclaimed", [False, True], ids=["marked", "reclaimed"])
def test_information_export_after_compaction_uses_surnames(
    live_response_service, monkeypatch, reclaimed
):
    """An Additional information file does the same."""
    harness = live_response_service
    respond(harness, "Please call about the parish picnic.")
    # As above, the export is captured from a later, unpinned refresh.
    snapshot, claim = prepare(response_source())
    promote(snapshot, claim, harness.campaign, harness.rings)
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        request = create_information_export(
            harness.service.store,
            user("admin@example.org").pk,
            campaign_id=harness.campaign.pk,
            query=InformationQuery(),
            history=False,
            format="csv",
            browser_timezone="UTC",
            request_key=uuid4(),
        )
    captured = request.information_snapshot.source_id
    names, details = _render(request.pk)
    assert names and all(name.startswith("Example, ") for name in names)
    assert details[FAMILY_NAMES_DETAIL] == FAMILY_NAMES_FULL
    _compact_capture(harness, monkeypatch, captured, reclaimed=reclaimed)
    names, details = _render(request.pk)
    assert names and all(name == "Example" for name in names)
    assert details[FAMILY_NAMES_DETAIL] == FAMILY_NAMES_SURNAME

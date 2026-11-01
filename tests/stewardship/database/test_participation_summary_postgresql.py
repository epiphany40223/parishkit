"""The Response dashboard's export freezes its Summary when requested (#1557).

Frozen migration 0142: the request's own transaction captures the page's
Summary (the statistics and the funnel) into an immutable
``stewardship_participation_export_snapshot``; the worker renders the file
from that capture alone, the database requires one on every participation
request but a latest-data handoff, and it admits only the closed shape.
"""

import csv
import io

import pytest
from django.db import DatabaseError, IntegrityError, connection, transaction

from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.reports import (
    export_services,
    participation_summary,
    response_dashboard,
    statistics_selection,
)
from parishkit.stewardship.reports.artifacts import ArtifactReceipt, open_artifact
from parishkit.stewardship.reports.export_models import (
    ExportPublication,
    ExportRequest,
    ParticipationExportSnapshot,
)
from parishkit.stewardship.reports.participation import SCOPE_LABELS

from .test_export_jobs_postgresql import (  # noqa: F401
    request_export,
    run_export,
    scenario,
)

pytestmark = pytest.mark.django_db(transaction=True)


def published_csv(scenario, request):  # noqa: F811
    """The published CSV of ``request``, as rows of dictionaries."""
    root = scenario[3]
    publication = ExportPublication.objects.get(request=request)
    with open_artifact(
        root,
        request.campaign_id,
        ArtifactReceipt(publication.attempt_id, publication.size, publication.sha256),
    ) as stream:
        body = stream.read().decode()
    # The file's header block (R59 on #2324) names the population the
    # request captured; the table follows its blank row.
    header, table = body.split("\r\n\r\n", 1)
    counted = str(SCOPE_LABELS[request.parameters["population_scope"]])
    assert header.split("\r\n")[1] == f"Families counted,{counted}"
    return list(csv.DictReader(io.StringIO(table)))


def test_the_request_freezes_the_summary_and_the_file_reads_only_it(
    scenario,  # noqa: F811
    monkeypatch,
):
    """The capture holds the page's funnel and figures; rendering never reads
    them again, so a later response or ParishSoft update cannot change the
    file, and no internal identifier is captured (#966)."""
    request = request_export(scenario)
    capture = ParticipationExportSnapshot.objects.get(request=request)
    assert (capture.campaign_id, capture.actor_id) == (
        request.campaign_id,
        request.requester_id,
    )
    document = capture.document
    assert [row["label"] for row in document["stages"]] == [
        "Invited",
        "Link followed",
        "Form opened",
        "Progressed past the first step",
        "Submitted",
    ]
    assert [row["label"] for row in document["population"]][:2] == [
        "Families",
        "Active Members",
    ]
    assert str(request.campaign_id) not in str(document)

    def no_reread(*args, **kwargs):
        """Rendering must use the frozen capture, never a new read."""
        raise AssertionError("The export re-read the Summary.")

    for module, name in (
        (participation_summary, "capture"),
        (response_dashboard, "read_metrics"),
        (statistics_selection, "capture_statistics"),
    ):
        monkeypatch.setattr(module, name, no_reread)
    assert run_export(scenario, request)
    rows = published_csv(scenario, request)
    summary = [row for row in rows if row["Section"] == "Summary"]
    stages = [row for row in summary if row["Group"] == "Families by stage"]
    assert [(row["Figure"], row["Value"]) for row in stages] == [
        (row["label"], str(row["count"])) for row in document["stages"]
    ]
    assert any(row["Figure"] == "Data as of" for row in summary)
    assert [row["Section"] for row in rows].count("Daily participation") == len(
        request.fact_set.days.all()
    )


def test_a_participation_request_commits_only_with_its_capture(
    scenario,  # noqa: F811
    monkeypatch,
):
    """The deferred check refuses a request without its Summary, and nothing
    of it (request, task or capture) is kept."""

    class Skipped:
        """A capture model whose create stores nothing."""

        class objects:  # noqa: N801 - mimics the manager attribute
            @staticmethod
            def create(**values):
                """Store nothing."""

    monkeypatch.setattr(export_services, "ParticipationExportSnapshot", Skipped)
    with pytest.raises(IntegrityError, match="requires its Summary capture"):
        request_export(scenario)
    assert not ExportRequest.objects.exists()
    assert not TaskRun.objects.filter(task_type=export_services.TASK_TYPE).exists()


def test_a_capture_is_one_per_request_and_never_changes(scenario):  # noqa: F811
    """A second capture for a committed request (made in an earlier
    transaction), an edit and a removal are all refused."""
    request = request_export(scenario)
    capture = ParticipationExportSnapshot.objects.get(request=request)
    with (
        pytest.raises(DatabaseError, match="capture is unavailable"),
        transaction.atomic(),
    ):
        ParticipationExportSnapshot.objects.create(
            request=request,
            campaign_id=request.campaign_id,
            actor_id=request.requester_id,
            correlation_id=request.correlation_id,
            document=capture.document,
        )
    for statement in (
        """UPDATE stewardship_participation_export_snapshot
           SET document=jsonb_set(document,'{stages,0,count}','0') WHERE id=%s""",
        "DELETE FROM stewardship_participation_export_snapshot WHERE id=%s",
    ):
        with (
            pytest.raises(DatabaseError, match="immutable"),
            transaction.atomic(),
            connection.cursor() as cursor,
        ):
            cursor.execute(statement, [capture.pk])
    assert ParticipationExportSnapshot.objects.get(pk=capture.pk).document == (
        capture.document
    )


def valid():
    """A capture document of the closed shape."""
    return {
        "observed_at": "2054-10-02T12:00:00+00:00",
        "population": [
            {"label": "Families", "value": 3, "note": ""},
            {"label": "Active Members", "value": None, "note": "Unknown."},
        ],
        "stages": [{"label": "Invited", "count": 2, "note": ""}],
    }


@pytest.mark.parametrize(
    "change",
    [
        # A field the files were not designed to hold, such as Family names.
        lambda value: value | {"families": ["Example"]},
        # An extra key inside a row.
        lambda value: (
            value
            | {"stages": [{"label": "Invited", "count": 2, "note": "", "duid": 1}]}
        ),
        lambda value: value | {"observed_at": "yesterday"},
        lambda value: value | {"population": value["population"][:1]},
        lambda value: (
            value | {"stages": [{"label": "Invited", "count": -1, "note": ""}]}
        ),
        lambda value: (
            value | {"stages": [{"label": "Invited", "count": 1.5, "note": ""}]}
        ),
        lambda value: (
            value
            | {
                "population": [
                    *value["population"][:1],
                    {"label": "", "value": 1, "note": ""},
                ]
            }
        ),
        lambda value: (
            value
            | {
                "population": [
                    value["population"][0],
                    {"label": "Active Members", "value": {"money": "1.5"}, "note": ""},
                ]
            }
        ),
    ],
)
def test_the_capture_shape_is_closed(scenario, monkeypatch, change):  # noqa: F811
    """SQL refuses any document but the closed shape, so the request too;
    the valid document itself is admitted, so each refusal is the change."""
    monkeypatch.setattr(export_services, "summary_capture", lambda *_: valid())
    assert request_export(scenario)
    monkeypatch.setattr(export_services, "summary_capture", lambda *_: change(valid()))
    with pytest.raises(IntegrityError, match="Invalid participation export summary"):
        request_export(scenario)
    assert ExportRequest.objects.count() == 1


def uncaptured(scenario, monkeypatch):  # noqa: F811
    """A participation request made in this transaction with no capture yet."""
    monkeypatch.setattr(export_services, "ParticipationExportSnapshot", _NoCapture)
    request = request_export(scenario)
    monkeypatch.undo()
    return request


class _NoCapture:
    """A capture model whose create stores nothing."""

    class objects:  # noqa: N801 - mimics the manager attribute
        @staticmethod
        def create(**values):
            """Store nothing."""


@pytest.mark.parametrize("change", ["actor", "campaign"])
def test_the_capture_guard_admits_only_its_own_new_request(
    scenario,  # noqa: F811
    monkeypatch,
    change,
):
    """A capture by anyone but the requester, or for another campaign, is
    refused by its guard. A request an earlier transaction committed is
    refused too (``test_a_capture_is_one_per_request_and_never_changes``)."""
    from uuid import uuid4

    with (
        pytest.raises(DatabaseError, match="capture is unavailable"),
        transaction.atomic(),
    ):
        request = uncaptured(scenario, monkeypatch)
        ParticipationExportSnapshot.objects.create(
            request=request,
            campaign_id=uuid4() if change == "campaign" else request.campaign_id,
            actor_id=uuid4() if change == "actor" else request.requester_id,
            correlation_id=request.correlation_id,
            document=valid(),
        )


def test_only_the_web_may_capture(scenario):  # noqa: F811
    """The worker renders captures but can never write one."""
    from parishkit.stewardship.deployment import ServiceRole

    from .test_background_grants_postgresql import task_login

    with task_login(ServiceRole.WORKER), connection.cursor() as cursor:
        cursor.execute(
            "SELECT has_table_privilege(current_user,"
            "'stewardship_participation_export_snapshot','INSERT'),"
            "has_table_privilege(current_user,"
            "'stewardship_participation_export_snapshot','SELECT')"
        )
        assert cursor.fetchone() == (False, True)


def test_unreadable_statistics_still_capture_the_funnel(
    scenario,  # noqa: F811
    monkeypatch,
):
    """A request made as the statistics became unreadable commits with the
    funnel and every Population figure unknown, with its reason."""
    from parishkit.stewardship.reports.statistics import StatisticsUnavailable

    def unreadable(campaign_id):
        """The statistics' inputs cannot be read."""
        raise StatisticsUnavailable("Campaign statistics are unavailable.")

    monkeypatch.setattr(statistics_selection, "capture_statistics", unreadable)
    request = request_export(scenario)
    document = ParticipationExportSnapshot.objects.get(request=request).document
    assert {row["value"] for row in document["population"]} == {None}
    assert all(row["note"] for row in document["population"])
    assert [row["label"] for row in document["stages"]][0] == "Invited"

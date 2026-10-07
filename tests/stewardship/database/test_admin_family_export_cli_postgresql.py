"""The Family-level export commands of ``pk-stewardship admin`` (ADM-11 PR 8e).

Process admission is replaced by the test's own Django assembly; everything
after it is the real command line on the restricted web login. Each command
creates the export record its page's form creates, through the page's own
service, with the same ``export_requested`` event; its rows reach only the
file, which the real worker renders and ``export download --stream`` (what
``export fetch`` runs) writes byte for byte as the page's download does.
"""

from dataclasses import asdict
from uuid import uuid4

import pytest
from django.urls import reverse

from parishkit.stewardship.audit.models import AuditContext, AuditEvent
from parishkit.stewardship.campaigns.read_guards import DownloadPool, ReadLimits
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.jobs.dispatch import execute_hint
from parishkit.stewardship.jobs.queues import WorkQueue
from parishkit.stewardship.reports.export_models import ExportRequest
from parishkit.stewardship.reports.export_services import TASK_TYPE
from parishkit.stewardship.reports.export_tasks import export_handler
from parishkit.stewardship.reports.financial import FinancialQuery

from ..test_financial_answers import CHECK, OPTIONS
from .auth_builders import signed_in
from .response_builders import activate_response_service
from .test_admin_export_cli_postgresql import download, read_only_session
from .test_admin_report_cli_postgresql import cli  # noqa: F401
from .test_background_grants_postgresql import task_login
from .test_export_cleanup_postgresql import expire_publication
from .test_export_views_postgresql import post, restricted_download_pool
from .test_financial_report_postgresql import pledge
from .test_financial_source_postgresql import financial_source
from .test_information_followup_postgresql import search
from .test_ministry_reports_postgresql import setup as ministry_setup
from .test_response_http_postgresql import load_form
from .test_runtime_auth_grants_postgresql import web_login

pytestmark = pytest.mark.django_db(transaction=True)


def requested():
    """Each ``export_requested`` event's context members, in order."""
    return [
        frozenset(context)
        for context in AuditContext.objects.filter(event__event_type="export_requested")
        .order_by("event__created_at")
        .values_list("context", flat=True)
    ]


def count(event_type):
    """How many ``event_type`` events have been recorded."""
    return AuditEvent.objects.filter(event_type=event_type).count()


def render(service, root, export_id):
    """Run the real worker's render of one export."""
    request = ExportRequest.objects.get(pk=export_id)
    with task_login(ServiceRole.WORKER, exact=True, reconnect=True):
        assert execute_hint(
            request.task_id,
            queue=WorkQueue.GENERAL,
            worker_id=uuid4(),
            handlers={TASK_TYPE: export_handler(store=service.store, root=root)},
        )


@pytest.fixture
def reports_root(tmp_path, settings):
    """The application's export store, private as the web requires."""
    root = tmp_path / "reports"
    root.mkdir(mode=0o700)
    settings.STEWARDSHIP_REPORTS_ROOT = root
    settings.STEWARDSHIP_DOWNLOAD_POOL = DownloadPool(ReadLimits(process_pool_size=1))
    return root


def test_a_financial_export_is_the_pages(
    response_service,
    google,
    cli,  # noqa: F811
    reports_root,
    settings,
):
    """The page's record, event and file; nothing of a Family on the terminal."""
    harness = response_service
    financial_source(harness, modules=["financial"], options=map(asdict, OPTIONS))
    harness = activate_response_service(harness)
    with web_login():
        pledge(harness, load_form(harness), shares={CHECK: ""})
    browser, _ = signed_in()
    run = cli()
    route = reverse("admin:financial_export")

    # The page's form, then the command: the same record kind and event.
    fields = FinancialQuery().form_values() | dict(
        format="csv", browser_timezone="UTC", request_key=str(uuid4())
    )
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        assert post(browser, route, fields).status_code == 302
    page = ExportRequest.objects.get(request_key=fields["request_key"])
    key = uuid4()
    argv = (
        "export",
        "financial",
        "--format",
        "csv",
        "--timezone",
        "UTC",
        "--yes",
        "--request-key",
        str(key),
    )
    # A fresh-gated command asks first: unanswered, nothing is done.
    code, document = run(*argv[:-3], "--request-key", str(key))
    assert code == 4 and document["error"]["code"] == "confirmation_required"
    assert not ExportRequest.objects.filter(request_key=key).exists()
    assert count("automation_fresh_gate") == 0
    code, document = run(*argv)
    assert code == 0, document
    result = document["result"]
    assert result["created"] and result["request_key"] == str(key)
    made = ExportRequest.objects.get(request_key=key)
    assert result["export"]["id"] == str(made.pk)
    assert result["export"]["state"] == "queued"
    # The same selection as the page's: kind, campaign, the whole retained
    # parameters (filters and giving proof), format and time zone.
    for field in ("report", "campaign_id", "parameters", "format", "browser_timezone"):
        assert getattr(made, field) == getattr(page, field), field
    first, second = requested()
    assert first == second
    assert count("admin_cmd_export_financial") == 1
    event = AuditEvent.objects.get(event_type="admin_cmd_export_financial")
    assert event.subject_id is not None
    # The full-scope session stood in for the page's recent sign-in, in the
    # export's own transaction (#547, the caller-aware require_fresh).
    [gate] = AuditEvent.objects.filter(event_type="automation_fresh_gate")
    assert gate.subject_id == event.subject_id
    # A repeat returns the same export and records nothing new.
    code, document = run(*argv)
    assert code == 0 and document["result"]["created"] is False
    assert document["result"]["export"]["id"] == str(made.pk)
    assert len(requested()) == 2 and count("admin_cmd_export_financial") == 1
    # The same key for another selection is the page's 409: invalid.
    code, document = run(
        *argv[:-2], "--filter", "amount=zero", "--request-key", str(key)
    )
    assert code == 1 and document["error"]["code"] == "invalid"
    # A search names a Family: refused before anything is done.
    for value in ("search=Example", "page=2", "amount=sometimes", "nofilter"):
        code, document = run(*argv[:-2], "--filter", value)
        assert code == 1 and document["error"]["code"] == "invalid", value
    assert len(requested()) == 2
    # A filter the page offers narrows the capture as on the page.
    code, document = run(*argv[:-2], "--filter", "amount=zero")
    assert code == 0, document
    zero = ExportRequest.objects.get(pk=document["result"]["export"]["id"])
    assert zero.parameters["filters"]["amount"] == "zero"

    # The file: rendered by the real worker, streamed byte for byte as the
    # page's download of the same export.
    render(run.service, reports_root, made.pk)
    code, body, streamed = download(made.pk, run.secret)
    assert code == 0, streamed
    assert body.startswith(b"Family,") and streamed["result"]["size"] == len(body)
    with restricted_download_pool(settings):
        job_route = reverse("admin:report_export_download", args=[made.pk])
        response, page_body = search(browser, job_route, {})
    assert response.status_code == 200 and page_body == body

    # Regenerating an expired financial file is fresh-gated too, as on the
    # status page: it asks first, then the session stands in for the
    # recent sign-in in the new export's transaction.
    expire_publication(made)
    regenerate = ("export", "regenerate", str(made.pk), "--request-key", str(uuid4()))
    gates = count("automation_fresh_gate")
    code, document = run(*regenerate)
    assert code == 4 and document["error"]["code"] == "confirmation_required"
    assert count("automation_fresh_gate") == gates
    code, document = run(*regenerate, "--yes")
    assert code == 0 and document["result"]["created"], document
    assert count("automation_fresh_gate") == gates + 1

    # A read-only session cannot request one, nor stand in for a sign-in.
    gates = count("automation_fresh_gate")
    reader = read_only_session(run.service)
    code, document = run(*argv[:-2], secret=reader)
    assert code == 1 and document["error"]["code"] == "denied"
    assert count("automation_fresh_gate") == gates


def test_ministry_and_information_exports(
    response_service,
    google,
    cli,  # noqa: F811
    reports_root,
):
    """The Ministry summary, a list, a packet and the information queue."""
    from parishkit.stewardship.reports.information import InformationQuery
    from parishkit.stewardship.reports.ministries import MinistryQuery

    harness = ministry_setup(response_service)
    browser, _ = signed_in()
    run = cli()
    base = ("--format", "csv", "--timezone", "UTC")
    cases = {
        "summary": ("export", "ministry", *base),
        "join": (
            "export",
            "ministry",
            *base,
            "--ministry",
            "9",
            "--requests",
            "join",
            "--filter",
            "history=all",
        ),
        "packet": ("export", "ministry-packet", *base, "--ministry", "9"),
        "information": ("export", "information", *base, "--history"),
    }
    made = {}
    for name, argv in cases.items():
        code, document = run(*argv)
        assert code == 0, (name, document)
        assert document["result"]["created"], name
        made[name] = ExportRequest.objects.get(pk=document["result"]["export"]["id"])
        assert made[name].campaign_id == harness.campaign.pk
    assert len(requested()) == 4
    # The pages' forms for the same selections make the same records: the
    # same parameters, and the same export_requested context members.
    forms = {
        "join": (
            reverse("admin:ministry_export"),
            MinistryQuery(history="all").form_values()
            | {"action": "join", "ministry": "9"},
        ),
        "information": (
            reverse("admin:information_export"),
            InformationQuery().form_values() | {"history": "yes"},
        ),
    }
    for name, (route, fields) in forms.items():
        fields = fields | dict(
            format="csv", browser_timezone="UTC", request_key=str(uuid4())
        )
        with task_login(ServiceRole.WEB, exact=True, reconnect=True):
            assert post(browser, route, fields).status_code == 302, name
        page = ExportRequest.objects.get(request_key=fields["request_key"])
        for field in ("report", "campaign_id", "parameters", "format"):
            assert getattr(page, field) == getattr(made[name], field), (name, field)
        contexts = requested()
        assert contexts[-1] == contexts[list(cases).index(name)], name
    assert len(requested()) == 6
    assert count("admin_cmd_export_ministry") == 2
    assert count("admin_cmd_export_ministry_packet") == 1
    assert count("admin_cmd_export_information") == 1

    # The packet's file: rendered and streamed, never printed.
    render(run.service, reports_root, made["packet"].pk)
    code, document = run("export", "status", str(made["packet"].pk))
    assert code == 0 and document["result"]["state"] == "ready", document
    assert "Example" not in str(document) and "@" not in str(document)
    code, body, streamed = download(made["packet"].pk, run.secret)
    assert code == 0 and len(body) == streamed["result"]["size"] > 0

    # The pages' refusals: detail filters on the summary, a lone
    # --requests, a Ministry outside the page's range, a duplicate in a
    # packet, a search.
    for argv in (
        ("export", "ministry", *base, "--filter", "state=new"),
        ("export", "ministry", *base, "--requests", "join"),
        ("export", "ministry", *base, "--ministry", "0", "--requests", "join"),
        ("export", "ministry", *base, "--filter", "size=25"),
        ("export", "ministry-packet", *base, "--ministry", "9", "--ministry", "9"),
        ("export", "information", *base, "--filter", "search=Example"),
    ):
        code, document = run(*argv)
        assert code == 1 and document["error"]["code"] == "invalid", argv
    # A Ministry the campaign does not offer: a bad selection, as the packet
    # form's check says, and nothing is created.
    code, document = run(
        "export", "ministry", *base, "--ministry", "123", "--requests", "join"
    )
    assert code == 1 and document["error"]["code"] == "invalid"
    assert len(requested()) == 6


@pytest.fixture
def with_keyrings(monkeypatch):
    """Give the admitted process a keyring loader: ``loads`` counts its calls.

    ``use(loader)`` installs ``loader(names)`` as the process's
    ``keyrings``, as ``admin_cli.admitted`` does with ``load_keyrings``.
    """
    import contextlib
    import dataclasses

    from parishkit.stewardship import admin_cli

    loads = []

    def use(loader):
        """Wrap the test's admission so its runtime carries ``loader``."""
        admitted = admin_cli.ADMISSION

        @contextlib.contextmanager
        def wrapped(configuration):
            """The test's admission, with the keyring loader."""
            with admitted(configuration) as runtime:
                yield dataclasses.replace(
                    runtime,
                    keyrings=lambda names: (loads.append(tuple(names)), loader(names))[
                        1
                    ],
                )

        monkeypatch.setattr(admin_cli, "ADMISSION", wrapped)

    use.loads = loads
    return use


def test_the_directory_and_mail_merge_exports(
    live_response_service,
    google,
    cli,  # noqa: F811
    reports_root,
    settings,
    with_keyrings,
):
    """The code MAC keyring loads only for a new request; the codes stay in the file."""
    from parishkit.stewardship.admin_cli import CredentialMismatch

    harness = live_response_service
    browser, _ = signed_in()
    run = cli()
    with_keyrings(lambda names: (harness.rings.mac,))
    # Both are fresh-gated, so they prompt: --yes answers.
    base = ("--format", "csv", "--timezone", "UTC", "--yes")
    key = uuid4()
    argv = ("export", "directory", *base, "--request-key", str(key))
    code, document = run(*argv)
    assert code == 0, document
    assert with_keyrings.loads == [("family_code_mac",)]
    made = ExportRequest.objects.get(request_key=key)
    assert made.report == "family_directory"
    assert harness.code not in str(document) and "Example" not in str(document)
    assert count("admin_cmd_export_directory") == 1 and len(requested()) == 1
    # The session stood in for the page's recent sign-in (#547).
    assert count("automation_fresh_gate") == 1
    # A repeat returns the same export without loading any key.
    code, document = run(*argv)
    assert code == 0 and document["result"]["created"] is False
    assert document["result"]["export"]["id"] == str(made.pk)
    assert with_keyrings.loads == [("family_code_mac",)]
    # The same key for the mail merge is the page's 409, also without a key.
    code, document = run("export", "postal", *base, "--request-key", str(key))
    assert code == 1 and document["error"]["code"] == "invalid"
    assert with_keyrings.loads == [("family_code_mac",)]
    # A search or a Family code is refused before anything, a key included.
    for value in ("search=Example", f"exact_code={harness.code}"):
        code, document = run("export", "directory", *base, "--filter", value)
        assert code == 1 and document["error"]["code"] == "invalid", value
    assert with_keyrings.loads == [("family_code_mac",)]
    # The mail merge, with a page filter.
    code, document = run("export", "postal", *base, "--filter", "phone=any")
    assert code == 0, document
    postal = ExportRequest.objects.get(pk=document["result"]["export"]["id"])
    assert postal.report == "postal_outreach"
    assert count("admin_cmd_export_postal") == 1

    # The codes reach only the file: the worker decrypts them, and the
    # stream is the page's download of the same export, byte for byte.
    request = ExportRequest.objects.get(pk=made.pk)
    with task_login(ServiceRole.WORKER, exact=True, reconnect=True):
        assert execute_hint(
            request.task_id,
            queue=WorkQueue.GENERAL,
            worker_id=uuid4(),
            handlers={
                TASK_TYPE: export_handler(
                    store=run.service.store,
                    root=reports_root,
                    general=harness.rings.general,
                )
            },
        )
    code, body, streamed = download(made.pk, run.secret)
    assert code == 0, streamed
    assert harness.code.encode() in body
    assert harness.code not in str(streamed)
    with restricted_download_pool(settings):
        response, page_body = search(
            browser, reverse("admin:report_export_download", args=[made.pk]), {}
        )
    assert response.status_code == 200 and page_body == body

    # A keyring that differs from the running web's: exit 2, nothing made.
    def differs(names):
        """The web published another receipt (a rotation in progress)."""
        raise CredentialMismatch("The web keyrings differ.")

    with_keyrings(differs)
    before = ExportRequest.objects.count()
    code, document = run("export", "directory", *base)
    assert code == 2 and document["error"]["code"] == "credential_mismatch"
    assert ExportRequest.objects.count() == before
    assert count("admin_cmd_export_directory") == 1


def test_the_directory_export_refusals(
    live_response_service,
    google,
    cli,  # noqa: F811
    reports_root,
    with_keyrings,
):
    """A read-only session is refused, and a busy key lock is exit 3."""
    import psycopg
    from django.db import connection

    from parishkit.stewardship.campaigns.credential_keys import KEY_LOCK

    harness = live_response_service
    signed_in()
    run = cli()
    with_keyrings(lambda names: (harness.rings.mac,))
    # Both are fresh-gated, so they prompt: --yes answers.
    base = ("--format", "csv", "--timezone", "UTC", "--yes")
    reader = read_only_session(run.service)
    code, document = run("export", "directory", *base, secret=reader)
    assert code == 1 and document["error"]["code"] == "denied"
    assert with_keyrings.loads == []
    # A key rotation holds the key-set lock exclusively: the export cannot
    # bind its selection now, and nothing is made.
    settings = connection.settings_dict
    with psycopg.connect(
        host=settings["HOST"],
        port=settings["PORT"],
        dbname=settings["NAME"],
        user=settings["USER"],
        password=settings["PASSWORD"],
        autocommit=False,
    ) as rotation:
        rotation.execute("SELECT pg_advisory_xact_lock(%s, %s)", KEY_LOCK)
        before = ExportRequest.objects.count()
        code, document = run("export", "directory", *base)
        assert code == 3 and document["error"]["code"] == "unavailable", document
        assert ExportRequest.objects.count() == before
        assert count("admin_cmd_export_directory") == 0
        rotation.rollback()
    code, document = run("export", "directory", *base)
    assert code == 0, document

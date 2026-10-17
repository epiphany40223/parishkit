"""An ended report export answers the fixed "no longer available" page (#1398).

Exports are point-in-time (operations spec, "Point-in-time exports"; UX
conventions, "Point-in-time downloads"): once a report export's window is
over, or it was cancelled, its status page, Download, Cancel and Retry all
answer one fixed page, 410 Gone, linking back to the report. The owner and
capability check comes first, so another reader's ended export is still the
plain denial and reveals nothing.
"""

from uuid import uuid4

import pytest
from django.urls import reverse

from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.reports.export_models import ExportRequest

from ..export_urls import export_action
from .test_background_grants_postgresql import task_login
from .test_export_views_postgresql import post
from .test_family_mail_preparation_postgresql import family_mail  # noqa: F401
from .test_report_workspace_postgresql import http_scenario, read  # noqa: F401

pytestmark = pytest.mark.django_db(transaction=True)

GONE = b"This export is no longer available."


def _create(browser, facts):
    """Queue one Participation chart export from the page's form; its page."""
    values = {
        "fact_set_id": str(facts.pk),
        "format": "csv",
        "browser_timezone": "UTC",
        "request_key": str(uuid4()),
    }
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        response = post(browser, reverse("admin:report_export_create"), values)
    assert response.status_code == 302, response
    return response["Location"], ExportRequest.objects.get(
        request_key=values["request_key"]
    )


def _commands(browser, page):
    """Each command's answer on the export at ``page``: {action: response}."""
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        return {
            "cancel": post(browser, export_action(page, "cancel")),
            "retry": post(
                browser, export_action(page, "retry"), {"request_key": str(uuid4())}
            ),
            "download": post(browser, export_action(page, "download")),
        }


def _assert_gone(browser, page, back):
    """The status page and every command answer the 410 page linking to
    ``back``, and none of them changed anything."""
    before = ExportRequest.objects.count()
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        response, body = read(browser, page)
    assert response.status_code == 410 and GONE in body
    assert f'href="{back}"'.encode() in body
    assert b"Download export" not in body and b"Regenerate" not in body
    for action, answer in _commands(browser, page).items():
        assert answer.status_code == 410 and GONE in answer.content, action
    assert ExportRequest.objects.count() == before


def test_an_ended_report_export_is_gone_everywhere(
    http_scenario,  # noqa: F811
    google,
    settings,
    tmp_path,
):
    """Expired and cancelled exports answer 410 on every route; another
    reader's ended export stays a plain 403 on every route."""
    from parishkit.stewardship.jobs.dispatch import execute_hint
    from parishkit.stewardship.jobs.queues import WorkQueue
    from parishkit.stewardship.reports.export_tasks import export_handler
    from parishkit.stewardship.reports.workspace import ReportQuery

    from ..policy_factory import address
    from .auth_builders import signed_in
    from .campaign_builders import change
    from .test_export_cleanup_postgresql import expire_publication

    setup, admin = http_scenario
    store, _, facts, _ = setup
    root = tmp_path / "gone-reports"
    root.mkdir(mode=0o700)
    settings.STEWARDSHIP_REPORTS_ROOT = root
    # The page's link back: Participation, with the export's own scope.
    back = ReportQuery(scope=facts.population_scope, timezone="UTC").url()
    back = back.replace("&", "&amp;")

    # An expired file: its window is over.
    expired_page, expired = _create(admin, facts)
    with task_login(ServiceRole.WORKER, exact=True, reconnect=True):
        assert execute_hint(
            expired.task_id,
            queue=WorkQueue.GENERAL,
            worker_id=uuid4(),
            handlers={"report_export": export_handler(store=store, root=root)},
        )
    expire_publication(expired)
    _assert_gone(admin, expired_page, back)

    # A cancelled one, before any file was made.
    cancelled_page, _ = _create(admin, facts)
    assert _commands(admin, cancelled_page)["cancel"].status_code == 302
    _assert_gone(admin, cancelled_page, back)

    # Another reader (Staff, who sees only their own exports) gets the
    # denial, never the 410 page or its report.
    rule = address("staff@example.org", roles=("staff",))
    change(
        store,
        store.active(),
        uuid4(),
        [{"operation": "add", "section": "login_rules", **rule}],
    )
    google[0].update(email="staff@example.org", sub="synthetic-staff-subject")
    staff, _ = signed_in()
    for page in (expired_page, cancelled_page):
        with task_login(ServiceRole.WEB, exact=True, reconnect=True):
            response, body = read(staff, page)
        assert response.status_code == 403 and GONE not in body
        for action, answer in _commands(staff, page).items():
            assert answer.status_code == 403, action
            assert GONE not in answer.content, action

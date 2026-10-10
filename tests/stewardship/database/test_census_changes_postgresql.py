"""Census changes worklist under the real web role (#528)."""

import io
from uuid import uuid4

import pytest
from django.db import connection, transaction
from openpyxl import load_workbook

from parishkit.stewardship.accounts.policy import Principal
from parishkit.stewardship.audit.models import AuditContext, AuditEvent
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.reports.census_changes import CensusQuery, census_changes
from parishkit.stewardship.responses.models import ProposedChange

from ..census_factory import address
from ..policy_factory import address as rule_address
from .auth_builders import signed_in
from .campaign_builders import change
from .response_builders import activate_response_service
from .test_background_grants_postgresql import task_login
from .test_export_views_postgresql import restricted_download_pool
from .test_information_followup_postgresql import search
from .test_ministry_responses_postgresql import respond, start
from .test_report_workspace_postgresql import read as get
from .test_response_http_postgresql import answers_for, load_form
from .test_runtime_auth_grants_postgresql import web_login

pytestmark = pytest.mark.django_db(transaction=True)

STAFF = Principal(uuid4(), frozenset({"staff"}), frozenset())
ADMIN = Principal(uuid4(), frozenset({"administrator"}), frozenset())
LEADER = Principal(uuid4(), frozenset({"ministry_leader"}), frozenset({4}))


def submit_live(harness):
    """One response that renames Member 3, an automatic change, and gives the
    Family a new home address, a By-hand change (no verified address read)."""
    with web_login():
        form = load_form(harness)
        answers = answers_for(form)
        answers["members"]["3"]["first_name"] = "Requested"
        answers["family"]["home_address"] = address(line1="9 Census Way")
        return respond(harness, form, answers)


def worklist(harness, principal=STAFF, **values):
    """Read through the restricted web grants, not the migration owner."""
    with task_login(ServiceRole.WEB, exact=True, reconnect=True), transaction.atomic():
        return census_changes(harness.campaign.pk, CensusQuery.parse(values), principal)


def directory_name(duid):
    """The Family's name as the directory shows it, from the current snapshot."""
    from parishkit.stewardship.source.snapshot_models import SourceCurrent
    from parishkit.stewardship.source.snapshot_names import snapshot_family_names

    snapshot = SourceCurrent.objects.values_list("snapshot_id", flat=True).get()
    return snapshot_family_names(snapshot, [duid])[duid]


def test_worklist_reads_proposals_with_their_status(response_service):
    """A live response's change is listed, worded, and To review only for an
    Administrator; Staff find it with the status filter; leaders never."""
    harness = response_service
    start(harness)
    harness = activate_response_service(harness)
    submit_live(harness)
    # The Family address is By hand and To do, with no ParishSoft value.
    (home,) = worklist(harness)["rows"]
    assert (home["who"], home["label"]) == ("Family", "Home address")
    assert home["route_label"] == "By hand" and home["status"] == "to_do"
    assert home["parishsoft_now"] == "" and "9 Census Way" in home["family_answer"]
    rows = [row for row in worklist(harness, ADMIN)["rows"] if row["who"] != "Family"]
    assert len(rows) == 1
    row = rows[0]
    assert row["label"] == "First name" and row["family_answer"] == "Requested"
    assert row["status"] == "to_review" and row["route_label"] == "Automatic"
    assert row["who"] and row["family_duid"]
    # Named as in every Admin table: surname, then heads (#932).
    assert ", " in row["family_name"]
    assert row["family_name"] == directory_name(row["family_duid"])
    assert len(worklist(harness, status="to_review")["rows"]) == 1
    by_hand = worklist(harness, status="all", route="by_hand")["rows"]
    assert [item["label"] for item in by_hand] == ["Home address"]
    assert worklist(harness, status="all", search="no such name")["rows"] == []
    with pytest.raises(PermissionError):
        worklist(harness, LEADER)


def test_native_page_and_downloads(response_service, google, settings):
    """The page filters by CSRF POST; CSV and XLSX carry the same rows, and
    the view and download are audited."""
    settings.STEWARDSHIP_DOWNLOAD_POOL = None  # restricted_download_pool sets it
    harness = response_service
    start(harness)
    harness = activate_response_service(harness)
    submit_live(harness)
    route = "/admin/reports/census/"
    browser, login = signed_in()
    assert login.status_code == 302
    with restricted_download_pool(settings):
        response, body = get(browser, route)
        assert response.status_code == 200 and response["Cache-Control"] == "no-store"
        assert b"Requested" in body and b"To review" in body
        assert get(browser, route + "?search=x")[0].status_code == 400
        response, body = search(browser, route, {"status": "conflict"})
        assert response.status_code == 200 and b"No matching census changes." in body
        for invalid in ({"status": "bad"}, {"sort": "id"}, {"size": "7"}):
            assert search(browser, route, invalid)[0].status_code == 400
        response, body = search(browser, route, {"sort": "-submitted"})
        assert response.status_code == 200 and b'aria-sort="descending"' in body
        export = route + "exports/"
        response, body = search(
            browser, export, {"format": "csv", "timezone": "UTC", "status": "all"}
        )
        assert response.status_code == 200 and response["Content-Type"] == "text/csv"
        assert "stewardship-census-changes-" in response["Content-Disposition"]
        assert b"Requested" in body and b"How it reaches ParishSoft" in body
        response, body = search(
            browser, export, {"format": "xlsx", "timezone": "UTC", "status": "all"}
        )
        assert response.status_code == 200
        book = load_workbook(io.BytesIO(body))
        assert book.sheetnames == ["Census changes"]
        answers = [cell.value for cell in book["Census changes"]["F"][1:]]
        assert "Requested" in answers
        assert search(browser, export, {"format": "pdf"})[0].status_code == 400
    assert AuditEvent.objects.filter(event_type="census_changes_viewed").exists()
    assert AuditEvent.objects.filter(event_type="census_changes_exported").exists()
    contexts = [
        AuditContext.objects.get(event=event).context
        for event in AuditEvent.objects.filter(
            event_type__in=("census_changes_viewed", "census_changes_exported")
        )
    ]
    # Access evidence is a count only: never a name, value or filter.
    assert contexts and all(
        set(context) == {"outcome", "count"} for context in contexts
    )


def test_testing_responses_are_excluded(response_service):
    """A Testing-mode response's proposals never reach the worklist."""
    harness = response_service
    start(harness)
    submission = submit_live(harness)
    assert submission.mode != "live"
    assert ProposedChange.objects.filter(submission=submission).exists()
    assert worklist(harness, ADMIN, status="all", history="yes")["rows"] == []
    # Positive control: the same rows appear once the response is live, so
    # the exclusion above is the mode filter, not a query that reads nothing.
    # Submissions are immutable, so the flip bypasses their triggers, as the
    # migration owner only.
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute("SET LOCAL session_replication_role = replica")
        cursor.execute(
            "UPDATE stewardship_submission SET mode='live', rehearsal_epoch_id=NULL"
            " WHERE id=%s",
            [submission.pk],
        )
        cursor.execute("SET LOCAL session_replication_role = origin")
    rows = worklist(harness, ADMIN, status="all", history="yes")["rows"]
    assert {row["label"] for row in rows} == {"First name", "Home address"}


@pytest.mark.parametrize(
    ("roles", "allowed"), [(("staff",), True), (("ministry_leader",), False)]
)
def test_staff_may_open_the_page_and_ministry_leaders_may_not(
    response_service, google, roles, allowed
):
    """Over HTTP, Staff read the worklist; a Ministry leader is refused."""
    harness = response_service
    start(harness)
    harness = activate_response_service(harness)
    submit_live(harness)
    store = harness.service.store
    rule = rule_address("reader@example.org", roles=roles)
    change(
        store,
        store.active(),
        uuid4(),
        [{"operation": "add", "section": "login_rules", **rule}],
    )
    google[0]["email"] = "reader@example.org"
    browser, login = signed_in()
    assert login.status_code == 302
    route = "/admin/reports/census/"
    response, body = get(browser, route)
    if allowed:
        assert response.status_code == 200 and b"9 Census Way" in body
    else:
        assert response.status_code in {403, 404} and b"9 Census Way" not in body
        # A real CSRF POST to the live route: refused by access, not CSRF.
        response, body = search(browser, route + "exports/", {"format": "csv"})
        assert response.status_code == 403 and b"9 Census Way" not in body

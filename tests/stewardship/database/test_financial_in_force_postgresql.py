"""Financial stewardship shows the pledge values in force (#2196, frozen file 0182).

The report, its exports and every reader of its selection use a reviewer's
Edit of the annual pledge, frequency and share methods while that Edit is
in force, else the Family's own answer (reports spec, "Values in force on
Financial stewardship"). These tests drive real decisions through the
Pledges service, read the report, a capture and the Pledges and hub views
on the restricted web login, and run the frozen file's own closing check.
"""

import re
from pathlib import Path

import pytest
from django.db import DatabaseError, connection, transaction
from django.urls import reverse

from parishkit.stewardship.campaigns.models import Campaign
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.reports.export_models import (
    ExportRequest,
    FinancialExportSnapshot,
)
from parishkit.stewardship.reports.financial import parse_money
from parishkit.stewardship.reports.financial_exports import export_document
from parishkit.stewardship.reports.hub_streams import read_hub_financial

from ..sql_operators import bare_operators
from ..test_financial_answers import CHECK, OTHER
from .auth_builders import signed_in
from .test_background_grants_postgresql import task_login
from .test_financial_exports_postgresql import capture
from .test_financial_report_postgresql import pledge, report
from .test_ministry_responses_postgresql import revisit
from .test_pledge_review_postgresql import admin, again, decide, pledged
from .test_pledge_share_methods_postgresql import edit, rows
from .test_report_workspace_postgresql import read as get
from .test_runtime_auth_grants_postgresql import web_login

pytestmark = pytest.mark.django_db(transaction=True)

MIGRATIONS = (
    Path(__file__).resolve().parents[3] / "src/parishkit/stewardship/schema/migrations"
)
FROZEN = MIGRATIONS / "0182_financial_in_force.sql"
# The body 0182 copied (0175's), and the function it replaces.
PREVIOUS = MIGRATIONS / "0175_campaign_families.sql"
NAME = "stewardship_financial_report_v1"
STOCK = {f"value_share_{OTHER}": "on", f"value_share_text_{OTHER}": "Stock"}


def only(harness, **values):
    """The report's one row (the harness campaign has one Family pledging)."""
    (row,) = report(harness, **values)["rows"]
    return row


def hub_amount(harness):
    """The Family hub's Financial tab's annual pledge, as it reads the report."""
    campaign = Campaign.objects.select_related("active_configuration").get(
        pk=harness.campaign.pk
    )
    duid = only(harness)["family_duid"]
    with task_login(ServiceRole.WEB, exact=True, reconnect=True), transaction.atomic():
        tab = read_hub_financial(campaign, duid, parish_name="Sample Parish")
    return tab.pledge.rows[0].answer


def test_an_edit_in_force_is_the_reports_pledge_until_it_is_undone(response_service):
    """An Edit's amount, frequency and share methods are the row, its filters
    and its summary, marked Edited with the Family's own answer beside it;
    they stay through Entered by hand and its undo, and Back to review of the
    decision returns the Family's answer. Pledges and the hub keep showing
    the Family's answer beside the Reviewer's value."""
    harness, submission = pledged(response_service)
    actor = admin()
    unedited = only(harness)
    assert (unedited["edited"], unedited["annual"]) == (False, parse_money("1234.50"))
    edit(harness, actor, submission, amount="2000", frequency="annual", **STOCK)
    page = report(harness)
    (row,) = page["rows"]
    assert row["edited"] is True
    assert row["annual"] == parse_money("2000.00")
    assert (row["frequency"], row["frequency_label"]) == ("annual", "Annual")
    assert [(share["label"], share["text"]) for share in row["shares"]] == [
        ("Other", "Stock")
    ]
    assert row["family_answer_text"] == "$1,234.50, Monthly; Bank check"
    # The summary counts the values in force.
    summary = page["summary"]
    assert summary["annual_total"] == parse_money("2000.00")
    assert summary["frequency_counts"] == {"annual": 1}
    assert summary["share_counts"] == {OTHER: 1}
    # So do the filters.
    for values, total in (
        ({"frequency": "annual"}, 1),
        ({"frequency": "monthly"}, 0),
        ({"share": OTHER}, 1),
        ({"share": CHECK}, 0),
        ({"pledge_min": "1500"}, 1),
        ({"pledge_max": "1500"}, 0),
    ):
        assert report(harness, **values)["total"] == total, values
    # Pledges and the hub show the Family's own answer in its place.
    _found, (drawn,) = rows(harness, actor)
    assert str(drawn["family_answer"]).startswith("$1,234.50, Monthly")
    assert "Bank check" in str(drawn["family_answer"])
    assert str(drawn["reviewer_value"]).startswith("$2,000.00, Annual")
    assert hub_amount(harness) == "$1,234.50"
    # The mark and its undo carry the Edit.
    decide(harness, actor, "entered_by_hand", [submission])
    assert only(harness)["edited"] is True
    decide(harness, actor, "back_to_review", [submission], note="Typed wrong")
    assert only(harness)["annual"] == parse_money("2000.00")
    # Back to review of the decision itself sets it aside.
    decide(harness, actor, "back_to_review", [submission])
    row = only(harness)
    assert (row["edited"], row["annual"], row["frequency"]) == (
        False,
        parse_money("1234.50"),
        "monthly",
    )
    assert [share["label"] for share in row["shares"]] == ["Bank check"]
    # An Accept of the same answer afterwards changes nothing here.
    decide(harness, actor, "accept", [submission])
    assert only(harness)["edited"] is False


def test_a_newer_answer_or_another_decision_leaves_the_familys_own(response_service):
    """A newer, different pledge answer from the Family sets the Edit aside;
    Accept and Ignore never change what the report shows."""
    harness, submission = pledged(response_service)
    actor = admin()
    edit(harness, actor, submission, amount="2000", frequency="annual", **STOCK)
    newer = again(harness, annual_pledge="900")
    row = only(harness)
    assert (row["edited"], row["annual"]) == (False, parse_money("900.00"))
    decide(harness, actor, "accept", [newer])
    assert (only(harness)["edited"], only(harness)["annual"]) == (
        False,
        parse_money("900.00"),
    )
    decide(harness, actor, "back_to_review", [newer])
    decide(harness, actor, "ignore", [newer])
    assert only(harness)["edited"] is False


def test_an_edited_zero_pledge_and_an_edited_cannot_contribute(response_service):
    """An edited zero pledge has no frequency and no share methods; an Edit
    above zero of a Family that cannot contribute is a pledge, not Cannot
    contribute, and the tip says what the Family answered."""
    harness, submission = pledged(response_service)
    actor = admin()
    edit(harness, actor, submission, amount="0", frequency="")
    row = only(harness)
    assert (row["edited"], row["annual"], row["frequency"], row["shares"]) == (
        True,
        parse_money("0.00"),
        None,
        [],
    )
    assert report(harness, amount="zero")["total"] == 1
    assert report(harness, frequency="none")["total"] == 1
    # A Family that cannot contribute, edited to a monthly pledge.
    with web_login():
        cannot = pledge(
            harness,
            revisit(harness),
            annual_pledge="0",
            frequency="",
            cannot_give=True,
            shares={},
        )
    assert only(harness)["cannot_give"] is True
    edit(
        harness,
        actor,
        cannot,
        amount="600",
        frequency="monthly",
        **{f"value_share_{CHECK}": "on"},
    )
    row = only(harness)
    assert (row["edited"], row["cannot_give"], row["frequency_label"]) == (
        True,
        False,
        "Monthly",
    )
    assert row["family_answer_text"] == "Cannot contribute"
    assert report(harness, amount="cannot_give")["total"] == 0
    assert report(harness, amount="nonzero")["total"] == 1


def test_a_capture_holds_the_values_in_force_and_its_files_mark_them(
    response_service,
):
    """An export captured with an Edit in force holds the reviewer's values
    and the Family's own beside them; its files say Edited on Pledges."""
    harness, submission = pledged(response_service)
    actor = admin()
    edit(harness, actor, submission, amount="2000", frequency="annual", **STOCK)
    # The capture's own row, as its insert trigger wrote it.
    request = ExportRequest.objects.get(pk=capture(harness, actor).pk)
    (row,) = FinancialExportSnapshot.objects.get(
        pk=request.financial_snapshot_id
    ).document["rows"]
    assert (row["annual_pledge"], row["frequency"], row["shares"]) == (
        "2000.00",
        "annual",
        {OTHER: "Stock"},
    )
    assert row["edited"] is True
    assert row["family_answer"] == {
        "annual_pledge": "1234.50",
        "frequency": "monthly",
        "cannot_give": False,
        "shares": {CHECK: ""},
    }
    document = export_document(request)
    cells = dict(zip(document.headings, document.rows[0], strict=True))
    assert cells["Annual pledge"] == parse_money("2000.00")
    assert cells["Frequency"] == "Annual"
    assert cells["Share methods"] == "Other: Stock"
    assert cells["Edited on Pledges"] == "Yes"


def test_the_page_marks_an_edited_pledge_with_the_familys_answer(
    response_service, google
):
    """The page's Annual pledge cell has the Edited chip and its tip once an
    Edit is in force, and none before."""
    harness, submission = pledged(response_service)
    route = reverse("admin:financial_report")
    browser, login = signed_in()
    assert login.status_code == 302

    def cell():
        """The rendered Annual pledge cell of the one row."""
        with task_login(ServiceRole.WEB, exact=True, reconnect=True):
            response, body = get(browser, route)
        assert response.status_code == 200
        rows = body[body.index(b"<tbody>") :]
        # Family is the row's header; Family DUID, Annual pledge, then
        # Installment, the text cell that carries the chip.
        return rows.split(b"<td")[3].split(b"</td>")[0]

    assert b"status-badge" not in cell()
    edit(harness, admin(), submission, amount="2000", frequency="annual", **STOCK)
    marked = cell()
    assert marked.startswith(b' class="nowrap">$2,000<span aria-hidden="true">/yr')
    assert (
        b'<span class="status-badge edited-chip"><span aria-hidden="true">Edited</span>'
        in marked
    )
    assert b'<span class="visually-hidden">Edited on Pledges</span>' in marked
    assert b'aria-controls="financial-edited-1"' in marked
    assert (b"The Family's answer: $1,234.50, Monthly; Bank check.") in marked


# --- The frozen file -------------------------------------------------------


class Rollback(Exception):
    """Ends a check that must leave the database as it found it."""


def statement(text):
    """The financial selection's ``CREATE OR REPLACE FUNCTION`` in ``text``."""
    start = text.index(f"CREATE OR REPLACE FUNCTION public.{NAME}(")
    return text[start : text.index("END $$;", start) + len("END $$;")]


def check():
    """0182's closing check: the settings it records first, and its DO block."""
    text = FROZEN.read_text(encoding="utf-8")
    start = text.index("SELECT set_config(")
    recorded = text[start : text.index("\nCREATE OR REPLACE FUNCTION", start)]
    return recorded + text[text.index("DO $check$") :]


def own_body(cursor):
    """Put 0182's own body back, rolled back with the test's transaction.

    0197 (#2335) has replaced the selection since, so its body is what a
    fresh install holds; 0182's check is of what 0182 installed.
    """
    cursor.execute(statement(FROZEN.read_text(encoding="utf-8")))


def test_the_check_passes_as_installed():
    """0182's body, as 0182 installed it, passes, and the selection runs."""
    with pytest.raises(Rollback), transaction.atomic(), connection.cursor() as cursor:
        own_body(cursor)
        cursor.execute(check())
        raise Rollback


def test_the_check_refuses_the_previous_body():
    """The selection put back to 0175's body fails the check."""
    with (
        pytest.raises(DatabaseError, match=r"Migration 0182 .* did not replace"),
        transaction.atomic(),
        connection.cursor() as cursor,
    ):
        cursor.execute(statement(PREVIOUS.read_text(encoding="utf-8")))
        cursor.execute(check())


def test_the_check_refuses_changed_grants():
    """A grant changed between the recorded state and the check fails it."""
    text = check()
    recorded, block = text[: text.index("DO $check$")], text[text.index("DO $check$") :]
    with (
        pytest.raises(DatabaseError, match="changed the selection's grants"),
        transaction.atomic(),
        connection.cursor() as cursor,
    ):
        own_body(cursor)
        cursor.execute(recorded)
        cursor.execute(
            f"REVOKE EXECUTE ON FUNCTION public.{NAME}(uuid,jsonb,integer,integer)"
            " FROM PUBLIC"
        )
        cursor.execute(block)


def test_the_check_runs_the_selection_so_a_missing_column_fails():
    """The check runs the whole selection, so a column it reads that is
    missing (0181's reviewed_shares) fails the file, not a later page."""
    with (
        pytest.raises(DatabaseError, match=r"column v\.reviewed_shares does not exist"),
        transaction.atomic(),
        connection.cursor() as cursor,
    ):
        own_body(cursor)
        cursor.execute(
            "ALTER TABLE stewardship_pledge_review DROP COLUMN reviewed_shares"
        )
        cursor.execute(check())


def normalized(body):
    """``body`` without its ``--`` comments, its white space collapsed, and
    each qualified operator (#2166) turned back to the bare one it replaced."""
    body = re.sub(r"OPERATOR\(pg_catalog\.([^)]+)\)", r"\1", body)
    return " ".join(re.sub(r"--[^\n]*", "", body).split())


def test_the_new_body_is_the_previous_one_plus_the_values_in_force():
    """Only the values-in-force clauses (and comments) changed, every operator
    qualified (#2166), so a copy of a stale predecessor fails here."""
    new_text = statement(FROZEN.read_text(encoding="utf-8"))
    assert not bare_operators(new_text)
    old = normalized(statement(PREVIOUS.read_text(encoding="utf-8")))
    new = normalized(new_text)
    # The responses step, from where 0175's began to its financial answer,
    # becomes the edits step and the values in force.
    begun = (
        "), responses AS MATERIALIZED ( SELECT s.id,s.submitted_at,s.family_version,"
        "s.annual_pledge,s.configuration_id, i.family_duid,first.submitted_at AS "
        "first_submitted_at, s.answers->'financial' AS financial,"
    )
    replaced = new[
        new.index("), edits AS MATERIALIZED (") : new.index("END AS family_answer,")
        + len("END AS family_answer,")
    ]
    assert "stewardship_pledge_review_states_v1(x.id,NULL)" in replaced
    joined = "LEFT JOIN stewardship_source_family p ON p.id=m.payload_id"
    shares = "r.financial->'shares' AS shares,"
    expected = (
        old.replace(begun, replaced)
        .replace(joined, joined + " LEFT JOIN edits e ON e.family_id=i.id")
        .replace(
            shares, shares + " r.family_answer IS NOT NULL AS edited,r.family_answer,"
        )
    )
    assert expected == new


def test_the_file_installs_its_body_over_the_previous_one():
    """On a database as it was before 0182, the file installs the body and
    its check passes; rolled back."""
    with pytest.raises(Rollback), transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(statement(PREVIOUS.read_text(encoding="utf-8")))
        cursor.execute(FROZEN.read_text(encoding="utf-8"))
        cursor.execute(
            "SELECT count(*) FROM pg_proc WHERE NOT prosecdef AND proname = %s"
            " AND prosrc LIKE '%%stewardship_pledge_review_states_v1(x.id,NULL)%%'",
            [NAME],
        )
        assert cursor.fetchone() == (1,)
        raise Rollback

"""Data to fix in ParishSoft (#1011): sections, roles, states and wording.

Pure tests: which sections each role sees and that this matches each detail
page's own admission, what a section says when it cannot count, the page's
template for zero, counted and unavailable sections, and the command's
document and catalog entry. Real reads are in
database/test_data_to_fix_postgresql.py.
"""

import io
import json
import logging
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from django.template.loader import render_to_string

from parishkit.stewardship import admin_cli, admin_data_to_fix
from parishkit.stewardship.accounts import admin_navigation, data_to_fix_views
from parishkit.stewardship.accounts.policy import Principal, allows
from parishkit.stewardship.admin_parity import LEDGER

ADMIN = Principal(uuid4(), frozenset({"administrator", "staff", "ministry_leader"}))
STAFF = Principal(uuid4(), frozenset({"staff"}))
LEADER = Principal(uuid4(), frozenset({"ministry_leader"}), frozenset({4}))
ROLES = {"administrator": ADMIN, "staff": STAFF, "ministry_leader": LEADER}
ALL = [section.key for section in data_to_fix_views.SECTIONS]
CAMPAIGN = UUID("00000000-0000-4000-8000-000000001011")


def campaign(modules=("financial", "ministry", "census"), state="active"):
    """A current campaign as the menu reason checks read it."""
    return SimpleNamespace(
        pk=CAMPAIGN,
        state=state,
        active_configuration=SimpleNamespace(
            values={"modules": list(modules)}, name="Stewardship 2027"
        ),
    )


CONFIGURATION = SimpleNamespace(mode="production")


@pytest.fixture(autouse=True)
def _downloaded(monkeypatch):
    """ParishSoft data has been downloaded, unless a test says otherwise.

    The menu reads it from the database (``admin_context.menu_state``);
    these tests run without one.
    """
    from parishkit.stewardship.accounts import admin_context

    monkeypatch.setattr(admin_context, "parishsoft_downloaded", lambda: True)


def test_the_sections_run_in_the_decided_order():
    """Form cannot open, refused addresses, no mail can reach, census, roster."""
    assert ALL == [
        "form_cannot_open",
        "refused_addresses",
        "unreachable_families",
        "census_changes",
        "roster_changes",
    ]


@pytest.mark.parametrize(
    ("role", "keys"),
    [
        ("administrator", ALL),
        ("staff", ["unreachable_families", "census_changes", "roster_changes"]),
        ("ministry_leader", []),
    ],
)
def test_each_role_sees_the_sections_it_may_open(role, keys):
    """Staff see sections 3 to 5; a Ministry leader sees none."""
    principal = ROLES[role]
    shown = [section.key for section in data_to_fix_views.visible_sections(principal)]
    assert shown == keys
    # The menu entry is offered exactly when a section is.
    entry = next(e for e in admin_navigation.MENU if e.name == "parishsoft_fixes")
    assert allows(principal, entry.capability) == bool(keys)


def _detail_admissions():
    """Each section's detail page admission, as ``(key, module, call)``."""
    from parishkit.stewardship.accounts import source_form_views
    from parishkit.stewardship.jobs import delivery_views, task_retries
    from parishkit.stewardship.reports import census_change_views, directory_views

    return [
        ("form_cannot_open", source_form_views, source_form_views._principal),
        ("refused_addresses", task_retries, delivery_views._principal),
        ("unreachable_families", directory_views, directory_views._principal),
        ("census_changes", census_change_views, census_change_views._principal),
    ]


@pytest.mark.parametrize("role", sorted(ROLES))
def test_a_section_shows_exactly_when_its_page_admits(role, monkeypatch):
    """The capability each section checks is its detail page's own decision."""
    principal = ROLES[role]
    shown = {s.key for s in data_to_fix_views.visible_sections(principal)}
    for key, module, admission in _detail_admissions():
        monkeypatch.setattr(
            module, "authenticated_admin", lambda *args, **kwargs: principal
        )
        try:
            # A plain page load: no automatic re-read (#2174).
            admission(SimpleNamespace(method="GET", headers={}), None)
            admitted = True
        except PermissionError:
            admitted = False
        assert admitted == (key in shown), (role, key)


def test_the_page_admits_staff_and_refuses_a_ministry_leader(monkeypatch):
    """The page's admission is "may open at least one section"."""
    for principal, admitted in ((ADMIN, True), (STAFF, True), (LEADER, False)):
        monkeypatch.setattr(
            data_to_fix_views,
            "authenticated_admin",
            lambda *args, principal=principal, **kwargs: principal,
        )
        if admitted:
            assert data_to_fix_views._principal(object(), None) is principal
        else:
            with pytest.raises(PermissionError):
                data_to_fix_views._principal(object(), None)


def _counted(monkeypatch, **counts):
    """Replace each section's read with a fixed answer (or an exception)."""
    reads = {}
    for key in ALL:
        answer = counts.get(key, {"count": 0})

        def read(principal, campaign_id, answer=answer):
            """The fixed answer, or the exception it holds."""
            if isinstance(answer, Exception):
                raise answer
            return dict(answer)

        reads[key] = read
    monkeypatch.setattr(data_to_fix_views, "READS", reads)


def _rows(principal=ADMIN, current=None, configuration=CONFIGURATION):
    """``read_sections`` for ``principal`` with ``current`` as the campaign."""
    return {
        row["key"]: row
        for row in data_to_fix_views.read_sections(principal, configuration, current)
    }


def test_counted_sections_link_their_pages(monkeypatch):
    """Each count links its detail page; the directory with its preset."""
    _counted(
        monkeypatch,
        refused_addresses={"count": 2, "families": 1},
        census_changes={"count": 7},
    )
    rows = _rows(current=campaign())
    assert rows["form_cannot_open"]["url"] == "/admin/system/source-form/"
    assert rows["refused_addresses"] == {
        "key": "refused_addresses",
        "label": "Refused addresses",
        "url": "/admin/mail/refusals/",
        "count": 2,
        "families": 1,
    }
    assert rows["unreachable_families"]["url"] == (
        "/admin/reports/families/?reach=neither"
    )
    assert rows["census_changes"]["count"] == 7
    assert data_to_fix_views.total(rows.values()) == 9


def test_without_a_campaign_sections_say_why(monkeypatch):
    """The campaign sections say there is none; the directory and census
    links are greyed out as their menu entries are; refusals still count."""
    _counted(monkeypatch, refused_addresses={"count": 1, "families": 1})
    rows = _rows(current=None)
    assert rows["refused_addresses"]["count"] == 1
    assert rows["form_cannot_open"]["reason"] == "No current campaign."
    # The source-form list opens without a campaign and says so itself.
    assert rows["form_cannot_open"]["url"]
    for key in ("unreachable_families", "census_changes"):
        assert rows[key]["reason"] == "No current campaign."
        assert rows[key]["url"] is None and "count" not in rows[key]


def test_a_campaign_without_the_census_greys_that_section(monkeypatch):
    """The menu's own reason, and the census is never read."""
    _counted(monkeypatch, census_changes=AssertionError("must not be read"))
    rows = _rows(current=campaign(modules=("financial",)))
    assert rows["census_changes"]["reason"] == (
        "This campaign does not include the census."
    )
    assert rows["census_changes"]["url"] is None


def test_a_failed_read_says_so_and_is_logged(monkeypatch, caplog):
    """The failing section says it could not count; the others still count;
    a WARNING names the section through its shaping step."""
    _counted(
        monkeypatch,
        census_changes=RuntimeError("private detail"),
        form_cannot_open={"count": 3},
    )
    with caplog.at_level(logging.WARNING, logger="parishkit.stewardship"):
        rows = _rows(current=campaign())
    assert rows["census_changes"]["reason"] == data_to_fix_views.FAILED
    assert rows["census_changes"]["url"] == "/admin/reports/census/"
    assert rows["form_cannot_open"]["count"] == 3
    (record,) = [r for r in caplog.records if r.msg == "report_shaping_failed"]
    assert record.levelno == logging.WARNING
    assert "private detail" not in caplog.text


def test_the_roster_section_links_ministry_requests_at_its_stage(monkeypatch):
    """Ministry requests filtered to To enter by hand, counted; greyed out
    with the menu's reason for a campaign without Ministries (never read)."""
    _counted(monkeypatch, roster_changes={"count": 4})
    row = _rows(principal=STAFF, current=campaign())["roster_changes"]
    assert row == {
        "key": "roster_changes",
        "label": "Ministry requests",
        "url": "/admin/reports/ministries/?stage=to_enter_by_hand",
        "count": 4,
    }
    _counted(monkeypatch, roster_changes=AssertionError("must not be read"))
    row = _rows(current=campaign(modules=("census",)))["roster_changes"]
    assert row["url"] is None and "count" not in row


class _Campaigns:
    """``Campaign.objects`` returning one campaign in ``state``."""

    def __init__(self, state):
        """Remember the state the stand-in campaign has."""
        self.state = state

    def only(self, *fields):
        """Any column choice reads the same stand-in."""
        return self

    def get(self, pk):
        """The stand-in campaign."""
        return SimpleNamespace(pk=pk, state=self.state)


@pytest.mark.parametrize(
    ("state", "count", "expected"),
    [
        ("active", 3, {"count": 3}),
        ("closed", 0, {"count": 0}),
        ("active", None, {"reason": data_to_fix_views.ROSTER_UNCOUNTED}),
        # Before go-live nothing is resolved: zero, and the read is skipped.
        ("draft", AssertionError("must not be read"), {"count": 0}),
    ],
)
def test_the_roster_count_is_the_menus_own_read(monkeypatch, state, count, expected):
    """The section answers ``admin_context.roster_entry_count``'s number."""
    from parishkit.stewardship.accounts import admin_context
    from parishkit.stewardship.campaigns.models import Campaign

    def entry_count(principal, current):
        """The menu's count, or the failure it stands for."""
        assert principal is STAFF and current.pk == CAMPAIGN
        if isinstance(count, Exception):
            raise count
        return count

    monkeypatch.setattr(
        data_to_fix_views, "_guarded", lambda campaign_id, read, **k: read()
    )
    monkeypatch.setattr(Campaign, "objects", _Campaigns(state))
    monkeypatch.setattr(admin_context, "roster_entry_count", entry_count)
    assert data_to_fix_views._roster_changes(STAFF, CAMPAIGN) == expected


def test_no_promoted_source_is_said_for_the_unreachable_count(monkeypatch):
    """Home's read answers None before any ParishSoft data: say so."""
    from parishkit.stewardship.accounts import admin_dashboard

    monkeypatch.setattr(
        data_to_fix_views, "_guarded", lambda campaign_id, read, **k: read()
    )
    monkeypatch.setattr(admin_dashboard, "unreachable_families", lambda pk: None)
    assert data_to_fix_views._unreachable_families(ADMIN, CAMPAIGN) == {
        "reason": data_to_fix_views.NO_SOURCE
    }


def render(rows, **extra):
    """The page's template with ``rows``, outside the Admin chrome."""
    return render_to_string(
        "stewardship/data-to-fix.html",
        {
            "campaign": campaign(),
            "refreshed_at": None,
            "sections": rows,
            "keys": [row["key"] for row in rows],
            "nothing_to_fix": all(row.get("count") == 0 for row in rows),
            "total": data_to_fix_views.total(rows),
            "uncounted": data_to_fix_views.uncounted(rows),
        }
        | extra,
    )


def _row(key, **values):
    """One section row as ``read_sections`` returns it."""
    section = next(s for s in data_to_fix_views.SECTIONS if s.key == key)
    return {"key": key, "label": section.label, "url": "/x/"} | values


def test_zero_sections_keep_their_blocks_and_say_what_zero_means():
    """Every block stays at zero, with its link; the data line says so."""
    html = render([_row(key, count=0) for key in ALL])
    assert html.count('class="panel data-to-fix-section"') == 5
    assert "None: every Family that can use the portal can open the form." in html
    assert "No refused addresses: campaign email reaches every address" in html
    assert "None: every campaign Family can be reached by email or postal mail." in html
    assert "None: every census change Families reported has been handled." in html
    assert "None: every accepted Ministry request has been entered in ParishSoft." in (
        html
    )
    assert "Nothing to fix right now." in html
    assert html.count('<a href="/x/">Open ') == 5
    assert "Stewardship 2027." in html and "ParishSoft data: not yet loaded." in html


def test_counts_and_unavailable_sections_are_worded():
    """Plural counts in plain words; a reason replaces the count and greys
    out the link with that reason as its tip; no "Nothing to fix" line."""
    html = render(
        [
            _row("form_cannot_open", count=1),
            _row("refused_addresses", count=3, families=2),
            _row("unreachable_families", url=None, reason="No current campaign"),
            _row("census_changes", count=12),
            _row("roster_changes", count=1),
        ]
    )
    assert "1 Family cannot open the Family form" in html
    assert "3 refused email addresses are not cleared yet, from 2 Families." in html
    assert "12 census changes that Families reported are still to be handled." in html
    assert (
        "1 accepted Ministry request is still to be entered in its ParishSoft "
        "roster by hand." in html
    )
    assert "Open Ministry requests" in html
    assert 'aria-disabled="true"' in html and "Open Campaign Family directory" in html
    assert 'id="data-to-fix-open-unreachable_families-tip"' in html
    assert "Nothing to fix right now." not in html


def test_staff_never_read_about_refused_addresses():
    """The About panel's refusal sentence is only for those who see them."""
    html = render([_row(key, count=0) for key in ALL[2:]])
    assert "refused" not in html.lower()


def test_the_document_is_counts_and_reasons_only():
    """One member per visible section; a reason as plain text."""
    rows = [
        _row("refused_addresses", count=2, families=1),
        _row("census_changes", reason=data_to_fix_views.FAILED),
    ]
    document = admin_data_to_fix.data_to_fix_model(campaign(), rows).to_document()
    assert document == {
        "campaign_id": str(CAMPAIGN),
        "sections": {
            "refused_addresses": {"count": 2, "families": 1},
            "census_changes": {"unavailable": str(data_to_fix_views.FAILED)},
        },
    }
    assert json.dumps(document)
    none = admin_data_to_fix.data_to_fix_model(None, []).to_document()
    assert none == {"campaign_id": None, "sections": {}}


def test_the_catalog_entry():
    """Any session, no state change, the page's event, no option."""
    entries = {entry["name"]: entry for entry in admin_cli.catalog()}
    entry = entries["report data-to-fix"]
    assert entry["scope"] == "read_only" and not entry["changes_state"]
    assert entry["audit_event"] == "data_to_fix_viewed"
    assert not entry["prompts"] and not entry["fresh_gated"]
    names = {option["name"] for option in entry["options"]}
    assert names == {"--config", "--session-stdin"} and entry["arguments"] == []
    assert entry["result_fields"] == ["campaign_id", "sections"]
    assert LEDGER["parishsoft_fixes"].detail == ("report data-to-fix",)


@pytest.mark.parametrize(
    "argv", [["report", "data-to-fix", "extra"], ["report", "data-to-fix", "--x"]]
)
def test_malformed_commands_are_usage_errors(argv):
    """The command takes no argument and no option."""
    out = io.StringIO()
    code = admin_cli.main(
        [*argv, "--config", "web.yaml", "--session-stdin"],
        stdin=io.BytesIO(b""),
        stdout=out,
        stderr=out,
    )
    assert code == 2 and json.loads(out.getvalue())["error"]["code"] == "usage"


def test_no_parishsoft_data_yet_is_not_a_failure(monkeypatch, caplog):
    """Before any promoted snapshot the scan refuses; the section says there
    is nothing to count yet, and no warning is logged."""
    from parishkit.stewardship.load_check import LoadCheckRefused
    from parishkit.stewardship.reports import source_form

    def refused(campaign_id):
        """No promoted source snapshot."""
        raise LoadCheckRefused("The load check requires a promoted source snapshot.")

    monkeypatch.setattr(
        data_to_fix_views, "_guarded", lambda campaign_id, read, **k: read()
    )
    monkeypatch.setattr(source_form, "blocked_families", refused)
    # Only this section's real read runs; the others answer at once.
    _counted(monkeypatch)
    data_to_fix_views.READS["form_cannot_open"] = data_to_fix_views._form_cannot_open
    with caplog.at_level(logging.WARNING, logger="parishkit.stewardship"):
        rows = _rows(current=campaign())
    assert rows["form_cannot_open"]["reason"] == str(data_to_fix_views.NO_SOURCE)
    assert rows["form_cannot_open"]["url"] == "/admin/system/source-form/"
    assert not [r for r in caplog.records if r.msg == "report_shaping_failed"]


def _view(monkeypatch, principal, *, query="", current=None):
    """Call the page's view as ``principal``, with ``current`` standing in
    for the configuration read (a value, or an exception to raise)."""
    from django.test import RequestFactory

    def read(store):
        """The configuration read's stand-in."""
        if isinstance(current, Exception):
            raise current
        return current

    monkeypatch.setattr(
        data_to_fix_views, "runtime", lambda: SimpleNamespace(store=None)
    )
    monkeypatch.setattr(
        data_to_fix_views, "authenticated_admin", lambda *args, **kwargs: principal
    )
    monkeypatch.setattr(data_to_fix_views, "current", read)
    return data_to_fix_views.data_to_fix(
        RequestFactory().get("/admin/parish/data-to-fix/" + query)
    )


def test_a_ministry_leader_gets_the_role_refusal(monkeypatch):
    """403 naming the page's roles (#1924), never Sent mail's error page."""
    response = _view(monkeypatch, LEADER)
    assert response.status_code == 403
    assert json.loads(response.content)["refusal"]["message"] == (
        "This page is for Administrators and Staff. Your access doesn't include it."
    )
    assert b"Delivery" not in response.content


def test_no_current_session_gets_the_signed_out_refusal(monkeypatch):
    """Without a current session: the shared signed-out refusal (#2058).

    A browser's page load with it goes to the sign-in page (the middleware,
    not run here); this is the typed 403 scripts get.
    """
    response = _view(monkeypatch, None)
    assert response.status_code == 403
    assert response.stewardship_refusal.signed_out is True
    assert json.loads(response.content)["refusal"]["link"] == {
        "url": "/admin/login",
        "label": "Sign in",
    }


@pytest.mark.parametrize("principal", [STAFF, ADMIN], ids=["staff", "administrator"])
@pytest.mark.parametrize(
    "current", [None, RuntimeError], ids=["restore review", "database outage"]
)
def test_an_outage_is_the_report_error_page(monkeypatch, principal, current):
    """A restore under review or a database outage is the report pages' 503,
    whose words and links every Admin role may follow."""
    from django.db import DatabaseError

    from parishkit.stewardship.web.report_errors import REPORT_UNAVAILABLE

    failure = DatabaseError("down") if current is RuntimeError else None
    response = _view(monkeypatch, principal, current=failure)
    assert response.status_code == 503
    assert response.stewardship_refusal is REPORT_UNAVAILABLE
    assert b"Delivery" not in response.content


def test_an_unknown_parameter_is_a_typed_400(monkeypatch):
    """Anything but a plain GET is refused with the closed invalid code."""
    from parishkit.stewardship.web.contracts import ErrorCode

    response = _view(monkeypatch, STAFF, query="?other=1")
    assert response.status_code == 400
    assert [error.code for error in response.stewardship_errors] == [ErrorCode.INVALID]


def test_the_counts_region_polls_the_passive_fragment():
    """The data line and the blocks are one live status region, polled every
    60 seconds from the status fragment and never told to reload (#1859)."""
    from django.urls import reverse

    html = render([_row(key, count=0) for key in ALL])
    status = reverse("admin:parishsoft_fixes_status")
    assert status == "/admin/parish/data-to-fix/status/"
    assert (
        f'<div data-live-status="data-to-fix" data-live-url="{status}" '
        'data-live-interval="60000" data-live-pending '
        'data-live-announce="Nothing to fix right now.">'
    ) in html
    region = html[html.index('data-live-status="data-to-fix"') :]
    assert 'id="data-to-fix-data"' in region
    assert region.count('class="panel data-to-fix-section"') == 5
    # The page's own content, without the Admin layout's JavaScript-off
    # notice (which must ask for a reload once JavaScript is on).
    content = html[html.index("<h1>") : html.index("</section></div>")]
    assert "reload" not in content.lower()
    assert "reload" not in str(data_to_fix_views.FAILED).lower()


def test_the_announced_sentence_is_the_total():
    """One short sentence, read out only when it changes: the total."""
    html = render([_row("refused_addresses", count=2, families=1)])
    assert 'data-live-announce="2 problems to fix in ParishSoft."' in html
    html = render([_row("census_changes", count=1)])
    assert 'data-live-announce="1 problem to fix in ParishSoft."' in html


def test_the_fragment_is_a_non_page_covered_by_the_command():
    """The fragment has no menu entry or page of its own, and its parity is
    the page's command."""
    assert "parishsoft_fixes_status" in admin_navigation.NON_PAGES
    assert LEDGER["parishsoft_fixes_status"].detail == ("report data-to-fix",)


@pytest.mark.parametrize(
    ("view", "activity"),
    [("data_to_fix", True), ("data_to_fix_status", False)],
    ids=["page", "fragment"],
)
def test_only_the_page_records_activity(monkeypatch, view, activity):
    """Opening the page renews the sign-in; a poll of the fragment never
    does. A Ministry leader is refused either way."""
    from django.test import RequestFactory

    seen = []

    def admit(*args, **kwargs):
        """Record the admission's options; admit a Ministry leader."""
        seen.append(kwargs)
        return LEADER

    monkeypatch.setattr(
        data_to_fix_views, "runtime", lambda: SimpleNamespace(store=None)
    )
    monkeypatch.setattr(data_to_fix_views, "authenticated_admin", admit)
    response = getattr(data_to_fix_views, view)(RequestFactory().get("/x/"))
    assert response.status_code == 403
    # The role refusal (#1924), not the generic sign-in denial.
    assert b"for Administrators and Staff" in response.content
    assert seen == [{"store": None, "activity": activity, "read_only": False}]


def test_the_fragment_refuses_a_query_key(monkeypatch):
    """Anything but a plain GET of the fragment is the closed invalid 400."""
    from django.test import RequestFactory

    from parishkit.stewardship.web.contracts import ErrorCode

    monkeypatch.setattr(
        data_to_fix_views, "authenticated_admin", lambda *args, **kwargs: STAFF
    )
    response = data_to_fix_views.data_to_fix_status(
        RequestFactory().get("/admin/parish/data-to-fix/status/?other=1")
    )
    assert response.status_code == 400
    assert [error.code for error in response.stewardship_errors] == [ErrorCode.INVALID]


def test_an_uncounted_section_is_never_announced_as_fewer_problems():
    """A section that could not count is said, not heard as a smaller total."""
    failed = data_to_fix_views.FAILED
    html = render([_row(key, reason=failed) for key in ALL])
    assert 'data-live-announce="Some problems could not be counted right now."' in html
    html = render(
        [
            _row("refused_addresses", count=3, families=2),
            _row("census_changes", reason=failed),
        ]
    )
    assert (
        'data-live-announce="3 problems to fix in ParishSoft; '
        'some could not be counted."' in html
    )
    # A section ruled out by the campaign's state is not a failure.
    html = render([_row(key, reason="No current campaign.") for key in ALL])
    assert 'data-live-announce="No problems counted right now."' in html


@pytest.mark.parametrize(
    "current", [None, RuntimeError], ids=["restore review", "database outage"]
)
def test_the_fragment_answers_an_outage_with_the_report_503(monkeypatch, current):
    """A restore under review or a database outage is the report pages' 503
    for a poll too, so the region keeps its counts and tries again."""
    from django.db import DatabaseError
    from django.test import RequestFactory

    from parishkit.stewardship.web.report_errors import REPORT_UNAVAILABLE

    failure = DatabaseError("down") if current is RuntimeError else None

    def read(store):
        """The configuration read's stand-in."""
        if failure:
            raise failure
        return None

    monkeypatch.setattr(
        data_to_fix_views, "runtime", lambda: SimpleNamespace(store=None)
    )
    monkeypatch.setattr(
        data_to_fix_views, "authenticated_admin", lambda *args, **kwargs: STAFF
    )
    monkeypatch.setattr(data_to_fix_views, "current", read)
    response = data_to_fix_views.data_to_fix_status(
        RequestFactory().get("/admin/parish/data-to-fix/status/")
    )
    assert response.status_code == 503
    assert response.stewardship_refusal is REPORT_UNAVAILABLE

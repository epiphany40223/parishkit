"""Update ParishSoft's read side and page, without a database (#528, #1688).

The plan table's wording (``census_publication.plan_row``) before and after
the run, its summary sentences, the order Check ParishSoft's and Update's
gates apply in, the confirmation's words and refusals, and the page,
its review dialog's answers and the Census changes link as the templates
draw them.
"""

from datetime import UTC, datetime
from html import unescape
from types import SimpleNamespace

import pytest

from parishkit.stewardship.reports import census_publication as publication
from parishkit.stewardship.reports.census_changes import shape
from parishkit.stewardship.web.tables import paginate

from .test_census_changes import row

CHECKED = datetime(2054, 10, 6, 15, 0, tzinfo=UTC)


def change(**values):
    """One approved automatic change as Census changes words it."""
    return shape(row(decision="approved", **values))


def field(outcome):
    """A plan's field row with ``outcome``."""
    return SimpleNamespace(outcome=outcome)


@pytest.mark.parametrize(
    ("outcome", "blocked", "accepted", "edited", "expected"),
    [
        # Will be written: compared with the Family's baseline.
        ("write", False, False, None, ("write", "555-0100", "555-0199")),
        # An edited value is what is written.
        ("write", False, False, "555-0142", ("write", "555-0100", "555-0142")),
        # Use the Family's value anyway compared with ParishSoft's value then.
        ("write", False, True, None, ("write", "555-0111", "555-0199")),
        # A write on a blocked Member is held back.
        ("write", True, False, None, ("held", "555-0100", "555-0199")),
        # Already in ParishSoft: both show the value to write.
        (
            "already_in_parishsoft",
            False,
            False,
            None,
            ("already_in_parishsoft", "555-0199", "555-0199"),
        ),
        # The plan kept no value for these, so the page says why.
        (
            "conflict",
            True,
            False,
            None,
            ("conflict", "Changed since the Family answered", ""),
        ),
        ("cannot_check", True, False, None, ("cannot_check", "Could not be read", "")),
    ],
)
def test_plan_row_words_each_outcome(outcome, blocked, accepted, edited, expected):
    """Each outcome shows what ParishSoft held at the check and what is
    written, as the reports spec words them."""
    values = {"current_value": "555-0111"}
    if edited is not None:
        values |= {"admin_value_set": True, "admin_value": edited}
    shown = publication.plan_row(
        field(outcome), change(**values), blocked=blocked, accepted=accepted
    )
    assert (shown["outcome"], shown["at_check"], shown["will_write"]) == expected
    assert shown["outcome_label"] == publication.OUTCOMES[expected[0]]
    assert (shown["member_duid"], shown["family_duid"]) == (41, 7001)


@pytest.mark.parametrize(
    ("counts", "expected"),
    [
        (
            {
                "write_fields": 12,
                "write_entities": 5,
                "blocked_entities": 2,
                "already": 3,
            },
            "12 changes for 5 Members will be written. 2 Members are held back. "
            "3 changes were already in ParishSoft.",
        ),
        (
            {
                "write_fields": 1,
                "write_entities": 1,
                "blocked_entities": 0,
                "already": 0,
            },
            "1 change for 1 Member will be written.",
        ),
        (
            {
                "write_fields": 0,
                "write_entities": 0,
                "blocked_entities": 1,
                "already": 1,
            },
            "Nothing will be written. 1 Member is held back. "
            "1 change was already in ParishSoft.",
        ),
    ],
)
def test_summary_counts_the_plan(counts, expected):
    """The summary states the counts the confirmation binds."""
    assert publication.summary(counts) == expected


def test_check_gates_apply_in_order(monkeypatch):
    """The switch first, then nothing approved, a run unfinished, and other
    campaign work; with none of them Check ParishSoft is available."""
    running, held = [False], [False]
    monkeypatch.setattr(publication, "publication_running", lambda _: running[0])
    monkeypatch.setattr(publication, "stopped_update", lambda _: False)

    def admit(campaign_id, *, mutating):
        """Refuse while other campaign work holds the campaign."""
        if held[0]:
            raise PermissionError("held")

    monkeypatch.setattr(publication, "admit_campaign", admit)

    def gate(**values):
        """The gate for a campaign with ``values`` overriding the defaults."""
        options = {"enabled": True, "waiting_changes": 1} | values
        return publication.check_gate("campaign", **options)

    assert gate() is None
    held[0] = True
    assert gate() is publication.HELD_CAMPAIGN
    running[0] = True
    assert gate() is publication.RUNNING
    assert gate(waiting_changes=0) is publication.NOTHING_APPROVED
    assert gate(enabled=False, waiting_changes=0) is publication.OFF
    for found in (publication.OFF, publication.RUNNING):
        refusal = found.refusal()
        assert refusal.reason == found.reason and str(refusal) == found.message


def test_enabled_follows_only_a_true_setting(settings):
    """Only True turns the page on; anything else leaves it off."""
    for value, expected in ((True, True), (False, False), ("1", False)):
        settings.STEWARDSHIP_PARISHSOFT_PUBLICATION = value
        assert publication.enabled() is expected
    del settings.STEWARDSHIP_PARISHSOFT_PUBLICATION
    assert publication.enabled() is False


def render(**context):
    """The page's HTML for ``context``, publication on by default."""
    from django.template.loader import render_to_string

    values = {
        "enabled": True,
        "waiting": {"changes": 2, "members": 1, "away": 0},
        "gate": None,
        "view": None,
        "table": None,
        "request_key": "k",
    }
    return unescape(
        render_to_string("stewardship/update-parishsoft.html", values | context)
    )


def test_page_switched_off_offers_no_button():
    """Off: the page says so and draws neither the check nor a plan."""
    html = render(enabled=False, waiting=None, gate=publication.OFF)
    assert "Updating ParishSoft is switched off" in html
    assert "<h1>Update ParishSoft</h1>" in html
    assert "Publish" not in html
    assert "Check ParishSoft</button>" not in html
    assert "Newest check" not in html


def test_page_draws_check_unavailable_with_its_reason():
    """A gate draws the button disabled, described by its reserved reason."""
    html = render(gate=publication.RUNNING)
    button = html.split("Check ParishSoft</button>")[0].rsplit("<button", 1)[1]
    assert "disabled" in button
    assert 'aria-describedby="publication-check-reason"' in button
    assert (
        '<p class="help reserve-line" id="publication-check-reason">'
        "An update is running. Wait for it to finish.</p>" in html
    )
    # Available: the same reserved line, empty, so nothing moves.
    html = render()
    button = html.split("Check ParishSoft</button>")[0].rsplit("<button", 1)[1]
    assert "disabled" not in button
    assert '<p class="help reserve-line" id="publication-check-reason"></p>' in html
    assert "2 changes are Ready to update, for 1 Members." in html
    assert "no longer in their Family" not in html
    # Rule 4's changes are counted apart (#1738, #1749's note).
    html = render(waiting={"changes": 2, "members": 1, "away": 3})
    assert (
        "3 Ready to update changes are for Members no longer in their Family "
        "in ParishSoft, so they are not checked." in html
    )


def test_page_shows_the_plan_in_the_census_changes_column_order():
    """The plan table follows Census changes' columns, then the plan's own;
    a Testing plan says nothing is written or marked."""
    rows = [
        publication.plan_row(field("write"), change(), blocked=False, accepted=False),
    ]
    view = {
        "plan": SimpleNamespace(created_at=CHECKED, testing=True),
        "checked_by": "admin@example.org",
        "rows": rows,
        "others": 2,
        "summary": "1 change for 1 Member will be written.",
        "offer": publication.Offer(None),
    }
    table = paginate(rows, {}, sorting=publication.SORTING)
    html = render(view=view, table=table)
    headings = [
        "Family",
        "Family DUID",
        "Who",
        "Member DUID",
        "What changed",
        "ParishSoft at the check",
        "Will write",
        "Outcome",
    ]
    positions = [html.index(f">{heading}<") for heading in headings]
    assert positions == sorted(positions)
    assert "Checked by admin@example.org at" in html
    assert "Testing mode: ParishSoft was checked, and nothing is written" in html
    assert "1 change for 1 Member will be written." in html
    assert "Will be written" in html and "555-0199" in html
    assert "2 other changes for these Members are not accepted" in html
    # A Testing plan offers neither Update nor Run again.
    assert 'data-review-open="update-review"' not in html
    assert 'data-review-open="rerun-review"' not in html


@pytest.mark.parametrize(
    ("administrator", "on", "expected"),
    [
        (True, True, "link"),
        (True, False, "unavailable"),
        (False, True, None),
    ],
)
def test_census_changes_links_to_the_page(administrator, on, expected):
    """Administrators get the link, unavailable with its reason while
    publication is off; Staff see nothing of it."""
    from django.template.loader import render_to_string

    from parishkit.stewardship.reports.census_change_views import SORTING
    from parishkit.stewardship.reports.census_changes import CensusQuery

    html = render_to_string(
        "stewardship/census-changes.html",
        {
            "table": paginate([], {}, sorting=SORTING),
            "query": CensusQuery(),
            "query_fields": {},
            "status_counts": {},
            "status_choices": [],
            "route_choices": [],
            "kind_choices": [],
            "administrator": administrator,
            "publication_on": on,
        },
    )
    link = 'href="/admin/reports/census/update-parishsoft/"'
    unavailable = 'aria-describedby="publication-off-reason"'
    assert (link in html) is (expected == "link")
    assert (unavailable in html) is (expected == "unavailable")
    if expected == "unavailable":
        assert "Updating ParishSoft is switched off, so Ready to update" in html
    if expected == "link":
        assert ">Update ParishSoft</a>" in html
    if expected is None:
        assert "Update ParishSoft" not in html
    assert "Publish" not in html


@pytest.mark.parametrize(
    ("result", "stage", "expected", "label"),
    [
        (("updated", ""), "updated", "updated", "Updated in ParishSoft"),
        # The final refresh's check confirmed the change's own row (#1689).
        (("updated", ""), "confirmed", "confirmed", "Confirmed"),
        (
            ("not_written", ""),
            "ready_to_update",
            "not_written",
            "Not written: changed since the check",
        ),
        (
            ("failed", "write_rejected"),
            "ready_to_update",
            "failed",
            "Failed: ParishSoft refused the change",
        ),
        (
            ("failed", "verify_mismatch"),
            "ready_to_update",
            "failed",
            "Failed: The details read back differently",
        ),
        (
            ("failed", "read_unavailable"),
            "ready_to_update",
            "failed",
            "Failed: ParishSoft did not answer",
        ),
        (("not_finished", ""), "ready_to_update", "not_finished", "Not finished"),
    ],
)
def test_a_written_change_shows_its_members_result(result, stage, expected, label):
    """Once the plan is confirmed, a planned write shows its Member's run
    result, worded as the reports spec words it; Updated becomes Confirmed
    once a refresh confirmed the change's own row."""
    shown = publication.plan_row(
        field("write"),
        change() | {"stage": stage},
        blocked=False,
        accepted=False,
        result=result,
    )
    assert (shown["outcome"], shown["outcome_label"]) == (expected, label)
    # Every result sorts in the Outcome column.
    assert publication.SORTING.parse({"sort": "outcome"})


def test_a_held_back_change_keeps_its_plan_outcome_after_the_run():
    """A result is only for planned writes: a held back or conflicting
    change keeps the outcome the check gave it."""
    for outcome, blocked, expected in (
        ("write", True, "held"),
        ("conflict", True, "conflict"),
        ("already_in_parishsoft", False, "already_in_parishsoft"),
    ):
        shown = publication.plan_row(
            field(outcome),
            change(),
            blocked=blocked,
            accepted=False,
            result=("updated", ""),
        )
        assert shown["outcome"] == expected


def test_confirmation_words_name_the_counts():
    """Update's button and question, and Run again's question, carry the
    plan's counts in words."""
    counts = {"write_fields": 12, "write_entities": 5, "blocked_entities": 0}
    assert publication.update_label(counts) == "Update 12 changes"
    assert publication.update_label(counts | {"write_fields": 1}) == "Update 1 change"
    assert (
        publication.update_question(counts)
        == "Write 12 changes for 5 Members to ParishSoft?"
    )
    assert publication.rerun_question(2) == "Try the 2 Members this run did not finish?"
    assert publication.rerun_question(1) == "Try the 1 Member this run did not finish?"


def test_update_gates_apply_in_order(monkeypatch):
    """Before its confirmation a plan offers Update, unavailable while it
    writes nothing, in Testing mode, while a run is unfinished, while other
    campaign work holds the campaign, and once it is an hour old; a Testing
    plan offers nothing."""
    from datetime import timedelta

    from django.utils import timezone

    state = {"production": True, "running": False, "held": False, "checking": False}
    monkeypatch.setattr(publication, "production", lambda: state["production"])
    monkeypatch.setattr(publication, "publication_running", lambda _: state["running"])
    monkeypatch.setattr(publication, "_held", lambda _: state["held"])
    monkeypatch.setattr(publication, "checking", lambda: state["checking"])
    counts = {"write_entities": 1, "write_fields": 1, "blocked_entities": 0}
    fresh = SimpleNamespace(
        pk="plan", campaign_id="c", testing=False, created_at=timezone.now()
    )
    old = SimpleNamespace(
        pk="plan",
        campaign_id="c",
        testing=False,
        created_at=timezone.now() - timedelta(hours=1, seconds=1),
    )
    assert publication.offer(fresh, counts, run=None) == publication.Offer("update")
    assert publication.offer(old, counts, run=None).gate is publication.EXPIRED
    state["held"] = True
    assert publication.offer(old, counts, run=None).gate is publication.HELD_CAMPAIGN
    # A check under way will make a newer plan: Update waits for it.
    state["checking"] = True
    assert publication.offer(old, counts, run=None).gate is publication.CHECKING
    state["running"] = True
    assert publication.offer(old, counts, run=None).gate is publication.RUNNING
    state["production"] = False
    assert publication.offer(old, counts, run=None).gate is publication.TESTING
    nothing = counts | {"write_entities": 0, "write_fields": 0}
    assert publication.offer(old, nothing, run=None).gate is (
        publication.NOTHING_TO_WRITE
    )
    testing = SimpleNamespace(**(vars(fresh) | {"testing": True}))
    assert publication.offer(testing, counts, run=None) == publication.Offer(None)


@pytest.mark.parametrize(
    ("state", "left", "held", "expected"),
    [
        # A run still going, or one that completed, shows its status only.
        ("running", 2, False, publication.Offer(None)),
        ("queued", 2, False, publication.Offer(None)),
        ("succeeded", 0, False, publication.Offer(None)),
        # A run that ended without completing offers Run again for what is
        # left, unavailable while other campaign work holds the campaign.
        ("failed", 2, False, publication.Offer("rerun", None, 2)),
        (
            "failed",
            1,
            True,
            publication.Offer("rerun", publication.HELD_CAMPAIGN, 1),
        ),
        # A cancelled run is not retried (rerun_publication retries only a
        # failed task).
        ("cancelled", 1, False, publication.Offer(None)),
        # Nothing left: nothing to run again.
        ("failed", 0, False, publication.Offer(None)),
    ],
)
def test_a_confirmed_plan_offers_run_again_only_after_an_unfinished_run(
    monkeypatch, state, left, held, expected
):
    """Once confirmed, the plan's region offers Run again only when its
    run's task ended without completing and a Member is unfinished."""
    monkeypatch.setattr(publication, "unfinished", lambda ids: ["e"] * left)
    monkeypatch.setattr(publication, "_held", lambda _: held)
    monkeypatch.setattr(publication, "production", lambda: True)
    plan = SimpleNamespace(pk="plan", campaign_id="c", testing=False)
    run = SimpleNamespace(state=state)
    counts = {"write_entities": 1, "write_fields": 1, "blocked_entities": 0}
    assert publication.offer(plan, counts, run=run) == expected
    if expected.kind == "rerun":
        # Testing mode greys Run again with its reason, first.
        monkeypatch.setattr(publication, "production", lambda: False)
        assert publication.offer(plan, counts, run=run).gate is publication.TESTING


def test_check_waits_for_a_stopped_update(monkeypatch):
    """While a confirmed plan has unfinished Members, Check ParishSoft is
    unavailable, so a newer check never strands its Run again."""
    monkeypatch.setattr(publication, "publication_running", lambda _: False)
    monkeypatch.setattr(publication, "_held", lambda _: False)
    stopped = [True]
    monkeypatch.setattr(publication, "stopped_update", lambda _: stopped[0])
    gate = publication.check_gate("c", enabled=True, waiting_changes=1)
    assert gate is publication.STOPPED
    stopped[0] = False
    assert publication.check_gate("c", enabled=True, waiting_changes=1) is None


def test_newest_plan_check_refuses_an_older_plan(monkeypatch):
    """Only the campaign's newest plan may be confirmed or run again."""
    newest = SimpleNamespace(pk="new")
    monkeypatch.setattr(publication, "newest_plan", lambda _: newest)
    assert publication.newest_plan_check("c", "new") is newest
    with pytest.raises(type(publication.SUPERSEDED.refusal())) as refused:
        publication.newest_plan_check("c", "old")
    assert refused.value.reason == "publication_plan_superseded"
    monkeypatch.setattr(publication, "newest_plan", lambda _: None)
    with pytest.raises(type(publication.SUPERSEDED.refusal())):
        publication.newest_plan_check("c", "new")


@pytest.mark.parametrize(
    ("sqlstate", "message", "expected"),
    [
        (
            "23505",
            "Another confirmed publication is still writing these changes",
            "publication_overlap",
        ),
        # The plan's one confirmation, made first by someone else.
        ("23505", "duplicate key value", "publication_plan_changed"),
        ("23514", "changed since preflight", "publication_plan_changed"),
        ("42501", "fresh Administrator authentication", "denied"),
        # Not a refusal: an outage, raised as it is.
        ("40001", "could not serialize", "outage"),
        (None, "connection lost", "outage"),
    ],
)
def test_a_refused_confirmation_is_worded_by_its_sqlstate(
    monkeypatch, sqlstate, message, expected
):
    """The confirmation guard's refusals become the page's codes; a sign-in
    no longer live is a denial, and any other error is not a refusal."""
    monkeypatch.setattr(publication, "_held", lambda _: False)
    monkeypatch.setattr(publication, "_expired", lambda _: False)
    from django.db import DatabaseError

    class Driver(Exception):
        """The driver's error, which carries the SQLSTATE."""

    cause = Driver()
    cause.sqlstate = sqlstate
    error = DatabaseError(message)
    error.__cause__ = cause
    plan = SimpleNamespace(campaign_id="c")
    refusal = publication.confirm_refusal(error, plan)
    if expected == "denied":
        assert type(refusal) is PermissionError
    elif expected == "outage":
        assert refusal is error
    else:
        assert refusal.reason == expected


def plan_view(offer, **values):
    """A plan view with one planned write, its ``offer`` and ``values``."""
    rows = [
        publication.plan_row(field("write"), change(), blocked=False, accepted=False)
    ]
    return (
        {
            "plan": SimpleNamespace(pk="plan-id", created_at=CHECKED, testing=False),
            "checked_by": "admin@example.org",
            "rows": rows,
            "others": 0,
            "summary": "1 change for 1 Member will be written.",
            "update_label": "Update 1 change",
            "offer": offer,
            "refreshing": False,
        }
        | values,
        paginate(rows, {}, sorting=publication.SORTING),
    )


def test_page_offers_update_with_its_dialog_and_reason():
    """Update opens the review dialog, posting the plan it shows; while it
    is unavailable it is greyed, described by its reserved reason."""
    view, table = plan_view(publication.Offer("update"))
    html = render(view=view, table=table)
    assert 'data-review-open="update-review"' in html
    assert 'action="/admin/reports/census/update-parishsoft/confirmation/"' in html
    assert '<input type="hidden" name="plan" value="plan-id">' in html
    button = html.split("Update 1 change</button>")[0].rsplit("<button", 1)[1]
    assert "disabled" not in button
    assert '<p class="help reserve-line" id="update-reason"></p>' in html
    # The page re-reads itself when shown again, so an hour-old check
    # comes back with Update unavailable.
    assert "data-reread-on-show" in html
    # Both dialogs sit outside every region, with fixed Apply addresses.
    assert html.index('id="update-review"') > html.index('id="publication-plan"')
    assert 'data-review-open="rerun-review"' not in html
    view, table = plan_view(publication.Offer("update", publication.EXPIRED))
    html = render(view=view, table=table)
    button = html.split("Update 1 change</button>")[0].rsplit("<button", 1)[1]
    assert "disabled" in button and 'aria-describedby="update-reason"' in button
    assert "This check is more than an hour old. Check ParishSoft again." in html
    assert "data-reread-on-show" not in html


def test_page_offers_run_again_and_the_final_refresh_line():
    """After a run that ended without completing, Run again replaces
    Update; while the final refresh is unfinished, the page says so."""
    view, table = plan_view(publication.Offer("rerun", None, 2), refreshing=True)
    html = render(view=view, table=table)
    assert 'data-review-open="rerun-review"' in html
    assert 'action="/admin/reports/census/update-parishsoft/retries/"' in html
    assert "Run again</button>" in html
    assert 'data-review-open="update-review"' not in html
    assert "ParishSoft is being refreshed so Census changes shows the new values." in (
        html
    )


def test_page_shows_the_update_runs_status():
    """A confirmed plan's run shows its live status in the plan's region,
    polled for the page's own wording, with its Background task link."""
    from parishkit.stewardship.campaigns.domain import Percentage

    task = {
        "id": "run-id",
        "state": "running",
        "active": True,
        "attempt": 1,
        "created_at": CHECKED.isoformat(),
        "updated_at": CHECKED.isoformat(),
        "initiator_id": "admin",
        "progress": {
            "phase": "unspecified",
            "current": 3,
            "total": 5,
            "display": Percentage(3, 5),
        },
    }
    view, table = plan_view(publication.Offer(None))
    html = render(
        view=view,
        table=table,
        update_status={
            "task": task,
            "work": {"events": []},
            "region_id": "publication-plan",
            "status_url": "/status/?view=update",
        },
        update_task_id="11111111-1111-4111-8111-111111111111",
    )
    assert 'data-live-url="/status/?view=update"' in html
    assert "Updating ParishSoft: 3 of 5 Members done" in html
    assert "View this update's Background task" in html
    assert "Run again</button>" not in html and "Update 1 change" not in html


def test_review_answers_carry_the_plan_counts_and_key():
    """Update's confirmation names the counts, describes the dialog in one
    sentence and carries the plan, counts and key into Apply; Run again's
    carries the plan and its command key; Apply's answer has no live
    status for the dialog to wait on."""
    from django.template.loader import render_to_string

    counts = {"write_entities": 5, "write_fields": 12, "blocked_entities": 2}
    html = unescape(
        render_to_string(
            "stewardship/update-parishsoft-review.html",
            {
                "update": {
                    "preview": True,
                    "plan": SimpleNamespace(pk="plan-id"),
                    "counts": counts,
                    "title": publication.update_question(counts),
                    "key": "key-id",
                }
            },
        )
    )
    assert 'data-review-title="Write 12 changes for 5 Members to ParishSoft?"' in html
    assert 'data-review-describedby="update-review-summary"' in html
    assert "2 Members are held back. This cannot be undone from here." in html
    for name, value in (
        ("plan", "plan-id"),
        ("write_entities", "5"),
        ("write_fields", "12"),
        ("blocked_entities", "2"),
    ):
        assert f'name="{name}" value="{value}"' in html
    assert 'name="request_key" value="key-id" data-review-preview' in html
    html = render_to_string(
        "stewardship/update-parishsoft-review.html",
        {
            "rerun": {
                "preview": True,
                "plan": SimpleNamespace(pk="plan-id"),
                "title": publication.rerun_question(2),
                "key": "command-id",
            }
        },
    )
    assert 'name="command_key" value="command-id" data-review-preview' in html
    started = render_to_string(
        "stewardship/update-parishsoft-review.html", {"update": {"started": True}}
    )
    assert "Update started." in started and "data-live-status" not in started


@pytest.mark.parametrize(
    ("held", "expired", "expected"),
    [
        (True, False, "publication_campaign_held"),
        (False, True, "publication_plan_expired"),
        (False, False, "publication_plan_changed"),
    ],
)
def test_a_constraint_refusal_says_which_rule(monkeypatch, held, expected, expired):
    """The guard's 23514 covers a held campaign, an old plan and a change;
    the page tells them apart by checking the plan again."""
    from django.db import DatabaseError

    class Driver(Exception):
        """The driver's error, which carries the SQLSTATE."""

    monkeypatch.setattr(publication, "_held", lambda _: held)
    monkeypatch.setattr(publication, "_expired", lambda _: expired)
    cause = Driver()
    cause.sqlstate = "23514"
    error = DatabaseError("refused")
    error.__cause__ = cause
    refusal = publication.confirm_refusal(error, SimpleNamespace(campaign_id="c"))
    assert refusal.reason == expected


def test_run_again_past_the_window_promises_no_writes():
    """A plan confirmed more than a day ago is only read back and closed,
    and Run again's confirmation says so."""
    from django.template.loader import render_to_string

    values = {
        "preview": True,
        "plan": SimpleNamespace(pk="plan-id"),
        "title": publication.rerun_question(1),
        "key": "k",
    }
    closing = render_to_string(
        "stewardship/update-parishsoft-review.html",
        {"rerun": values | {"closing": True}},
    )
    assert "nothing more is written" in closing and "before anything" not in closing
    writing = render_to_string(
        "stewardship/update-parishsoft-review.html",
        {"rerun": values | {"closing": False}},
    )
    assert "before anything is written" in writing

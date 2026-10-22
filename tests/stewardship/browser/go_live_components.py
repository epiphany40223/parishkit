"""Real go-live templates with synthetic nonprivate view projections."""

from types import SimpleNamespace as Value
from urllib.parse import parse_qs
from uuid import UUID

from django.template.loader import render_to_string
from django.urls import reverse

from parishkit.stewardship.accounts import (
    campaign_wizard,
    confirmation_views,
    go_live_check,
    go_live_confirm_views,
    stages,
)
from parishkit.stewardship.accounts.go_live_check import Progress
from parishkit.stewardship.accounts.go_live_confirm import Confirm
from parishkit.stewardship.accounts.go_live_prepare import STAGES, Prepare
from parishkit.stewardship.campaigns.domain import Percentage

# The real addresses of Go live's Check and Prepare steps and Production
# activation, where their forms post and their Refresh links lead (#519 PR
# 7b-2). A test answers each in-place read with a later version of the page
# (the "-fresh" paths below), so the refreshed region differs.
GO_LIVE = reverse("admin:go_live")
PREPARE = reverse("admin:go_live_prepare")
PRODUCTION = reverse("admin:production_progress")
# Go live's Confirm Production step (#1585 slice 4), where Prepare's
# Continue leads once the links are ready and where its dialog posts.
CONFIRM = reverse("admin:go_live_confirm")
# Where the fixture server's redirect after Production activation's Retry
# lands: a stand-in for the page once the retry has run, at the same path.
PRODUCTION_RETRIED = PRODUCTION + "?retried=1"
# A preparing page, and what its polls read once preparation has moved on.
PRODUCTION_PREPARING = "/production-preparing"
PRODUCTION_LATER = "/production-preparing-later"
# Production activation's Cancel go-live dialog answers (#1585 slice 5).
CANCEL_INVENTORY = {
    "occurrences": 1234,
    "cancellable": 1200,
    "messages": 1200,
    "failed": 34,
    "delivered": 0,
    "blocking": 0,
}

# Go live's summary of a Financial stewardship campaign part set up, drawn
# from the real steps (campaign_wizard.summary_lines, #1404 PR 7).
_PROGRESS = campaign_wizard.Progress(
    done=frozenset({"settings", "load", "share_options", "images", "mail_test"}),
    saved=frozenset({"images"}),
    kept=frozenset({"share_options"}),
)
SUMMARY = campaign_wizard.summary_lines(
    campaign_wizard.build(
        True,
        "go_live",
        done=set(_PROGRESS.done),
        mail_test_url="/admin/campaign/content/test/1/",
    ).steps,
    _PROGRESS,
    content={"edited": 3, "kept": 2, "default": 14},
    schedules={"initial": 1, "reminder": 3},
)

# Rows as content_views.resave_recommended builds them; the long subject
# checks wrapping at phone width.
RESAVE = (
    {
        "label": "Family welcome",
        "subject": None,
        "url": "/admin/campaign/content/page/welcome/",
        "blocked": False,
    },
    {
        "label": "Initial invitation",
        "subject": "Your annual census and stewardship renewal for the coming year",
        "url": "/admin/campaign/content/email/initial/"
        "00000000-0000-0000-0000-000000000061/",
        "blocked": True,
    },
)


def components(context, admin):
    """Keep acknowledgement and durable status browser tests free of infrastructure."""
    campaign = Value(
        pk=UUID(int=58),
        state="active",
        active_configuration=Value(
            name="Annual census",
            starts_at=context["server_now"],
            ends_at=context["deadline"],
        ),
    )
    preview = Value(
        observed_at=context["server_now"],
        target_state="active",
        source=Value(observed_at=context["server_now"]),
        cleanup=Value(
            submissions=1234,
            families=1000,
            messages=5000,
            unresolved=0,
            inventory=Value(total=12000),
        ),
        digests=Value(
            daily_messages=2,
            weekly_messages=0,
            coalesced_slots=19,
            empty_weekly_reports=1,
            blocked_groups=0,
        ),
        # Saved content to re-save (#838): a page and a blocked invitation.
        resave=RESAVE,
    )
    ready = {
        "campaign": campaign,
        "preview": preview,
        "problems": [],
        "counts": Value(
            active=1500,
            messages=1000,
            coalesced_slots=2000,
            skipped_slots=0,
            blocked_families=0,
        ),
        "email_eligibility": Percentage(1000, 1500),
        "no_email": Percentage(500, 1500),
        "inventory": [("submissions", 1234)],
        "origin_verified": True,
    }
    pages = production_pages(context, admin, campaign)
    return (
        pages
        | check_pages(context, admin, ready)
        | prepare_pages(context, admin)
        | confirm_pages(context, admin)
    )


def production_shown(context, campaign, state, **values):
    """``confirmation_progress.progress``'s read for a page ``state``.

    ``preparing``, ``retrying``, ``failed`` and ``prepared`` are a direct
    activation's initial mail preparation; ``scheduled``, ``started`` and
    ``closed`` have none. ``values`` replace the read's own.
    """
    now = context["server_now"]
    direct = state in {"preparing", "retrying", "failed", "prepared"}
    task = {"failed": "failed", "prepared": "succeeded"}.get(state, "running")
    shown = {
        "campaign": Value(
            **(
                vars(campaign)
                | {
                    "state": "active" if direct or state == "started" else state,
                    "delivery_paused": False,
                }
            )
        ),
        "receipt": Value(created_at=now),
        "demand": Value(
            phase="families" if state != "prepared" else "done",
            groups_completed=1234,
            items_completed=5678 if state != "prepared" else 9000,
            failure_code="recovery_required" if state in {"retrying", "failed"} else "",
        )
        if direct
        else None,
        "task": Value(state=task, updated_at=now) if direct else None,
        "complete": state == "prepared",
        "control": "synthetic-retry" if state == "failed" else None,
        "outcomes": [
            {
                "label": "Family message candidates",
                "preview": 5000,
                "actual": 4800 if state != "prepared" else 5000,
                "difference": -200 if state != "prepared" else 0,
                "complete": state == "prepared",
            }
        ]
        if direct
        else [],
        "withdrawal_available": state == "scheduled",
    }
    return shown | values


def production_pages(context, admin, campaign):
    """Production activation in its states (#1585 slice 5), by path.

    Each page's context is the view's own (``confirmation_views._context``).
    PRODUCTION, the real address its forms post to, is a failed preparation
    with Retry; the scheduled pages offer (or not) Cancel go-live, whose
    dialog's answers follow.
    """
    paused = production_shown(context, campaign, "scheduled")
    paused["campaign"].delivery_paused = True
    states = (
        (PRODUCTION, production_shown(context, campaign, "failed")),
        (PRODUCTION_RETRIED, production_shown(context, campaign, "prepared")),
        (PRODUCTION_PREPARING, production_shown(context, campaign, "preparing")),
        # The same preparation a few groups later, in its next phase: only
        # Technical details' values differ, so a test sees an open
        # disclosure take them in place (#2016).
        (
            PRODUCTION_LATER,
            production_shown(
                context,
                campaign,
                "preparing",
                demand=Value(
                    phase="reports",
                    groups_completed=2345,
                    items_completed=5678,
                    failure_code="",
                ),
            ),
        ),
        # The longest step bar: all four Go live steps, Activation current.
        ("/go-live-steps", production_shown(context, campaign, "preparing")),
        ("/production-retrying", production_shown(context, campaign, "retrying")),
        ("/production-prepared", production_shown(context, campaign, "prepared")),
        ("/production-scheduled", production_shown(context, campaign, "scheduled")),
        ("/production-scheduled-paused", paused | {"withdrawal_available": False}),
        ("/production-started", production_shown(context, campaign, "started")),
        ("/production-closed", production_shown(context, campaign, "closed")),
    )
    pages = {
        path: (
            "text/html",
            render_to_string(
                "stewardship/production-progress.html",
                context | {"admin_chrome": admin} | confirmation_views._context(shown),
            ),
        )
        for path, shown in states
    }
    for path, values in (
        (
            "/production-cancel-review",
            {"review": "synthetic-cancel", "inventory": CANCEL_INVENTORY},
        ),
        (
            "/production-cancel-review-blocking",
            {"refused": "blocking", "inventory": CANCEL_INVENTORY | {"blocking": 2}},
        ),
        ("/production-cancel-review-no-reason", {"refused": "no_reason"}),
        ("/production-cancel-review-started", {"refused": "started"}),
        ("/production-cancel-review-changed", {"refused": "changed"}),
    ):
        pages[path] = (
            "text/html",
            render_to_string(
                "stewardship/production-cancel-review.html",
                values | {"check_url": GO_LIVE, "reason_max": 2000},
            ),
        )
    return pages


def check(context, state, stage=None, **values):
    """The Check step's view context in ``state`` (go_live_views._check_context).

    ``stage`` is go_live_check's Progress (idle by default); the step bar
    is the real one for the state, and the region follows quickly (every
    2 seconds) so a test sees an in-place update without waiting long.
    """
    stage = stage or Progress("idle")
    return values | {
        "stage": stage,
        "state": state,
        "wizard": go_live_check.bar(state),
        "live_interval": None if state in {"started", "stopping"} else 2000,
        "live_limit": 3 * 60 * 60 * 1000,
        "warnings": {"backup_at": context["server_now"], "debug": False},
    }


def check_pages(context, admin, ready):
    """The Check step in each of its states (#1585), by fixture path.

    GO_LIVE, the real address every Check form posts to, shows the check
    passed, so a test that checks readiness in place (from
    /go-live-unchecked) receives it, and a reload of the address shows the
    same. /go-live is not ready, with content to re-save.
    """
    problems = [
        {
            "text": "Save exactly one initial invitation schedule.",
            "links": [("/admin/campaign/schedules/", "Dates and mail schedules")],
        },
        {
            "text": "Preview and successfully send a selected Family email.",
            "links": [
                (
                    "/admin/campaign/content/test/1/",
                    "Preview and send a test of “Hello”",
                ),
                ("/admin/campaign/content/test/2/", "Preview and send a test of “Bye”"),
            ],
        },
    ]
    idle = ready | {"origin_verified": None}
    started_row = Value(
        pk=UUID(int=59),
        state="cleanup_running",
        created_at=context["server_now"],
        campaign=ready["campaign"],
    )
    stopped_row = Value(**(vars(started_row) | {"state": "cancelled"}))
    # A Back to Preview and test email, from the Campaign setup stepper the
    # chrome builds for this page (drawn nowhere else on it), with the
    # summary lines it holds there (campaign_wizard.summary_lines, #1404 PR 7).
    stepper = Value(
        current=Value(key="go_live"),
        previous=Value(
            url="/admin/campaign/content/test/1/", label="Preview and test email"
        ),
        summary=SUMMARY,
    )
    states = (
        ("/go-live", "not_ready", None, idle | {"problems": problems}),
        (
            "/go-live-problems",
            "not_ready",
            None,
            idle
            | {
                "problems": problems[:1],
                "preview": Value(**(vars(ready["preview"]) | {"resave": ()})),
            },
        ),
        ("/go-live-unchecked", "unchecked", None, idle),
        (
            "/go-live-failed",
            "unchecked",
            None,
            idle | {"origin_verified": False},
        ),
        (GO_LIVE, "passed", None, ready),
        (
            "/go-live-back",
            "unchecked",
            None,
            idle | {"admin_chrome": admin | {"campaign_wizard": stepper}},
        ),
        (
            "/go-live-closed",
            "closed",
            None,
            idle
            | {
                "preview": Value(
                    **(vars(ready["preview"]) | {"target_state": "closed"})
                )
            },
        ),
        (
            "/go-live-started",
            "started",
            Progress("started", started_row, started_by="admin@example.org"),
            {"campaign": ready["campaign"]},
        ),
        (
            "/go-live-stopping",
            "stopping",
            Progress(
                "stopping", started_row, stop_kind="cleanup", done=4210, total=9800
            ),
            {"campaign": ready["campaign"]},
        ),
        (
            "/go-live-stop-failed",
            "stop_failed",
            Progress(
                "stop_failed",
                stopped_row,
                stop_kind="links",
                done=12,
                total=1031,
                retry="synthetic-retry-stop",
                failed=(Value(), Value(updated_at=context["server_now"])),
            ),
            {"campaign": ready["campaign"]},
        ),
        (
            "/go-live-stopped",
            "unchecked",
            Progress("idle", stopped_row, stopped_at=context["server_now"]),
            idle,
        ),
    )
    return {
        path: (
            "text/html",
            render_to_string(
                "stewardship/go-live-check.html",
                context
                | {"admin_chrome": admin}
                | check(context, state, stage, **values),
            ),
        )
        for path, state, stage, values in states
    } | {
        # The Start go-live dialog's answers (go-live-start-review.html).
        "/go-live-review": (
            "text/html",
            _review({"preview": ready["preview"], "token": "synthetic-start"}),
        ),
        "/go-live-review-started": (
            "text/html",
            _review({"refused": "started", "stage": Progress("started", started_row)}),
        ),
        "/go-live-review-stopping": (
            "text/html",
            _review(
                {"refused": "stopping", "stage": Progress("stopping", started_row)}
            ),
        ),
        "/go-live-review-origin": (
            "text/html",
            _review({"refused": "origin_unverified"}),
        ),
    }


def _review(start):
    """Draw one answer of the Start go-live dialog."""
    return render_to_string(
        "stewardship/go-live-start-review.html",
        {"start": start, "check_url": GO_LIVE},
    )


def actions():
    """The fixture server's answers to Check's POSTs, by ``action`` (#1585).

    Every Check form posts to GO_LIVE, as the real view's do: the check and
    the retry redirect back there (so the in-place answer is the check
    passed), Review answers the dialog's review, and Apply redirects to the
    step in progress, Prepare, which the dialog then opens. Every Prepare
    form posts to PREPARE: Retry and Refresh and prepare again redirect back
    there, Stop go-live's Review answers its dialog, and its Apply redirects
    to Check.
    """
    return {
        GO_LIVE: {
            "verify": (303, GO_LIVE, ""),
            "retry_stop": (303, GO_LIVE, ""),
            "review": (200, None, "/go-live-review"),
            "confirm": (303, PREPARE, ""),
        },
        PREPARE: {
            "retry": (303, PREPARE, ""),
            "again": (303, PREPARE, ""),
            "review_stop": (200, None, "/go-live-prepare-review"),
            "stop": (303, GO_LIVE, ""),
        },
        # Confirm Production's dialog: Review answers it; Apply with the
        # typed word opens Production activation, without it is refused.
        CONFIRM: {
            "review": (200, None, "/go-live-confirm-review"),
            "confirm": (303, PRODUCTION, ""),
        },
        # Production activation (#1585 slice 5): Retry redirects back to it
        # (a stand-in once the retry has run); Cancel go-live's Review answers
        # its dialog, and its Apply with a reason opens Check.
        PRODUCTION: {
            "retry": (303, PRODUCTION_RETRIED, ""),
            "review_cancel": (200, None, "/production-cancel-review"),
            "cancel": (303, GO_LIVE, ""),
        },
    }


def action_answer(path, body, responses):
    """The answer to a POST of ``body`` to ``path``, or None when not Go live's.

    A review answer names the fixture page whose markup it carries.
    """
    answers = actions().get(path)
    if answers is None:
        return None
    fields = parse_qs(body.decode(), keep_blank_values=True)
    action = fields.get("action", [""])[0]
    status, location, page = answers.get(action, (400, None, ""))
    typed = fields.get("typed", [""])[0]
    # As the view: Apply needs the typed word, spaces around it ignored.
    if path == CONFIRM and action == "confirm" and typed.strip() != "Production":
        status, location, page = 400, None, "/go-live-confirm-review-typed"
    # As the view: Cancel go-live's Apply needs a reason of more than spaces.
    reason = fields.get("reason", [""])[0]
    if path == PRODUCTION and action == "cancel" and not reason.strip():
        status, location, page = 400, None, "/production-cancel-review-no-reason"
    return status, location, responses[page][1] if page else ""


def prepare_stage(state, reason=None, index=0, current="active", **values):
    """A Prepare step state (go_live_prepare.Prepare) with its stage boxes.

    The boxes before ``index`` are done and the one at ``index`` is
    ``current``, as ``stages.rows`` marks them. Every state of a go-live in
    progress offers Stop go-live.
    """
    controls = {"stop": "synthetic-stop"} | values.pop("controls", {})
    started = state not in {"stopped", "not_started", "confirmed"}
    return Prepare(
        state,
        Value(pk=UUID(int=59)),
        reason=reason,
        boxes=tuple(stages.rows(STAGES, index, current)) if started else (),
        controls=controls if started else {},
        **values,
    )


def prepare_pages(context, admin):
    """The Prepare step in each of its states (#1585 slice 3), by fixture path.

    PREPARE, the real address every Prepare form posts to, shows Testing
    data being deleted, so a Retry answered in place (from
    /go-live-prepare-failed) receives it and the page keeps following it.
    """
    campaign = Value(
        pk=UUID(int=58),
        state="draft",
        active_configuration=Value(
            name="Annual census",
            starts_at=context["server_now"],
            ends_at=context["deadline"],
        ),
    )
    states = (
        (PREPARE, prepare_stage("running", "cleanup", done=4210, total=9800)),
        ("/go-live-prepare-refresh", prepare_stage("running", "refresh", 1)),
        (
            "/go-live-prepare-links",
            prepare_stage("running", "preparing", 2, done=812, total=1031),
        ),
        (
            "/go-live-prepare-again-links",
            prepare_stage("running", "repreparing", 2, preparation=2),
        ),
        (
            "/go-live-prepare-failed",
            prepare_stage(
                "failed",
                "cleanup",
                0,
                "failed",
                controls={"retry": "synthetic-retry"},
                failed_at=context["server_now"],
            ),
        ),
        (
            "/go-live-prepare-again",
            # Nothing runs while the step waits, so the links box is Not
            # started, never In progress.
            prepare_stage(
                "again",
                "hold_ended",
                2,
                "waiting",
                controls={"again": "synthetic-again"},
            ),
        ),
        (
            "/go-live-prepare-ready",
            prepare_stage("ready", None, 3, confirm_url=CONFIRM),
        ),
        ("/go-live-prepare-closed", prepare_stage("closed", None, 1)),
        ("/go-live-prepare-stopped", prepare_stage("stopped", stopped_by_other=True)),
    )
    pages = {}
    for path, stage in states:
        values = {
            "stage": stage,
            "campaign": campaign,
            "wizard": go_live_check.bar(
                go_live_check.bar_state(stage),
                current="prepare",
                prepared=stage.state == "ready",
            ),
            "live_pending": stage.started,
            # Quick, so a test sees an in-place update without waiting long.
            "live_interval": 2000,
            "live_limit": 3 * 60 * 60 * 1000,
            "max_preparations": 4,
        }
        pages[path] = (
            "text/html",
            render_to_string(
                "stewardship/go-live-prepare.html",
                context | {"admin_chrome": admin} | values,
            ),
        )
    review = prepare_stage("ready", None, 3, stop_kind="links")
    pages["/go-live-prepare-review"] = (
        "text/html",
        render_to_string(
            "stewardship/go-live-stop-review.html",
            {"stage": review, "refused": None, "check_url": GO_LIVE},
        ),
    )
    pages["/go-live-prepare-review-stopped"] = (
        "text/html",
        render_to_string(
            "stewardship/go-live-stop-review.html",
            {
                "stage": prepare_stage("stopped", stopped_by_other=True),
                "refused": "stopped",
                "check_url": GO_LIVE,
            },
        ),
    )
    return pages


def confirm_shown(context, state, prepare_state="ready", reason=None, **values):
    """A Confirm Production state (go_live_confirm.Confirm) with its preview.

    The preview's counts are synthetic, its target "active" unless
    ``values`` say otherwise; ``problems`` are its readiness problem codes.
    """
    campaign = Value(
        pk=UUID(int=58),
        state="draft",
        active_configuration=Value(
            name="Annual census",
            starts_at=context["server_now"],
            ends_at=context["deadline"],
        ),
    )
    target = values.pop("target", "active")
    problems = values.pop("problems", ())
    preview = Value(
        readiness=Value(
            target_state=target,
            observed_at=context["server_now"],
            campaign=campaign,
            configuration=Value(family_templates=()),
        ),
        families=Value(
            counts=Value(
                active=1500,
                email_eligible=1000,
                no_eligible_email=500,
                messages=1031,
                coalesced_slots=20,
                skipped_slots=3,
            )
        ),
        digests=Value(
            daily_messages=2,
            weekly_messages=1,
            coalesced_slots=4,
            empty_weekly_reports=1,
        ),
        problems=tuple(problems),
    )
    prepare = prepare_stage(
        prepare_state,
        reason,
        3 if prepare_state == "ready" else 2,
        **values,
    )
    prepare = Prepare(
        **(
            vars(prepare)
            | {
                "request": Value(
                    pk=UUID(int=59),
                    created_at=context["server_now"],
                    campaign=campaign,
                )
            }
        )
    )
    shows_preview = state in {"ready", "problems"}
    return Confirm(state, prepare, preview if shows_preview else None)


def confirm_pages(context, admin):
    """The Confirm Production step in its states (#1585 slice 4), by path.

    CONFIRM, the real address its dialog posts to, is ready. Each page's
    context is the view's own (``go_live_confirm_views._context``), except
    that the region re-reads itself every 2 seconds, so a test sees an
    in-place update without waiting long.
    """
    states = (
        (CONFIRM, confirm_shown(context, "ready")),
        (
            "/go-live-confirm-scheduled",
            confirm_shown(context, "ready", target="scheduled"),
        ),
        (
            "/go-live-confirm-problems",
            confirm_shown(context, "problems", problems=("other_campaign_live",)),
        ),
        (
            "/go-live-confirm-hold",
            confirm_shown(
                context,
                "hold_ended",
                "again",
                "hold_ended",
                controls={"again": "synthetic-again"},
            ),
        ),
        (
            "/go-live-confirm-closed",
            confirm_shown(context, "closed", "closed"),
        ),
        (
            "/go-live-confirm-moved",
            confirm_shown(context, "moved", "running", "repreparing", preparation=2),
        ),
        # Go-live is not in progress: stopped by another Administrator,
        # never started, or Production already confirmed.
        (
            "/go-live-confirm-stopped",
            confirm_shown(context, "stopped", "stopped", stopped_by_other=True),
        ),
        (
            "/go-live-confirm-not-started",
            confirm_shown(context, "not_started", "not_started"),
        ),
        (
            "/go-live-confirm-confirmed",
            confirm_shown(context, "confirmed", "confirmed"),
        ),
    )
    pages = {}
    for path, shown in states:
        values = go_live_confirm_views._context(shown) | {"live_interval": 2000}
        pages[path] = (
            "text/html",
            render_to_string(
                "stewardship/go-live-confirm.html",
                context | {"admin_chrome": admin} | values,
            ),
        )
    ready = states[0][1]
    for path, values in (
        ("/go-live-confirm-review", {"review": "synthetic-review", "shown": ready}),
        ("/go-live-confirm-review-typed", {"refused": "typed"}),
        ("/go-live-confirm-review-changed", {"refused": "changed"}),
        ("/go-live-confirm-review-due", {"refused": "due"}),
        ("/go-live-confirm-review-closed", {"refused": "closed"}),
        ("/go-live-confirm-review-origin", {"refused": "origin_unverified"}),
    ):
        answer = values | {
            "check_url": GO_LIVE,
            "prepare_url": PREPARE,
            "here": CONFIRM,
        }
        if "shown" in values:
            answer |= values["shown"].counts()
        pages[path] = (
            "text/html",
            render_to_string("stewardship/go-live-confirm-review.html", answer),
        )
    return pages

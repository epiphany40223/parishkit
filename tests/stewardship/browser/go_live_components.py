"""Real go-live templates with synthetic nonprivate view projections."""

from types import SimpleNamespace as Value
from urllib.parse import parse_qs
from uuid import UUID

from django.template.loader import render_to_string
from django.urls import reverse

from parishkit.stewardship.accounts import go_live_check, stages
from parishkit.stewardship.accounts.go_live_check import Progress
from parishkit.stewardship.accounts.go_live_prepare import STAGES, Prepare
from parishkit.stewardship.campaigns.domain import Percentage

# The real addresses of Go live's Check and Prepare steps and Production
# activation, where their forms post and their Refresh links lead (#519 PR
# 7b-2). A test answers each in-place read with a later version of the page
# (the "-fresh" paths below), so the refreshed region differs.
GO_LIVE = reverse("admin:go_live")
PREPARE = reverse("admin:go_live_prepare")
PRODUCTION = reverse("admin:production_progress")
# Where Prepare's Continue leads once the links are ready (today's Confirm
# Production page, until #1585 slice 4).
CONFIRM = reverse("admin:production_confirmation", args=[UUID(int=59), UUID(int=60)])
# Where the fixture server's redirect after Retry failed mail preparation
# lands: a stand-in for the page once the retry has run, at the same path.
PRODUCTION_RETRIED = PRODUCTION + "?retried=1"

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
        "confirmation_token": "synthetic-confirmation",
    }
    confirmation = ready | {
        "state": Value(
            target_state="active",
            observed_at=context["server_now"],
            impact_revision=1234,
        ),
        "transition": Value(pk=UUID(int=59)),
        "confirmation_token": "synthetic-confirmation",
    }
    production = {
        "campaign": campaign,
        "receipt": Value(created_at=context["server_now"]),
        "demand": Value(
            phase="families",
            groups_completed=1234,
            items_completed=5678,
            failure_code="recovery_required",
        ),
        "task": Value(state="failed", updated_at=context["server_now"]),
        "complete": False,
        "control": "synthetic-retry",
        "outcomes": [
            {
                "label": "Family message candidates",
                "preview": 5000,
                "actual": 4800,
                "difference": -200,
            }
        ],
    }
    # A later version of Production activation, for its Refresh to bring in
    # place: the mail prepared once go-live can no longer be cancelled.
    finished = Value(state="succeeded", updated_at=context["server_now"])
    produced = production | {
        "demand": Value(
            phase="done", groups_completed=2000, items_completed=9000, failure_code=""
        ),
        "task": finished,
        "complete": True,
        "control": None,
        "withdrawal_available": False,
    }
    pages = {
        path: (
            "text/html",
            render_to_string(template, context | {"admin_chrome": admin} | values),
        )
        for path, template, values in (
            (
                PRODUCTION,
                "stewardship/production-progress.html",
                production | {"withdrawal_available": True},
            ),
            (
                "/production-progress-fresh",
                "stewardship/production-progress.html",
                produced,
            ),
            (
                "/production-confirmation-unchecked",
                "stewardship/production-confirmation.html",
                confirmation | {"confirmation_token": None, "origin_verified": None},
            ),
            (
                PRODUCTION_RETRIED,
                "stewardship/production-progress.html",
                produced,
            ),
            (
                "/production-confirmation",
                "stewardship/production-confirmation.html",
                confirmation,
            ),
            (
                "/production-progress",
                "stewardship/production-progress.html",
                production,
            ),
            (
                "/production-scheduled",
                "stewardship/production-progress.html",
                production
                | {
                    "campaign": Value(**(vars(campaign) | {"state": "scheduled"})),
                    "demand": None,
                    "withdrawal_available": True,
                },
            ),
            (
                "/production-withdrawal",
                "stewardship/production-withdrawal.html",
                {
                    "campaign": campaign,
                    "available": True,
                    "preview": {
                        "reason": "Correct the schedule",
                        "inventory": {
                            "occurrences": 1234,
                            "cancellable": 1200,
                            "messages": 1200,
                            "failed": 34,
                            "delivered": 0,
                            "blocking": 0,
                        },
                    },
                    "withdrawal_token": "synthetic-withdrawal",
                },
            ),
        )
    }
    return pages | check_pages(context, admin, ready) | prepare_pages(context, admin)


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
    # chrome builds for this page (drawn nowhere else on it).
    stepper = Value(
        current=Value(key="go_live"),
        previous=Value(
            url="/admin/campaign/content/test/1/", label="Preview and test email"
        ),
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
    }


def action_answer(path, body, responses):
    """The answer to a POST of ``body`` to ``path``, or None when not Go live's.

    A review answer names the fixture page whose markup it carries.
    """
    answers = actions().get(path)
    if answers is None:
        return None
    action = parse_qs(body.decode()).get("action", [""])[0]
    status, location, page = answers.get(action, (400, None, ""))
    return status, location, responses[page][1] if page else ""


def posts():
    """The fixture server's answer to Retry failed mail preparation (#519 PR
    7c-3): the view's Post/Redirect/Get redirect back to Production activation,
    which a route cannot fulfil on every engine, landing on a stand-in for the
    page once the retry has run."""
    return {PRODUCTION: (303, PRODUCTION_RETRIED, "")}


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
                "failed", "cleanup", 0, "failed", controls={"retry": "synthetic-retry"}
            ),
        ),
        (
            "/go-live-prepare-again",
            prepare_stage(
                "again", "hold_ended", 2, controls={"again": "synthetic-again"}
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
                "started" if stage.started else "idle",
                current="prepare",
                prepared=stage.state == "ready",
                confirm_url=stage.confirm_url,
            ),
            "live_pending": stage.started,
            # Quick, so a test sees an in-place update without waiting long.
            "live_interval": 2000,
            "live_limit": 3 * 60 * 60 * 1000,
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

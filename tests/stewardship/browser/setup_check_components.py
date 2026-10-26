"""The Check parish data page in each state it shows (#2201).

Each is drawn from the real template with the stepper of a setup whose
earlier steps are done: before any check, while a check waits for the
installer's answer, with a current ready check, and after a failed one.
"""

from datetime import timedelta
from uuid import uuid4

from django.template.loader import render_to_string

from parishkit.stewardship.accounts.setup_views import check_page

from .setup_back_path_components import ORGANIZATION, check, wizard

CHECK_INITIAL = "/setup-check-initial"
CHECK_WAITING = "/setup-check-waiting"
CHECK_READY = "/setup-check-ready"
CHECK_FAILED = "/setup-check-failed"
CHECK_UNAVAILABLE = "/setup-check-unavailable"
CHECK_STALE = "/setup-check-stale"
PAGES = (
    CHECK_INITIAL,
    CHECK_WAITING,
    CHECK_READY,
    CHECK_FAILED,
    CHECK_UNAVAILABLE,
    CHECK_STALE,
)
# The Check parish data form's start mark (#2271) and the address its
# automatic start posts to (the setup overview, action ``check``).
AUTO_START = " data-auto-start"
START_POST = "**/admin/setup"


def keep_start_manual(page):
    """Serve every page without its start mark, so it waits for a press.

    A page drawn before any check (or after a stale one) submits its own
    form once on arrival (#2271). A test of how such a page looks, not of
    the start, serves it unmarked so no POST goes to the fixture server.
    """

    def serve(route):
        """Answer a page load with the mark removed; leave other reads."""
        if route.request.resource_type != "document":
            route.fallback()
            return
        fetched = route.fetch()
        route.fulfill(response=fetched, body=fetched.text().replace(AUTO_START, ""))

    page.route("**/*", serve)


def answer_start(page, origin, fixture, *, hold=False):
    """Answer the page's start POST with the page ``fixture`` (HTTP 200).

    The in-place submit swaps in the answer as it does a redirect's page.
    Returns ``(posts, routes, body)``: the start requests' bodies, the held
    routes (with ``hold`` a start stays unanswered until the test fulfils
    its route with ``body``), and the answer's page.
    """
    posts, routes = [], []
    body = page.request.get(origin + fixture).text()

    def answer(route):
        """Count a start; answer it now, or when the test says."""
        if route.request.method != "POST":
            route.fallback()
            return
        posts.append(route.request.post_data)
        if hold:
            routes.append(route)
            return
        route.fulfill(status=200, content_type="text/html; charset=utf-8", body=body)

    page.route(START_POST, answer)
    return posts, routes, body


def components(context, admin, now):
    """The Check parish data page before, during and after a check."""

    def page(shown, *, checked=False):
        """One Check parish data page showing the check ``shown`` (or none)."""
        steps = wizard(checked=checked)
        values = {
            "draft": {
                "status": {"attempt_id": uuid4(), "state": "collecting", "version": 4},
                "sections": {},
                "idle_at": now + timedelta(minutes=30),
                "absolute_at": now + timedelta(hours=12),
                "watchdog_at": None,
                "source_checked": checked,
            },
            "source_step": steps.step("source"),
        } | check_page(shown, ORGANIZATION)
        return (
            "text/html",
            render_to_string(
                "stewardship/setup-source.html",
                context | {"admin_chrome": admin, "wizard": steps} | values,
            ),
        )

    return {
        CHECK_INITIAL: page(None),
        CHECK_WAITING: page(check("queued")),
        CHECK_READY: page(check("ready"), checked=True),
        CHECK_FAILED: page(
            check("failed", error="parishsoft_organization_not_reached")
        ),
        CHECK_UNAVAILABLE: page(check("failed", error="parishsoft_unavailable")),
        CHECK_STALE: page(check("ready", current=False)),
    }

"""The one ordered list of initial-setup wizard pages and its stepper state.

Every wizard page, its Back/Continue neighbors and the progress stepper derive
from ``PAGES`` so the order cannot drift between templates and redirects. The
stepper is presentation only: each page's own view still performs every
ownership, state and prerequisite check, and a stepper link grants nothing.
"""

from dataclasses import dataclass

from django.http import HttpResponseRedirect
from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from .setup_content_values import CONTENT_STEPS
from .setup_policy import SetupState


@dataclass(frozen=True)
class Page:
    """One wizard page: its stepper key, visible label and URL route."""

    key: str
    label: object
    route: str
    args: tuple = ()
    required: bool = True

    @property
    def url(self):
        """Resolve the page URL from the admin namespace."""
        return reverse(self.route, args=self.args)


# Order matters twice over. Credential pages accept input only within five
# minutes of the Google sign-in (sessions.require_fresh), and signing in again
# ends this setup attempt, so every credential and the public settings its
# staging depends on (mail and Testing for Google Workspace, Slack for its
# token) come first. Then the source load, which needs the Parish profile and
# the ParishSoft connection, precedes the first campaign that needs its catalog.
PAGES = (
    Page(
        "parishsoft",
        _("ParishSoft connection"),
        "admin:setup_credential",
        ("parishsoft",),
    ),
    Page("mail", _("Outgoing email settings"), "admin:setup_step", ("mail",)),
    Page("testing", _("Testing recipient"), "admin:setup_step", ("testing",)),
    Page(
        "google_workspace",
        _("Google Workspace connection"),
        "admin:setup_credential",
        ("google_workspace",),
    ),
    Page("slack", _("Slack notifications (optional)"), "admin:setup_step", ("slack",)),
    Page(
        "slack_credential", _("Slack connection"), "admin:setup_credential", ("slack",)
    ),
    Page("parish", _("Parish profile"), "admin:setup_step", ("parish",)),
    Page("source", _("Load parish data"), "admin:setup_source"),
    Page("branding", _("Parish logo"), "admin:setup_branding"),
    Page("access", _("Administrative access"), "admin:setup_step", ("access",)),
    Page("campaign", _("First campaign"), "admin:setup_campaign"),
    Page(
        "content",
        _("Pages and email templates"),
        "admin:setup_content",
        required=False,
    ),
    Page("shares", _("How Families will share"), "admin:setup_shares"),
    Page("schedules", _("Mail schedules"), "admin:setup_schedules"),
    Page("preview", _("Review"), "admin:setup_preview"),
    Page("mail_test", _("Test email"), "admin:setup_mail"),
    Page("slack_test", _("Test Slack"), "admin:setup_notification"),
    Page("finish", _("Finish setup"), "admin:setup_confirmation"),
)
BY_KEY = {page.key: page for page in PAGES}
# Review and the pages after it need every required data-entry page first.
FINAL = frozenset({"preview", "mail_test", "slack_test", "finish"})
# Only an editable (or loading) attempt shows the stepper; frozen and ended
# attempts have their own progress and status pages.
ACTIVE = frozenset({SetupState.COLLECTING, SetupState.LOADING})


@dataclass(frozen=True)
class Step:
    """One rendered stepper entry; ``url`` is None when it cannot be opened yet."""

    number: int
    key: str
    label: object
    href: str
    url: str | None
    state: str
    status: object
    current: bool
    fix_url: str | None = None
    fix_label: object = None


@dataclass(frozen=True)
class Wizard:
    """Stepper entries plus the current page's Back and Continue neighbors."""

    steps: tuple
    previous: Step | None
    next: Step | None
    completed: int
    total: int

    def step(self, key):
        """Return the entry for ``key``, or None when it does not apply."""
        return next((step for step in self.steps if step.key == key), None)

    @property
    def current(self):
        """The entry for the page being shown, or None off the ordered list."""
        return next((step for step in self.steps if step.current), None)

    @property
    def resume(self):
        """The first open page still needing work, else the review page."""
        for step in self.steps:
            if step.state == "todo" and BY_KEY[step.key].required:
                return step
        return self.step("preview")


def applicable(key, sections):
    """Optional Slack pages and the share page appear only when they apply."""
    if key in {"slack_credential", "slack_test"}:
        return bool(sections.get("slack", {}).get("enabled"))
    if key == "shares":
        campaign = sections.get("campaign", {}).get("campaign", {})
        return "financial" in campaign.get("modules", [])
    return True


def _done(key, draft, credentials, tests):
    """Whether the page's saved data (or check) is currently complete.

    ``credentials`` maps each staged target to whether its staged settings still
    match the saved public settings; a mismatch must be re-entered. ``tests``
    holds the accepted readiness tests of the current draft revision.
    """
    sections = draft.sections
    if key in {"parishsoft", "google_workspace"}:
        return credentials.get(key) is True
    if key == "slack_credential":
        return credentials.get("slack") is True
    if key == "source":
        return draft.source_task_id is not None and draft.status.state != (
            SetupState.LOADING
        )
    if key == "content":
        return any(sections.get(step, {}).get("values") for step in CONTENT_STEPS)
    if key == "shares":
        return "campaign" in sections
    if key in {"mail_test", "slack_test"}:
        return key in tests
    if key == "preview":
        return all(
            _done(page.key, draft, credentials, tests)
            for page in PAGES
            if page.required
            and page.key not in FINAL
            and applicable(page.key, sections)
        )
    if key == "finish":
        return False
    return key in sections


def _blocker(key, draft, done):
    """Return (reason, page key that fixes it) when a page cannot open yet.

    These mirror the prerequisites each page's own view enforces. The ordering
    rule (no jumping past unfinished steps) is applied separately in build().
    """
    if draft.status.state == SetupState.LOADING and key != "source":
        return _("Available after the parish data load finishes."), "source"
    requirements = {
        "source": (
            ("parishsoft", "parish"),
            _("Save the Parish profile and ParishSoft connection first."),
        ),
        "google_workspace": (
            ("mail", "testing"),
            _("Save the outgoing email settings and Testing recipient first."),
        ),
        "campaign": (("source",), _("Load parish data first.")),
        "content": (("campaign",), _("Save the first campaign first.")),
        "shares": (("campaign",), _("Save the first campaign first.")),
        "schedules": (("campaign",), _("Save the first campaign first.")),
    }
    if key in FINAL:
        needed = tuple(
            page.key
            for page in PAGES
            if page.required and page.key not in FINAL and page.key in done
        )
        reason = _("Complete the required steps first.")
    elif key in requirements:
        needed, reason = requirements[key]
    else:
        return None
    missing = next((name for name in needed if not done[name]), None)
    return (reason, missing) if missing else None


def build(draft, current=None, *, credentials=None, tests=frozenset()):
    """Derive the stepper for a collecting or loading draft; None otherwise.

    This is a pure function of already-admitted draft data so the ordering,
    done-state and availability rules are unit-testable without a database.

    Navigation is conventional: completed steps and the first unfinished
    required step link to their pages; later unfinished steps are listed but
    not linked until every earlier required step is done (optional steps
    never hold anything back). A completed step still reads "Completed"
    while the data load runs, but is not linked because its page cannot
    accept changes then.
    """
    if draft is None or draft.status.state not in ACTIVE:
        return None
    credentials = credentials or {}
    pages = [page for page in PAGES if applicable(page.key, draft.sections)]
    done = {page.key: _done(page.key, draft, credentials, tests) for page in pages}
    # The first unfinished required step; everything unfinished after it waits.
    frontier = next(
        (page.key for page in pages if page.required and not done[page.key]), None
    )
    reached = True
    steps = []
    for number, page in enumerate(pages, start=1):
        blocked = _blocker(page.key, draft, done)
        # "locked" is ordering only: the page's view has no such prerequisite,
        # so callers checking real prerequisites look for "blocked" alone.
        locked = False
        if page.key == frontier:
            reached = False
        elif not reached and not done[page.key] and not blocked:
            blocked, locked = (_("Finish the earlier steps first."), frontier), True
        if page.key == "source" and draft.status.state == SetupState.LOADING:
            state, status = "todo", _("In progress")
        elif done[page.key]:
            state, status = "done", _("Completed")
        elif blocked:
            state, status = (
                "locked" if locked else "blocked",
                _("Not available yet: %(reason)s") % {"reason": blocked[0]},
            )
        elif not page.required:
            state, status = "todo", _("Optional")
        else:
            state, status = "todo", _("Not done yet")
        steps.append(
            Step(
                number=number,
                key=page.key,
                label=page.label,
                href=page.url,
                url=None if blocked else page.url,
                state=state,
                status=status,
                current=page.key == current,
                fix_url=BY_KEY[blocked[1]].url if blocked else None,
                fix_label=BY_KEY[blocked[1]].label if blocked else None,
            )
        )
    keys = [step.key for step in steps]
    index = keys.index(current) if current in keys else None
    return Wizard(
        steps=tuple(steps),
        previous=steps[index - 1] if index else None,
        next=steps[index + 1] if index is not None and index + 1 < len(steps) else None,
        completed=sum(step.state == "done" for step in steps),
        total=len(steps),
    )


def observed(draft):
    """Read staged-credential freshness and accepted tests for the stepper.

    Only non-secret receipt columns are read, exactly those the preview and
    test status pages already read for the same admitted original attempt.
    """
    from .setup_delivery_models import SetupMailDelivery
    from .setup_notification_models import SetupSlackDelivery
    from .setup_secret_models import SetupSealedCredential

    sections = draft.sections
    expected = {
        "parishsoft": None,
        "google_workspace": "mail" in sections
        and "testing" in sections
        and sections["mail"] | {"recipient": sections["testing"]["testing_recipient"]},
        "slack": "slack" in sections
        and {"channel_id": sections["slack"]["channel_id"]},
    }
    credentials = {
        target: expected[target] is None or settings == expected[target]
        for target, settings in SetupSealedCredential.objects.filter(
            attempt_id=draft.status.attempt_id, scrubbed_at=None
        ).values_list("target", "settings")
    }
    tests = {
        key
        for key, model in (
            ("mail_test", SetupMailDelivery),
            ("slack_test", SetupSlackDelivery),
        )
        if model.objects.filter(
            attempt_id=draft.status.attempt_id,
            state="accepted",
            attempt_version=draft.status.version,
        ).exists()
    }
    return credentials, frozenset(tests)


def wizard_for(draft, current=None):
    """Build the stepper for an admitted draft, reading its receipts when active."""
    if draft is None or draft.status.state not in ACTIVE:
        return None
    credentials, tests = observed(draft)
    return build(draft, current, credentials=credentials, tests=tests)


def continue_after(request, service, current):
    """Redirect a successful save to the next applicable wizard page.

    The draft is re-read after the save so a choice just made (enabling Slack,
    choosing financial stewardship) decides which page comes next.
    """
    from .setup_drafts import view_draft

    wizard = wizard_for(view_draft(request, service), current)
    if wizard is None or wizard.next is None:
        return HttpResponseRedirect(reverse("admin:setup"))
    return HttpResponseRedirect(wizard.next.href)

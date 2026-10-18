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


# The Parish profile comes first: it tells the administrator, before anything
# technical, that they are configuring their own parish. Each test follows the
# step whose input it proves, so a failed test is one step after the page that
# fixes it (#1578): Outgoing email settings, Testing recipient and Google
# Workspace connection (whose key is staged with those two as its public
# scope), then Test email; then the one Slack step (switch, channel and bot
# token), then Test Slack. A test needs only those inputs, never the loaded
# data, the logos or the access lists. A credential step accepts a sign-in
# of any age (#1547). Logos and access come next. ParishSoft
# connection and Load parish data are the last data-entry pages and next to
# each other (#1353): the load can take a few minutes, so everything else is
# done before the wait, and a load that fails on a wrong key or organization
# is one step after the page that fixes it. No data-entry page needs the
# loaded data. Review comes last, so it is the last step before Finish setup
# and shows the tests' results (#1476). Setup collects nothing about a
# campaign (#142): an Administrator creates it afterwards from Home, with
# Create the campaign.
PAGES = (
    Page("parish", _("Parish profile"), "admin:setup_step", ("parish",)),
    Page("mail", _("Outgoing email settings"), "admin:setup_step", ("mail",)),
    Page("testing", _("Testing recipient"), "admin:setup_step", ("testing",)),
    Page(
        "google_workspace",
        _("Google Workspace connection"),
        "admin:setup_credential",
        ("google_workspace",),
    ),
    Page("mail_test", _("Test email"), "admin:setup_mail"),
    Page("slack", _("Slack notifications (optional)"), "admin:setup_step", ("slack",)),
    Page("slack_test", _("Test Slack"), "admin:setup_notification"),
    Page("branding", _("Parish logos"), "admin:setup_branding"),
    Page("access", _("Administrative access"), "admin:setup_step", ("access",)),
    Page(
        "parishsoft",
        _("ParishSoft connection"),
        "admin:setup_credential",
        ("parishsoft",),
    ),
    Page("source", _("Load parish data"), "admin:setup_source"),
    Page("preview", _("Review"), "admin:setup_preview"),
    Page("finish", _("Finish setup"), "admin:setup_confirmation"),
)
BY_KEY = {page.key: page for page in PAGES}
# Review and Finish setup need every required page first, the tests included.
FINAL = frozenset({"preview", "finish"})
# The staged credential each test step proves.
TESTS = {"mail_test": "google_workspace", "slack_test": "slack"}
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
    def onward(self):
        """The step a completed current step opens (wizard rule 8).

        That is the next later step not done yet, or the next step when every
        later one is done; so a fix saved on an earlier step goes straight to
        the stale credential or test it invalidated (#1476). When that step is
        blocked (its view refuses it, e.g. Test email behind a stale key), it
        is the step needing work instead, so the save never lands on the
        "complete the earlier steps first" page. A merely "locked" step is
        ordering only and its page opens, so it is kept.
        """
        later = self.steps[self.current.number :] if self.current else ()
        chosen = next((step for step in later if step.state != "done"), self.next)
        return self.resume if chosen and chosen.state == "blocked" else chosen

    @property
    def resume(self):
        """The first open page still needing work, else the review page."""
        for step in self.steps:
            if step.state == "todo" and BY_KEY[step.key].required:
                return step
        return self.step("preview")


def applicable(key, sections):
    """Test Slack appears only when Slack is turned on."""
    if key == "slack_test":
        return bool(sections.get("slack", {}).get("enabled"))
    return True


def _done(key, draft, credentials, tests):
    """Whether the page's saved data (or check) is currently complete.

    ``credentials`` maps each staged target to whether its staged settings still
    match the saved public settings; a mismatch must be re-entered. ``tests``
    holds the readiness tests accepted for their current inputs (the staged
    credential they used, still sealed with the current settings, #1578).
    """
    sections = draft.sections
    if key in {"parishsoft", "google_workspace"}:
        return credentials.get(key) is True
    if key == "slack":
        # One step saves the switch, the channel and the bot token (#1578):
        # while Slack is on it is done only with a token current for the
        # saved channel.
        return key in sections and (
            not sections[key]["enabled"] or credentials.get("slack") is True
        )
    if key == "source":
        # Bound to a load whose staged data is still valid, by review's own
        # predicate (#1353): a failed load returns setup to collecting too,
        # and a successful one goes stale when its credential changes; both
        # show as not done, with Load again.
        return draft.source_loaded
    if key in {"mail_test", "slack_test"}:
        return key in tests
    if key == "preview":
        # Review is done once the admin has opened it for the current draft
        # version with every earlier required page complete, the tests
        # included (#1476), not merely when it could be opened; any later edit
        # bumps the version and asks for it again.
        return getattr(draft, "reviewed", {}).get(
            "preview"
        ) == draft.status.version and all(
            _done(page.key, draft, credentials, tests)
            for page in PAGES[: PAGES.index(BY_KEY["preview"])]
            if page.required and applicable(page.key, sections)
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
            ("parish", "parishsoft"),
            _("Save the Parish profile and ParishSoft connection first."),
        ),
        "google_workspace": (
            ("mail", "testing"),
            _("Save the outgoing email settings and Testing recipient first."),
        ),
        # Each test needs only its own inputs (#1578); the email test also
        # names the parish, from the first step.
        "mail_test": (
            ("parish", "mail", "testing", "google_workspace"),
            _(
                "Save the outgoing email settings, Testing recipient and Google "
                "Workspace connection first."
            ),
        ),
        "slack_test": (
            ("slack",),
            _("Save the Slack channel and bot token first."),
        ),
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


def _unordered(key, draft):
    """Whether an unfinished step stays open whatever comes before it.

    The running load's own step stays the page to follow while it runs,
    even when an earlier step is still to do (an attempt that loaded before
    the email test, as the order before #1578 had it). A saved Slack step
    stays a link from every step (#1499), since it is where Slack is turned
    off, even while its token still needs entering (#1578).
    """
    if key == "source":
        return draft.status.state == SetupState.LOADING
    return key == "slack" and "slack" in draft.sections


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
        elif (
            not reached
            and not done[page.key]
            and not blocked
            and not _unordered(page.key, draft)
        ):
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


def staged_scope(sections):
    """The public settings each staged credential must be sealed with now.

    A staged credential is current only while its settings equal these: the
    Google Workspace key is sealed with the mail and Testing settings, the
    Slack token with the saved channel. ParishSoft (None) carries its own
    organization, which its page compares. A target whose settings are not
    saved yet maps to False, which no staged settings equal.
    """
    from .provider_context import workspace_scope

    return {
        "parishsoft": None,
        "google_workspace": "mail" in sections
        and "testing" in sections
        and workspace_scope(sections["mail"], sections["testing"]["testing_recipient"]),
        "slack": sections.get("slack", {}).get("enabled", False)
        and {"channel_id": sections["slack"]["channel_id"]},
    }


def _receipts(draft):
    """Each staged target's freshness, and the binding of the current ones.

    Returns ``(credentials, bindings)``: ``credentials`` maps each staged
    target to whether its settings still match the saved public settings,
    and ``bindings`` maps each current one to (id, version, fingerprint),
    what a test of it is bound to (#1578). Only non-secret receipt columns
    are read.
    """
    from .setup_secret_models import SetupSealedCredential

    expected = staged_scope(draft.sections)
    credentials, bindings = {}, {}
    for target, *binding, settings in SetupSealedCredential.objects.filter(
        attempt_id=draft.status.attempt_id, scrubbed_at=None
    ).values_list("target", "id", "version", "fingerprint", "settings"):
        credentials[target] = expected[target] is None or settings == expected[target]
        if credentials[target]:
            bindings[target] = tuple(binding)
    return credentials, bindings


def accepted_tests(draft, bindings=None):
    """The newest accepted test of each kind whose inputs are still current.

    Maps "mail_test" and "slack_test" to that delivery's id. A test counts
    while the staged credential it used is unscrubbed at the same version
    and key fingerprint and still sealed with the current settings (#1578),
    whatever else was saved since; the readiness guard in SQL admits the
    same binding. ``bindings`` is ``_receipts``'s, when already read.
    """
    from .setup_delivery_models import SetupMailDelivery
    from .setup_notification_models import SetupSlackDelivery

    if bindings is None:
        bindings = _receipts(draft)[1]
    found = {}
    for key, model in (
        ("mail_test", SetupMailDelivery),
        ("slack_test", SetupSlackDelivery),
    ):
        binding = bindings.get(TESTS[key])
        if binding is None:
            continue
        identifier = (
            model.objects.filter(
                attempt_id=draft.status.attempt_id,
                state="accepted",
                credential_id=binding[0],
                credential_version=binding[1],
                fingerprint=binding[2],
            )
            .order_by("-finished_at", "-id")
            .values_list("id", flat=True)
            .first()
        )
        if identifier is not None:
            found[key] = identifier
    return found


def observed(draft):
    """Read staged-credential freshness and accepted tests for the stepper.

    Only non-secret receipt columns are read, exactly those the preview and
    test status pages already read for the same admitted original attempt.
    """
    credentials, bindings = _receipts(draft)
    return credentials, frozenset(accepted_tests(draft, bindings))


def wizard_for(draft, current=None):
    """Build the stepper for an admitted draft, reading its receipts when active."""
    if draft is None or draft.status.state not in ACTIVE:
        return None
    credentials, tests = observed(draft)
    return build(draft, current, credentials=credentials, tests=tests)


def continue_after(request, service, current):
    """Redirect a successful save to the next applicable page not done yet.

    The draft is re-read after the save so a choice just made (enabling Slack,
    choosing financial stewardship) decides which page comes next, and a step
    the save invalidated (a stale key, the tests) is the one opened
    (``Wizard.onward``, wizard rule 8).
    """
    from .setup_drafts import view_draft

    wizard = wizard_for(view_draft(request, service), current)
    onward = wizard and wizard.onward
    if onward is None:
        return HttpResponseRedirect(reverse("admin:setup"))
    return HttpResponseRedirect(onward.href)

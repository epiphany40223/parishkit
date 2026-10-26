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
# token), then Test Slack. A test needs only those inputs, never ParishSoft's
# data, the logos or the access lists. A credential step accepts a sign-in
# of any age (#1547). Logos and access come next. ParishSoft
# connection and Check parish data are the last data-entry pages and next to
# each other (#1353, #2201): a check that fails on a wrong key or
# organization is one step after the page that fixes it. The check reads
# only the organization's name and ParishSoft's Family and Member totals;
# the final load at Finish setup is the only full load, and no data-entry
# page needs ParishSoft's data. Off-site backups (optional) is the last
# optional step, just before Finish setup (Administrator, 2026-10-26, #2200):
# it needs only the mail settings, saved much earlier, and Finish setup never
# waits for it. Finish setup comes last; it checks the tests' results itself
# (#1476), and no separate Review page comes before it (#2145). Setup
# collects nothing about a campaign (#142): an Administrator creates it
# afterwards from Home, with Create the campaign.
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
    Page("source", _("Check parish data"), "admin:setup_source"),
    Page(
        "backup",
        _("Off-site backups (optional)"),
        "admin:setup_step",
        ("backup",),
        required=False,
    ),
    Page("finish", _("Finish setup"), "admin:setup_confirmation"),
)
BY_KEY = {page.key: page for page in PAGES}
# Finish setup needs every required page first, the tests included.
FINAL = frozenset({"finish"})
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
        ordering only and its page opens, so it is kept, unless it is
        optional (Off-site backups, #2200): an earlier required step still
        needs work then, and that is the one opened.
        """
        later = self.steps[self.current.number :] if self.current else ()
        chosen = next((step for step in later if step.state != "done"), self.next)
        detour = chosen and (
            chosen.state == "blocked"
            or (chosen.state == "locked" and not BY_KEY[chosen.key].required)
        )
        return self.resume if detour else chosen

    @property
    def resume(self):
        """The first open page still needing work, else Finish setup."""
        for step in self.steps:
            if step.state == "todo" and BY_KEY[step.key].required:
                return step
        return self.step("finish")


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
        # The newest parish data check is ready and checked the staged key
        # and organization saved now (#2201): a failed check, or one of an
        # earlier key or organization, shows as not done, with Check again.
        return draft.source_checked
    if key in {"mail_test", "slack_test"}:
        return key in tests
    if key == "finish":
        return False
    return key in sections


def _blocker(key, draft, done):
    """Return (reason, page key that fixes it) when a page cannot open yet.

    These mirror the prerequisites each page's own view enforces. The ordering
    rule (no jumping past unfinished steps) is applied separately in build().
    """
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

    A saved Slack step stays a link from every step (#1499), since it is
    where Slack is turned off, even while its token still needs entering
    (#1578).
    """
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
        if done[page.key]:
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
    previous = steps[index - 1] if index else None
    # Back passes over an optional step that cannot open yet (Off-site
    # backups while an earlier required step needs work, #2200), so the page
    # after it keeps a Back to the step before.
    if previous and previous.url is None and not BY_KEY[previous.key].required:
        previous = steps[index - 2] if index > 1 else None
    return Wizard(
        steps=tuple(steps),
        previous=previous,
        next=steps[index + 1] if index is not None and index + 1 < len(steps) else None,
        completed=sum(step.state == "done" for step in steps),
        total=len(steps),
    )


def staged_scope(sections):
    """The public settings each staged credential must be sealed with now.

    A staged credential is current only while its settings equal these: the
    Google Workspace key is sealed with the mail and Testing settings, the
    Slack token with the saved channel. ParishSoft (None) carries its own
    organization, chosen on its page from the organizations the key reaches
    (#1554): it is current once one is chosen. A target whose settings are
    not saved yet maps to False, which no staged settings equal.
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
        credentials[target] = (
            "organization_id" in settings
            if expected[target] is None
            else settings == expected[target]
        )
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

    Only non-secret receipt columns are read, exactly those Finish setup and the
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

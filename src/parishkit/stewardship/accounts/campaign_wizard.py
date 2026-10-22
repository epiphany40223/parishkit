"""The Campaign setup stepper over the existing campaign pages (#142 slice 4).

While the current campaign is a draft in Testing mode, the Campaign setup
pages show a stepper with the setup wizard's look (admin-portal spec,
"Create the campaign", Guided steps). Each step is the existing settings
page, so every save stays that page's own review, confirm and apply; the
stepper only shows where the Administrator is, what is completed, and links
Back and Next to the neighbouring pages. It grants nothing: each page still
performs every access, state and prerequisite check of its own.

Completion is read from what was applied, plus one stored fact. Campaign
settings is completed when the campaign is created. Load ParishSoft data is
completed once the campaign has its Families (#1404 PR 5). Share options, Pages
and emails, Dates and mail schedules and Campaign images are completed once
a change confirmed on that page has been applied since the campaign was
created, which ``step_keys`` recognizes from each applied request's patch
(defaults written by Create the campaign never count, because they arrive in
the creation request), or once the Administrator pressed that step's
**Continue to <next step>** while its form held no change (#1404): that
stores a ``CampaignSetupReview`` row (``record_review``), the step "kept as
is". Preview and test email is completed by an accepted test of the initial
invitation's current revision. Go live completes only by going
live, and the stepper is no longer shown then.

The stepper's summary names the next step that is not done yet and offers
Leave campaign setup; the action row at the bottom of each step page
(campaign-step-actions.html) holds Back and the one primary Continue. The
sub-pages a step opens (an email's editor, a new or edited mail schedule,
the Campaign calendar, and Campaign settings' content history) draw the
same stepper with their step current and a Return to <step> link instead
(``SUBPAGES``).

On Campaign settings, Share options and Campaign images (``DIRECT_STEPS``)
the same primary becomes **Save and continue** once the page's form holds a
change (#1404 PR 4; on Campaign images a chosen image, PR 4c): the form
posts its ``save`` action, which ``save_step`` records
directly, with no review dialog, while the stepper still applies; the page
follows the change and then opens the next step not done yet. The
sub-pages that save (Pages and emails' editor, New and Edit scheduled email;
``DIRECT_SUBPAGES``, #1404 PR 4b) save the same way with **Save and go
back**, and then open their step again. Outside the wizard those pages keep
their own Save (or Preview changes) and review dialog. Dates and mail
schedules with no schedule saved proposes a starting set (``suggest``, #1404
PR 6, ``schedule_suggestions``): its primary is then **Add these schedules
and continue**, which saves them directly the same way, and Continue without
schedules for now becomes the secondary.

Go live's Check step lists one summary line per earlier step
(``summary_lines``, #1404 PR 7), from the same completion read
(``progress``) and the applied document, so it costs no query.
"""

from dataclasses import dataclass, replace
from datetime import timedelta
from uuid import uuid4

from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.utils.translation import ngettext

from . import stages
from .admin_navigation import PAGES as NAMES
from .campaign_wizard_models import REVIEW_STEPS, CampaignSetupReview
from .setup_wizard import Step, Wizard


@dataclass(frozen=True)
class Page:
    """One step: its key, the Admin URL name of the page it opens, and a name.

    ``name`` is the step's own name where it differs from the page's: Load
    ParishSoft data opens Refresh from ParishSoft (#1404 PR 5).
    """

    key: str
    route: str
    name: object = None

    @property
    def label(self):
        """The step's name: its own, else the page's, as the menu uses it."""
        return self.name or NAMES[self.route].label


# The order a campaign is prepared in. Ministries and funds are part of
# Campaign settings' one form (#1119), so they are not a step of their own.
PAGES = (
    Page("settings", "campaign_settings"),
    # The campaign's Families, Family codes and giving come from a full
    # ParishSoft refresh, requested from this step once the campaign exists.
    Page("load", "source_refresh", _("Load ParishSoft data")),
    Page("share_options", "share_settings"),
    Page("content", "content_catalog"),
    Page("schedules", "schedule_settings"),
    Page("images", "artwork_settings"),
    Page("mail_test", "campaign_mail"),
    Page("go_live", "go_live"),
)
# The steps an applied change's patch can complete (see ``step_keys``).
CHANGE_STEPS = frozenset({"share_options", "content", "schedules", "images"})
# Pages and emails' change to a schedule only re-points it at the email's
# new revision; any other schedule change is made on Dates and mail
# schedules.
_CONTENT_SCHEDULE_VALUES = frozenset({"template_version", "subject"})
# Dates and mail schedules' campaign window: a change of only these values is
# that page's (Campaign settings can make the same change, and it counts too).
_WINDOW_VALUES = frozenset({"timezone", "start_date", "end_date"})
# The step pages whose own form the action row's primary saves directly
# (#1404 PR 4; Campaign images, PR 4c), and that form's id on each of them:
# ``DIRECT_FORM`` unless ``STEP_FORMS`` names another.
DIRECT_STEPS = frozenset({"settings", "share_options", "images"})
DIRECT_FORM = "settings-form"
STEP_FORMS = {"images": "artwork-save-form"}
# The sub-pages whose form the action row's Save and go back saves directly
# (#1404 PR 4b), by URL name, and that form's id by their step.
DIRECT_SUBPAGES = frozenset(
    {"content_edit", "content_revision", "schedule_new", "schedule_edit"}
)
SUBPAGE_FORMS = {"content": "content-form", "schedules": "schedule-entry-form"}
# The form Dates and mail schedules' Add these schedules and continue posts
# (#1404 PR 6; schedule-settings.html).
SUGGESTED_FORM = "suggested-schedules-form"


@dataclass(frozen=True)
class CampaignWizard(Wizard):
    """The Campaign setup stepper for one page, beyond the setup wizard's.

    ``upcoming`` is where Continue goes: the next step after the current
    one that is not done yet, or simply the next one when every later step
    is done. ``sub`` marks a sub-page of the current step. ``no_schedules``
    marks Dates and mail schedules with no schedule saved, whose Continue
    says it goes on without one. ``load`` is the campaign's ParishSoft
    data while it has no Families: "offer" (with ``refresh_key``, the
    button's request key), "loading" once a full refresh is under way, or
    while a just-created campaign waits for the load the system starts for
    it (``LOAD_START_WAIT``, #1951; no ``load_task`` yet),
    "failed" when the newest full refresh did not finish, whose button then
    reads Try again, or "empty" when it succeeded yet left no Families
    (#1587). ``load_task`` is that newest refresh's Background task
    while it is loading or failed, and ``load_progress`` its (phase,
    current, total), for Load ParishSoft data's stage boxes. ``families``
    is the campaign's Family count, read only on that step once loaded.
    ``creating`` marks step 1 before the campaign exists (``creation``):
    Create the campaign draws its own action row inside its form, so the
    shared row draws nothing. ``direct`` marks a step page whose form the
    primary saves directly (``DIRECT_STEPS``), or a sub-page whose form Save
    and go back saves directly (``DIRECT_SUBPAGES``), with ``save_key``, the
    request key that form posts. ``suggest`` marks Dates and mail schedules
    proposing its suggested schedules (no schedule saved, and an Initial
    invitation email to send; #1404 PR 6), which its primary adds directly.
    ``summary`` is the Go live step's one line per earlier step
    (``summary_lines``, #1404 PR 7), read only on that step.
    """

    upcoming: Step | None = None
    sub: bool = False
    no_schedules: bool = False
    load: str | None = None
    refresh_key: object = None
    load_task: object = None
    load_progress: tuple | None = None
    families: int | None = None
    creating: bool = False
    direct: bool = False
    save_key: object = None
    suggest: bool = False
    summary: tuple = ()

    @property
    def direct_form(self):
        """The id of the form a direct save posts, or None.

        A step page's by its step (``STEP_FORMS``, else ``DIRECT_FORM``); a
        sub-page's form by its step (``SUBPAGE_FORMS``); the suggested
        schedules' form while they are proposed (``SUGGESTED_FORM``).
        """
        if not self.direct:
            return None
        if self.suggest:
            return SUGGESTED_FORM
        if self.sub:
            return SUBPAGE_FORMS[self.current.key]
        return STEP_FORMS.get(self.current.key, DIRECT_FORM)

    @property
    def reviewable(self):
        """Whether the current step's Continue records it as kept as is."""
        return self.current is not None and self.current.key in REVIEW_STEPS

    @property
    def load_view(self):
        """Load ParishSoft data's one state.

        Offer, loading, failed, empty (loaded, no Families) or loaded.
        """
        return self.load or "loaded"

    @property
    def load_stages(self):
        """Load ParishSoft data's stage boxes for ``load_view`` (#1404 PR 5)."""
        phase = self.load_progress[0] if self.load_progress else None
        return load_stages(self.load_view, phase)


def shown(campaign, mode, *, admin, go_live):
    """Whether the stepper applies to the current campaign at all.

    Only Administrators prepare a campaign, only a Testing draft is being
    prepared, and once a go-live has started (``go_live``, the chrome's
    flag) its own steps take over because campaign settings are locked.
    """
    return bool(
        admin
        and campaign is not None
        and campaign.state == "draft"
        and mode == "testing"
        and not go_live
    )


def step_keys(patch, campaign_id):
    """The steps one applied change completes, from its stored patch.

    ``patch`` is a request's list of operations. A campaign update of
    ``share_options`` alone, or of ``artwork`` alone, comes from Share
    options or Campaign images, whose editors write exactly that value;
    Campaign settings also writes (default) share options whenever Financial
    stewardship is turned on or off, alongside the modules, and that must
    not count. A schedule added, removed or edited (anything but Pages and
    emails' re-pointing of a schedule at an email's new revision), or a
    change of only the campaign's dates and time zone, comes from Dates and
    mail schedules, even when it also adds the email it sends; any other
    change to content comes from Pages and emails. The change that
    created the campaign completes none of them: its default text and share
    options are not the Administrator's choices. The read never fails on an
    unexpected item shape.
    """
    owner = str(campaign_id)
    keys = set()
    content = False
    for item in patch if isinstance(patch, list) else ():
        if not isinstance(item, dict):
            continue
        section = item.get("section")
        values = item.get("values") if isinstance(item.get("values"), dict) else {}
        if section == "campaigns" and item.get("id") == owner:
            if item.get("operation") == "add":
                return set()
            if item.get("operation") != "update" or not values:
                continue
            if values.keys() == {"share_options"}:
                keys.add("share_options")
            elif values.keys() == {"artwork"}:
                keys.add("images")
            elif values.keys() <= _WINDOW_VALUES:
                keys.add("schedules")
        elif section == "schedules":
            if item.get("operation") != "update" or not (
                values.keys() <= _CONTENT_SCHEDULE_VALUES
            ):
                keys.add("schedules")
        elif section == "content":
            content = True
    if content and "schedules" not in keys:
        keys.add("content")
    return keys


def build(
    financial,
    current,
    *,
    done,
    mail_test_url,
    sub=False,
    no_schedules=False,
    load=None,
    direct=False,
    load_task=None,
    load_progress=None,
    families=None,
    suggest=False,
    summary=(),
):
    """Derive the stepper for the page ``current`` (a step key) as a ``Wizard``.

    A pure function of what the database read found, so the order, the
    applicable steps and their states are unit-testable without one.
    ``financial`` decides whether Share options applies; ``done`` holds the
    completed step keys; ``mail_test_url`` is Preview and test email for the
    invitation's current revision, or None when there is none to test. Every
    other step is always a link: Back and Continue never save or discard
    anything on the page, and each page keeps its own prerequisites.
    ``sub``, ``no_schedules``, ``direct``, ``suggest``, ``summary`` and the
    ``load`` facts (``load``, ``load_task``, ``load_progress``,
    ``families``) are carried through (``CampaignWizard``); a ``direct``
    (or ``suggest``) stepper draws a fresh ``save_key``.
    """
    pages = [page for page in PAGES if page.key != "share_options" or financial]
    steps = []
    for number, page in enumerate(pages, start=1):
        url = (
            mail_test_url if page.key == "mail_test" else reverse(f"admin:{page.route}")
        )
        if page.key in done:
            state, status = "done", _("Completed")
        elif url is None:
            state = "blocked"
            status = _("Not available yet: %(reason)s") % {
                "reason": _("the campaign has no invitation email to test.")
            }
        else:
            state, status = "todo", _("Not done yet")
        steps.append(
            Step(
                number=number,
                key=page.key,
                label=page.label,
                href=url,
                url=url,
                state=state,
                status=status,
                current=page.key == current,
            )
        )
    keys = [step.key for step in steps]
    index = keys.index(current) if current in keys else None
    # Back and Next skip a step that cannot open (no invitation to test), so
    # neither dead-ends on it.
    before = [step for step in steps[:index] if step.url] if index is not None else []
    after = (
        [step for step in steps[index + 1 :] if step.url] if index is not None else []
    )
    # Never land on a completed step (#1404): Continue skips to the next
    # step not done yet, or the next step when every later one is done.
    upcoming = next((step for step in after if step.state != "done"), None)
    return CampaignWizard(
        steps=tuple(steps),
        previous=before[-1] if before else None,
        next=after[0] if after else None,
        completed=sum(step.state == "done" for step in steps),
        total=len(steps),
        upcoming=upcoming or (after[0] if after else None),
        sub=sub,
        no_schedules=no_schedules,
        load=load,
        refresh_key=uuid4() if load in {"offer", "failed"} else None,
        load_task=load_task,
        load_progress=load_progress,
        families=families,
        direct=direct or suggest,
        save_key=uuid4() if direct or suggest else None,
        suggest=suggest,
        summary=tuple(summary),
    )


@dataclass(frozen=True)
class SummaryLine:
    """One line of Go live's summary: a step's name, its page, what it holds."""

    label: object
    url: str | None
    text: object


# Pages and emails' item states, in the summary's order, in the list's own
# words (content-catalog.html); "kept" is a Default text item kept as is.
_CONTENT_WORDS = (
    ("edited", lambda n: ngettext("%(n)s edited", "%(n)s edited", n)),
    ("kept", lambda n: ngettext("%(n)s kept as is", "%(n)s kept as is", n)),
    ("default", lambda n: ngettext("%(n)s default text", "%(n)s default text", n)),
    ("empty", lambda n: ngettext("%(n)s empty", "%(n)s empty", n)),
)


def _step_summary(key, progress, content, schedules):
    """The summary text of the step ``key`` (``summary_lines``)."""
    done = key in progress.done
    if key == "settings":
        if key in progress.saved:
            return _("changed since the campaign was created")
        return _("as created")
    if key == "load":
        return _("loaded") if done else _("not loaded yet")
    if key == "content":
        parts = [
            words(content[state]) % {"n": content[state]}
            for state, words in _CONTENT_WORDS
            if content.get(state)
        ]
        return ", ".join(str(part) for part in parts) or _("no pages or emails yet")
    if key == "schedules":
        initial, reminders = schedules.get("initial", 0), schedules.get("reminder", 0)
        parts = [
            ngettext("%(n)s invitation", "%(n)s invitations", initial) % {"n": initial}
            if initial
            else None,
            ngettext("%(n)s reminder", "%(n)s reminders", reminders) % {"n": reminders}
            if reminders
            else None,
        ]
        return ", ".join(part for part in parts if part) or _(
            "no Family mail scheduled yet"
        )
    if key == "mail_test":
        return _("test accepted") if done else _("not done yet")
    if key in progress.saved:
        return _("changed")
    if key in progress.kept:
        return _("kept the defaults")
    return _("not done yet")


def summary_lines(steps, progress, *, content, schedules):
    """Go live's summary: one ``SummaryLine`` per step before Go live (#1404 PR 7).

    A pure function of the stepper's ``steps``, its ``progress`` read, the
    Pages and emails item counts (``content``: edited, kept, default and
    empty) and the campaign's Family mail schedules by kind (``schedules``:
    initial and reminder), so no step needs a review dialog of its own and
    every line is unit-testable without a database (admin-portal spec, the
    Go live step of the stepper).
    """
    return tuple(
        SummaryLine(
            step.label,
            step.url,
            _step_summary(step.key, progress, content, schedules),
        )
        for step in steps
        if step.key != "go_live"
    )


def _window_change(patch, campaign_id):
    """Whether ``patch`` updates only the campaign's dates and time zone.

    Campaign settings and Dates and mail schedules write the same patch,
    so ``progress`` credits both steps with it.
    """
    owner = str(campaign_id)
    return any(
        isinstance(item, dict)
        and item.get("section") == "campaigns"
        and item.get("id") == owner
        and item.get("operation") == "update"
        and isinstance(item.get("values"), dict)
        and item["values"]
        and item["values"].keys() <= _WINDOW_VALUES
        for item in (patch if isinstance(patch, list) else ())
    )


def _summary_facts(configuration, campaign, items):
    """The counts ``summary_lines`` takes, from the applied document in hand.

    ``content`` counts the Pages and emails list's rows by the state it
    shows, ``items`` being the items kept as is (``Progress.items``);
    ``schedules`` counts the campaign's invitation and reminder schedules.
    Neither reads the database.
    """
    from .content_views import catalog_entries

    pages, emails = catalog_entries(configuration, campaign)
    content = {}
    for row in [*pages, *(rev for email in emails for rev in email["revisions"])]:
        state = row["state"]
        if state == "default" and row["item"] in items:
            state = "kept"
        content[state] = content.get(state, 0) + 1
    schedules = {}
    for row in configuration.active_configuration.canonical_document["sections"].get(
        "schedules", []
    ):
        values = row["values"]
        if values.get("campaign_id") == str(campaign.pk):
            kind = values.get("kind")
            schedules[kind] = schedules.get(kind, 0) + 1
    return content, schedules


# Why every later step is unavailable on Create the campaign (#1404 PR 3),
# in both Admin steppers' "Not available yet: <reason>" form.
CREATE_FIRST = _("Not available yet: Create the campaign first.")


def creation():
    """The stepper on Create the campaign, step 1 before the campaign exists.

    Step 1 is Campaign settings, opened on Create the campaign's own
    address, and every later step is unavailable until the campaign exists
    (admin-portal spec, "Create the campaign"; ui-conventions "Wizards").
    Share options is listed, because the campaign's modules are not chosen
    yet; once the campaign exists it is shown only with Financial
    stewardship. ``upcoming`` names step 2 for the summary's "Next:" line;
    it has no address, so nothing links to it. Built from constants only:
    drawing it reads nothing from the database.
    """
    steps = tuple(
        Step(
            number=number,
            key=page.key,
            label=page.label,
            href=reverse("admin:campaign_create") if number == 1 else None,
            url=reverse("admin:campaign_create") if number == 1 else None,
            state="todo" if number == 1 else "blocked",
            status=_("Not done yet") if number == 1 else CREATE_FIRST,
            current=number == 1,
        )
        for number, page in enumerate(PAGES, start=1)
    )
    return CampaignWizard(
        steps=steps,
        previous=None,
        next=None,
        completed=0,
        total=len(steps),
        upcoming=steps[1],
        creating=True,
    )


def for_creation(match, mode, *, admin):
    """The stepper for Create the campaign while no campaign exists, or None.

    Only an Administrator's Create the campaign page in Testing mode draws
    it, as the page itself is admitted only then; every other page without
    a current campaign draws no stepper.
    """
    if not (
        admin
        and mode == "testing"
        and getattr(match, "namespace", None) == "admin"
        and getattr(match, "url_name", None) == "campaign_create"
    ):
        return None
    return creation()


def resume(wizard):
    """The first step not done yet that can open, else the last step.

    Home's "Continue campaign setup" opens it (#1404).
    """
    return next(
        (step for step in wizard.steps if step.state != "done" and step.url),
        wizard.steps[-1],
    )


def invitation_revision(configuration, campaign_id):
    """The initial invitation's current revision (its record id), or None.

    Preview and test email for this revision is the stepper's test step, as
    it is Home's next step (``admin_dashboard``). One small indexed query.
    """
    from .content_models import ContentVersion

    return (
        ContentVersion.objects.filter(
            configuration_id=configuration.active_configuration_id,
            campaign_id=campaign_id,
            kind="email",
            slot="initial",
        )
        .order_by("record_id")
        .values_list("record_id", flat=True)
        .first()
    )


def _creation_sequence(campaign_id):
    """The activation sequence that added the campaign, or 0 when none did.

    Create the campaign's request adds it with an ``add`` operation; a
    campaign that arrived any other way (a setup before #142) counts every
    applied change. The ``@>`` containment is served by the request table's
    ``config_request_patch_lookup`` GIN index (jsonb_path_ops), so it does
    not scan every patch. Returned as a subquery expression, not a value,
    so ``completed`` reads it inside its patch query: one statement, not
    two, on every page that draws the stepper and on Home.
    """
    from django.db.models import Subquery, Value
    from django.db.models.functions import Coalesce

    from .runtime_models import ConfigurationActivation

    first = (
        ConfigurationActivation.objects.filter(
            request__patch__contains=[
                {"operation": "add", "section": "campaigns", "id": str(campaign_id)}
            ]
        )
        # The same row ``.first()`` chose before: the lowest primary key.
        .order_by("pk")
        .values("sequence")[:1]
    )
    return Coalesce(Subquery(first), Value(0))


@dataclass(frozen=True)
class Progress:
    """How each Campaign setup step stands, as one read finds it (``progress``).

    ``done`` holds the completed step keys. ``saved`` the steps with an
    applied change of their own since the creation (Campaign settings' own
    changes included, which never complete a step but name its summary
    line), ``kept`` those whose Continue kept them as is, and ``items`` the
    Pages and emails items kept as is (their ``item`` keys).
    """

    done: frozenset
    saved: frozenset = frozenset()
    kept: frozenset = frozenset()
    items: frozenset = frozenset()


def completed(campaign, revision):
    """The step keys completed for ``campaign`` (``progress``'s ``done``)."""
    return set(progress(campaign, revision).done)


def _settings_change(patch, campaign_id):
    """Whether ``patch`` updates the campaign's own row (``step_keys`` aside).

    It is Campaign settings' change when no step claims it: the summary
    line then says the settings changed since the creation (#1404 PR 7).
    """
    owner = str(campaign_id)
    return any(
        isinstance(item, dict)
        and item.get("section") == "campaigns"
        and item.get("id") == owner
        and item.get("operation") == "update"
        for item in (patch if isinstance(patch, list) else ())
    )


def progress(campaign, revision):
    """How each step of ``campaign`` stands, read from what was applied.

    ``revision`` is the invitation's current revision (or None). Applied
    changes since the campaign's creation, and the steps kept as is
    (``CampaignSetupReview``, the whole step only), complete the page
    steps; the campaign's Families complete Load ParishSoft data, and an
    accepted test of ``revision`` completes Preview and test email. A
    stored review is never undone by a later change.
    It reads every patch applied since the creation: only the step pages,
    an applied step change's status and an Administrator's Home call it,
    while a Testing draft is being prepared, whose history is a few dozen
    changes, in a constant number of queries. The same read returns the
    Pages and emails items kept as is, for Go live's summary line.
    """
    from django.db.models import CharField, Q, Value

    from parishkit.stewardship.campaigns.credential_models import FamilyCampaign

    from .campaign_mail_models import CampaignMailTest
    from .runtime_models import ConfigurationActivation

    saved = set()
    patches = ConfigurationActivation.objects.filter(
        sequence__gt=_creation_sequence(campaign.pk), request__isnull=False
    ).values_list("request__patch", flat=True)
    for patch in patches:
        keys = step_keys(patch, campaign.pk)
        if not keys and _settings_change(patch, campaign.pk):
            keys = {"settings"}
        elif _window_change(patch, campaign.pk):
            # Only the dates or time zone changed: Campaign settings and
            # Dates and mail schedules both write that patch, so it counts
            # for both (no added query: the patch is already in hand).
            keys = keys | {"settings"}
        saved |= keys
    # The steps kept as is, Pages and emails' items kept as is, and "load"
    # once the campaign has a Family (one row is enough), in one UNION
    # statement: Home's query budget is fixed.
    loaded = (
        FamilyCampaign.objects.filter(campaign_id=campaign.pk)
        .annotate(
            step=Value("load", output_field=CharField()),
            item=Value("", output_field=CharField()),
        )
        .order_by()
        .values_list("step", "item")[:1]
    )
    rows = set(
        CampaignSetupReview.objects.filter(campaign_id=campaign.pk)
        .filter(Q(item="") | Q(step="content"))
        .order_by()
        .values_list("step", "item")
        .union(loaded)
    )
    kept = {step for step, item in rows if not item}
    done = {"settings"} | saved | kept
    if (
        revision is not None
        and CampaignMailTest.objects.filter(
            campaign_id=campaign.pk, template__record_id=revision, state="accepted"
        ).exists()
    ):
        done.add("mail_test")
    return Progress(
        done=frozenset(done),
        saved=frozenset(saved),
        kept=frozenset(kept - {"load"}),
        items=frozenset(item for step, item in rows if item),
    )


def _financial(campaign):
    """Whether the campaign has Financial stewardship, which Share options needs."""
    values = campaign.active_configuration.values or {}
    return "financial" in values.get("modules", ())


def _test_url(revision):
    """Preview and test email for ``revision``, or None without one."""
    return reverse("admin:campaign_mail", args=[revision]) if revision else None


# Each step page's URL name, to its step key.
ROUTES = {page.route: page.key for page in PAGES}
# Each sub-page a step opens, to that step's key (#1404): they draw the
# stepper with their step current and a Return to <step> link. Campaign
# images' upload answer is that page itself, drawn again with its error.
SUBPAGES = {
    "content_edit": "content",
    "content_revision": "content",
    # Content history is opened from Campaign settings, its trail's parent.
    "content_history": "settings",
    "content_history_revision": "settings",
    "schedule_new": "schedules",
    "schedule_edit": "schedules",
    "schedule_calendar": "schedules",
}
_SAME_PAGE = {"artwork_upload": "images"}


# A refresh run that ended without loading anything: the load "failed".
_UNFINISHED = frozenset({"failed", "cancelled"})

# How long Load ParishSoft data waits for the load the system starts for a
# new campaign before it offers its own button (#1951). Creating the
# campaign changes the refresh scope (the giving window), so the refresh
# scheduler's next loop, seconds later, requests the current full slot for
# the new scope (source.production.produce_refreshes): that refresh is the
# campaign's load. Until it is requested the step already shows the load as
# started (Start the load In progress, no button) and follows it; a
# scheduler that has not requested it after this long is not going to, so
# the step then offers the button as before.
LOAD_START_WAIT = timedelta(minutes=2)

# The major stages of a full ParishSoft refresh, in the order its worker
# reports them (source/refreshing.py, then source/execution.py's promotion),
# as Load ParishSoft data's stage boxes name them (#1404 PR 5), after the
# shared Start the load box for the time the run waits for a worker (#1916).
LOAD_STAGES = (
    stages.START,
    ("fetching", _("Download from ParishSoft")),
    ("staging", _("Save the downloaded records")),
    ("validating", _("Check the new data")),
    ("promoting", _("Make the new data current")),
)


def load_stages(load, phase):
    """The stage boxes for the load in state ``load`` at its run's ``phase``.

    Not loaded yet ("offer"): every stage waiting. Loaded: every stage done.
    Loading: Start the load in progress until a worker picks the run up,
    then the stages before the run's phase done and its phase in progress
    (``stages.queued_index``). Failed: the stage the run stopped in is
    marked failed, Start the load when it stopped before a worker took it.
    """
    keys = [key for key, _label in LOAD_STAGES]
    index = stages.queued_index(keys, phase)
    if load in {"loaded", "empty"}:
        # A load that ran and finished is done even with no Families.
        return stages.rows(LOAD_STAGES, len(keys))
    if load == "loading":
        return stages.rows(LOAD_STAGES, index)
    if load == "failed":
        return stages.rows(LOAD_STAGES, index, "failed")
    return stages.rows(LOAD_STAGES, -1)


def task_stages(task):
    """The stage boxes for one refresh run's ``task`` on its Background task page.

    The task page's Current status, which Refresh from ParishSoft also
    draws, shows the same boxes as Load ParishSoft data (#1579), marked by
    the task's own state: Start the load in progress while the run is queued
    for a worker, waiting to retry or being recovered (#1916), since each
    attempt starts again from the download (source/refreshing.py) and waits
    for a worker again; Start the load Done and the run's phase in progress
    while it runs (``stages.queued_index``); every box done once it
    succeeded; the stage it stopped in failed when it failed; and, when
    cancelled, the stages it finished done and the rest waiting, since
    nothing failed.
    """
    keys = [key for key, _label in LOAD_STAGES]
    index = stages.queued_index(keys, task["progress"]["phase"])
    state = task["state"]
    if state == "succeeded":
        return stages.rows(LOAD_STAGES, len(keys))
    if state == "running":
        return stages.rows(LOAD_STAGES, index)
    if state == "failed":
        return stages.rows(LOAD_STAGES, index, "failed")
    if state == "cancelled":
        return stages.rows(LOAD_STAGES, index, "waiting")
    return stages.rows(LOAD_STAGES, 0)


def load_status(task, *, families):
    """Load ParishSoft data's live status for one refresh run's ``task``.

    ``task`` is the run as the Background task read names it (``_task_read``).
    The passive status fragment (``?view=load``) draws the step's live region
    from it while the run is watched: loading while it is under way and
    failed once it failed or was cancelled. A run that succeeded reads as
    loaded only when ``families`` (``current_campaign_has_families``) says
    the campaign has its Families, the same test the page's re-read makes
    (``completed``); otherwise it is "empty" (loaded, but no Families
    matched; #1587), so the region never says and announces "loaded" just
    before the re-read says it is not (#1517 review, L2). The settled
    answer's follow-up link then re-reads the page in place, which draws
    the step's full state (the Family count, Continue, Try again).
    """
    from parishkit.stewardship.jobs.models import NONTERMINAL_STATES

    state = task["state"]
    load = (
        "loading"
        if state in NONTERMINAL_STATES
        else "failed"
        if state in _UNFINISHED
        else "loaded"
        if families
        else "empty"
    )
    progress = task["progress"]
    return {
        "load": load,
        "load_task": task["id"],
        "stages": load_stages(load, progress["phase"]),
        "progress": (progress["phase"], progress["current"], progress["total"]),
        "polled": True,
    }


def awaited_load_status():
    """Load ParishSoft data's live region for the current campaign, or None.

    The passive status (``refresh_views.campaign_load_status``) that the
    step polls while the system's own load for a new campaign has not been
    requested yet (#1951) draws the region from this: the step's state as
    the page reads it (``_load_state``), so once the load is requested the
    region follows its run, and once the campaign has its Families (or the
    wait is over) it settles, and its follow-up link re-reads the page in
    place. None when there is no current campaign. Small queries: the
    current campaign, whether it has a Family, then (while it has none)
    ``_load_state``'s one or two.
    """
    from parishkit.stewardship.campaigns.models import Campaign

    from .runtime_models import SystemConfiguration

    current = SystemConfiguration.objects.values("current_campaign_id")
    campaign = Campaign.objects.filter(pk__in=current).first()
    if campaign is None:
        return None
    done = {"load"} if current_campaign_has_families() else set()
    load, task, progress = _load_state(campaign, done)
    load = load or "loaded"
    return {
        "load": load,
        "load_task": task,
        "stages": load_stages(load, progress[0] if progress else None),
        "progress": progress,
        "polled": True,
    }


def current_campaign_has_families():
    """Whether the current campaign has a Family, in one query.

    Load ParishSoft data's status poll asks it once its run has succeeded,
    so the poll calls the data loaded exactly when the page would
    (``completed``'s "load"); the run itself names no campaign.
    """
    from parishkit.stewardship.campaigns.credential_models import FamilyCampaign

    from .runtime_models import SystemConfiguration

    current = SystemConfiguration.objects.values("current_campaign_id")
    return FamilyCampaign.objects.filter(campaign_id__in=current).exists()


def _load_state(campaign, done):
    """The campaign's ParishSoft data while it has no Families.

    Returns (state, task, progress). ``done`` holds the completed step keys
    (``completed``), whose "load" says the campaign has its Families; then
    it is (None, None, None) and nothing more is read. Otherwise one small
    query reads the newest *full* ParishSoft refresh requested since the
    campaign was created (an earlier one could not load it; a quick
    refresh never counts, #1587; the full requests are a subquery, so it
    is one statement): "loading" while it is
    under way (requested and not finished), "failed" when it failed or was
    cancelled, so the step says so and offers Try again rather than looking
    as if no load was started (#1404 PR 5). With no full refresh yet, a
    campaign created less than ``LOAD_START_WAIT`` ago is "loading" with no
    task (the system is about to start its load, #1951), else "offer"; that
    check reads the database's clock, one more statement, so a step page
    runs one more statement before the campaign's first full refresh than
    after it (#1984). While loading or
    failed, ``task`` is that run's Background task id and ``progress`` its
    (phase, current, total), which Load ParishSoft data's stage boxes and
    status poll use. The step pages show the state at the top, and Load
    ParishSoft data draws its one state by it. A succeeded run is "empty":
    it loaded, but the campaign still has no Families.
    """
    from parishkit.stewardship.jobs.models import NONTERMINAL_STATES, TaskRun
    from parishkit.stewardship.source.refresh_models import SourceRefreshRequest
    from parishkit.stewardship.source.requests import TASK_TYPE

    if "load" in done:
        return None, None, None
    newest = (
        TaskRun.objects.filter(
            task_type=TASK_TYPE,
            created_at__gte=campaign.created_at,
            # Retries keep the request, so this names every run of a full one.
            domain_request_id__in=SourceRefreshRequest.objects.filter(
                kind="full"
            ).values("pk"),
        )
        .order_by("-created_at", "-pk")
        .values_list("pk", "state", "phase", "progress_current", "progress_total")
        .first()
    )
    if newest is None:
        from .admin_editing import database_now

        # The database's clock, the one that stamped created_at, so an app
        # clock skew never stretches or cuts the wait.
        if database_now() - campaign.created_at < LOAD_START_WAIT:
            return "loading", None, None
        return "offer", None, None
    task, state, *progress = newest
    if state in NONTERMINAL_STATES:
        return "loading", task, tuple(progress)
    if state in _UNFINISHED:
        return "failed", task, tuple(progress)
    return "empty", task, tuple(progress)


def for_request(match, campaign, configuration):
    """The stepper for the page ``match`` resolved, or None off the steps.

    The caller has already decided the stepper is ``shown``. Only the steps'
    own pages and their sub-pages (``SUBPAGES``) draw it, and Preview and
    test email only for the invitation's current revision, the one its step
    opens. Off those pages nothing is read, so other Admin pages pay nothing
    for it.
    """
    name = getattr(match, "url_name", None)
    if getattr(match, "namespace", None) != "admin":
        return None
    current = ROUTES.get(name) or _SAME_PAGE.get(name) or SUBPAGES.get(name)
    if current is None:
        return None
    financial = _financial(campaign)
    if current == "share_options" and not financial:
        return None
    revision = invitation_revision(configuration, campaign.pk)
    if current == "mail_test" and str(match.kwargs.get("revision_id")) != str(revision):
        return None
    sub = name in SUBPAGES
    no_schedules = suggest = False
    if current == "schedules" and not sub:
        from parishkit.stewardship.campaigns.domain import CampaignState
        from parishkit.stewardship.campaigns.lifecycle import structural_edit_admitted

        from .content_views import _records
        from .schedule_reads import campaign_schedules
        from .schedule_suggestions import entries

        no_schedules = not campaign_schedules(configuration, campaign.pk)
        # The same set the page lists and its save adds (#1404 PR 6); read
        # from the applied document already in hand, so no query.
        # Only while the campaign's dates may still change, as the page
        # draws the panel and its form only then (``schedule_views``). Work
        # holding the campaign needs no check here: the page itself is then
        # refused as stale (``schedule_state``), so no action row is drawn.
        suggest = (
            no_schedules
            and campaign.pk == configuration.current_campaign_id
            and structural_edit_admitted(
                CampaignState(campaign.state),
                ever_active=campaign.ever_active,
                locked=campaign.structural_locked,
            )
            and bool(
                entries(
                    campaign.active_configuration.values,
                    _records(configuration, campaign.pk),
                    timezone.now(),
                )
            )
        )
    read = progress(campaign, revision)
    done = set(read.done)
    load, load_task, load_progress = _load_state(campaign, done)
    families = None
    if current == "load" and load is None and not sub:
        # Load ParishSoft data says how many Families the load brought: one
        # count, on that step only, so no other page pays for it.
        from parishkit.stewardship.campaigns.credential_models import FamilyCampaign

        families = FamilyCampaign.objects.filter(campaign_id=campaign.pk).count()
    wizard = build(
        financial,
        current,
        done=done,
        mail_test_url=_test_url(revision),
        sub=sub,
        no_schedules=no_schedules,
        load=load,
        load_task=load_task,
        load_progress=load_progress,
        families=families,
        direct=name in DIRECT_SUBPAGES if sub else current in DIRECT_STEPS,
        suggest=suggest,
    )
    if current == "go_live" and not sub:
        # Go live's Check step lists what each earlier step holds (#1404
        # PR 7), from the read above and the applied document: no query.
        content, schedules = _summary_facts(configuration, campaign, read.items)
        wizard = replace(
            wizard,
            summary=summary_lines(
                wizard.steps, read, content=content, schedules=schedules
            ),
        )
    return wizard


# ``current_wizard``'s default: the caller has not read the revision.
_UNREAD = object()


def current_wizard(configuration, *, revision=_UNREAD):
    """The stepper over the current campaign, opened on no step, or None.

    None unless the stepper is shown (``shown``, read here: an
    Administrator is assumed, as the caller already checked). Home's
    Continue campaign setup and the step Continue read it (#1404).
    ``revision`` is the invitation's current revision when the caller has
    already read it in the same snapshot (Home's dashboard does, for its
    test email step), so it is not read twice; left out, it is read here.
    """
    from parishkit.stewardship.campaigns.credential_models import (
        CampaignCredentialState,
    )

    campaign = configuration.current_campaign if configuration else None
    go_live = bool(
        campaign
        and CampaignCredentialState.objects.filter(
            campaign=campaign, go_live_gate=True
        ).exists()
    )
    if not shown(
        campaign, configuration and configuration.mode, admin=True, go_live=go_live
    ):
        return None
    if revision is _UNREAD:
        revision = invitation_revision(configuration, campaign.pk)
    return build(
        _financial(campaign),
        None,
        done=completed(campaign, revision),
        mail_test_url=_test_url(revision),
    )


def _system():
    """The runtime row with its current campaign and that campaign's values."""
    from .runtime_models import SystemConfiguration

    return SystemConfiguration.objects.select_related(
        "current_campaign__active_configuration", "active_configuration__parish"
    ).first()


def current():
    """The stepper over the current campaign, read now, or None (``current_wizard``)."""
    return current_wizard(_system())


def stale_item():
    """The refusal of Keep as is for an item Pages and emails no longer lists.

    The editor was drawn for a saved text that has since been saved again
    or removed (another tab, another Administrator), so the mark would name
    text nobody reviewed (#1404 PR 6b).
    """
    from parishkit.stewardship.web.refusals import UserFacingStale

    return UserFacingStale(
        _(
            "That page or email has changed since this page was opened, so "
            "it was not marked."
        ),
        fix=_("Return to Pages and emails, then open it again."),
        reason="stale_page",
    )


def record_review(store, actor_id, step, item=""):
    """Record ``step`` (or one of its items) kept as is; return where to go.

    The Administrator pressed Continue to <next step> on an unchanged step
    page (#1404), or Keep as is and go back in Pages and emails' editor
    (``item``, a ``campaign_wizard_models.item_key``; #1404 PR 6b). Under
    the shared work order it rechecks the Administrator and that the
    stepper still applies (a Testing draft, no go-live), and that an
    ``item`` is one the Pages and emails list shows now (``stale_item``
    otherwise), then stores one ``CampaignSetupReview`` with its
    ``campaign_setup_step_reviewed`` audit event, which SQL requires
    (schema/migrations/0105_campaign_setup_review.sql). A step or item
    already kept as is records nothing again. It returns the step Continue
    opens next, or for an item its own step (Pages and emails), which the
    editor returns to. An item mark never completes the step: only the
    list's own Continue does. Raises PermissionError, ValueError or
    ``stale_item`` when it may not record; nothing is changed then.
    """
    from parishkit.stewardship.audit.schemas import Action, ActorKind, Outcome
    from parishkit.stewardship.audit.services import record_action
    from parishkit.stewardship.campaigns.work_locks import work_transaction

    from .policy import Capability, allows, current_principal

    if step not in REVIEW_STEPS:
        raise ValueError("Unknown Campaign setup step.")
    if item and step != "content":
        raise ValueError("Only Pages and emails items are kept one by one.")
    with work_transaction():
        if not allows(current_principal(store, actor_id), Capability.CONFIGURE):
            raise PermissionError("Campaign setup is unavailable.")
        configuration = _system()
        if current_wizard(configuration) is None:
            raise ValueError("Campaign setup is not in progress.")
        campaign = configuration.current_campaign
        if step == "share_options" and not _financial(campaign):
            raise ValueError("Share options do not apply to this campaign.")
        if item:
            from .content_views import catalog_items

            if item not in catalog_items(configuration, campaign):
                raise stale_item()
        kept = CampaignSetupReview.objects.filter(
            campaign_id=campaign.pk, step=step, item=item
        )
        if not kept.exists():
            review = CampaignSetupReview.objects.create(
                campaign=campaign, step=step, item=item, actor_id=actor_id
            )
            record_action(
                Action.CAMPAIGN_SETUP_STEP_REVIEWED,
                actor_kind=ActorKind.PORTAL_USER,
                actor_id=actor_id,
                subject_id=review.pk,
                parish_id=configuration.active_configuration.parish.pk,
                campaign_id=campaign.pk,
                context={"outcome": Outcome.SUCCEEDED},
            )
        revision = invitation_revision(configuration, campaign.pk)
        wizard = build(
            _financial(campaign),
            step,
            done=completed(campaign, revision),
            mail_test_url=_test_url(revision),
        )
        return wizard.current if item else wizard.upcoming


def setup_ended():
    """The refusal of a direct save once the campaign wizard no longer applies.

    The page was drawn while the campaign was a Testing draft with no
    go-live started; since then it was scheduled, went live, or a go-live
    started, so the change needs the page's ordinary Save and review.
    """
    from parishkit.stewardship.web.refusals import UserFacingStale

    return UserFacingStale(
        _("Campaign setup has ended, so nothing was saved."),
        fix=_("Reload the page, then make your change again and review it."),
        reason="stale_page",
    )


def _status_redirect(request, request_id):
    """The redirect to one change's Change status, which the page follows."""
    from django.http import HttpResponseRedirect

    from . import admin_navigation

    admin_navigation.remember_origin(request, request_id)
    return HttpResponseRedirect(
        reverse("admin:configuration_request", args=[request_id])
    )


def repeated_save(request, actor, key):
    """The redirect a repeated direct save answers with, or None (#1404 PR 4).

    A save already recorded with this save's request ``key`` (a double
    click, or a retry after its answer was lost, even once it is applied
    and the page's version is no longer the current one) is answered with
    that change's status before the form is checked again, as a repeated
    Apply is, so it is never refused as stale nor recorded twice. The key
    (``admin_editing.direct_key``) comes from the page's key and the posted
    values, so a different change from the same page never matches here and
    is checked, and recorded or refused, as a save of its own. One indexed
    query on the actor and key.
    """
    from .request_models import ConfigurationChangeRequest

    existing = (
        ConfigurationChangeRequest.objects.filter(
            actor_id=actor.identity, request_key=key
        )
        .values_list("pk", flat=True)
        .first()
    )
    return None if existing is None else _status_redirect(request, existing)


def save_step(request, service, actor, *, token, salt, current_scope, campaign_id):
    """Record a campaign wizard step's direct save (#1404 PR 4); its status.

    ``token`` is the step's own Review, signed by this same request with the
    page's request key (``admin_editing.direct_key``) and never shown: a
    direct save is a confirmed Review without the dialog (ui-conventions,
    "Wizards", rule 5). ``confirm_intent`` keeps every check a confirmation
    makes (the Administrator and capability, the version and snapshot under
    the work lock, the request key's one receipt, no added campaign) and
    records the same audited configuration request. Under the same lock,
    ``current_scope`` is wrapped so the save is refused (``setup_ended``)
    unless the stepper still applies to the page's campaign
    (``campaign_id``). Returns the redirect to the change's Change status,
    which the page follows in place (ui-v1.js, form[data-direct-save]).
    """
    from .admin_editing import confirm_intent

    def scope(service):
        """``current_scope``, refused once the wizard no longer applies."""
        configuration = _system()
        if current_wizard(configuration) is None or str(
            configuration.current_campaign_id
        ) != str(campaign_id):
            raise setup_ended()
        return current_scope(service)

    receipt = confirm_intent(
        request,
        service,
        actor,
        token=token,
        salt=salt,
        link=request.path,
        current_scope=scope,
        direct=True,
    )
    return _status_redirect(request, receipt.request_id)


@dataclass(frozen=True)
class StatusLink:
    """Change status's way back into the stepper once a step's change applied.

    ``back`` is True for a sub-page's change (Return to <step>), False for
    the step's own (Continue to the next step).
    """

    url: str
    label: object
    back: bool


def continue_for(receipt):
    """Where Change status leads once a step's change is applied, or None.

    Change status offers it once the change is applied, while the stepper is
    shown (Change status itself already required the Administrator
    capability).
    A change made on a sub-page (an email's editor, a mail schedule) leads
    Return to its step (#1404); any other step change leads on to the next
    step not done yet. The change that created the campaign is not a step's
    change (it goes straight on to the next step instead), so it gets none.
    """
    from .request_models import ConfigurationChangeRequest

    if receipt.state != "applied":
        return None
    configuration = _system()
    wizard = current_wizard(configuration)
    if wizard is None:
        return None
    campaign = configuration.current_campaign
    patch = (
        ConfigurationChangeRequest.objects.filter(pk=receipt.request_id)
        .values_list("patch", flat=True)
        .first()
    )
    keys = step_keys(patch, campaign.pk)
    if not keys:
        return None
    entries = patch if isinstance(patch, list) else []
    item_change = any(
        isinstance(item, dict) and item.get("section") == "schedules"
        for item in entries
    )
    for key in ("content", "schedules"):
        step = wizard.step(key)
        if key in keys and (key == "content" or item_change) and step:
            return StatusLink(step.url, step.label, back=True)
    order = [page.key for page in PAGES]
    revision = invitation_revision(configuration, campaign.pk)
    upcoming = build(
        _financial(campaign),
        max(keys, key=order.index),
        done=completed(campaign, revision),
        mail_test_url=_test_url(revision),
    ).upcoming
    if upcoming is None or not upcoming.url:
        return None
    return StatusLink(upcoming.url, upcoming.label, back=False)

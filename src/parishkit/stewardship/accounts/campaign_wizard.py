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
invitation's current revision. Go-live readiness completes only by going
live, and the stepper is no longer shown then.

The stepper's summary names the next step that is not done yet and offers
Leave campaign setup; the action row at the bottom of each step page
(campaign-step-actions.html) holds Back and the one primary Continue. The
sub-pages a step opens (an email's editor, a new or edited mail schedule,
the Campaign calendar, and Campaign settings' content history) draw the
same stepper with their step current and a Return to <step> link instead
(``SUBPAGES``).
"""

from dataclasses import dataclass
from uuid import uuid4

from django.urls import reverse
from django.utils.translation import gettext_lazy as _

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


@dataclass(frozen=True)
class CampaignWizard(Wizard):
    """The Campaign setup stepper for one page, beyond the setup wizard's.

    ``upcoming`` is where Continue goes: the next step after the current
    one that is not done yet, or simply the next one when every later step
    is done. ``sub`` marks a sub-page of the current step. ``no_schedules``
    marks Dates and mail schedules with no schedule saved, whose Continue
    says it goes on without one. ``load`` is the campaign's ParishSoft
    data while it has no Families: "offer" (with ``refresh_key``, the
    button's request key), "loading" once a refresh is under way, or
    "failed" when the newest refresh did not finish, whose button then
    reads Try again. ``load_task`` is that newest refresh's Background task
    while it is loading or failed, and ``load_progress`` its (phase,
    current, total), for Load ParishSoft data's stage boxes. ``families``
    is the campaign's Family count, read only on that step once loaded.
    ``creating`` marks step 1 before the campaign exists (``creation``):
    Create the campaign draws its own action row inside its form, so the
    shared row draws nothing.
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

    @property
    def reviewable(self):
        """Whether the current step's Continue records it as kept as is."""
        return self.current is not None and self.current.key in REVIEW_STEPS

    @property
    def load_view(self):
        """Load ParishSoft data's one state: offer, loading, failed or loaded."""
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
    load_task=None,
    load_progress=None,
    families=None,
):
    """Derive the stepper for the page ``current`` (a step key) as a ``Wizard``.

    A pure function of what the database read found, so the order, the
    applicable steps and their states are unit-testable without one.
    ``financial`` decides whether Share options applies; ``done`` holds the
    completed step keys; ``mail_test_url`` is Preview and test email for the
    invitation's current revision, or None when there is none to test. Every
    other step is always a link: Back and Continue never save or discard
    anything on the page, and each page keeps its own prerequisites.
    ``sub``, ``no_schedules`` and the ``load`` facts (``load_task``,
    ``load_progress``, ``families``) are carried through
    (``CampaignWizard``).
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
    )


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


def completed(campaign, revision):
    """The step keys completed for ``campaign``, read from what was applied.

    ``revision`` is the invitation's current revision (or None). Applied
    changes since the campaign's creation, and the steps kept as is
    (``CampaignSetupReview``, the whole step only), complete the page
    steps; the campaign's Families complete Load ParishSoft data, and an
    accepted test of ``revision`` completes Preview and test email. A
    stored review is never undone by a later change.
    It reads every patch applied since the creation: only the step pages,
    an applied step change's status and an Administrator's Home call it,
    while a Testing draft is being prepared, whose history is a few dozen
    changes, in a constant number of queries.
    """
    from django.db.models import CharField, Value

    from parishkit.stewardship.campaigns.credential_models import FamilyCampaign

    from .campaign_mail_models import CampaignMailTest
    from .runtime_models import ConfigurationActivation

    done = {"settings"}
    patches = ConfigurationActivation.objects.filter(
        sequence__gt=_creation_sequence(campaign.pk), request__isnull=False
    ).values_list("request__patch", flat=True)
    for patch in patches:
        done |= step_keys(patch, campaign.pk)
    # The steps kept as is, and "load" once the campaign has a Family (one
    # row is enough), in one UNION statement: Home's query budget is fixed.
    loaded = (
        FamilyCampaign.objects.filter(campaign_id=campaign.pk)
        .annotate(step=Value("load", output_field=CharField()))
        .order_by()
        .values_list("step", flat=True)[:1]
    )
    done |= set(
        CampaignSetupReview.objects.filter(campaign_id=campaign.pk, item="")
        .order_by()
        .values_list("step", flat=True)
        .union(loaded)
    )
    if (
        revision is not None
        and CampaignMailTest.objects.filter(
            campaign_id=campaign.pk, template__record_id=revision, state="accepted"
        ).exists()
    ):
        done.add("mail_test")
    return done


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

# The major stages of a full ParishSoft refresh, in the order its worker
# reports them (source/refreshing.py, then source/execution.py's promotion),
# as Load ParishSoft data's stage boxes name them (#1404 PR 5).
LOAD_STAGES = (
    ("fetching", _("Download from ParishSoft")),
    ("staging", _("Save the downloaded records")),
    ("validating", _("Check the new data")),
    ("promoting", _("Make the new data current")),
)


def load_stages(load, phase):
    """The stage boxes for the load in state ``load`` at its run's ``phase``.

    Not loaded yet ("offer"): every stage waiting. Loaded: every stage done.
    Loading: the stages before the run's phase done and its phase in
    progress (none yet while it is queued). Failed: the stage the run
    stopped in is marked failed, the first one when it stopped before any.
    """
    from . import stages

    keys = [key for key, _label in LOAD_STAGES]
    index = keys.index(phase) if phase in keys else -1
    if load == "loaded":
        return stages.rows(LOAD_STAGES, len(keys))
    if load == "loading":
        return stages.rows(LOAD_STAGES, index)
    if load == "failed":
        return stages.rows(LOAD_STAGES, max(index, 0), "failed")
    return stages.rows(LOAD_STAGES, -1)


def load_status(task):
    """Load ParishSoft data's live status for one refresh run's ``task``.

    ``task`` is the run as the Background task read names it (``_task_read``).
    The passive status fragment (``?view=load``) draws the step's live region
    from it while the run is watched: loading while it is under way, loaded
    once it succeeded and failed once it failed or was cancelled. The
    settled answer's follow-up link then re-reads the page in place, which
    draws the step's full state (the Family count, Continue, Try again).
    """
    from parishkit.stewardship.jobs.models import NONTERMINAL_STATES

    state = task["state"]
    load = (
        "loading"
        if state in NONTERMINAL_STATES
        else "failed"
        if state in _UNFINISHED
        else "loaded"
    )
    progress = task["progress"]
    return {
        "load": load,
        "load_task": task["id"],
        "stages": load_stages(load, progress["phase"]),
        "progress": (progress["phase"], progress["current"], progress["total"]),
        "polled": True,
    }


def _load_state(campaign, done):
    """The campaign's ParishSoft data while it has no Families.

    Returns (state, task, progress). ``done`` holds the completed step keys
    (``completed``), whose "load" says the campaign has its Families; then
    it is (None, None, None) and nothing more is read. Otherwise one small
    query reads the newest ParishSoft refresh requested since the campaign
    was created (an earlier one could not load it): "loading" while it is
    under way (requested and not finished), "failed" when it failed or was
    cancelled, so the step says so and offers Try again rather than looking
    as if no load was started (#1404 PR 5), else "offer". While loading or
    failed, ``task`` is that run's Background task id and ``progress`` its
    (phase, current, total), which Load ParishSoft data's stage boxes and
    status poll use. The step pages show the state at the top, and Load
    ParishSoft data draws its one state by it.
    """
    from parishkit.stewardship.jobs.models import NONTERMINAL_STATES, TaskRun
    from parishkit.stewardship.source.requests import TASK_TYPE

    if "load" in done:
        return None, None, None
    newest = (
        TaskRun.objects.filter(task_type=TASK_TYPE, created_at__gte=campaign.created_at)
        .order_by("-created_at", "-pk")
        .values_list("pk", "state", "phase", "progress_current", "progress_total")
        .first()
    )
    if newest is None:
        return "offer", None, None
    task, state, *progress = newest
    if state in NONTERMINAL_STATES:
        return "loading", task, tuple(progress)
    if state in _UNFINISHED:
        return "failed", task, tuple(progress)
    return "offer", None, None


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
    no_schedules = False
    if current == "schedules" and not sub:
        from .schedule_reads import campaign_schedules

        no_schedules = not campaign_schedules(configuration, campaign.pk)
    done = completed(campaign, revision)
    load, load_task, load_progress = _load_state(campaign, done)
    families = None
    if current == "load" and load is None and not sub:
        # Load ParishSoft data says how many Families the load brought: one
        # count, on that step only, so no other page pays for it.
        from parishkit.stewardship.campaigns.credential_models import FamilyCampaign

        families = FamilyCampaign.objects.filter(campaign_id=campaign.pk).count()
    return build(
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
    )


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


def record_review(store, actor_id, step):
    """Record ``step`` kept as is; return the step Continue opens next.

    The Administrator pressed Continue to <next step> on an unchanged step
    page (#1404). Under the shared work order it rechecks the Administrator
    and that the stepper still applies (a Testing draft, no go-live), then
    stores one ``CampaignSetupReview`` with its
    ``campaign_setup_step_reviewed`` audit event, which SQL requires
    (schema/migrations/0105_campaign_setup_review.sql). A step already kept
    as is records nothing again. Raises PermissionError or ValueError when
    it may not record; nothing is changed then.
    """
    from parishkit.stewardship.audit.schemas import Action, ActorKind, Outcome
    from parishkit.stewardship.audit.services import record_action
    from parishkit.stewardship.campaigns.work_locks import work_transaction

    from .policy import Capability, allows, current_principal

    if step not in REVIEW_STEPS:
        raise ValueError("Unknown Campaign setup step.")
    with work_transaction():
        if not allows(current_principal(store, actor_id), Capability.CONFIGURE):
            raise PermissionError("Campaign setup is unavailable.")
        configuration = _system()
        if current_wizard(configuration) is None:
            raise ValueError("Campaign setup is not in progress.")
        campaign = configuration.current_campaign
        if step == "share_options" and not _financial(campaign):
            raise ValueError("Share options do not apply to this campaign.")
        kept = CampaignSetupReview.objects.filter(
            campaign_id=campaign.pk, step=step, item=""
        )
        if not kept.exists():
            review = CampaignSetupReview.objects.create(
                campaign=campaign, step=step, actor_id=actor_id
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
        return build(
            _financial(campaign),
            step,
            done=completed(campaign, revision),
            mail_test_url=_test_url(revision),
        ).upcoming


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

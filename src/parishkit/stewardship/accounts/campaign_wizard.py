"""The Campaign setup stepper over the existing campaign pages (#142 slice 4).

While the current campaign is a draft in Testing mode, the Campaign setup
pages show a stepper with the setup wizard's look (admin-portal spec,
"Create the campaign", Guided steps). Each step is the existing settings
page, so every save stays that page's own review, confirm and apply; the
stepper only shows where the Administrator is, what is completed, and links
Back and Next to the neighbouring pages. It grants nothing: each page still
performs every access, state and prerequisite check of its own.

Completion is read, never stored. Campaign settings is completed when the
campaign is created. Share options, Pages and emails, Dates and mail
schedules and Campaign images are completed once a change confirmed on that
page has been applied since the campaign was created, which ``step_keys``
recognizes from each applied request's patch (defaults written by Create
the campaign never count, because they arrive in the creation request).
Preview and test email is completed by an accepted test of the initial
invitation's current revision. Go-live readiness completes only by going
live, and the stepper is no longer shown then.
"""

from dataclasses import dataclass

from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from .admin_navigation import PAGES as NAMES
from .setup_wizard import Step, Wizard


@dataclass(frozen=True)
class Page:
    """One step: its key and the Admin URL name of the page it opens."""

    key: str
    route: str

    @property
    def label(self):
        """The page's own name, as the menu and its heading use it."""
        return NAMES[self.route].label


# The order a campaign is prepared in. Ministries and funds are part of
# Campaign settings' one form (#1119), so they are not a step of their own.
PAGES = (
    Page("settings", "campaign_settings"),
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


def build(financial, current, *, done, mail_test_url):
    """Derive the stepper for the page ``current`` (a step key) as a ``Wizard``.

    A pure function of what the database read found, so the order, the
    applicable steps and their states are unit-testable without one.
    ``financial`` decides whether Share options applies; ``done`` holds the
    completed step keys; ``mail_test_url`` is Preview and test email for the
    invitation's current revision, or None when there is none to test. Every
    other step is always a link: Next and Back are plain links that never
    save or discard anything, and each page keeps its own prerequisites.
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
    return Wizard(
        steps=tuple(steps),
        previous=before[-1] if before else None,
        next=after[0] if after else None,
        completed=sum(step.state == "done" for step in steps),
        total=len(steps),
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
    not scan every patch.
    """
    from .runtime_models import ConfigurationActivation

    return (
        ConfigurationActivation.objects.filter(
            request__patch__contains=[
                {"operation": "add", "section": "campaigns", "id": str(campaign_id)}
            ]
        )
        .values_list("sequence", flat=True)
        .first()
        or 0
    )


def completed(campaign, revision):
    """The step keys completed for ``campaign``, read from what was applied.

    ``revision`` is the invitation's current revision (or None). Applied
    changes since the campaign's creation complete the page steps; an
    accepted test of ``revision`` completes Preview and test email.
    It reads every patch applied since the creation: only the step pages and
    an applied step change's status call it, while a Testing draft is being
    prepared, whose history is a few dozen changes, in a constant number of
    queries.
    """
    from .campaign_mail_models import CampaignMailTest
    from .runtime_models import ConfigurationActivation

    done = {"settings"}
    patches = ConfigurationActivation.objects.filter(
        sequence__gt=_creation_sequence(campaign.pk), request__isnull=False
    ).values_list("request__patch", flat=True)
    for patch in patches:
        done |= step_keys(patch, campaign.pk)
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


def for_request(match, campaign, configuration):
    """The stepper for the page ``match`` resolved, or None off the steps.

    The caller has already decided the stepper is ``shown``. Only the steps'
    own pages draw it, and Preview and test email only for the invitation's
    current revision, the one its step opens. Off those pages nothing is
    read, so other Admin pages pay nothing for it.
    """
    current = ROUTES.get(getattr(match, "url_name", None))
    if current is None or getattr(match, "namespace", None) != "admin":
        return None
    financial = _financial(campaign)
    if current == "share_options" and not financial:
        return None
    revision = invitation_revision(configuration, campaign.pk)
    if current == "mail_test" and str(match.kwargs.get("revision_id")) != str(revision):
        return None
    return build(
        financial,
        current,
        done=completed(campaign, revision),
        mail_test_url=_test_url(revision),
    )


def continue_for(receipt):
    """The step after the one an applied change completed, or None.

    Change status (and an in-place settings page's status, which reads it)
    offers it as "Continue to the next step" once the change is applied,
    while the stepper is shown (Change status itself already required the
    Administrator capability). The change that created the campaign is not
    a step's change (its status offers loading the campaign's ParishSoft
    data instead), so it gets none.
    """
    from parishkit.stewardship.campaigns.credential_models import (
        CampaignCredentialState,
    )

    from .request_models import ConfigurationChangeRequest
    from .runtime_models import SystemConfiguration

    if receipt.state != "applied":
        return None
    configuration = SystemConfiguration.objects.select_related(
        "current_campaign__active_configuration"
    ).first()
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
    patch = (
        ConfigurationChangeRequest.objects.filter(pk=receipt.request_id)
        .values_list("patch", flat=True)
        .first()
    )
    keys = step_keys(patch, campaign.pk)
    if not keys:
        return None
    order = [page.key for page in PAGES]
    revision = invitation_revision(configuration, campaign.pk)
    wizard = build(
        _financial(campaign),
        max(keys, key=order.index),
        done=completed(campaign, revision),
        mail_test_url=_test_url(revision),
    )
    return wizard.next if wizard.next and wizard.next.url else None

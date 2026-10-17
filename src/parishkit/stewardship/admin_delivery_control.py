"""``send controls``: the Pause and resume mail page's read (ADM-11 PR 7b).

Pause and resume mail (``admin:delivery_control``) shows the current
campaign's live mail as counts, whether delivery is paused and why, the
provider and sender check a resume needs, and what can be paused, resumed or
resolved now. The command reads it through the page's own request-free
status (``delivery_control_commands.controls``), so the page and the command
line cannot drift.

It admits passively with the page's ``CONFIGURE`` capability (so
Administrators only, and a read-only session reads), reads in one read
transaction, rechecks the session before reporting, and records nothing, as
the page's GET records nothing. Everything it prints is a count, a time or a
choice, never a recipient, a message or a provider response; the one text is
the pausing Administrator's own reason, which the page shows too. The
page's fresh sign-in hint is browser chrome and is left out, as are the
fingerprints a preview binds.

The page's controls follow, in their own block below: ``send pause-preview``,
``send resume-preview`` and ``send resolve-preview`` build the page's own
previews and ``send confirm`` confirms one, through the page's own
functions (see "The controls").
"""

from dataclasses import dataclass
from uuid import UUID

from .admin_reads import NotAvailable, ReadModel, _held, _recheck
from .admin_settings import _read

# The page's per-type inventory counts, and its recovery impacts, by name.
TYPE_COUNTS = ("queued", "held", "submitting", "unknown")
FAMILY_RECOVERY = (
    "selected",
    "coalesced",
    "skipped",
    "blocked",
    "deferred",
    "unmaterialized",
)
DIGEST_RECOVERY = ("selected", "slots", "unmaterialized", "prepared", "blocked")


@dataclass(frozen=True)
class DeliveryControls(ReadModel):
    """The current campaign's live mail, as Pause and resume mail shows it.

    ``available`` is the page's own check (a current Production campaign
    that is scheduled, active or closed). The page draws its controls only
    then, so ``pause_available`` (not paused: the page's Preview pause),
    ``resume_available``, ``resolve_available``, ``resolution`` (the
    Resolve held messages choices) and the recovery impacts are false or
    None otherwise, as is everything the page draws only while paused.
    ``paused``, ``paused_at``, ``reason``, ``inventory``, ``next_due`` and
    ``health`` are facts, printed whatever ``available`` says; ``paused_at``
    and ``reason`` are None unless ``paused``. ``inventory`` is the live
    mail by state, with ``types`` by mail type; ``next_due`` the next
    scheduled send; ``health`` the provider and sender check;
    ``report_recovery`` is the page's overdue Admin digests.
    """

    campaign_id: UUID
    available: bool
    paused: bool
    paused_at: str | None
    reason: str | None
    inventory: dict
    next_due: dict
    health: dict
    pause_available: bool
    resume_available: bool
    resolve_available: bool
    resolution: dict | None
    family_recovery: dict | None
    report_recovery: dict | None


def _pick(values, names):
    """Only the named members of one of the page's count documents, or None."""
    return None if values is None else {name: values[name] for name in names}


def _kinds(by_kind, values):
    """A mapping keyed by mail type as a list, the type a ``kind`` value.

    Mail types (``daily_digest``, ``initial``…) are data, not member
    names, so they never meet the member-name rules; the list is in type
    order. ``values(item)`` gives each entry's other members.
    """
    return [{"kind": kind, **values(item)} for kind, item in sorted(by_kind.items())]


def _resolution(status):
    """The Resolve held messages choices, or None where the page shows none.

    The page draws them only for a paused, closed Production campaign
    (``available`` and ``resolve_available``); elsewhere ``clear`` would
    read as offered with nothing to clear. Each type's label is a lazy
    translation, printed as text under ``name``.
    """
    if not (status["available"] and status["resolve_available"]):
        return None
    choices = status["resolution"]
    return {
        "types": [
            {
                "kind": item["kind"],
                "name": str(item["label"]),
                "held": item["held"],
                "cancellable": item["cancellable"],
            }
            for item in choices["types"]
        ],
        **{name: choices[name] for name in ("release", "cancel", "clear")},
    }


def controls_model(campaign, status):
    """``send controls``'s document from the page's ``controls`` status.

    The SQL views' documents are projected member by member, so their
    internal fingerprints (which only a preview binds) never print. The
    digest impact prints as ``report_recovery``: member names never say
    "digest", which names configuration versions elsewhere.
    """
    from .admin_report_reads import _instant

    current, available = status["inventory"], status["available"]
    paused = campaign.delivery_paused
    due = status["next_due"]
    # Drawn only when the page offers its controls, and only while paused.
    recovery = available and paused
    return DeliveryControls(
        campaign_id=campaign.pk,
        available=available,
        paused=paused,
        paused_at=_instant(campaign.paused_at) if paused else None,
        reason=campaign.pause_reason if paused else None,
        inventory={
            **_pick(current, (*TYPE_COUNTS, "stranded")),
            "types": _kinds(current["types"], lambda item: _pick(item, TYPE_COUNTS)),
        },
        next_due={
            "at": due["at"],
            "count": due["count"],
            "types": _kinds(due["types"], lambda count: {"count": count}),
        },
        health=_pick(
            status["health"],
            ("ready", "required", "trouble", "checked_at", "expires_at"),
        ),
        pause_available=available and not paused,
        resume_available=available and status["resume_available"],
        resolve_available=available and status["resolve_available"],
        resolution=_resolution(status),
        family_recovery=_pick(
            status["family_recovery"] if recovery else None, FAMILY_RECOVERY
        ),
        report_recovery=_pick(
            status["digest_recovery"] if recovery else None, DIGEST_RECOVERY
        ),
    )


def read_controls(caller, service):
    """``send controls``: Pause and resume mail's status of the current campaign.

    No current campaign is ``not_available``, as the page's current-campaign
    wrapper finds nothing to show.
    """
    from .accounts.admin_editing import editable_configuration
    from .accounts.delivery_control_commands import controls
    from .campaigns.models import Campaign

    def read():
        """The page's campaign and status, without its fresh sign-in hint."""
        runtime = editable_configuration(service)
        if runtime.current_campaign_id is None:
            raise NotAvailable("There is no current campaign.")
        campaign = Campaign.objects.select_related("active_configuration").get(
            pk=runtime.current_campaign_id
        )
        return controls_model(campaign, controls(campaign, runtime))

    return _read(caller, service, read)


# ------------------------------------------------- the controls (PR 7b, #463)
#
# ``send pause-preview``, ``send resume-preview`` and ``send resolve-preview``
# are the page's Preview pause, Preview resume and the Resolve held messages
# form's Preview; ``send confirm`` is each preview's Confirm button. Each runs
# the page's own function in ``delivery_control_commands`` (``preview_pause``,
# ``preview_resume``, ``preview_resolution`` and ``confirm``) with the
# command's caller, so every check the page makes, and the SQL guard
# ``stewardship_delivery_control_guard_v1`` beneath it (including resume's
# refusal while any mail is still being sent or not sure it arrived), applies
# unchanged. They add no send path: a resume or a release only lets held mail
# go through the ordinary delivery workers, as the page's does.
#
# A preview admits as the page's preview post does (recording activity, so a
# full-scope session), records no event and prints the page's counts plus
# ``preview.token``, the page's own signed binding (five minutes), so a
# token from either channel confirms on the other. ``send confirm`` is
# fresh-gated on the page: a full-scope session stands in for the recent
# sign-in (``require_fresh``'s automation branch, which records
# ``automation_fresh_gate`` and the ``fresh_gated`` notice), and the
# command's ``admin_cmd_send_confirm`` is recorded in the page's own
# transaction through ``confirm``'s ``record`` hook. An exact repeat of a
# confirmed token returns the original control and records nothing.

# The page's Resolve held messages types (``RESOLVABLE_TYPES``), spelled out
# for the parser, which is built before Django is set up; a test keeps them
# equal.
RESOLVE_TYPES = ("daily_digest", "receipt", "weekly_digest")
# The Resolve held messages decisions ``_closed_selection`` accepts; a test
# keeps them equal.
RESOLVE_DECISIONS = ("release", "cancel", "clear")


@dataclass(frozen=True)
class ControlPreview(ReadModel):
    """A pause, resume or resolution preview, as the page's preview panel.

    ``action`` is ``pause``, ``resume`` or ``resolve``; ``reason`` the
    Administrator's own reason; ``expires_at`` when the token stops being
    accepted. ``inventory`` is the live mail the preview bound, as ``send
    controls`` prints it. A resume's ``plan`` is ``before_start`` (nothing
    is overdue yet: held mail keeps its time) or ``family`` (the overdue
    recovery, with ``family_recovery`` and ``report_recovery``, as ``send
    controls`` prints them). A resolution's ``resolution`` is its
    ``decision``, the selected ``types`` and, per type, the follow-up
    ``coverage`` the page lists. Members a preview does not have are null.
    The binding's fingerprints never print; ``preview.token`` carries them,
    signed, as the page's hidden form field does.
    """

    action: str
    campaign_id: UUID
    reason: str
    expires_at: str
    inventory: dict
    plan: str | None
    family_recovery: dict | None
    report_recovery: dict | None
    resolution: dict | None
    preview: dict


@dataclass(frozen=True)
class ControlConfirm(ReadModel):
    """``send confirm``: the recorded control and the page's status after it.

    ``created`` is false for an exact repeat of a token already confirmed,
    which records nothing new. ``command_id`` is the control's id (the
    token's binding key); ``status`` is ``send controls``'s document, read
    after the commit.
    """

    created: bool
    command_id: UUID
    action: str
    status: dict


def _inventory(current):
    """The bound inventory's counts, as ``send controls`` prints them."""
    return {
        **_pick(current, (*TYPE_COUNTS, "stranded")),
        "types": _kinds(current["types"], lambda item: _pick(item, TYPE_COUNTS)),
    }


def preview_model(binding, token=None):
    """``ControlPreview`` from the page's signed binding, without fingerprints.

    ``token`` is the binding as the page signed it; the prompt, which reads
    an existing token, passes none.
    """
    selection = binding["selection"]
    resumes = binding["action"] == "resume"
    family = resumes and selection.get("plan") == "family"
    resolution = None
    if binding["action"] == "resolve":
        resolution = {
            "decision": selection["decision"],
            "types": list(selection["types"]),
            "coverage": _kinds(
                selection["coverage"],
                lambda item: _pick(item, ("messages", "items", "daily_ranges")),
            ),
        }
    return ControlPreview(
        action=binding["action"],
        campaign_id=UUID(binding["campaign"]),
        reason=binding["reason"],
        expires_at=binding["expires"],
        inventory=_inventory(binding["inventory"]),
        plan=selection.get("plan") if resumes else None,
        family_recovery=_pick(selection["family"], FAMILY_RECOVERY) if family else None,
        report_recovery=_pick(selection["digests"], DIGEST_RECOVERY)
        if family
        else None,
        resolution=resolution,
        preview={"token": token},
    )


def _page_refusals(function):
    """Run one of the page's functions, its refusals as the command's codes.

    As the page's view does: another installer finishing is a stale
    refusal (retry), and a campaign that vanished is not available. A
    tampered token is ``invalid``.
    """
    from django.core import signing
    from django.core.exceptions import ObjectDoesNotExist

    from .accounts.installation_lock import ConfigurationBusy
    from .storage import StaleRecordError

    try:
        return function()
    except ConfigurationBusy:
        raise StaleRecordError(
            "Another operation is finishing; retry delivery control."
        ) from None
    except ObjectDoesNotExist:
        raise NotAvailable("Delivery controls are unavailable.") from None
    except signing.BadSignature:
        raise ValueError("The preview token is not valid.") from None


def _current_campaign(service):
    """The current campaign's id, which the page's address names; or refuse."""
    from .accounts.admin_editing import editable_configuration
    from .campaigns.work_locks import read_transaction

    with read_transaction():
        campaign_id = editable_configuration(service).current_campaign_id
    if campaign_id is None:
        raise NotAvailable("There is no current campaign.")
    return campaign_id


def preview_control(caller, service, action, *, reason, decision=None, types=None):
    """``send pause-preview``, ``resume-preview`` or ``resolve-preview``.

    Admits as the page's preview post does (an ended session is exit 5,
    Staff ``denied``), then runs the page's own preview for the current
    campaign: a reason the page refuses is ``invalid``; a control the page
    does not offer now, a needed sender check, mail still being sent or not
    sure it arrived (resume), or a selection that does not fit the held
    mail (resolution) is ``stale_version``. Changes nothing and records no
    event, as the page's preview records none.
    """
    from .accounts import delivery_control_commands as commands
    from .admin_changes import _admit_change, _ended_or_raise

    actor = _admit_change(caller, service)

    def step():
        """The page's preview for the current campaign, in its transaction."""
        campaign_id = _current_campaign(service)
        if action == "pause":
            return commands.preview_pause(caller, service, campaign_id, reason=reason)
        if action == "resume":
            return commands.preview_resume(caller, service, campaign_id, reason=reason)
        return commands.preview_resolution(
            caller,
            service,
            campaign_id,
            reason=reason,
            decision=decision,
            types=types,
        )

    try:
        binding, token = _page_refusals(lambda: _held(step))
    except (PermissionError, NotAvailable) as error:
        _ended_or_raise(caller, service, actor, error)
    _recheck(caller, service.store, actor)
    return preview_model(binding, token)


def confirm_prompt_lines(token):
    """The confirmation prompt's summary, from the token's signed binding.

    The page's preview panel in words: what the confirmation does, the
    Administrator's reason, the counts it acts on and the panel's own
    caution (``CAUTIONS``, and for a resolution each type's coverage under
    the panel's ``COVERAGE_LABELS``), the same text the page draws; never a
    recipient or message. A token that is not one of the page's is ``invalid`` before
    the prompt; its expiry and inputs are checked when it is confirmed.
    """
    from .accounts import delivery_control_commands as commands

    binding = _page_refusals(lambda: commands._binding(token))
    model = preview_model(binding)
    counts = model.inventory
    reason = f"Reason: {model.reason}"
    if model.action == "pause":
        return (
            "Pause live Family email for the current campaign: every unsent "
            "live email is held when you confirm.",
            reason,
            f"Unsent messages to hold: {counts['queued']}. Still sending: "
            f"{counts['submitting']}. Not sure it arrived: {counts['unknown']}.",
            str(commands.CAUTIONS["pause"]),
        )
    if model.action == "resume":
        if model.plan == "before_start":
            impact = (
                "The campaign has not started, so nothing is overdue. Held "
                f"messages to release: {counts['held']}."
            )
        else:
            family, reports = model.family_recovery, model.report_recovery
            impact = (
                f"Overdue Family emails to send: {family['selected']}. "
                f"Duplicate overdue sends to skip: {family['coalesced']}. "
                f"Overdue sends that no longer apply: {family['skipped']}. "
                f"Admin digest reports to send: {reports['selected']}."
            )
        return (
            "Resume live Family email for the current campaign.",
            reason,
            impact,
            str(commands.CAUTIONS["resume"]),
        )
    resolution = model.resolution
    held = {item["kind"]: item["held"] for item in counts["types"]}
    lines = [
        f"Resolve held messages of the closed campaign: {resolution['decision']}"
        + (f" {', '.join(resolution['types'])}." if resolution["types"] else "."),
        reason,
        *(
            f"{kind}: {held.get(kind, 0)} held messages."
            for kind in resolution["types"]
        ),
    ]
    if counts["stranded"]:
        lines.append(
            "Held invitations and reminders to cancel because the campaign is "
            f"closed: {counts['stranded']}."
        )
    labels = commands.COVERAGE_LABELS
    lines += [
        f"{item['kind']}: {labels['items']}: {item['items']}; "
        f"{labels['daily_ranges']}: {item['daily_ranges']}."
        for item in resolution["coverage"]
    ]
    lines.append(str(commands.CAUTIONS["resolve"]))
    return tuple(lines)


def confirm_control(caller, service, *, token, context):
    """``send confirm``: the page's Confirm button for a reviewed control.

    Admits as the page does, then runs the page's own ``confirm`` for the
    current campaign: it passes the fresh gate in its own transaction
    (``automation_fresh_gate`` and the ``fresh_gated`` notice), rechecks the
    token's age (five minutes), the campaign and configuration versions,
    the live mail and the sender check, and the SQL guard checks them again
    on insert. Expired or out of date is ``stale_version``; altered
    ``invalid``; another Administrator's ``denied``. The command's
    ``admin_cmd_send_confirm`` is recorded with the control.
    ``context["committed"]`` is set once the control may have committed
    (exit 6 then).
    """
    from django.db import DatabaseError

    from .accounts import delivery_control_commands as commands
    from .admin_changes import _admit_change, _ended_or_raise
    from .admin_integration_actions import _command_event
    from .audit.schemas import Action
    from .observability import _guard_refusal

    actor = _admit_change(caller, service)
    campaign_id = _held(lambda: _current_campaign(service))
    recorded = []

    def record(command):
        """The command's own event, in the control's transaction."""
        _command_event(Action.ADMIN_CMD_SEND_CONFIRM, caller, actor)
        recorded.append(command.pk)

    try:
        command = _page_refusals(
            lambda: commands.confirm(
                caller, service, campaign_id, token=token, record=record
            )
        )
    except PermissionError as error:
        _ended_or_raise(caller, service, actor, error)
    except DatabaseError as error:
        # A failure at the commit itself may have committed.
        if recorded and not _guard_refusal(error):
            context["committed"] = True
        raise
    context["committed"] = True
    return ControlConfirm(
        created=bool(recorded),
        command_id=command.pk,
        action=command.action,
        status=read_controls(caller, service).to_document(),
    )

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
fingerprints a preview binds. The pause, resume and resolution previews and
their confirmation are still owed by PR 7b.
"""

from dataclasses import dataclass
from uuid import UUID

from .admin_reads import NotAvailable, ReadModel
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
        health=_pick(status["health"], ("ready", "checked_at", "expires_at")),
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

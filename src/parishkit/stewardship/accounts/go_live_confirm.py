"""The Go live page's Confirm Production step: its state (#1585 slice 4).

Once the Family links are prepared on current ParishSoft data (the Prepare
step's ``ready``), Confirm Production (admin-portal spec, "Go live page")
shows one impact preview and confirms in the shared review dialog, with the
typed ``Production`` word. The step is drawn from durable state on every
read, so a browser reload returns to it with its current status. Its state
is one of:

- ``ready``: the impact preview is built and readiness has no problem; the
  one primary opens the Confirm Production dialog.
- ``problems``: a readiness problem the step cannot fix by itself (another
  campaign went live, live mail is uncertain, an integration check is out
  of date); no primary.
- ``hold_ended``: the attempt's refresh hold ended before confirming;
  the primary is Prepare's Refresh and prepare again.
- ``closed``: the campaign ended while waiting; only Stop go-live is left.
- ``moved``: Prepare is no longer done (ParishSoft data changed, so new
  links are being prepared; or the links were never ready); the step leads
  to Prepare.
- ``stopped``, ``not_started`` and ``confirmed``: go-live is not in
  progress; the step leads to Check or Production activation.

Presentation only: it grants nothing. Stop go-live and Refresh and prepare
again are Prepare's own controls (``go_live_prepare``), posted to Prepare.
The Confirm Production dialog's Apply mints the SQL confirmation token
then, from a re-checked preview (``go_live_confirm_views``).
"""

from dataclasses import dataclass

from parishkit.stewardship.campaigns.confirmation_models import ProductionConfirmation
from parishkit.stewardship.campaigns.domain import Percentage
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.storage import StaleRecordError

from . import go_live_prepare
from .admin_editing import principal
from .confirmation_preview import ConfirmationPreview, collect_preview


@dataclass(frozen=True)
class Confirm:
    """The Confirm Production step as drawn now (see the module docstring).

    ``prepare`` is the Prepare step read it comes from: its request, its
    controls (Stop go-live, Refresh and prepare again) and the prepared
    links. ``preview`` is the impact preview (``ready`` and ``problems``).
    ``confirmed_by_other`` is set when another Administrator confirmed.
    """

    state: str
    prepare: go_live_prepare.Prepare
    preview: ConfirmationPreview | None = None
    confirmed_by_other: bool = False

    @property
    def request(self):
        """The go-live request, or None before any."""
        return self.prepare.request

    @property
    def started(self):
        """Whether a go-live is in progress."""
        return self.prepare.started

    @property
    def target(self):
        """ "active" or "scheduled": what confirming now would do."""
        return self.preview.readiness.target_state if self.preview else None

    @property
    def start_passed(self):
        """Whether the campaign start passed after go-live started (#462).

        The line "The campaign start time has passed, so it will become
        active at once." is then shown: going live now activates the
        campaign rather than scheduling it.
        """
        if self.preview is None:
            return False
        starts = self.preview.readiness.campaign.active_configuration.starts_at
        return self.request.created_at < starts <= self.preview.readiness.observed_at

    @property
    def reprepared(self):
        """Whether ParishSoft data changed and the links were prepared again."""
        return self.prepare.preparation > 1

    def counts(self):
        """The preview's headline counts for the page and the dialog."""
        families = self.preview.families.counts
        digests = self.preview.digests
        return {
            "families": families,
            "email_eligibility": Percentage(families.email_eligible, families.active),
            "no_email": Percentage(families.no_eligible_email, families.active),
            "report_messages": digests.daily_messages + digests.weekly_messages,
            "digests": digests,
        }


def read(request, service, campaign_id, *, preview=True):
    """The Confirm Production step of the current Testing draft, read now.

    Prepare's read decides where the go-live stands. Only once its links
    are ready is the impact preview built (``preview`` False skips it: the
    dialog's Apply builds its own, after the public web address check).
    The preview's readiness re-check runs under the attempt's freshness
    rule (``confirmation_readiness.go_live_source``).
    """
    shown = go_live_prepare.read(request, service, campaign_id)
    if shown.state == "confirmed":
        return Confirm(
            "confirmed", shown, confirmed_by_other=_by_other(request, service, shown)
        )
    if not shown.started:
        return Confirm(shown.state, shown)
    if shown.state == "closed":
        return Confirm("closed", shown)
    if shown.state == "again" and shown.reason == "hold_ended":
        return Confirm("hold_ended", shown)
    if shown.state != "ready":
        return Confirm("moved", shown)
    if not preview:
        return Confirm("ready", shown)
    try:
        built = collect_preview(
            request, service, campaign_id, shown.request.pk, shown.prepared.pk
        )
    except StaleRecordError:
        # The links stopped being current, or the mail impact moved, between
        # Prepare's read and the preview's: the next read settles which.
        return Confirm("moved", shown)
    problems = set(built.problems)
    if "campaign_closed" in problems:
        # Nothing to preview: the step says to stop, then review the dates.
        return Confirm("closed", shown)
    if problems:
        return Confirm("problems", shown, built)
    return Confirm("ready", shown, built)


def _by_other(request, service, shown):
    """Whether another Administrator than the viewer confirmed Production."""
    with work_transaction():
        actor = principal(request, service, passive=True)
        confirmed = (
            ProductionConfirmation.objects.filter(request_id=shown.request.pk)
            .values_list("actor_id", flat=True)
            .first()
        )
    return confirmed is not None and confirmed != actor.identity

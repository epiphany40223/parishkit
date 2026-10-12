"""The Family timeline export: one Family's timeline as a file (ADM-11 PR 8g).

The Family timeline page (``family_timeline_views``) shows an Administrator one
Family's identity and every line of its timeline. This export captures what
that page shows, through the page's own reads (``read_timeline`` and the
identity read), into an immutable ``TimelineExportSnapshot`` when it is
requested, and the shared export lifecycle renders it into a CSV, XLSX or PDF
file (``timeline_documents``). The capture is taken inside the campaign's
export lock, as the SQL captures of the other kinds are, so it cannot mix with
a lifecycle change.

The page builds its timeline in Python, not in a SQL report function, so the
web supplies the captured document. SQL checks everything around it (frozen
migration 0063): only the web, for a current Administrator, an admitted
campaign, the active configuration, a Family of that campaign, the document's
closed shape and its event count. The Family code is never captured; the
worker decrypts it into the file for a requester who may see codes.
"""

from uuid import UUID, uuid4

from parishkit.stewardship.accounts.policy import Capability
from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.audit.schemas import Action, Outcome
from parishkit.stewardship.campaigns.work_locks import export_transaction
from parishkit.stewardship.jobs.storage import enqueue
from parishkit.stewardship.schema_primitives import timezone_names

from .export_models import ExportRequest, TimelineExportSnapshot
from .export_services import (
    TASK_TYPE,
    ExportConflict,
    ExportRequestBound,
    admit_campaign,
    audit,
    authorize,
)

REPORT = "family_timeline"
MODES = ("production", "testing")


class RehearsalEnded(ExportConflict):
    """A Testing timeline export's rehearsal has ended, so it is not regenerated.

    The capture is bound to the rehearsal it was taken in; once that one is
    no longer the campaign's active rehearsal (a new one, or the return to
    Production), the Family's records it named are gone from the page, so
    the export is made again instead.
    """


def rehearsal_ended(request):
    """Whether ``request`` is a Testing timeline export whose rehearsal ended.

    The status page shows Regenerate unavailable with this reason, and a
    regeneration is refused with it, rather than a bare denial.
    """
    from .response_dashboard import rehearsal_epoch

    if request.report != REPORT or request.parameters.get("mode") != "testing":
        return False
    epoch = rehearsal_epoch(request.campaign_id)
    return epoch is None or str(epoch) != request.parameters.get("rehearsal_epoch_id")


def capture(campaign_id, family, mode, epoch, *, current_mode):
    """The page's full timeline of ``family``, as a JSON document.

    ``mode`` and ``epoch`` are the page's scope (Testing reads the campaign's
    active rehearsal); ``current_mode`` is the system mode now, which the page
    uses to attribute sign-ins. Instants are ISO 8601 with their offset; the
    words are the page's own, already translated. The code is left out.
    """
    from parishkit.stewardship.accounts.sessions import database_now
    from parishkit.stewardship.campaigns.models import Campaign

    from .family_timeline import read_timeline
    from .family_timeline_views import read_identity
    from .response_metrics import ResponseScope

    timeline = read_timeline(
        ResponseScope(campaign_id, mode, epoch),
        family.pk,
        full=True,
        current_mode=current_mode,
    )
    # The page's Reminders line (#861) needs the campaign's configuration.
    values = (
        Campaign.objects.select_related("active_configuration")
        .get(pk=campaign_id)
        .active_configuration.values
    )
    identity = read_identity(family, codes=False, workgroup=values)
    summary, engagement = timeline.summary, timeline.engagement
    email = summary.last_email

    def instant(value):
        """An aware instant as ISO 8601, or None."""
        return value.isoformat() if value is not None else None

    return {
        "family": {
            "id": str(family.pk),
            "duid": identity.duid,
            "name": identity.name,
            "envelope": identity.envelope,
            "reach": str(identity.reach_label),
            # The Reminder WorkGroup holding the Family now, or None: the
            # page's Reminders line.
            "reminder_workgroup": identity.reminder_workgroup,
        },
        "mode": mode,
        "as_of": database_now().isoformat(),
        # The page's Summary panel: submitted and when, the last email, the
        # furthest step and the last time the Family was seen on the form.
        "summary": {
            "submissions": summary.count,
            "first_submitted_at": instant(summary.first_at),
            "last_submitted_at": instant(summary.last_at),
            "last_email": {
                "name": str(email.name),
                "at": instant(email.at),
                "outcome": str(email.outcome),
            }
            if email is not None
            else None,
            "furthest_step": str(engagement.furthest_label)
            if engagement is not None and engagement.furthest_label
            else None,
            "furthest_at": instant(engagement.furthest_at)
            if engagement is not None
            else None,
            "last_seen_at": instant(engagement.last_seen_at)
            if engagement is not None
            else None,
        },
        "events": [
            {
                "at": event.at.isoformat(),
                "what": str(event.what),
                "detail": str(event.detail or ""),
            }
            for event in timeline.events
        ],
    }


def create_timeline_export(
    store,
    user_id,
    *,
    campaign_id,
    family_id,
    mode,
    format,
    browser_timezone,
    request_key,
    snapshot=None,
):
    """Capture one Family's timeline and queue its file, as the page's form does.

    Only an Administrator may request it, as only an Administrator sees the
    full timeline (and Testing records) on the page. A request key already
    used for the same selection returns that export; for another selection
    it is ``ExportRequestBound`` (the page's 409). ``snapshot`` is the
    retained capture an expired export is regenerated from, never accepted
    from a form.
    """
    from parishkit.stewardship.campaigns.credential_models import FamilyCampaign

    from .response_dashboard import rehearsal_epoch

    if any(
        not isinstance(value, UUID)
        for value in (user_id, campaign_id, family_id, request_key)
    ):
        raise ValueError("Export identities must be canonical UUIDs.")
    if mode not in MODES:
        raise ValueError("Unsupported timeline mode.")
    if type(format) is not str or format not in {"csv", "xlsx", "pdf"}:
        raise ValueError("Unsupported timeline export format.")
    if type(browser_timezone) is not str or browser_timezone not in timezone_names():
        raise ValueError("Export timezone must be an IANA name.")
    if snapshot is not None and not isinstance(snapshot, TimelineExportSnapshot):
        raise TypeError("Regeneration requires a retained timeline capture.")

    def admit(*args):
        """Both enqueue and its owning transaction recheck current authorization."""
        principal = authorize(store, user_id)
        if "administrator" not in principal.roles:
            raise PermissionError("The Family timeline export is for Administrators.")
        admit_campaign(campaign_id, mutating=True)
        return True

    with export_transaction(campaign_id):
        admit()
        # A Family of another campaign is refused like a missing one.
        family = FamilyCampaign.objects.get(pk=family_id, campaign_id=campaign_id)
        epoch = rehearsal_epoch(campaign_id) if mode == "testing" else None
        if (
            snapshot is not None
            and mode == "testing"
            and (
                epoch is None
                or str(epoch) != snapshot.parameters.get("rehearsal_epoch_id")
            )
        ):
            raise RehearsalEnded(
                "The Testing rehearsal this export was made in has ended; "
                "export the timeline again."
            )
        if mode == "testing" and epoch is None:
            raise PermissionError("The campaign has no Testing rehearsal to export.")
        parameters = {
            "family_id": str(family.pk),
            "mode": mode,
            "rehearsal_epoch_id": str(epoch) if epoch else None,
        }
        previous = ExportRequest.objects.filter(
            requester_id=user_id, request_key=request_key
        ).first()
        if previous is not None:
            if (
                previous.report,
                previous.campaign_id,
                previous.parameters,
                previous.format,
                previous.browser_timezone,
            ) != (REPORT, campaign_id, parameters, format, browser_timezone) or (
                snapshot is not None and previous.timeline_snapshot_id != snapshot.pk
            ):
                raise ExportRequestBound("Export request identity is already bound.")
            return previous
        system = SystemConfiguration.objects.get()
        configuration_id = system.active_configuration_id
        correlation_id = uuid4()
        if snapshot is None:
            snapshot = TimelineExportSnapshot.objects.create(
                campaign_id=campaign_id,
                family=family,
                configuration_id=configuration_id,
                actor_id=user_id,
                correlation_id=correlation_id,
                parameters=parameters,
                document=capture(
                    campaign_id, family, mode, epoch, current_mode=system.mode
                ),
            )
            # The insert trigger sets the time and the event count.
            snapshot.refresh_from_db(fields=["created_at", "row_count"])
        elif snapshot.campaign_id != campaign_id or snapshot.parameters != parameters:
            raise ValueError("Retained timeline scope differs from the request.")
        identifier = uuid4()
        task = enqueue(
            task_type=TASK_TYPE,
            domain_request_id=identifier,
            actor_id=user_id,
            correlation_id=correlation_id,
            idempotency_key=identifier,
            admit=admit,
        )
        request = ExportRequest.objects.create(
            id=identifier,
            campaign_id=campaign_id,
            requester_id=user_id,
            request_key=request_key,
            task_id=task.run_id,
            timeline_snapshot=snapshot,
            configuration_id=configuration_id,
            report=REPORT,
            format=format,
            browser_timezone=browser_timezone,
            parameters=parameters,
            authorization_scope={"capability": Capability.CAMPAIGN_REPORT.value},
            actor_id=user_id,
            correlation_id=correlation_id,
        )
        audit(
            Action.EXPORT_REQUESTED,
            request,
            user_id,
            outcome=Outcome.STARTED,
            count=snapshot.row_count,
        )
        return request

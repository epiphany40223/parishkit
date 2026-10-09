"""The names behind a chosen-Family test review, as an export file (#817).

The Send to chosen Families page shows each DUID with its Family's name so
the Administrator recognizes the Families before real Family data goes to
the Testing recipient. The command line prints only the DUIDs; ``pk-admin
test families-preview --names`` instead captures the names the review read
(``FamilyTestNamesSnapshot``) and requests a ``family_test_names`` export
of them through the shared export lifecycle. The worker renders the CSV and
``export fetch`` downloads it, so the names reach nothing but the file.

The capture is the review's own: the caller passes the names the page's
``prepare`` read (``snapshot_family_names``) in the review's transaction,
and SQL checks that the capture is one row per reviewed DUID, in order, by
an Administrator, for the current Testing draft.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import ClassVar
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from parishkit.stewardship.accounts.policy import Capability, allows
from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.audit.schemas import Action, Outcome
from parishkit.stewardship.campaigns.work_locks import export_transaction
from parishkit.stewardship.jobs.storage import enqueue
from parishkit.stewardship.schema_primitives import timezone_names

from .export_models import ExportRequest, FamilyTestNamesSnapshot
from .export_services import (
    TASK_TYPE,
    ExportRequestBound,
    admit_campaign,
    audit,
    authorize,
)

REPORT = "family_test_names"
HEADINGS = ("Family DUID", "Family name")


def parameters_for(revision_id, duids):
    """The retained selection: the email revision and the DUIDs, in order."""
    return {"revision": str(revision_id), "duids": [int(duid) for duid in duids]}


def create_family_test_names_export(
    store,
    user_id,
    *,
    campaign_id,
    revision_id,
    families,
    browser_timezone,
    request_key,
    snapshot=None,
):
    """Capture the reviewed names once and request their CSV export.

    ``families`` is the review's ``(duid, name)`` pairs in its order. A
    repeated ``request_key`` returns the export it made when the selection
    (revision, DUIDs, time zone) is the same, and is refused
    (``ExportRequestBound``) otherwise; the names are not compared, as a
    renamed Family is the same one. ``snapshot`` is the retained capture
    for regeneration (with ``families`` None), never a caller's input. The
    caller holds the review's transaction; this takes the campaign's
    export lock inside it.
    """
    if any(
        not isinstance(value, UUID)
        for value in (user_id, campaign_id, revision_id, request_key)
    ):
        raise ValueError("Export identities must be canonical UUIDs.")
    if type(browser_timezone) is not str or browser_timezone not in timezone_names():
        raise ValueError("Export timezone must be an IANA name.")
    if snapshot is None:
        families = tuple(families or ())
        if not families:
            raise ValueError("Name at least one Family DUID.")
        parameters = parameters_for(revision_id, (duid for duid, _ in families))
    elif not isinstance(snapshot, FamilyTestNamesSnapshot) or families is not None:
        raise ValueError("Regeneration cannot change the retained selection.")
    elif snapshot.campaign_id != campaign_id:
        raise ValueError("Retained names belong to another campaign.")
    else:
        parameters = snapshot.parameters

    def admit(*args):
        """Both enqueue and its owning transaction recheck current authorization.

        The page needs the configure capability, which only an Administrator
        holds; SQL repeats that for the capture.
        """
        actor = authorize(store, user_id)
        if not allows(actor, Capability.CONFIGURE):
            raise PermissionError("The chosen-Family names are unavailable.")
        admit_campaign(campaign_id, mutating=True)
        return True

    with export_transaction(campaign_id):
        admit()
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
                None if snapshot is None else previous.family_test_names_snapshot_id,
            ) != (
                REPORT,
                campaign_id,
                parameters,
                "csv",
                browser_timezone,
                None if snapshot is None else snapshot.pk,
            ):
                raise ExportRequestBound("Export request identity is already bound.")
            return previous
        configuration_id = SystemConfiguration.objects.get().active_configuration_id
        correlation_id = uuid4()
        if snapshot is None:
            rows = [{"duid": int(duid), "name": name} for duid, name in families]
            snapshot = FamilyTestNamesSnapshot.objects.create(
                campaign_id=campaign_id,
                configuration_id=configuration_id,
                actor_id=user_id,
                correlation_id=correlation_id,
                parameters=parameters,
                document={"rows": rows},
                row_count=len(rows),
            )
            # The insert trigger sets the capture time.
            snapshot.refresh_from_db(fields=["created_at"])
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
            family_test_names_snapshot=snapshot,
            configuration_id=configuration_id,
            report=REPORT,
            format="csv",
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


@dataclass(frozen=True, repr=False)
class FamilyTestNamesDocument:
    """The captured names as the shared CSV writer renders them."""

    metadata: tuple[tuple[str, str], ...]
    rows: tuple[tuple[str, ...], ...]
    item_count: int
    requested_at: datetime
    headings: ClassVar[tuple[str, ...]] = HEADINGS
    title: ClassVar[str] = "Chosen-Family test names"
    sheet_name: ClassVar[str] = "Families"


def names_document(request):
    """The retained capture of one export, with its provenance as metadata.

    Every row is the Family's DUID and the name the review showed ("" for a
    DUID the source does not know, as the page shows none).
    """
    snapshot = request.family_test_names_snapshot
    zone = ZoneInfo(request.browser_timezone)
    rows = snapshot.document["rows"]
    if len(rows) != snapshot.row_count:
        raise ValueError("The names export requires every reviewed Family.")
    metadata = (
        ("Report", FamilyTestNamesDocument.title),
        ("Parish", request.configuration.parish.name),
        ("Campaign reference", str(request.campaign_id)),
        ("Email revision", snapshot.parameters["revision"]),
        ("Captured at", snapshot.created_at.astimezone(zone)),
        ("Requested at", request.created_at.astimezone(zone)),
        ("Display timezone", request.browser_timezone),
        ("Families", f"{snapshot.row_count:,}"),
        (
            "Privacy",
            "Real Family names. Share only with authorized recipients, and "
            "delete the file once used.",
        ),
    )
    return FamilyTestNamesDocument(
        metadata,
        tuple((str(row["duid"]), row["name"]) for row in rows),
        snapshot.row_count,
        request.created_at,
    )

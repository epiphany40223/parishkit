"""``report source-form``: Families the form cannot open (ADM-11, #774).

The command reads the Administrator's list on System health
(``accounts.source_form_views``) through the page's own functions: its
admission (``_principal``: Administrators only, as System health), its
current-campaign read, ``reports.source_form.blocked_families`` inside the
campaign read guard, and its audit (``source_form_viewed`` with the number
of Families listed).

As the specification's inventory row says, it prints counts and DUIDs
only: each finding's Family DUID, Member DUID and the field in the page's
plain words, never the Family name the page shows and never a Member value.
The host's ``pk-stewardship source-form-check`` runs the same check.
"""

from dataclasses import dataclass
from uuid import UUID

from .admin_reads import ReadModel, _held, _recheck
from .admin_report_reads import _admit, _no_abort, _read_failures, _refusal


@dataclass(frozen=True)
class SourceFormReport(ReadModel):
    """The list's counts and findings, by DUID.

    ``campaign_id`` is the current campaign, or None when there is none (the
    counts are then 0 and ``rows`` empty, as the page's "no campaign" text).
    ``eligible`` is how many portal-eligible Families were checked and
    ``families`` how many cannot open the form. ``rows`` has one finding per
    row, sorted by Family then Member: ``family_duid``, ``member_duid`` (None
    for an unusable Member record) and ``field``, the page's words for it.
    """

    campaign_id: UUID | None
    eligible: int
    families: int
    rows: list


def source_form_model(campaign_id, result):
    """The command's projection of ``blocked_families``: no Family name."""
    return SourceFormReport(
        campaign_id=campaign_id,
        eligible=result["eligible"],
        families=result["families"],
        rows=[
            {
                "family_duid": row["family_duid"],
                "member_duid": row["member_duid"],
                "field": row["field"],
            }
            for row in result["rows"]
        ],
    )


def read_source_form(caller, service):
    """``report source-form``: the page's list for the current campaign.

    Admits passively with the page's admission, so a read-only session reads
    and a reader without the page's capability is ``denied``. Reads the
    configuration row with ``.get()``, as the page does, so a missing row
    fails closed (``not_available``) rather than reading as "no campaign".
    With a current campaign, the scan runs inside the campaign read guard,
    whose ``authorize`` repeats the page's admission; a scan that cannot run
    now (no promoted source, a restore review, the guard's deadline) is
    ``unavailable``. The session is rechecked before anything is reported.
    The page's ``source_form_viewed`` is recorded once: succeeded with the
    Families listed, or failed with 0 once a read has begun.
    """
    from django.db import DatabaseError

    from parishkit.config import ConfigError

    from .accounts.source_form_views import _audit, _current_campaign, _principal
    from .audit.schemas import Outcome
    from .campaigns.read_guards import CampaignReadGuard, GuardedResponse
    from .observability import Event, emit_failure
    from .reports.source_form import blocked_families
    from .storage import StorageInvariantError

    def step():
        """Admit, scan under the guard, recheck and audit, as the page."""
        actor = _admit(caller, service, _principal)
        campaign_id = _current_campaign()
        if campaign_id is None:
            _recheck(caller, service.store, actor)
            _audit(actor, Outcome.SUCCEEDED, 0)
            return source_form_model(None, {"eligible": 0, "families": 0, "rows": []})
        holder = {"result": None, "done": False}

        def authorize(guard):
            """The page's fresh admission inside the guard."""
            fresh = _principal(caller, service.store, read_only=True)
            if fresh.identity != actor.identity:
                raise PermissionError("Source form list access changed.")

        def content():
            """The page's scan (or its cached result), under the guard."""
            holder["result"] = blocked_families(campaign_id)
            guard.check()
            return iter(())

        guard = CampaignReadGuard([campaign_id], authorize=authorize, abort=_no_abort)
        try:
            try:
                GuardedResponse(guard, content).close()
            except (*_read_failures(), ConfigError) as error:
                _refusal(caller, service, actor, error)
            _recheck(caller, service.store, actor)
            # The read is complete: from here no failed event is recorded.
            holder["done"] = True
            _audit(actor, Outcome.SUCCEEDED, holder["result"]["families"])
            return source_form_model(campaign_id, holder["result"])
        finally:
            if not holder["done"]:
                # The page's ``finish(False)``; failing to record it never
                # hides the read's own error.
                try:
                    _audit(actor, Outcome.FAILED, 0)
                except (DatabaseError, StorageInvariantError) as error:
                    emit_failure(error, event=Event.REPORT_AUDIT_FAILED)

    return _held(step)

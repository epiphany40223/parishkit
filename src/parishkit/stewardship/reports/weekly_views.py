"""Protected weekly report links retain current Admin authority through closure."""

from django.core.exceptions import ObjectDoesNotExist
from django.db import DatabaseError
from django.template.loader import render_to_string
from django.views.decorators.http import require_GET

from parishkit.config import ConfigError
from parishkit.stewardship.accounts.authentication import denial, runtime
from parishkit.stewardship.accounts.limiting import LimiterUnavailable
from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.accounts.sessions import authenticated_admin
from parishkit.stewardship.audit.schemas import Action, ActorKind, Outcome
from parishkit.stewardship.audit.services import record_action
from parishkit.stewardship.campaigns.read_guards import ReadUnavailable
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.observability import Event, emit_failure
from parishkit.stewardship.storage import StorageInvariantError
from parishkit.stewardship.web.responses import campaign_response
from parishkit.stewardship.web.security import private_response

from .export_services import admit_campaign
from .weekly_grants import REPORT_FIELDS
from .weekly_models import WeeklyDigestSnapshot
from .weekly_presentation import page_number, snapshot_context


def _principal(request, store, *, read_only=False):
    """Private Admin mail reports are not the later Staff follow-up work queue."""
    principal = authenticated_admin(
        request, store=store, activity=not read_only, read_only=read_only
    )
    if principal is None or "administrator" not in principal.roles:
        raise PermissionError("Administrator report access is required.")
    return principal


@require_GET
def snapshot(request, snapshot_id, *, item_id=None):
    """Authorize opaque snapshot/item selections before any private response bytes."""
    finish, handed_off = None, False
    try:
        service = runtime()
        principal = _principal(request, service.store)
        try:
            page = page_number(request.GET, detail=item_id is not None)
        except ValueError:
            return private_response("Invalid report request.\n", status=400)
        retained = WeeklyDigestSnapshot.objects.only("id", "campaign_id").get(
            pk=snapshot_id
        )
        admit_campaign(retained.campaign_id, mutating=False)
        finalized = False

        def audit(outcome):
            """Journal opaque access, never Family text or recipient addresses."""
            with work_transaction():
                current = SystemConfiguration.objects.select_related(
                    "active_configuration__parish"
                ).get()
                record_action(
                    Action.WEEKLY_DIGEST_VIEWED,
                    actor_kind=ActorKind.PORTAL_USER,
                    actor_id=principal.identity,
                    subject_id=snapshot_id,
                    parish_id=current.active_configuration.parish.pk,
                    campaign_id=retained.campaign_id,
                    context={"outcome": outcome},
                )

        def finish(completed):
            """Audit server-side completion after the read barrier has closed."""
            nonlocal finalized
            if not finalized:
                try:
                    audit(Outcome.SUCCEEDED if completed else Outcome.FAILED)
                except (DatabaseError, StorageInvariantError) as error:
                    emit_failure(error, event=Event.REPORT_AUDIT_FAILED)
                else:
                    finalized = True

        try:
            audit(Outcome.STARTED)
        except BaseException:
            finish = None
            raise

        def authorize(guard):
            """Recheck session and campaign after acquiring the purge/read barrier."""
            _principal(request, service.store, read_only=True)
            admit_campaign(retained.campaign_id, mutating=False)
            if not WeeklyDigestSnapshot.objects.filter(
                pk=snapshot_id, campaign_id=retained.campaign_id
            ).exists():
                raise ReadUnavailable("Retained weekly report is unavailable.")

        def content():
            """Render only the requested bounded subset inside the response guard."""
            selected = WeeklyDigestSnapshot.objects.only(*REPORT_FIELDS).get(
                pk=snapshot_id
            )
            context = snapshot_context(selected, page=page, item_id=item_id)
            return iter(
                (
                    render_to_string(
                        "stewardship/weekly-digest.html", context, request=request
                    ).encode(),
                )
            )

        response = campaign_response(
            request,
            [retained.campaign_id],
            authorize=authorize,
            open_content=content,
            on_close=finish,
        )
        handed_off = response.status_code == 200 and response.streaming
        return response
    except (PermissionError, ObjectDoesNotExist):
        return denial()
    except (
        ConfigError,
        DatabaseError,
        LimiterUnavailable,
        ReadUnavailable,
        StorageInvariantError,
    ):
        return denial(status=503, retry=5)
    finally:
        if finish is not None and not handed_off:
            finish(False)

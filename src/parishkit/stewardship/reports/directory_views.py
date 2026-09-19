"""Native private-POST Family directories with response-lifetime read admission."""

from uuid import uuid4

from django.core.exceptions import ObjectDoesNotExist
from django.db import DatabaseError
from django.http import HttpResponse
from django.template.loader import render_to_string
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from parishkit.stewardship.accounts.authentication import denial, runtime
from parishkit.stewardship.accounts.cryptography import CryptographicError
from parishkit.stewardship.accounts.family_authentication import (
    runtime as family_runtime,
)
from parishkit.stewardship.accounts.policy import Capability, allows
from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.accounts.sessions import authenticated_admin
from parishkit.stewardship.audit.schemas import Action, ActorKind, Outcome
from parishkit.stewardship.audit.services import record_action
from parishkit.stewardship.campaigns.domain import Percentage
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.observability import Event, emit_failure
from parishkit.stewardship.schema_primitives import timezone_names
from parishkit.stewardship.storage import StorageInvariantError
from parishkit.stewardship.web.presentation import out_of
from parishkit.stewardship.web.responses import campaign_response

from .directories import PAGE_SIZE, REASONS, DirectoryQuery, directory_page
from .export_services import admit_campaign
from .export_views import SAFE_FAILURES
from .read_admission import admit_report_read


def _principal(request, store, *, read_only=False):
    """Manual codes are ordinary Admin/Staff data, not a public-login capability."""
    principal = authenticated_admin(
        request, store=store, activity=not read_only, read_only=read_only
    )
    if not allows(principal, Capability.FAMILY_CODES):
        raise PermissionError("Family directory access is unavailable.")
    return principal


def _audit(principal, campaign_id, *, postal, outcome, count, total, query):
    """Record scope/count, never names, codes, search strings or viewed contacts."""
    with work_transaction():
        system = SystemConfiguration.objects.select_related(
            "active_configuration__parish"
        ).get()
        record_action(
            Action.POSTAL_OUTREACH_VIEWED if postal else Action.FAMILY_DIRECTORY_VIEWED,
            actor_kind=ActorKind.PORTAL_USER,
            actor_id=principal.identity,
            subject_id=campaign_id,
            parish_id=system.active_configuration.parish.pk,
            campaign_id=campaign_id,
            context=query.audit_values()
            | {"outcome": outcome, "count": count, "matching_count": total},
        )


def _error(campaign_id, *, postal, status):
    """Render fixed recovery text without reflecting private input or DB failures."""
    response = HttpResponse(
        render_to_string(
            "stewardship/directory-error.html",
            {
                "campaign_id": campaign_id,
                "report_route": "admin:postal_directory"
                if postal
                else "admin:family_directory",
                "status": status,
            },
        ),
        status=status,
        headers={"Cache-Control": "no-store"},
    )
    response.stewardship_safe_error = True
    if status == 503:
        response["Retry-After"] = "5"
    return response


@require_http_methods(["GET", "POST"])
def directory(request, campaign_id, *, postal=False):
    """Recheck roles/scope through rendering and streaming; audit after guard close."""
    finish, handed_off = None, False
    try:
        service = runtime()
        principal = _principal(request, service.store)
        if request.GET:
            raise ValueError("Directory filters require private POST state.")
        parameters = request.POST.copy()
        parameters.pop("csrfmiddlewaretoken", None)
        query = DirectoryQuery.parse(parameters)
        admit_report_read(campaign_id)
        _audit(
            principal,
            campaign_id,
            postal=postal,
            outcome=Outcome.STARTED,
            count=0,
            total=0,
            query=query,
        )
        finalized, count, total = False, 0, 0

        def finish(completed):
            """Access audit stays parish-owned and cannot mutate purge inventory."""
            nonlocal finalized
            if finalized:
                return
            finalized = True
            try:
                _audit(
                    principal,
                    campaign_id,
                    postal=postal,
                    outcome=Outcome.SUCCEEDED if completed else Outcome.FAILED,
                    count=count,
                    total=total,
                    query=query,
                )
            except (DatabaseError, StorageInvariantError) as error:
                emit_failure(error, event=Event.REPORT_AUDIT_FAILED)

        def authorize(guard):
            """An earlier cookie, report or code match never grants stale access."""
            current = _principal(request, service.store, read_only=True)
            if current.identity != principal.identity:
                raise PermissionError("Directory access changed.")
            admit_report_read(campaign_id)

        def content():
            """No source query, key operation or rendering escapes the read guard."""
            nonlocal count, total
            rings = family_runtime()
            report = directory_page(
                campaign_id, query, postal=postal, general=rings.general, mac=rings.mac
            )
            count = len(report["rows"])
            total = report["total"]
            mutable = True
            try:
                admit_campaign(campaign_id, mutating=True)
            except PermissionError:
                mutable = False
            route = "admin:postal_directory" if postal else "admin:family_directory"
            context = report | {
                "postal_proportion": out_of(
                    Percentage(report["postal_total"], report["active_total"])
                ),
                "campaign_id": campaign_id,
                "postal": postal,
                "query": query,
                "query_fields": query.form_values(),
                "report_url": reverse(route, args=(campaign_id,)),
                "reasons": REASONS,
                "mutable": mutable,
                "request_key": uuid4(),
                "export_timezones": sorted(timezone_names()),
                "previous_page": query.page - 1 if query.page > 1 else None,
                "next_page": query.page + 1
                if query.page * PAGE_SIZE < report["total"]
                else None,
            }
            return iter(
                (
                    render_to_string(
                        "stewardship/directory.html", context, request=request
                    ).encode(),
                )
            )

        response = campaign_response(
            request,
            [campaign_id],
            authorize=authorize,
            open_content=content,
            on_close=finish,
        )
        if response.status_code == 503 and not response.streaming:
            # The shared guard has already released its read transaction. Give
            # this report its safe recovery navigation, never private contents.
            return _error(campaign_id, postal=postal, status=503)
        handed_off = response.status_code == 200 and response.streaming
        return response
    except (PermissionError, ObjectDoesNotExist):
        return denial()
    except (*SAFE_FAILURES, StorageInvariantError, CryptographicError, UnicodeError):
        return _error(campaign_id, postal=postal, status=503)
    except ValueError:
        return _error(campaign_id, postal=postal, status=400)
    finally:
        if finish is not None and not handed_off:
            finish(False)

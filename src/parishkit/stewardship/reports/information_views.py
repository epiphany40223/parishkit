"""Private native staff queue, history and optimistic follow-up editing."""

from uuid import UUID, uuid4

from django.core.exceptions import ObjectDoesNotExist
from django.db import DatabaseError
from django.http import HttpResponse
from django.shortcuts import redirect
from django.template.loader import render_to_string
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from parishkit.stewardship.accounts.authentication import denial, runtime
from parishkit.stewardship.accounts.models import PortalUser
from parishkit.stewardship.accounts.policy import Capability, allows
from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.accounts.sessions import authenticated_admin
from parishkit.stewardship.audit.schemas import Action, ActorKind, Outcome
from parishkit.stewardship.audit.services import record_action
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.observability import Event, emit_failure
from parishkit.stewardship.responses.information import update_information
from parishkit.stewardship.responses.models import AdditionalInformationItem
from parishkit.stewardship.schema_primitives import timezone_names
from parishkit.stewardship.storage import StaleRecordError, StorageInvariantError
from parishkit.stewardship.web.contracts import expected_version, filters
from parishkit.stewardship.web.responses import campaign_response

from .export_services import admit_campaign
from .export_views import SAFE_FAILURES
from .information import (
    PAGE_SIZE,
    InformationQuery,
    information_history,
    information_page,
    parse_page,
)
from .read_admission import admit_report_read


def _principal(request, store, *, read_only=False):
    """Every queue, item and mutation reloads the current Staff capability."""
    principal = authenticated_admin(
        request, store=store, activity=not read_only, read_only=read_only
    )
    if not allows(principal, Capability.ADDITIONAL_FOLLOWUP):
        raise PermissionError("Staff follow-up is unavailable.")
    return principal


def _error(campaign_id, *, item_id=None, status=400):
    """No private form/exception values or database-dependent context processors."""
    response = HttpResponse(
        render_to_string(
            "stewardship/information-error.html",
            {
                "campaign_id": campaign_id,
                "item_id": item_id,
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


def _audit(principal, campaign_id, outcome):
    """Retain parish-owned access evidence, not private search or displayed values."""
    with work_transaction():
        system = SystemConfiguration.objects.select_related(
            "active_configuration__parish"
        ).get()
        record_action(
            Action.INFORMATION_VIEWED,
            actor_kind=ActorKind.PORTAL_USER,
            actor_id=principal.identity,
            subject_id=campaign_id,
            parish_id=system.active_configuration.parish.pk,
            campaign_id=campaign_id,
            context={"outcome": outcome},
        )


def _page_response(request, campaign_id, *, item_id=None):
    """Guard every data query, template render and byte; audit response completion."""
    finish, handed_off = None, False
    try:
        service = runtime()
        principal = _principal(request, service.store)
        if item_id is None:
            if request.GET:
                raise ValueError("Search and filters require a POST body.")
            parameters = request.POST.copy()
            parameters.pop("csrfmiddlewaretoken", None)
            query = InformationQuery.parse(parameters)
            history_page = 1
        else:
            values = filters(request.GET, allowed={"page"})
            history_page = parse_page(values.get("page", "1"))
            query = InformationQuery(disposition="all")
        admit_report_read(campaign_id)
        _audit(principal, campaign_id, Outcome.STARTED)
        finalized = False

        def finish(completed):
            """Read-only guards close before writing permanent access evidence."""
            nonlocal finalized
            if finalized:
                return
            finalized = True
            try:
                _audit(
                    principal,
                    campaign_id,
                    Outcome.SUCCEEDED if completed else Outcome.FAILED,
                )
            except (DatabaseError, StorageInvariantError) as error:
                emit_failure(error, event=Event.REPORT_AUDIT_FAILED)

        def authorize(guard):
            """Do not trust the pre-audit principal or a previously visible item."""
            current = _principal(request, service.store, read_only=True)
            if current.identity != principal.identity:
                raise PermissionError("Staff follow-up access changed.")
            admit_report_read(campaign_id)

        def content():
            """All lazy SQL and rendering stay within the response-owned barrier."""
            result = information_page(campaign_id, query, item_id=item_id)
            mutable = True
            try:
                admit_campaign(campaign_id, mutating=True)
            except PermissionError:
                mutable = False
            history, more_history = (
                information_history(
                    item_id, history_page, version=result["rows"][0]["version"]
                )
                if item_id
                else ((), False)
            )
            ids = {row.actor_id for row in history} | {
                row.followed_up_by_id for row in history
            }
            ids.update(row["followed_up_by_id"] for row in result["rows"])
            labels = {
                str(key): email
                for key, email in PortalUser.objects.filter(
                    pk__in=ids - {None}
                ).values_list("id", "email")
            }
            for row in result["rows"]:
                row["completed_by"] = labels.get(
                    str(row["followed_up_by_id"]), "Former portal user"
                )
            for row in history:
                row.actor_label = labels.get(str(row.actor_id), "Former portal user")
                row.completer_label = labels.get(
                    str(row.followed_up_by_id), "Former portal user"
                )
            context = dict(
                result,
                campaign_id=campaign_id,
                query=query,
                query_fields=query.form_values(),
                mutable=mutable,
                item=result["rows"][0] if item_id else None,
                history=history,
                history_page=history_page,
                previous_history=history_page - 1 if history_page > 1 else None,
                next_history=history_page + 1 if more_history else None,
                request_key=uuid4(),
                export_timezones=sorted(timezone_names()) if not item_id else (),
                previous_page=query.page - 1 if query.page > 1 else None,
                next_page=query.page + 1
                if query.page * PAGE_SIZE < result["total"]
                else None,
            )
            return iter(
                (
                    render_to_string(
                        "stewardship/information.html", context, request=request
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
        handed_off = response.status_code == 200 and response.streaming
        return response
    except (PermissionError, ObjectDoesNotExist):
        return denial()
    except (*SAFE_FAILURES, StorageInvariantError):
        return _error(campaign_id, item_id=item_id, status=503)
    except ValueError:
        return _error(campaign_id, item_id=item_id)
    finally:
        if finish is not None and not handed_off:
            finish(False)


@require_http_methods(["GET", "POST"])
def queue(request, campaign_id):
    """Default GET is safe; private filtering and pagination use native POST."""
    return _page_response(request, campaign_id)


@require_GET
def detail(request, campaign_id, item_id):
    """Expose current status and bounded history for this campaign's item only."""
    return _page_response(request, campaign_id, item_id=item_id)


def change_values(parameters):
    """Strict checkbox/form grammar; unchecked boxes are absent, never ambiguous."""
    required = {"expected_version", "request_key", "notes"}
    flags = {"follow_up_needed", "followed_up", "confirm_clear"}
    keys = set(parameters) - {"csrfmiddlewaretoken"}
    if (
        not required <= keys
        or keys - required - flags
        or any(len(parameters.getlist(key)) != 1 for key in parameters)
        or any(parameters[key] != "yes" for key in keys & flags)
    ):
        raise ValueError("Invalid follow-up form.")
    return dict(
        expected_version=expected_version(parameters["expected_version"]),
        request_key=UUID(parameters["request_key"]),
        # Native forms encode textarea line breaks as CRLF, while maxlength
        # counts the browser's LF representation. Keep storage/replay canonical.
        notes=parameters["notes"].replace("\r\n", "\n").replace("\r", "\n"),
        **{flag: flag in keys for flag in flags},
    )


@require_POST
def update(request, campaign_id, item_id):
    """Bind the route's campaign, recheck policy in the owner, then redirect safely."""
    try:
        service = runtime()
        principal = _principal(request, service.store)
        if request.GET:
            raise ValueError("Follow-up edits require a POST body.")
        values = change_values(request.POST)
        if not AdditionalInformationItem.objects.filter(
            pk=item_id, submission__campaign_id=campaign_id, submission__mode="live"
        ).exists():
            raise PermissionError("This item is unavailable.")
        update_information(service.store, principal.identity, item_id, **values)
        response = redirect(
            "admin:information_item", campaign_id=campaign_id, item_id=item_id
        )
        response["Cache-Control"] = "no-store"
        return response
    except StaleRecordError:
        return _error(campaign_id, item_id=item_id, status=409)
    except (PermissionError, ObjectDoesNotExist):
        return denial()
    except (*SAFE_FAILURES, StorageInvariantError):
        return _error(campaign_id, item_id=item_id, status=503)
    except ValueError:
        return _error(campaign_id, item_id=item_id)

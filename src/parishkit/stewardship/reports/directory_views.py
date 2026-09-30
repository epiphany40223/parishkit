"""The native private-POST Family directory with response-lifetime read admission.

One page serves both uses that used to be separate pages: the Family-code
directory and postal outreach. The "Include mailing columns" checkbox
(``mailing``, off by default) adds the addressee and mailing-address columns
and makes the export a postal mail merge. It is independent of the filters:
the selection's ``postal`` flag chooses columns and the export kind, never
which Families are listed (#202).
"""

from urllib.parse import urlencode
from uuid import uuid4

from django.core.exceptions import ObjectDoesNotExist
from django.db import DatabaseError, transaction
from django.http import HttpResponse, HttpResponseRedirect
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
from parishkit.stewardship.observability import Event, debug_swallowed, emit_failure
from parishkit.stewardship.schema_primitives import timezone_names
from parishkit.stewardship.storage import StorageInvariantError
from parishkit.stewardship.web.presentation import out_of
from parishkit.stewardship.web.responses import campaign_response

from .directories import (
    PAGE_SIZE,
    REACH,
    REASONS,
    DirectoryQuery,
    directory_page,
    testing_codes_context,
)
from .directory_documents import export_headings, head_names
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


# Closed, non-private values a link may carry (GET): the reach presets (such
# as "no campaign mail can reach") and the mailing-columns preset.
LINK_PRESETS = frozenset({"reach", "mailing"})


def mailing_option(parameters, *, default):
    """Remove and return the mailing-columns choice from the submitted form.

    ``parameters`` is a mutable QueryDict. The checkbox sends ``mailing=yes``
    only when checked; hidden fields carry ``yes`` or ``no``. ``default``
    applies when the field is absent: the old postal route and forms rendered
    before the pages merged default to mailing columns on.
    """
    values = parameters.pop("mailing", ["yes" if default else "no"])
    if len(values) != 1 or values[0] not in {"yes", "no"}:
        raise ValueError("Invalid mailing-columns choice.")
    return values[0] == "yes"


def _audit(principal, campaign_id, *, postal, outcome, count, total, query):
    """Record scope/count, never names, codes, search strings or viewed contacts."""
    # An audit append takes no row locks, so it need not join the
    # writers' work order; waiting there stalled report pages behind
    # every source promotion and installer.
    with transaction.atomic():
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


def _error(campaign_id, *, status):
    """Render fixed recovery text without reflecting private input or DB failures."""
    debug_swallowed("report request refused")
    response = HttpResponse(
        render_to_string(
            "stewardship/directory-error.html",
            {"campaign_id": campaign_id, "status": status},
        ),
        status=status,
        headers={"Cache-Control": "no-store"},
    )
    response.stewardship_safe_error = True
    if status == 503:
        response["Retry-After"] = "5"
    return response


def _legacy_postal_redirect(request, campaign_id):
    """Send a bookmarked postal-outreach link to the merged page's preset.

    The old page listed the Families that need postal mail, so the preset is
    mailing columns with reach "By postal mail only" (the Families its mail
    merge held) unless the link named another known reach. Anything else is
    dropped, so no private value ever enters the new URL.
    """
    reach = request.GET.getlist("reach")
    reach = reach[0] if len(reach) == 1 and reach[0] in REACH else "mail"
    presets = {"reach": reach}
    return HttpResponseRedirect(
        reverse("admin:family_directory", args=(campaign_id,))
        + "?"
        + urlencode(presets | {"mailing": "yes"})
    )


def _mailing_rows(rows):
    """Add each row's mail-merge addressee, as the postal export names it."""
    for row in rows:
        row["addressee"] = head_names(row["heads"]) or row["family_name"]


@require_http_methods(["GET", "POST"])
def directory(request, campaign_id, *, postal=False):
    """Recheck roles/scope through rendering and streaming; audit after guard close.

    ``postal`` is set only by the old postal-outreach route: a GET there
    redirects to this page with mailing columns on, and a POST from a form
    rendered before the pages merged is served here with mailing columns on.
    """
    if postal and request.method == "GET":
        return _legacy_postal_redirect(request, campaign_id)
    finish, handed_off = None, False
    try:
        service = runtime()
        principal = _principal(request, service.store)
        # Filters are private POST state. The exceptions are the closed link
        # presets (?reach=neither, ?mailing=yes), which carry no private value
        # and let other pages link to "Families no campaign mail can reach"
        # and to the mailing columns.
        if request.GET and (
            request.method != "GET" or not set(request.GET) <= LINK_PRESETS
        ):
            raise ValueError("Directory filters require private POST state.")
        parameters = (request.GET if request.GET else request.POST).copy()
        parameters.pop("csrfmiddlewaretoken", None)
        postal = mailing_option(parameters, default=postal)
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
            if postal:
                _mailing_rows(report["rows"])
            mutable = True
            try:
                admit_campaign(campaign_id, mutating=True)
            except PermissionError:
                mutable = False
            testing = testing_codes_context(campaign_id)
            context = (
                report
                | testing
                | {
                    "postal_proportion": out_of(
                        Percentage(report["postal_total"], report["active_total"])
                    ),
                    "campaign_id": campaign_id,
                    "mailing": postal,
                    "query": query,
                    "query_fields": query.form_values()
                    | {"mailing": "yes" if postal else "no"},
                    "report_url": reverse(
                        "admin:family_directory", args=(campaign_id,)
                    ),
                    "reasons": REASONS,
                    "reaches": REACH,
                    "export_headings": export_headings(
                        postal=postal, reach=query.reach
                    ),
                    "unreachable_url": reverse(
                        "admin:family_directory", args=(campaign_id,)
                    )
                    + "?reach=neither",
                    "mutable": mutable,
                    "request_key": uuid4(),
                    "export_timezones": sorted(timezone_names()),
                    "previous_page": query.page - 1 if query.page > 1 else None,
                    "next_page": query.page + 1
                    if query.page * PAGE_SIZE < report["total"]
                    else None,
                }
            )
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
            return _error(campaign_id, status=503)
        handed_off = response.status_code == 200 and response.streaming
        return response
    except (PermissionError, ObjectDoesNotExist):
        return denial()
    except (*SAFE_FAILURES, StorageInvariantError, CryptographicError, UnicodeError):
        return _error(campaign_id, status=503)
    except ValueError:
        return _error(campaign_id, status=400)
    finally:
        if finish is not None and not handed_off:
            finish(False)

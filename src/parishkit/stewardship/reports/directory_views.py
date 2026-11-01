"""The private-POST Campaign Family directory, with read admission.

One page serves both uses that used to be separate pages: the Family-code
directory and postal outreach. The "Include mailing columns" checkbox
(``mailing``, off by default) adds the addressee and mailing-address columns
and makes the export a postal mail merge. It also lists only the Families
postal invitations are for: Campaign mail can reach is "By postal mail
only", whatever reach was chosen (#951; ``DirectoryQuery.postal``). That
rule is applied when the rows are selected (``selection_parameters``), never
to the page's own filters, so the reach the reader chose comes back when the
mailing columns are turned off; the audit records the reach applied.
"""

from dataclasses import replace
from urllib.parse import urlencode
from uuid import uuid4

from django.core.exceptions import ObjectDoesNotExist
from django.db import DatabaseError, transaction
from django.http import HttpResponse
from django.template.loader import render_to_string
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from parishkit.stewardship.accounts import role_refusal
from parishkit.stewardship.accounts.authentication import denial, runtime
from parishkit.stewardship.accounts.cryptography import CryptographicError
from parishkit.stewardship.accounts.family_authentication import (
    runtime as family_runtime,
)
from parishkit.stewardship.accounts.policy import Capability
from parishkit.stewardship.accounts.role_refusal import RoleDenied, require
from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.accounts.sessions import authenticated_admin
from parishkit.stewardship.audit.schemas import Action, ActorKind, Outcome
from parishkit.stewardship.audit.services import record_action
from parishkit.stewardship.observability import Event, debug_swallowed, emit_failure
from parishkit.stewardship.storage import StorageInvariantError
from parishkit.stewardship.web.filter_errors import (
    FilterError,
    error_context,
    refused,
)
from parishkit.stewardship.web.refusals import with_status_code
from parishkit.stewardship.web.responses import campaign_response
from parishkit.stewardship.web.tables import report_table

from .directories import (
    CHECKS,
    DIRECTORY_SORTING,
    FIND_MINIMUM,
    PAGE_SIZE,
    REACH,
    REASONS,
    RESPONSES,
    DirectoryQuery,
    NoRehearsal,
    admit_mode,
    directory_page,
    find_families,
    response_columns,
    staff_entered,
    testing_codes_context,
)
from .directory_documents import head_names
from .export_services import admit_campaign
from .export_views import SAFE_FAILURES
from .read_admission import admit_report_read
from .report_paging import carried_filters, clamp_query, pop_page_size
from .response_dashboard import shown_mode

# The element id of the filter field a refusal can be shown at (#1332),
# and the reserved line it is described by: the complete gate's hint row
# after Apply, which shows the message over the gate's hint.
FIELD_IDS = {"exact_code": "exact-code"}
FIELD_LINES = {"exact_code": "directory-filters"}


def _principal(request, store, *, read_only=False):
    """Manual codes are ordinary Admin/Staff data, not a public-login capability."""
    principal = authenticated_admin(
        request, store=store, activity=not read_only, read_only=read_only
    )
    return require(principal, Capability.FAMILY_CODES)


# Closed, non-private values a link may carry (GET): the reach presets (such
# as "no campaign mail can reach"), the mailing-columns preset and, for the
# family directory's response presets (#933; the Response dashboard no longer
# links them), the Response and ParishSoft data to check choices, the
# response columns and the sort. Each is a closed vocabulary
# ``DirectoryQuery.parse`` checks; search text and exact codes stay POST
# only. There is no mode: the site's mode decides (#2324).
LINK_PRESETS = frozenset({"reach", "mailing", "response", "responses", "check", "sort"})


def page_address(report_url, query, *, postal):
    """The address bar after an in-place answer (``data-page-address``, #536).

    A preset link leaves its query in the address bar, so without this a
    Reload or Back after a filter change would silently bring the preset
    back. ui-v1.js replaces the address with this one after each in-place
    POST answer. When the filters are link-safe (no search and no exact
    code) it is the page with the closed ``LINK_PRESETS`` values that differ
    from the defaults, in the order the dashboard's links use, so a reload
    keeps them (on the first page: the page number is not a preset).
    Otherwise it is the bare page, since search text and codes never enter
    a URL and the other filters alone would be a different list.
    """
    if query.search or query.exact_code:
        return report_url
    defaults = DirectoryQuery()
    values = {
        key: getattr(query, key)
        for key in ("response", "responses", "check", "sort", "reach")
        if getattr(query, key) != getattr(defaults, key)
    }
    if postal:
        values["mailing"] = "yes"
    return report_url + ("?" + urlencode(values) if values else "")


def mailing_option(parameters):
    """Remove and return the mailing-columns choice from the submitted form.

    ``parameters`` is a mutable QueryDict. The checkbox sends ``mailing=yes``
    only when checked; hidden fields carry ``yes`` or ``no``; an absent field
    means off.
    """
    values = parameters.pop("mailing", ["no"])
    if len(values) != 1 or values[0] not in {"yes", "no"}:
        raise ValueError("Invalid mailing-columns choice.")
    return values[0] == "yes"


def _audit(principal, campaign_id, *, postal, outcome, count, total, query):
    """Record scope/count, never names, codes, search strings or viewed contacts.

    A mailing-columns view records the reach its rows were selected with,
    "By postal mail only" (#951), not the page's own reach filter.
    """
    if postal:
        query = query.postal()
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


def _error(campaign_id, *, status, no_rehearsal=False):
    """Render fixed recovery text without reflecting private input or DB failures.

    ``no_rehearsal`` (a 409) says Testing responses were asked for after the
    campaign's rehearsal ended, so there are none to list (#933).
    """
    debug_swallowed("report request refused")
    response = HttpResponse(
        render_to_string(
            "stewardship/directory-error.html",
            {
                "campaign_id": campaign_id,
                "status": status,
                "no_rehearsal": no_rehearsal,
            },
        ),
        status=status,
        headers={"Cache-Control": "no-store"},
    )
    response.stewardship_safe_error = True
    if status == 503:
        response["Retry-After"] = "5"
    # The refusal log's codes for this page of its own (#1403).
    return with_status_code(response, *(["rehearsal_ended"] if no_rehearsal else []))


def _mailing_rows(rows):
    """Add each row's mail-merge addressee, as the postal export names it.

    Every row has a usable mailing address: mailing columns list only
    "By postal mail only" Families (#951).
    """
    for row in rows:
        row["addressee"] = (
            head_names(row["heads"], row["family_name"]) or row["family_name"]
        )


def _column_count(postal, dates, counts, checks, *, testing=False):
    """How many columns the table has, for its "No matching Families." row.

    Family, Family DUID, Envelope number, Family code, Campaign email
    deliverable and Contact details always; Responded in Production only
    (it reads live responses, so a Testing view leaves it out rather than
    mix modes, #933); the response columns, What to check and the two
    mailing columns when shown.
    """
    fixed = 6 if testing else 7
    return fixed + len(dates) + len(counts) + int(checks) + (2 if postal else 0)


@require_http_methods(["GET", "POST"])
def directory(request, campaign_id):
    """Recheck roles/scope through rendering and streaming; audit after guard close.

    A malformed Exact Family code (``FilterError``, #1332) draws the page
    again, answered 400, with the values sent in the filter form, the error
    at the code field and in the summary, and the results, export and links
    for no filters. The sort and the mailing-columns choice sent are kept:
    neither is refused, and the choice picks the columns and the export's
    kind. The audit then ends denied.
    Every other refusal of the filters keeps the error page.
    """
    finish, handed_off, refusal = None, False, None
    try:
        service = runtime()
        principal = _principal(request, service.store)
        # Filters are private POST state. The exceptions are the closed link
        # presets (LINK_PRESETS, such as ?reach=neither or ?mailing=yes),
        # which carry no private value and let other pages link to "Families
        # no campaign mail can reach", the mailing columns and the response
        # dashboard's lists of Families.
        if request.GET and (
            request.method != "GET" or not set(request.GET) <= LINK_PRESETS
        ):
            raise ValueError("Directory filters require private POST state.")
        parameters = (request.GET if request.GET else request.POST).copy()
        parameters.pop("csrfmiddlewaretoken", None)
        # Whose responses are read follows the site's mode (#2324), for
        # Administrators only (``shown_mode``): an old link's or form's mode
        # is refused, as an unknown field is.
        if "mode" in parameters:
            raise ValueError("The directory's mode follows the site's mode.")
        mode = shown_mode(campaign_id, principal)
        postal = mailing_option(parameters)
        # The selection pages 50 rows at a time, so that is the only size.
        pop_page_size(parameters, (PAGE_SIZE,), default=PAGE_SIZE)
        try:
            query = DirectoryQuery.parse(parameters)
        except FilterError as error:
            # Every closed choice was accepted before the code was checked,
            # so the sort sent is one the selection knows.
            refusal = error
            query = DirectoryQuery.parse({"sort": error.query.sort})
        query = replace(query, mode=mode)
        admit_mode(principal, query.mode)
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
            outcome = Outcome.SUCCEEDED if completed else Outcome.FAILED
            if completed and refusal is not None:
                # The page was shown, but not for the filters asked for.
                outcome = Outcome.DENIED
            try:
                _audit(
                    principal,
                    campaign_id,
                    postal=postal,
                    outcome=outcome,
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
            admit_mode(current, query.mode)
            admit_report_read(campaign_id)

        def content():
            """No source query, key operation or rendering escapes the read guard."""
            nonlocal count, total, query
            rings = family_runtime()
            report = directory_page(
                campaign_id, query, postal=postal, general=rings.general, mac=rings.mac
            )
            moved = clamp_query(query, report["total"], PAGE_SIZE)
            if moved is not None:
                query = moved
                report = directory_page(
                    campaign_id,
                    query,
                    postal=postal,
                    general=rings.general,
                    mac=rings.mac,
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
            testing = testing_codes_context(campaign_id, principal)
            report_url = reverse("admin:family_directory")
            # The response columns the Response choice shows (#933):
            # Submissions follows the envelope number and the dates follow
            # What to check (Family first, #932).
            dates, counts = (
                response_columns(query.response)
                if query.responses == "yes"
                else ((), ())
            )
            for row in report["rows"]:
                row["date_cells"] = [(column, row[column.field]) for column in dates]
                row["count_cells"] = [row[column.field] for column in counts]
            context = (
                report
                | testing
                | {
                    "campaign_id": campaign_id,
                    "mailing": postal,
                    "query": query,
                    "query_fields": query.form_values()
                    | {"mailing": "yes" if postal else "no"},
                    "report_url": report_url,
                    "page_address": page_address(report_url, query, postal=postal),
                    "table": report_table(
                        report["rows"],
                        number=query.page,
                        size=PAGE_SIZE,
                        total=report["total"],
                        # The page never sends a mode (#2324).
                        carry=[
                            pair
                            for pair in carried_filters(
                                query, ("mailing", "yes" if postal else "no")
                            )
                            if pair[0] != "mode"
                        ],
                        sorting=DIRECTORY_SORTING,
                        sort=query.sort,
                        action=report_url,
                        sizes=(PAGE_SIZE,),
                    ),
                    "reasons": REASONS,
                    "reaches": REACH,
                    "responses": RESPONSES,
                    "checks": CHECKS,
                    "testing_responses": query.mode == "testing",
                    "date_columns": dates,
                    "count_columns": counts,
                    "column_count": _column_count(
                        postal,
                        dates,
                        counts,
                        query.check != "any",
                        testing=query.mode == "testing",
                    ),
                    # Where Back from an Open form tab returns (#529): this
                    # page, with its filters, sort and page.
                    "page_path": request.get_full_path(),
                    # Responses Staff entered through Open form (#529).
                    "staff_entered": staff_entered(
                        campaign_id, [row["family_id"] for row in report["rows"]]
                    ),
                    "unreachable_url": reverse("admin:family_directory")
                    + "?reach=neither",
                    "rehearsal": report.get("rehearsal"),
                    "mutable": mutable,
                    "request_key": uuid4(),
                }
            )
            if refusal is not None:
                # The filter form shows what was sent (filter_query;
                # otherwise query), not what the results use (none). The
                # hint row keeps the message's height once it clears on the
                # first edit (filter_line), so nothing moves.
                context |= error_context(refusal, FIELD_IDS, FIELD_LINES) | {
                    "filter_query": refusal.query,
                    "filter_sizes": {FIELD_LINES[refusal.field]: refusal.message},
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
            return _error(campaign_id, status=503)
        handed_off = response.status_code == 200 and response.streaming
        if handed_off and refusal is not None:
            # The refusal was known before the response was made, so its
            # status is set before any byte is sent.
            refused(response)
        return response
    except (PermissionError, ObjectDoesNotExist):
        return denial()
    except (*SAFE_FAILURES, StorageInvariantError, CryptographicError, UnicodeError):
        return _error(campaign_id, status=503)
    except NoRehearsal:
        return _error(campaign_id, status=409, no_rehearsal=True)
    except ValueError:
        return _error(campaign_id, status=400)
    finally:
        if finish is not None and not handed_off:
            finish(False)


def _finder(request, store, *, read_only=False):
    """A viewer who may open the directory page and the Timeline."""
    principal = _principal(request, store, read_only=read_only)
    return require(principal, Capability.CAMPAIGN_REPORT)


@require_http_methods(["POST"])
def find_family(request, campaign_id):
    """The header's Find a Family results, as a fragment for its script (#561).

    A non-page action: the shared Admin header's search box posts its text
    here (CSRF-protected, so the text never enters a URL, a log line or the
    browser history) and shows the answer under the box. The viewer must be
    able to open both the directory page, whose search this runs, and the
    Timeline each result opens: Administrators and Staff. Both are
    rechecked inside the campaign read guard, as the directory does, and the
    search is audited as a directory view (search used, row counts; never
    the text). Refusals answer with an empty body and the status alone; the
    script shows its own fixed text for them. The one exception is a
    current session whose role lacks the search (a Ministry leader, or a
    role changed since the page was drawn): it gets the shared role
    refusal's typed JSON, whose sentence and fix the script shows, so a
    signed-in reader is never told to sign in again (#2189).
    """
    finish, handed_off = None, False
    try:
        service = runtime()
        principal = _finder(request, service.store)
        parameters = request.POST.copy()
        parameters.pop("csrfmiddlewaretoken", None)
        if request.GET or set(parameters) != {"search"}:
            raise ValueError("Find a Family takes only its search text.")
        query = DirectoryQuery.parse({"search": parameters["search"].strip()})
        if len(query.search) < FIND_MINIMUM:
            raise ValueError("Find a Family needs a longer search.")
        admit_report_read(campaign_id)
        _audit(
            principal,
            campaign_id,
            postal=False,
            outcome=Outcome.STARTED,
            count=0,
            total=0,
            query=query,
        )
        finalized, count, total = False, 0, 0

        def finish(completed):
            """Record the search's outcome once, with its row counts."""
            nonlocal finalized
            if finalized:
                return
            finalized = True
            try:
                _audit(
                    principal,
                    campaign_id,
                    postal=False,
                    outcome=Outcome.SUCCEEDED if completed else Outcome.FAILED,
                    count=count,
                    total=total,
                    query=query,
                )
            except (DatabaseError, StorageInvariantError) as error:
                emit_failure(error, event=Event.REPORT_AUDIT_FAILED)

        def authorize(guard):
            """Both roles' access is current, not just what the request began with."""
            current = _finder(request, service.store, read_only=True)
            if current.identity != principal.identity:
                raise PermissionError("Directory access changed.")
            admit_report_read(campaign_id)

        def content():
            """Run the search and render its results under the read guard."""
            nonlocal count, total
            found = find_families(campaign_id, query)
            count, total = len(found["rows"]), found["total"]
            context = found | {
                "campaign_id": campaign_id,
                "directory_url": reverse("admin:family_directory"),
                "search": query.search,
                "more": total > count,
            }
            return iter(
                (
                    render_to_string(
                        "stewardship/find-family-results.html",
                        context,
                        request=request,
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
    except RoleDenied as error:
        return role_refusal.response(error)
    except (PermissionError, ObjectDoesNotExist):
        return _bare(403)
    except (*SAFE_FAILURES, StorageInvariantError, CryptographicError, UnicodeError):
        return _bare(503)
    except ValueError:
        return _bare(400)
    finally:
        if finish is not None and not handed_off:
            finish(False)


def _bare(status):
    """An empty, uncached refusal: the box's script words it for the reader."""
    debug_swallowed("find a family refused")
    response = HttpResponse(status=status, headers={"Cache-Control": "no-store"})
    response.stewardship_safe_error = True
    # The refusal log's code for this refusal (#1444).
    with_status_code(response)
    if status == 503:
        response["Retry-After"] = "5"
    return response

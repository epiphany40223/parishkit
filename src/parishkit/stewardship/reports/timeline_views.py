"""The Timeline page of one Family, for Administrators and Staff (#477, #1570).

``reports/families/<family>/`` shows one Family of the current campaign
(``timeline``): a summary for both roles, and the full timeline for
Administrators only. ``<family>`` is the Family's campaign record id, an
opaque random UUID that, like a Mail message's, names no person; the Family's
name, DUID, envelope number and code never enter the URL, and the page's title
leaves the name out so browser history does not keep it. The page replaced
the Family timeline at the same address (#1570): nothing is redirected.

Admission, the campaign read guard, the role recheck inside it and the audit
follow the other campaign report pages (the response dashboard's
``response_dashboard``, say). The page shows Production records only (the
Administrator removed its Testing view on #1570), so a ``mode`` in the
address is refused like any unknown filter. The Family code is decrypted
under the credential key-set lock inside the guard, as the directory page
does.

The Administrator's timeline is a shared Admin table (``web/tables.py``)
whose When heading sorts it in place, newest first by default. Its rows are
few (one Family's lines), so it shows them all: no paging, no navigator. A
filter bar (``TimelineQuery``) narrows its lines by kind and by From and
Through days in the browser's zone, in place. The URL carries only closed
choices: ``kind``, ``start``, ``end``, ``zone``, ``sort`` and ``size=all``,
plus the hub's ``tab``.

The page is the Family's information hub (#1678, admin-portal spec "Family
information hub"): a header strip, then the shared tabs (``web/tabs.py``).
The Timeline tab holds everything above. The Family tab and one tab per
Member (slice 2) show the Family's census answers against their baseline,
with each change's review-pipeline stage (``hub_census``); the other tabs
come in later slices and are drawn unavailable ("Coming soon") until then
(``hub_tabs``).
"""

from dataclasses import dataclass, replace
from datetime import date, timedelta
from urllib.parse import urlencode

from django.core.exceptions import ObjectDoesNotExist
from django.db import DatabaseError, transaction
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_POST, require_safe

from parishkit.stewardship.accounts.authentication import denial, runtime
from parishkit.stewardship.accounts.campaign_family_test import (
    chosen_family_test_url,
)
from parishkit.stewardship.accounts.cryptography import CryptographicError
from parishkit.stewardship.accounts.family_authentication import (
    runtime as family_runtime,
)
from parishkit.stewardship.accounts.policy import Capability, allows
from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.accounts.sessions import authenticated_admin, database_now
from parishkit.stewardship.audit.schemas import Action, ActorKind, Outcome
from parishkit.stewardship.audit.services import record_action
from parishkit.stewardship.campaigns.credential_keys import key_set_lock
from parishkit.stewardship.campaigns.credential_models import FamilyCampaign
from parishkit.stewardship.campaigns.family_identity import code_context
from parishkit.stewardship.campaigns.models import Campaign
from parishkit.stewardship.observability import Event, emit_failure
from parishkit.stewardship.schema_primitives import timezone_names
from parishkit.stewardship.source.snapshot_models import SourceCurrent
from parishkit.stewardship.source.snapshot_names import snapshot_family_facts
from parishkit.stewardship.source.source_status import NAME_NOT_RECORDED
from parishkit.stewardship.source.workgroups import applied_evidence
from parishkit.stewardship.storage import StorageInvariantError
from parishkit.stewardship.web.contracts import filters
from parishkit.stewardship.web.dates import browser_day_start
from parishkit.stewardship.web.report_errors import report_unavailable
from parishkit.stewardship.web.responses import campaign_response
from parishkit.stewardship.web.security import private_response
from parishkit.stewardship.web.tables import ALL, paginate
from parishkit.stewardship.web.tabs import PARAMETER as TAB
from parishkit.stewardship.web.tabs import Tab, tab_set

from .export_views import SAFE_FAILURES
from .hub_census import HubCensus, read_hub_census
from .read_admission import admit_report_read
from .response_metrics import ResponseScope
from .timelines import KINDS, REACH, TIMELINE_SORTING, narrow, read_timeline

# The days a From or Through filter accepts, as on Census changes.
EARLIEST, LATEST = date(2000, 1, 1), date(2999, 12, 31)
# The hub's content sections, in tab order (#1678). "members" stands for
# the Member tabs, one per Member (``hub_census``). The Family and Member
# tabs are built (slice 2); the tabs not built yet (slices 3 and 4), with
# their labels, are drawn unavailable with COMING_SOON while they apply to
# the campaign (``hub_sections``).
SECTIONS = ("family", "members", "financial", "other")
COMING = (
    ("financial", _("Financial")),
    ("other", _("Cannot participate and Additional information")),
)
COMING_SOON = _("Coming soon")
# The Family and Member tabs' one panel template; the context's
# ``hub_subject`` is the chosen tab's subject.
CENSUS_PANEL = "stewardship/hub-census-panel.html"


@dataclass(frozen=True)
class TimelineQuery:
    """The timeline's filters: a ``KINDS`` key and From and Through days.

    ``start`` and ``end`` are ISO days ("2026-10-04", or "" when unset) in
    the browser's IANA ``zone``, which the page script fills in (the Admin
    portal requires JavaScript, #565), as on System logs and Census changes.
    The zone is kept only while a day needs it.
    """

    kind: str = "all"
    start: str = ""
    end: str = ""
    zone: str = ""

    @classmethod
    def parse(cls, values):
        """Accept only closed choices; ValueError for anything else.

        ``values`` holds single strings already checked by ``filters``. A day
        must be a whole ISO date in range, From may not follow Through, and a
        day needs a zone from the IANA catalog; without days, any zone is
        dropped, so it never stays in the page's links.
        """
        query = cls(**values)
        if query.kind not in KINDS:
            raise ValueError("Invalid timeline kind.")
        for value in (query.start, query.end):
            if not value:
                continue
            # fromisoformat() alone also takes "20261004" and week dates.
            day = date.fromisoformat(value)
            if day.isoformat() != value or not EARLIEST <= day <= LATEST:
                raise ValueError("Invalid timeline day.")
        if query.start and query.end and query.start > query.end:
            raise ValueError("From is after Through.")
        if not (query.start or query.end):
            return replace(query, zone="")
        if query.zone not in timezone_names():
            raise ValueError("Timeline days need the browser's time zone.")
        return query

    @property
    def bounds(self):
        """The From and Through days as a half-open UTC interval; None if unset.

        Through is inclusive, so its bound is the start of the next day: a
        daylight-saving day is 23 or 25 hours (``browser_day_start``).
        """
        start = date.fromisoformat(self.start) if self.start else None
        end = date.fromisoformat(self.end) if self.end else None
        return (
            browser_day_start(start, self.zone) if start else None,
            browser_day_start(end + timedelta(days=1), self.zone) if end else None,
        )

    @property
    def carry(self):
        """The filters as (name, value) pairs, defaults left out, for links."""
        pairs = [("kind", self.kind if self.kind != "all" else "")]
        pairs += [("start", self.start), ("end", self.end), ("zone", self.zone)]
        return [(name, value) for name, value in pairs if value]

    @property
    def applied(self):
        """Whether any filter other than the default is applied."""
        return bool(self.carry)


def timeline_url(family_id, sort=None):
    """A Family's timeline URL, keeping a ``sort`` other than the default."""
    path = reverse("admin:timeline", args=[family_id])
    if sort is None or sort == TIMELINE_SORTING.default:
        return path
    return path + "?" + urlencode([("sort", sort)])


def parse_query(parameters):
    """The page's URL choices: ``(query, table values)``; anything else refused.

    The filters are ``TimelineQuery``'s. The table's ``sort`` must be one of
    the timeline's tokens and its ``size``, which the sort heading carries,
    may only be ``all``: the timeline is never paged. ``mode`` is no longer a
    choice (#1570), so it is refused as an unknown filter.
    """
    values = filters(
        parameters, allowed={"kind", "start", "end", "zone", "sort", "size", TAB}
    )
    # The hub's tab (#1678): tab_set reads it from the address itself, and
    # any key it does not know shows the first tab, as the tab rules say.
    values.pop(TAB, None)
    table = {key: values.pop(key) for key in ("sort", "size") if key in values}
    if table.get("size", ALL) != ALL:
        raise ValueError("The timeline is never paged.")
    TIMELINE_SORTING.parse(table)
    return TimelineQuery.parse(values), table


def timeline_table(lines, query, values):
    """The filtered timeline as one unpaged page of the shared table."""
    return paginate(
        narrow(lines, query.kind, query.bounds),
        values,
        default=ALL,
        carry=query.carry,
        sorting=TIMELINE_SORTING,
    )


@dataclass(frozen=True)
class Identity:
    """Who the page is about: name (None when ParishSoft no longer has the
    Family), DUID, envelope number, Family code (None without one), and
    whether campaign email can reach it (``reach``, a ``REACH`` key)."""

    duid: int
    name: str | None = None
    envelope: int | None = None
    code: str | None = None
    email_deliverable: bool = False
    reach: str = ""
    # The campaign's Reminder WorkGroup name when this Family is in it now
    # (#861): its Reminders are skipped.
    reminder_workgroup: str | None = None

    @property
    def reach_label(self):
        """Whether campaign email can reach the Family, and if not why, in words."""
        if self.reach in REACH:
            return REACH[self.reach]
        return REACH["deliverable"] if self.email_deliverable else _("No")


def open_form(identity, *, testing_codes, current=True, eligible=True):
    """Whether Open form is offered, and if not, why: ``(available, reason)``.

    The form is opened with the Family's code; in Testing mode the Family
    sign-in accepts only rehearsal codes, so a live code would be refused.
    The hand-off (``assisted_entry.open_form``) also refuses a campaign that
    is not the current one (``current``) and a Family that may not use the
    Family form (``eligible``, its ``portal_eligible``), so the page shows
    those refusals too rather than offering a button that fails (#1331).
    """
    if identity.code is None:
        return False, "no_code"
    if testing_codes:
        return False, "testing"
    if not current:
        return False, "not_current"
    if not eligible:
        return False, "not_eligible"
    return True, ""


def hub_sections(values):
    """The hub's content sections that apply to a campaign, as ``SECTIONS`` keys.

    ``values`` is the campaign configuration's values. The Family tab needs
    census; the Member tabs census or Ministry stewardship; Financial needs
    Financial stewardship; the last tab needs Ministry stewardship (where
    cannot-participate answers are given) or Additional information. A tab
    whose subject is not in the campaign is left out (ui-conventions
    "Conditional fields").
    """
    modules = set(values.get("modules", ()))
    applies = {
        "family": "census" in modules,
        "members": "census" in modules or "ministry" in modules,
        "financial": "financial" in modules,
        "other": "ministry" in modules or bool(values.get("additional_information")),
    }
    return tuple(key for key in SECTIONS if applies[key])


def hub_tabs(page_url, parameters, sections, census=None):
    """The hub's ``TabSet``: Timeline, Family, the Members, then the rest.

    ``parameters`` is the address's (``request.GET``); ``sections`` are the
    ``hub_sections`` keys that apply and ``census`` the ``HubCensus`` read
    for them (None reads as no response yet). The Family tab is drawn
    whenever census applies, saying so when the Family has no census
    answers; each Member tab is named by its Member, and is unavailable
    ("Coming soon") in a campaign without census, whose Member tabs show
    Ministry joins and leaves from a later slice. Each tab not built yet is
    drawn unavailable with the reason "Coming soon". A tab's link carries
    nothing but ``tab``: the Timeline's filters are its panel's own.
    """
    census = census or HubCensus()
    tabs = [Tab("timeline", _("Timeline"), template="stewardship/timeline-panel.html")]
    if "family" in sections:
        tabs.append(Tab("family", _("Family"), template=CENSUS_PANEL))
    if "members" in sections:
        tabs += [
            Tab(member.key, member.label, template=CENSUS_PANEL)
            if census.census
            else Tab(member.key, member.label, unavailable=COMING_SOON)
            for member in census.members
        ]
    tabs += [
        Tab(key, label, unavailable=COMING_SOON)
        for key, label in COMING
        if key in sections
    ]
    return tab_set(page_url, parameters, tabs)


def page_context(campaign, family_id, identity, timeline, as_of, **options):
    """The page's template context; a pure function of its inputs.

    ``timeline`` is the ``timelines.Timeline``. The ``options`` are ``full``
    (an Administrator's view: the timeline, its filters and its export),
    ``show_codes`` (the role may see Family codes; without it the code and
    Open form are left out), ``testing_codes`` and ``family_test_url`` (the
    chosen-Family test page's link in Testing mode, given only to roles
    that may open that page), ``current`` and ``eligible`` (whether the
    campaign is the current one and the Family may use the Family form, for
    Open form), ``query``, the validated ``TimelineQuery``, ``values``, the
    timeline table's validated ``sort`` and ``size``, ``export``, the
    export form's values (``export_context``), shown only on the full view,
    and for the hub's tabs (``hub_tabs``) ``parameters``, the address's,
    ``sections``, the content tabs that apply (``hub_sections``), and
    ``census``, the Family and Member tabs' ``HubCensus``.
    """
    full = options.get("full", False)
    query = options.get("query") or TimelineQuery()
    table = None
    if full:
        table = timeline_table(timeline.events, query, options.get("values", {}))
    available, reason = open_form(
        identity,
        testing_codes=options.get("testing_codes", False),
        current=options.get("current", True),
        eligible=options.get("eligible", True),
    )
    page_url = reverse("admin:timeline", args=[family_id])
    census = options.get("census") or HubCensus()
    tabs = hub_tabs(
        page_url,
        options.get("parameters", {}),
        options.get("sections", ()),
        census,
    )
    return {
        "campaign": campaign,
        "identity": identity,
        "full": full,
        "timeline": timeline,
        # The hub's Last portal action (#1678), for Administrators and Staff
        # (Administrator decision, 2026-10-18).
        "last_action": timeline.last_action,
        "tabs": tabs,
        # The chosen Family or Member tab's subject (None on another tab, and
        # on the Family tab before any census answers); its panel is the only
        # one drawn.
        "hub_subject": census.subject(tabs.selected),
        "census_tabs": "family" in options.get("sections", ()),
        "census_url": reverse("admin:census_changes"),
        "table": table,
        "query": query,
        "kinds": KINDS,
        # Every line was filtered out, rather than none recorded yet.
        "filtered_out": table is not None and not table.rows and bool(timeline.events),
        "as_of": as_of,
        "show_codes": options.get("show_codes", False),
        "open_form": available,
        "open_form_reason": reason,
        # Open form posts here (#529); the code never goes into a URL.
        "open_form_url": reverse("admin:family_open_form", args=[family_id]),
        "family_test_url": options.get("family_test_url"),
        "page_url": page_url,
        "export": options.get("export") if full else None,
    }


def export_context(campaign_id, family_id):
    """The export form's values: its address, key, time zones and availability."""
    from uuid import uuid4

    from .export_services import admit_campaign

    try:
        admit_campaign(campaign_id, mutating=True)
        mutable = True
    except PermissionError:
        mutable = False
    return {
        "url": reverse("admin:timeline_export", args=[family_id]),
        "request_key": uuid4(),
        "timezones": sorted(timezone_names()),
        "mutable": mutable,
    }


def _principal(request, store, *, read_only=False):
    """A current Admin or Staff user, reloaded from policy on every call."""
    principal = authenticated_admin(
        request, store=store, activity=not read_only, read_only=read_only
    )
    if not allows(principal, Capability.CAMPAIGN_REPORT):
        raise PermissionError("The timeline is unavailable.")
    return principal


def _full(principal):
    """Only Administrators see the full timeline; Staff get the summary."""
    return "administrator" in principal.roles


def _audit(principal, campaign_id, family_id, outcome):
    """Record the view: who, which Family of which campaign, and how it ended.

    The subject is the Family's opaque campaign record id, as a Mail message
    view names its message (``DELIVERY_VIEWED``), so a later review can tell
    who looked at which Family; a view left unrecorded could never be filled
    in. No name, DUID, code, filter or shown value is copied. The
    Administrator confirmed recording the Family on 2026-10-05 (reports
    spec). An append takes no row locks, so it runs after the read guard
    closes.
    """
    with transaction.atomic():
        system = SystemConfiguration.objects.select_related(
            "active_configuration__parish"
        ).get()
        record_action(
            Action.FAMILY_TIMELINE_VIEWED,
            actor_kind=ActorKind.PORTAL_USER,
            actor_id=principal.identity,
            subject_id=family_id,
            parish_id=system.active_configuration.parish.pk,
            campaign_id=campaign_id,
            context={"outcome": outcome},
        )


def read_identity(family, *, codes, workgroup=None):
    """The Family's name and envelope number, its code and its email reach.

    The name and envelope number come from the latest ParishSoft data (two
    snapshot reads, ``snapshot_family_facts``). The code is decrypted only
    when ``codes`` (the role may see Family codes), under the key-set lock,
    so a rotation cannot change keys between the read and the decryption;
    the caller holds the read guard's transaction. ``workgroup`` is the
    campaign's configuration values, to tell whether its Reminder WorkGroup
    holds this Family now. While a rename waits for a refresh, that is the
    previous WorkGroup, whose name is the one shown (#1305).
    """
    workgroup_name = None
    if workgroup is not None:
        evidence = applied_evidence(workgroup)
        if evidence is not None and family.family_duid in evidence["family_duids"]:
            workgroup_name = evidence["name"]
    snapshot = SourceCurrent.objects.values_list("snapshot_id", flat=True).first()
    # A blank surname is a data gap, worded as the name (#1216).
    facts = snapshot_family_facts(
        snapshot, [family.family_duid], NAME_NOT_RECORDED
    ).get(family.family_duid)
    code = None
    if codes and family.code_ciphertext:
        rings = family_runtime()
        with key_set_lock(rings.general, rings.mac):
            code = rings.general.decrypt(
                family.code_ciphertext, context=code_context(family.pk)
            ).decode("ascii")
    return Identity(
        family.family_duid,
        facts.name if facts else None,
        facts.envelope if facts else None,
        code,
        family.email_deliverable,
        family.deliverability_reason,
        workgroup_name,
    )


@require_safe
def timeline(request, campaign_id, family_id):
    """One Family's summary (Admin and Staff) and timeline (Admin only)."""
    finish, handed_off = None, False
    try:
        service = runtime()
        principal = _principal(request, service.store)
        try:
            query, values = parse_query(request.GET)
        except ValueError:
            return private_response("Invalid report filters.\n", status=400)
        admit_report_read(campaign_id)
        finalized, found = False, False

        def finish(completed):
            """Audit completion once, after the read-only transaction closes.

            Only a view of a Family that exists in this campaign is audited,
            as a Mail message view is only once its message is found: an
            unknown id or another campaign's Family must not leave a record
            pairing the two.
            """
            nonlocal finalized
            if finalized or not found:
                return
            finalized = True
            try:
                _audit(
                    principal,
                    campaign_id,
                    family_id,
                    Outcome.SUCCEEDED if completed else Outcome.FAILED,
                )
            except (DatabaseError, StorageInvariantError) as error:
                emit_failure(error, event=Event.REPORT_AUDIT_FAILED)

        def authorize(guard):
            """A role change while the page is produced ends it."""
            nonlocal principal
            fresh = _principal(request, service.store, read_only=True)
            if fresh.identity != principal.identity:
                raise PermissionError("Timeline access changed.")
            principal = fresh
            admit_report_read(campaign_id)

        def content():
            """Read the Family once under the guard and render the page."""
            nonlocal found
            campaign = Campaign.objects.select_related("active_configuration").get(
                pk=campaign_id
            )
            # A Family of another campaign is refused like a missing one.
            family = FamilyCampaign.objects.get(pk=family_id, campaign_id=campaign_id)
            found = True
            full = _full(principal)
            codes = allows(principal, Capability.FAMILY_CODES)
            # Read once: the runtime mode attributes sign-ins and decides
            # whether a live code would be accepted now.
            system = SystemConfiguration.objects.get()
            testing_codes = system.mode == "testing"
            # Production records only (#1570): the Testing view was removed.
            records = read_timeline(
                ResponseScope(campaign_id),
                family.pk,
                full=full,
                current_mode=system.mode,
            )
            # The hub's Family and Member tabs (#1678): read on every load,
            # whichever tab is chosen, since the Member tabs' names need it.
            sections = hub_sections(campaign.active_configuration.values)
            census = None
            if "members" in sections:
                census = read_hub_census(
                    campaign_id, family.pk, census="family" in sections
                )
            # The chosen-Family test page is Administrator only (CONFIGURE).
            test_url = None
            if codes and testing_codes and allows(principal, Capability.CONFIGURE):
                test_url = chosen_family_test_url(system, campaign)
            context = page_context(
                campaign,
                family.pk,
                read_identity(
                    family,
                    codes=codes,
                    workgroup=campaign.active_configuration.values,
                ),
                records,
                database_now(),
                full=full,
                show_codes=codes,
                testing_codes=testing_codes,
                current=system.current_campaign_id == campaign_id,
                eligible=family.portal_eligible,
                family_test_url=test_url,
                query=query,
                values=values,
                export=export_context(campaign_id, family.pk) if full else None,
                parameters=request.GET,
                sections=sections,
                census=census,
            )
            page = render_to_string(
                "stewardship/timeline.html", context, request=request
            )
            return iter((page.encode(),))

        response = campaign_response(
            request,
            [campaign_id],
            authorize=authorize,
            open_content=content,
            on_close=finish,
        )
        # Admission refused before streaming (busy or closing): the Admin
        # report error page, as the other campaign reports answer.
        if response.status_code == 503 and not response.streaming:
            return report_unavailable()
        handed_off = response.status_code == 200 and response.streaming
        return response
    except (PermissionError, ObjectDoesNotExist):
        return denial()
    except (*SAFE_FAILURES, StorageInvariantError, CryptographicError):
        return report_unavailable()
    except ValueError as error:
        # The filters were validated above, so this is a shaping fault.
        emit_failure(error, event=Event.REPORT_SHAPING_FAILED)
        return report_unavailable()
    finally:
        if finish is not None and not handed_off:
            finish(False)


@require_POST
def export(request, campaign_id, family_id):
    """Queue this Family's timeline file, as the page's export form posts it.

    The page's full timeline is the Administrator's view, so only an
    Administrator may export it (``create_timeline_export`` rechecks inside its
    lock). The form carries the format, the time zone and a one-time request
    key; anything else (a ``mode`` included, since #1570) is refused, and the
    export is of Production records, as the page shows. The file can hold the
    Family's live code.
    """
    from uuid import UUID

    from .export_services import ExportRequestBound
    from .export_ui import _redirect
    from .timeline_exports import create_timeline_export

    try:
        service = runtime()
        principal = _principal(request, service.store)
        if not _full(principal):
            raise PermissionError("The timeline export is for Administrators.")
        parameters = request.POST.copy()
        parameters.pop("csrfmiddlewaretoken", None)
        fields = {"format", "browser_timezone", "request_key"}
        if (
            request.GET
            or set(parameters) != fields
            or any(len(parameters.getlist(key)) != 1 for key in parameters)
        ):
            return private_response("Invalid timeline export.\n", status=400)
        admit_report_read(campaign_id)
        result = create_timeline_export(
            service.store,
            principal.identity,
            campaign_id=campaign_id,
            family_id=family_id,
            mode="production",
            format=parameters["format"],
            browser_timezone=parameters["browser_timezone"],
            request_key=UUID(parameters["request_key"]),
        )
        return _redirect(result.pk)
    except ExportRequestBound:
        return private_response(
            "This export form was already used for a different export.\n", status=409
        )
    except (PermissionError, ObjectDoesNotExist):
        return denial()
    except (*SAFE_FAILURES, StorageInvariantError):
        return report_unavailable()
    except ValueError:
        return private_response("Invalid timeline export.\n", status=400)

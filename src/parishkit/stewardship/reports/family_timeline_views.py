"""The Family timeline page for Administrators and Staff (#477, PR 6).

``reports/families/<family>/`` shows one Family of the current campaign
(``family_timeline``): a summary for both roles, and the full timeline for
Administrators only. ``<family>`` is the Family's campaign record id, an
opaque random UUID that, like a Mail message's, names no person; the Family's
name, DUID, envelope number and code never enter the URL, and the page's title
leaves the name out so browser history does not keep it.

Admission, the campaign read guard, the role recheck inside it and the audit
follow the response lists (``response_list_views``). Production is the
default; Administrators may choose the campaign's active Testing rehearsal
(``mode=testing``) and Staff may not. The Family code is decrypted under the
credential key-set lock inside the guard, as the directory page does.

The Administrator's timeline is a shared Admin table (``web/tables.py``)
whose When heading sorts it in place, newest first by default. Its rows are
few (one Family's events), so it shows them all: no paging, no navigator.
The URL carries only closed choices: ``mode``, ``sort`` and ``size=all``.
"""

from dataclasses import dataclass
from urllib.parse import urlencode

from django.core.exceptions import ObjectDoesNotExist
from django.db import DatabaseError, transaction
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_safe

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
from parishkit.stewardship.source.snapshot_models import SourceCurrent
from parishkit.stewardship.source.snapshot_names import snapshot_family_facts
from parishkit.stewardship.source.workgroups import current_evidence
from parishkit.stewardship.storage import StorageInvariantError
from parishkit.stewardship.web.contracts import filters
from parishkit.stewardship.web.report_errors import report_unavailable
from parishkit.stewardship.web.responses import campaign_response
from parishkit.stewardship.web.security import private_response
from parishkit.stewardship.web.tables import ALL, paginate

from .export_views import SAFE_FAILURES
from .family_timeline import REACH, TIMELINE_SORTING, read_timeline
from .read_admission import admit_report_read
from .response_dashboard import rehearsal_epoch
from .response_metrics import MODES, ResponseScope


def timeline_url(family_id, mode="production", sort=None):
    """A Family's timeline URL with the closed ``mode`` and ``sort`` choices.

    Defaults are left out; a sort other than the default is kept, so the
    mode switch keeps the order the reader chose.
    """
    path = reverse("admin:family_timeline", args=[family_id])
    values = [("mode", mode)] if mode != "production" else []
    if sort is not None and sort != TIMELINE_SORTING.default:
        values.append(("sort", sort))
    return path + ("?" + urlencode(values) if values else "")


def parse_query(parameters):
    """The page's URL choices: ``(mode, table values)``; anything else refused.

    ``mode`` is production or testing. The table's ``sort`` must be one of
    the timeline's tokens and its ``size``, which the sort heading carries,
    may only be ``all``: the timeline is never paged.
    """
    values = filters(parameters, allowed={"mode", "sort", "size"})
    mode = values.pop("mode", "production")
    if mode not in MODES or values.get("size", ALL) != ALL:
        raise ValueError("Invalid Family timeline choices.")
    TIMELINE_SORTING.parse(values)
    return mode, values


def timeline_table(events, mode, values):
    """The timeline as one unpaged page of the shared table, in the chosen sort."""
    carry = [("mode", mode)] if mode != "production" else []
    return paginate(events, values, default=ALL, carry=carry, sorting=TIMELINE_SORTING)


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


def open_form(identity, *, testing_codes):
    """Whether Open form is offered, and if not, why: ``(available, reason)``.

    The form is opened with the Family's code; in Testing mode the Family
    sign-in accepts only rehearsal codes, so a live code would be refused.
    """
    if identity.code is None:
        return False, "no_code"
    if testing_codes:
        return False, "testing"
    return True, ""


def page_context(campaign, family_id, identity, mode, timeline, as_of, **options):
    """The page's template context; a pure function of its inputs.

    ``timeline`` is the ``family_timeline.Timeline`` (None for a Testing view
    with no rehearsal). The ``options`` are ``full`` (an Administrator's
    view: the timeline and the mode switch), ``show_codes`` (the role may see
    Family codes; without it the code and Open form are left out),
    ``testing_codes`` and ``family_test_url`` (the active parishioner family directory's
    Testing-mode link, given only to roles that may open that page), and
    ``values``, the timeline table's validated ``sort`` and ``size``.
    """
    full = options.get("full", False)
    values = options.get("values", {})
    table = None
    if full and timeline is not None:
        table = timeline_table(timeline.events, mode, values)
    # The checked request's sort, kept even when no table is shown (a Testing
    # view with no rehearsal), so switching back to Production keeps it.
    sort = table.sort if table is not None else values.get("sort")
    testing_codes = options.get("testing_codes", False)
    available, reason = open_form(identity, testing_codes=testing_codes)
    return {
        "campaign": campaign,
        "identity": identity,
        "mode": mode,
        "testing": mode == "testing",
        "full": full,
        "timeline": timeline,
        "table": table,
        "as_of": as_of,
        "show_codes": options.get("show_codes", False),
        "open_form": available,
        "open_form_reason": reason,
        "family_test_url": options.get("family_test_url"),
        "production_url": timeline_url(family_id, sort=sort),
        "testing_url": timeline_url(family_id, "testing", sort),
        "directory_url": reverse("admin:family_directory"),
    }


def _principal(request, store, *, read_only=False):
    """A current Admin or Staff user, reloaded from policy on every call."""
    principal = authenticated_admin(
        request, store=store, activity=not read_only, read_only=read_only
    )
    if not allows(principal, Capability.CAMPAIGN_REPORT):
        raise PermissionError("The Family timeline is unavailable.")
    return principal


def _full(principal):
    """Only Administrators see the full timeline; Staff get the summary."""
    return "administrator" in principal.roles


def _admit_mode(principal, mode):
    """Testing records are shown to Administrators only."""
    if mode == "testing" and not _full(principal):
        raise PermissionError("Testing responses are shown to Administrators only.")


def _audit(principal, campaign_id, family_id, outcome):
    """Record the view: who, which Family of which campaign, and how it ended.

    The subject is the Family's opaque campaign record id, as a Mail message
    view names its message (``DELIVERY_VIEWED``), so a later review can tell
    who looked at which Family; a view left unrecorded could never be filled
    in. No name, DUID, code or shown value is copied. The Administrator
    confirmed recording the Family on 2026-10-05 (reports spec). An
    append takes no row locks, so it runs after the read guard closes.
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
    snapshot reads), as on the response lists. The code is decrypted only
    when ``codes`` (the role may see Family codes), under the key-set lock,
    so a rotation cannot change keys between the read and the decryption;
    the caller holds the read guard's transaction. ``workgroup`` is the
    campaign's configuration values, to tell whether its Reminder WorkGroup
    holds this Family now.
    """
    workgroup_name = None
    if workgroup is not None:
        name, evidence = current_evidence(workgroup)
        if evidence is not None and family.family_duid in evidence["family_duids"]:
            workgroup_name = name
    snapshot = SourceCurrent.objects.values_list("snapshot_id", flat=True).first()
    facts = snapshot_family_facts(snapshot, [family.family_duid], "Family").get(
        family.family_duid
    )
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
def family_timeline(request, campaign_id, family_id):
    """One Family's summary (Admin and Staff) and timeline (Admin only)."""
    finish, handed_off = None, False
    try:
        service = runtime()
        principal = _principal(request, service.store)
        try:
            mode, values = parse_query(request.GET)
        except ValueError:
            return private_response("Invalid report filters.\n", status=400)
        _admit_mode(principal, mode)
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
                raise PermissionError("Family timeline access changed.")
            _admit_mode(fresh, mode)
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
            epoch = rehearsal_epoch(campaign_id) if mode == "testing" else None
            timeline = None
            if mode == "production" or epoch is not None:
                timeline = read_timeline(
                    ResponseScope(campaign_id, mode, epoch),
                    family.pk,
                    full=full,
                    current_mode=system.mode,
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
                mode,
                timeline,
                database_now(),
                full=full,
                show_codes=codes,
                testing_codes=testing_codes,
                family_test_url=test_url,
                values=values,
            )
            page = render_to_string(
                "stewardship/family-timeline.html", context, request=request
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
        # The mode was validated above, so this is a shaping fault.
        emit_failure(error, event=Event.REPORT_SHAPING_FAILED)
        return report_unavailable()
    finally:
        if finish is not None and not handed_off:
            finish(False)

"""The Timeline page of one Admin portal user, for Administrators (#1866).

``users/<user>/timeline/`` (``user_timeline``) shows one user of the Users
page: a summary and every line of the user's timeline (``user_timelines``),
in the Family Timeline's shared table, newest first with the When heading
reversing it in place, paged with the shared navigator (a person can have
thousands of lines). ``<user>`` is the user's stable record id; the address
never enters a URL, and the page's title leaves the Name out so browser
history does not keep it.

Admission and the recheck after rendering follow the other Users routes
(``user_views``): Administrators only (``MANAGE_USERS``), read in one
read-only snapshot, rechecked before the answer is released, never cached,
and audited by the user's record id (``user_timeline_viewed``), plus one
``family_timeline_viewed`` per Family whose records it shows. Unlike
them it reads the configurations' records directly rather than through the
applied configuration, as a Family's Timeline does: it only reads history,
so it still answers during a restore review (admin-portal spec). The filter
bar is the Family Timeline's (``reports.timeline_views.TimelineQuery``),
with this page's Kind choices; the URL carries only closed choices.

A user who is a ParishSoft Family member also sees, below the summary, one
panel per Family with that Family's Timeline summary fields, and the
Family's lines join the table, tagged with its name (#1867, admin-portal
spec "User timeline Family part"). The Family code is shown and decrypted
only for a reader who may see Family codes, as on the Family's Timeline.
"""

from dataclasses import dataclass, replace

from django.db import transaction
from django.shortcuts import render
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_safe

from parishkit.stewardship.audit.schemas import Action, ActorKind, Outcome
from parishkit.stewardship.audit.services import record_action
from parishkit.stewardship.campaigns.work_locks import read_transaction
from parishkit.stewardship.reports.timeline_views import TimelineQuery
from parishkit.stewardship.reports.timelines import TIMELINE_SORTING, narrow
from parishkit.stewardship.web.contracts import filters
from parishkit.stewardship.web.refusals import UserFacingMissing
from parishkit.stewardship.web.tables import paginate, table_parameters

from .admin_editing import error_response, principal
from .authentication import runtime
from .policy import Capability, allows
from .sessions import database_now
from .user_timelines import KINDS, read_timeline, record_family_views
from .user_views import ERRORS, _recheck

# Rows a page of the timeline shows by default (the shared table's sizes
# offer the others).
PAGE_SIZE = 50


@dataclass(frozen=True)
class UserTimelineQuery(TimelineQuery):
    """The Family Timeline's filters, with a user timeline's Kind choices.

    The day and zone rules are ``TimelineQuery``'s own; only the Kind is
    checked against ``user_timelines.KINDS`` instead.
    """

    @classmethod
    def parse(cls, values):
        """Accept only closed choices; ValueError for anything else."""
        kind = values.get("kind", "all")
        if kind not in KINDS:
            raise ValueError("Invalid timeline kind.")
        return replace(super().parse({**values, "kind": "all"}), kind=kind)


def parse_query(parameters):
    """The page's URL choices: ``(query, table values)``; anything else refused.

    The filters are ``UserTimelineQuery``'s; ``page``, ``size`` and
    ``sort`` are the shared table's, checked here on an empty table so a
    refused choice is refused before anything is read.
    """
    values = filters(
        parameters,
        allowed={"kind", "start", "end", "zone"} | table_parameters(),
    )
    table = {key: values.pop(key) for key in table_parameters() if key in values}
    paginate((), table, default=PAGE_SIZE, sorting=TIMELINE_SORTING)
    return UserTimelineQuery.parse(values), table


def missing():
    """An unknown user id: never a user, or an old or mistyped link."""
    return UserFacingMissing(
        _("That user was not found."),
        fix=_("Return to Users to see the current list."),
        link=reverse("admin:users"),
        link_label=_("Users"),
    )


def page_context(timeline, query, values, as_of, *, show_codes=False):
    """The page's template context; a pure function of its inputs.

    ``timeline`` is the ``UserTimeline``, ``query`` the validated
    ``UserTimelineQuery`` and ``values`` the table's validated page, size
    and sort. ``show_codes`` says the reader may see Family codes, for the
    Family panels' Family code and Open form (#1867).
    """
    person = timeline.person
    # Family form activity is offered only when a Family's form lines are
    # listed (ui-conventions "Conditional fields"). An address that still
    # names it (an older link) reads as All, as an unoffered tab falls back
    # to the first, so the Kind menu never shows a choice it does not hold.
    kinds = {
        key: label
        for key, label in KINDS.items()
        if key != "form" or timeline.form_lines
    }
    if query.kind not in kinds:
        query = replace(query, kind="all")
    table = paginate(
        narrow(timeline.events, query.kind, query.bounds),
        values,
        default=PAGE_SIZE,
        carry=query.carry,
        sorting=TIMELINE_SORTING,
    )
    return {
        "person": person,
        "summary": timeline.summary,
        "accounts": timeline.accounts,
        "table": table,
        "query": query,
        "kinds": kinds,
        # The ParishSoft Families the address belongs to (#1867), each a
        # ``FamilyPart``; none shows the "not found" line instead.
        "families": timeline.families,
        "show_codes": show_codes,
        # Every line was filtered out, rather than none recorded yet.
        "filtered_out": not table.rows and bool(timeline.events),
        "as_of": as_of,
        "page_url": reverse("admin:user_timeline", args=[person.id]),
        # Only a current user has an Edit page.
        "edit_url": reverse("admin:user_edit", args=[person.id])
        if person.role
        else None,
    }


@require_safe
def user_timeline(request, user_id):
    """One user's summary and timeline, for Administrators."""
    try:
        service = runtime()
        actor = principal(request, service, capability=Capability.MANAGE_USERS)
        query, values = parse_query(request.GET)
        codes = allows(actor, Capability.FAMILY_CODES)
        with read_transaction():
            try:
                timeline = read_timeline(
                    user_id,
                    actor.identity,
                    codes=codes,
                    # The chosen-Family test page is Administrator only, as
                    # on the Family's Timeline.
                    test_link=allows(actor, Capability.CONFIGURE),
                    # Page views are read only while they are shown (#2006).
                    views=query.kind == "views",
                )
            except LookupError:
                # Refused before any audit: no row pairs a reader with an id
                # that names no user.
                raise missing() from None
            as_of = database_now()
        response = render(
            request,
            "stewardship/user-timeline.html",
            page_context(timeline, query, values, as_of, show_codes=codes),
        )
        with transaction.atomic():
            current = _recheck(request, service, actor)
            record_action(
                Action.USER_TIMELINE_VIEWED,
                actor_kind=ActorKind.PORTAL_USER,
                actor_id=current.identity,
                subject_id=user_id,
                context={"outcome": Outcome.SUCCEEDED},
            )
            # Each Family whose records the page showed, as its own
            # Timeline records a view (#1867).
            record_family_views(timeline, current.identity)
        response["Cache-Control"] = "no-store"
        return response
    except ERRORS as error:
        return error_response(error)

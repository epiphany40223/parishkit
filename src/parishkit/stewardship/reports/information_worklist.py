"""Additional information on the shared review worklist (#1685).

``information.information_page`` reads the requests (the installed SQL
selection names, searches and dates them); this module places each one in
its pipeline stage, filters and counts them by stage, turns one page of them
into the review worklist's rows (``web/review_worklist.build``) and records
the worklist's decisions through ``responses.information``
(``InformationDecisions``, the stream's ``DecisionService``). The stages,
words and role actions are ``parishkit.stewardship.pipeline``'s; nothing here
decides what is allowed: the service and its SQL guard (schema
information_followup.sql, migration 0126) refuse what a reader may not do.
"""

from collections import Counter
from dataclasses import dataclass, replace
from uuid import UUID

from django.db import IntegrityError
from django.urls import reverse
from django.utils.datastructures import MultiValueDict
from django.utils.html import format_html
from django.utils.text import Truncator

from parishkit.stewardship.accounts.models import PortalUser
from parishkit.stewardship.observability import Event, emit_failure
from parishkit.stewardship.pipeline import Stage, Stream, information_placement
from parishkit.stewardship.responses.information import (
    StaleItem,
    decide_information,
    information_stage,
)
from parishkit.stewardship.responses.models import (
    AdditionalInformationItem,
    AdditionalInformationRevision,
)
from parishkit.stewardship.source.source_status import source_status_label
from parishkit.stewardship.web.contracts import filters
from parishkit.stewardship.web.presentation import duid
from parishkit.stewardship.web.review_worklist import (
    EVERY_STAGE,
    RETIRED_DATES,
    WORDS,
    Column,
    StaleDecision,
    build,
    page_address,
)
from parishkit.stewardship.web.tables import Sorting, fold, name_order

from .information import InformationQuery, information_page

# The stream's stages, in the order the Stage filter lists them (review
# pipeline spec, "Additional information stream"). History (superseded or
# withdrawn requests) is a choice of its own after them.
STAGES = (Stage.TO_REVIEW, Stage.TO_HANDLE, Stage.IGNORED, Stage.HANDLED)
STAGE_CHOICES = frozenset(
    {*(stage.value for stage in STAGES), Stage.HISTORY.value, EVERY_STAGE}
)
# The menu's and My review work's read of the stream (#2101;
# ``admin_context.OPEN_COUNTS`` embeds it): the campaign's current live
# requests grouped by the state ``information_placement`` reads, as JSON
# text. ``staff_ignored`` is ``responses.information.staff_ignored`` in SQL:
# the latest Staff revision's review decision is Ignored, or (saved before
# migration 0126) it has none. Superseded and withdrawn requests are
# History, which no card or menu entry counts. ``group_stages`` places it.
# Takes ``%(campaign)s``.
OPEN_GROUPS = """
SELECT coalesce(jsonb_agg(jsonb_build_array(
        g.follow_up_needed, g.followed_up, g.staff_ignored, g.n)), '[]')::text
FROM (
    SELECT i.follow_up_needed, i.followed_up_at IS NOT NULL AS followed_up,
        coalesce((SELECT coalesce(r.review_decision, 'ignored')='ignored'
            FROM stewardship_information_revision r WHERE r.item_id=i.id
            ORDER BY r.expected_version DESC LIMIT 1), false) AS staff_ignored,
        count(*) AS n
    FROM stewardship_additional_information i
    JOIN stewardship_submission s ON s.id=i.submission_id
    WHERE s.campaign_id=%(campaign)s AND s.mode='live'
        AND i.disposition='current_actionable'
    GROUP BY 1, 2, 3
) g
"""


def group_stages(groups):
    """``OPEN_GROUPS``' decoded groups counted by stage value (#2101).

    Each ``[follow_up_needed, followed_up, staff_ignored, count]`` group is
    placed by ``information_placement``, the mapping the worklist places
    each request with (``requests``), so a count cannot drift from its
    Stage choice. A state the mapping does not place raises ValueError.
    """
    counts = Counter()
    for needed, done, ignored, count in groups:
        placement = information_placement(
            disposition="current_actionable",
            follow_up_needed=needed,
            followed_up=done,
            staff_ignored=ignored,
        )
        counts[placement.stage.value] += count
    return dict(counts)


# The identity columns, in the shared column order (UX conventions, "Table
# column order"), each sorted by ``SORTING``'s tokens.
COLUMNS = (
    Column(
        "family",
        "Family",
        sort="family",
        detail="family_duid",
        detail_heading="Family DUID",
        detail_sort="duid",
    ),
)
# How much of the Family's text the row shows; its request page shows all.
EXCERPT = 240
# PostgreSQL's SQLSTATE for a unique violation: a racing second decision of
# the same version, which reads as the stale notice.
UNIQUE_VIOLATION = "23505"


def _text(value):
    """Case-insensitive text sort key; a missing value sorts as empty."""
    return (value or "").casefold()


# The list is complete in memory, so every column sorts here. Families sort
# by the shown name (surname first, then heads), as the directory does; rows
# equal in the chosen column keep the default order (UX conventions,
# "Default row order", #2124): the name, the Family DUID, then the request's
# own id (a request names no Member).
SORTING = Sorting.by_column(
    {
        "family": lambda row: fold(row["family_name"]) or None,
        "duid": lambda row: row["family_duid"],
        "submitted": lambda row: row["submitted_at"],
    },
    default="family",
    descending_first={"submitted"},
    then=lambda row: name_order(row["family_name"], row["family_duid"], str(row["id"])),
)
# The queued complete export's own sort for each table sort, so the file
# lists the requests in the table's order.
EXPORT_SORTS = {
    "family": "name",
    "-family": "name_desc",
    "duid": "duid",
    "-duid": "duid_desc",
    "submitted": "oldest",
    "-submitted": "newest",
}
# What the queued export's follow-up filters can say for each Stage choice
# (disposition, needed, completed): To review and Ignored are both "not
# needed, not completed" there, and History is every request, current or
# not; the export pane says so.
EXPORT_FILTERS = {
    Stage.TO_REVIEW.value: ("current_actionable", "no", "no"),
    Stage.IGNORED.value: ("current_actionable", "no", "no"),
    Stage.TO_HANDLE.value: ("current_actionable", "yes", "no"),
    Stage.HANDLED.value: ("current_actionable", "any", "yes"),
    Stage.HISTORY.value: ("all", "any", "any"),
    EVERY_STAGE: ("current_actionable", "any", "any"),
}


@dataclass(frozen=True, repr=False)
class WorklistQuery:
    """The worklist's filters: the Stage, then the selection's own.

    ``information`` is the selection's query (search and, from the CLI, dates
    and their ``--zone``) over every request; the Stage is applied here, since the
    selection cannot tell an Ignored request from one To review.
    """

    stage: str = Stage.TO_REVIEW.value
    information: InformationQuery = InformationQuery(disposition="all")

    @classmethod
    def parse(cls, parameters):
        """Single bounded values: a known Stage, then the selection's filters.

        A date given without a known ``--zone`` raises ``UnknownZone``, and
        any other bad value a ValueError, as the selection's own parse does.
        """
        if not hasattr(parameters, "getlist"):
            parameters = MultiValueDict(
                {key: [value] for key, value in parameters.items()}
            )
        values = filters(
            parameters, allowed={"stage", "search", "start", "end", "zone"}
        )
        stage = values.pop("stage", Stage.TO_REVIEW.value)
        if stage not in STAGE_CHOICES:
            raise ValueError("Invalid information stage.")
        query = InformationQuery.parse(values | {"disposition": "all"})
        return cls(stage=stage, information=query)

    def form_values(self):
        """The fields every form posts so its answer shows the same view."""
        values = self.information.form_values()
        return {
            "stage": self.stage,
            **{key: values[key] for key in ("search", "start", "end", "zone")},
        }

    def export_values(self, sort):
        """The queued export's filters for this view and the table's ``sort``."""
        disposition, needed, completed = EXPORT_FILTERS[self.stage]
        return self.information.form_values() | {
            "disposition": disposition,
            "needed": needed,
            "completed": completed,
            "sort": EXPORT_SORTS.get(sort, "name"),
        }


def _latest(ids):
    """Each request's newest Staff revision, by request id (text): one read."""
    return {
        str(revision.item_id): revision
        for revision in AdditionalInformationRevision.objects.filter(item_id__in=ids)
        .order_by("item_id", "-expected_version")
        .distinct("item_id")
    }


def requests(campaign_id, query, *, history=False):
    """Every request the filters match, each with its placement, and the counts.

    Returns ``(metadata, rows, counts)``: the selection's metadata (the
    ParishSoft data the names come from), the rows at the chosen Stage, and
    each stage's count (History's too) over every request the search and
    dates match, for the Stage filter's choices. ``history`` keeps History's
    rows under Every stage too: a review region's check (#2174) finds a
    drawn request whatever stage it has moved to.
    """
    result = information_page(campaign_id, query.information, whole=True)
    rows = result["rows"]
    latest = _latest([row["id"] for row in rows])
    labels = dict(
        PortalUser.objects.filter(
            pk__in={revision.actor_id for revision in latest.values()}
        ).values_list("id", "email")
    )
    counts = {}
    shown = []
    for row in rows:
        revision = latest.get(row["id"])
        row["placement"] = information_stage(RowItem(row), revision)
        row["decided_by"] = (
            labels.get(revision.actor_id, "Former portal user") if revision else ""
        )
        row["decided_at"] = revision.created_at if revision else None
        row["note"] = revision.notes if revision else ""
        stage = row["placement"].stage.value
        counts[stage] = counts.get(stage, 0) + 1
        if query.stage == stage or (
            query.stage == EVERY_STAGE and (history or stage != Stage.HISTORY.value)
        ):
            shown.append(row)
    return result["metadata"], shown, counts


@dataclass(frozen=True, slots=True)
class RowItem:
    """A selection row seen as the stored request ``information_stage`` reads."""

    row: dict

    @property
    def disposition(self):
        """The request's disposition."""
        return self.row["disposition"]

    @property
    def follow_up_needed(self):
        """Whether follow-up is needed."""
        return self.row["follow_up_needed"]

    @property
    def followed_up_at(self):
        """When follow-up was completed, or None."""
        return self.row["followed_up_at"]


def request_name(row):
    """How the icons, dialogs and notices name one request."""
    if row.get("family_name"):
        return f"the request from {row['family_name']}"
    return f"the request from Family DUID {row['family_duid']}"


def _family(row):
    """The Family cell: the name, or the shared Not in ParishSoft badge."""
    if row["family_name"]:
        return row["family_name"]
    return format_html(
        '<span class="status-badge" data-source-status="missing">{}</span>',
        source_status_label("missing"),
    )


def worklist_rows(rows):
    """The review worklist's rows for one page of placed requests.

    Each keeps the selection's keys and gains what ``review_worklist.build``
    reads: the id, version and name, its identity cells, and the Family's
    answer, shortened, as the link to the request's own page.
    """
    found = []
    for row in rows:
        link = reverse("admin:information_item", args=[row["id"]])
        found.append(
            row
            | {
                "id": str(row["id"]),
                "name": request_name(row),
                "family": _family(row),
                "family_duid": duid(row["family_duid"]),
                "family_answer": format_html(
                    '<a href="{}">{}</a>',
                    link,
                    Truncator(row["text"]).chars(EXCERPT),
                ),
            }
        )
    return found


def stage_counts(counts):
    """The Stage filter's counts without History, which Every stage leaves out."""
    return {
        stage.value: counts.get(stage.value, 0)
        for stage in STAGES
        if counts.get(stage.value)
    }


def worklist_page(counts, query, table, action, *, mutable, prefix="information"):
    """The page's ``worklist`` and its table, from the placed requests.

    ``table`` is the placed rows' page (``paginate``); its rows become the
    worklist's rows. ``mutable`` is false while other campaign work holds
    the campaign: the reader is then offered no action, as the service
    would refuse them. History is a Stage choice of its own, which Every
    stage does not count. The view's address carries its Stage alone
    (To review is the plain address), the one choice its GET takes
    (#1818), so a reload after an in-place answer draws the same Stage.
    ``prefix`` keeps the element ids apart where another page draws these
    rows (the Family hub's Additional tab, #2064).
    """
    rows = worklist_rows(table.rows)
    table = replace(table, rows=rows, method="post", action=action)
    worklist = build(
        Stream.ADDITIONAL_INFORMATION,
        rows,
        frozenset({"staff"}) if mutable else frozenset(),
        prefix=prefix,
        action=action,
        columns=COLUMNS,
        stage=query.stage,
        counts=stage_counts(counts),
        stages=STAGES,
        items="requests",
        caption="Additional information requests",
        baseline=False,
        reviewer=False,
        fields={
            key: value
            for key, value in query.form_values().items()
            if key not in {"stage", *RETIRED_DATES}
        },
        filters_template="stewardship/information-worklist-filters.html",
        export_template="stewardship/information-worklist-export.html",
        address=page_address(
            action,
            [("stage", "" if query.stage == Stage.TO_REVIEW.value else query.stage)],
        ),
    )
    history = Stage.HISTORY.value
    worklist.stage_choices.insert(
        -1, (history, f"History ({counts.get(history, 0):,})")
    )
    return worklist, table


def notice_name(family_duid):
    """A decided request as a notice names it: by its Family's DUID.

    DUIDs, not names, so the notice reads the same whatever the ParishSoft
    data holds; the row itself shows the name.
    """
    return f"The request from Family DUID {family_duid}"


class InformationDecisions:
    """The stream's worklist decisions in one campaign (``DecisionService``).

    ``decide`` records the decision through ``decide_information``, all or
    none, and returns the notice the page shows. A request someone else
    changed first is ``StaleDecision``, named by its Family's DUID.
    """

    def __init__(self, store, campaign_id):
        self.store = store
        self.campaign_id = campaign_id

    def _names(self, versions):
        """Each chosen request's notice name, by request id."""
        return {
            pk: notice_name(family_duid)
            for pk, family_duid in AdditionalInformationItem.objects.filter(
                pk__in=list(versions), submission__campaign_id=self.campaign_id
            ).values_list("pk", "submission__family__family_duid")
        }

    def decide(self, principal, decision):
        """Record ``decision`` for ``principal``; the notice the page shows."""
        if decision.fields:
            raise ValueError("Additional information decisions take no value.")
        versions = {UUID(key): version for key, version in decision.versions.items()}
        names = self._names(versions)
        try:
            recorded = decide_information(
                self.store,
                principal.identity,
                self.campaign_id,
                decision.action,
                versions,
                note=decision.note,
                request_key=decision.key,
                bulk=decision.bulk,
            )
        except StaleItem as error:
            raise StaleDecision(names.get(error.item_id)) from None
        except IntegrityError as error:
            # Only the expected race (a second decision of the same version)
            # becomes the stale notice; any other refusal is logged and fails.
            if getattr(error.__cause__, "sqlstate", None) == UNIQUE_VIOLATION:
                raise StaleDecision() from None
            emit_failure(error, event=Event.TASK_FAILED)
            raise
        words = WORDS[decision.action].done
        if len(recorded) > 1:
            return f"{len(recorded):,} requests {words}"
        return f"{names[recorded[0].item_id]} {words}"

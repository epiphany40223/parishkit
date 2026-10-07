"""The lists of Families behind the response funnel's counts (#477, PR 5).

Each list is one ``ResponseList``: which per-Family funnel rows it holds
(``response_metrics.FamilyResponse``, the rows behind the dashboard's tiles),
its columns, its one closed filter (``show``) and its default order. Four
lists are chosen from the funnel rows alone, so each one's length equals the
dashboard figure it sits behind:

- **submitted**: every Family that submitted (the Submitted stage);
- **started**: the form opened but nothing submitted (Form opened minus
  Submitted, since those stages are nested);
- **not-opened**: a delivered invitation but no form opened;
- **more-than-once**: more than one submission (the figure of that name).

The fifth, **data-quality**, is a live view of ParishSoft rather than of the
funnel: the campaign's active Families whose current ParishSoft record has a
blank mailing name or envelope number 0, the two problems seen on launch day.

Names, envelope numbers and mailing names come from the current source
snapshot (``source.snapshot_names``) in two queries for any number of
Families. Sorting, filtering and paging happen here in memory: the funnel
rows are one statement of about 1,100 rows at launch scale. Everything in
this module except ``read_list`` is a pure function, so it is tested without
a database.
"""

import csv
import io
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urlencode

from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from parishkit.stewardship.audit.schemas import Action
from parishkit.stewardship.campaigns.credential_models import FamilyCampaign
from parishkit.stewardship.campaigns.models import CampaignWorkGate
from parishkit.stewardship.source.snapshot_models import SourceCurrent
from parishkit.stewardship.source.snapshot_names import snapshot_family_facts
from parishkit.stewardship.web.contracts import filters
from parishkit.stewardship.web.dates import csv_text
from parishkit.stewardship.web.exports import csv_cell
from parishkit.stewardship.web.tables import Sorting, table_parameters

from .response_metrics import MODES, FamilyResponse, response_families

# The filter value that keeps every row of a list; it is left out of URLs.
EVERYONE = "all"


@dataclass(frozen=True)
class ListedFamily:
    """One Family on a list: its funnel row and its current ParishSoft facts.

    ``facts`` is None when the Family is missing from the current snapshot
    (an archived campaign's Family that ParishSoft no longer has, say).
    """

    response: FamilyResponse
    facts: object = None

    @property
    def duid(self):
        """The Family's ParishSoft DUID."""
        return self.response.family_duid

    @property
    def name(self):
        """The directory's surname-and-heads name, or None when unknown."""
        return self.facts.name if self.facts else None

    @property
    def envelope(self):
        """The ParishSoft envelope number, or None when there is none."""
        return self.facts.envelope if self.facts else None

    @property
    def mailing_name(self):
        """The ParishSoft mailing name; None when blank or unknown."""
        return (self.facts.mailing_name or None) if self.facts else None

    @property
    def problems(self):
        """What to check in the ParishSoft record, as short phrases."""
        if self.facts is None:
            return ()
        found = []
        if not self.facts.mailing_name:
            found.append(str(_("Blank mailing name")))
        if self.facts.envelope == 0:
            found.append(str(_("Envelope number 0")))
        return tuple(found)


@dataclass(frozen=True)
class Column:
    """One column of a list, on the page and in the CSV file.

    ``key`` is its sort token; ``value`` reads it from a ``ListedFamily``
    (an aware datetime, an int, a str, or None when missing); ``kind`` says
    how the page shows it: ``instant`` (a localized time), ``count`` (a
    grouped number), ``id`` (an identifier, never grouped), ``text`` or
    ``family`` (the row's heading cell). ``missing`` is the page's text for
    a missing value; the CSV leaves it blank. Times and counts sort
    descending on their first click.
    """

    key: str
    heading: str
    value: Callable
    kind: str
    missing: str = ""

    @property
    def descending_first(self):
        """Whether a first click sorts largest or newest first."""
        return self.kind in {"instant", "count"}

    @property
    def css_class(self):
        """Numbers and identifiers line up at the end of their cells."""
        return "numeric" if self.kind in {"count", "id"} else ""

    def sort_key(self, row):
        """The value to sort by: text ignores case; missing sorts last."""
        value = self.value(row)
        if value is None or value == "":
            return None
        return value.casefold() if isinstance(value, str) else value


@dataclass(frozen=True)
class Choice:
    """One value of a list's ``show`` filter: its label and which rows it keeps."""

    value: str
    label: str
    keeps: Callable


@dataclass(frozen=True)
class ResponseList:
    """One list: what it holds, how it is shown, filtered, sorted and audited.

    ``selects`` picks the funnel rows the list holds; ``needs_facts`` marks a
    list that is chosen by its ParishSoft facts instead (data quality), from
    the campaign's active Families. ``choices`` is the ``show`` filter, its
    first value keeping every row; a list with one choice has no filter.
    """

    key: str
    title: str
    description: str
    columns: tuple
    default_sort: str
    choices: tuple
    viewed: Action
    exported: Action
    selects: Callable = None
    needs_facts: bool = False
    choice_label: str = _("Show")

    @property
    def sorting(self):
        """Every column sorts, both ways; the shared tables parse the token."""
        return Sorting.by_column(
            {column.key: column.sort_key for column in self.columns},
            default=self.default_sort,
            descending_first={c.key for c in self.columns if c.descending_first},
        )

    def choice(self, value):
        """The filter choice named ``value``; anything else is refused."""
        for choice in self.choices:
            if choice.value == value:
                return choice
        raise ValueError("Unknown list filter.")


def _keep_all(row):
    """The first choice of every filter: keep every row."""
    return True


FAMILY = Column(
    "family",
    _("Family"),
    lambda row: row.name,
    "family",
    _("Not in the latest ParishSoft data"),
)
DUID = Column("duid", _("Family DUID"), lambda row: row.duid, "id")
ENVELOPE = Column("envelope", _("Envelope number"), lambda row: row.envelope, "id")
FIRST_SUBMITTED = Column(
    "submitted",
    _("First submitted"),
    lambda row: row.response.submitted_at,
    "instant",
    _("Not yet"),
)
SUBMISSIONS = Column(
    "submissions", _("Submissions"), lambda row: row.response.submissions, "count"
)

LISTS = {
    spec.key: spec
    for spec in (
        ResponseList(
            key="submitted",
            title=_("Families that submitted"),
            description=_("Every Family with a submission"),
            columns=(FIRST_SUBMITTED, FAMILY, DUID, ENVELOPE, SUBMISSIONS),
            # Chronological: the first submission first.
            default_sort="submitted",
            choices=(
                Choice(EVERYONE, _("Everyone"), _keep_all),
                Choice(
                    "invited",
                    _("With a delivered invitation"),
                    lambda row: row.response.invited_at is not None,
                ),
                Choice(
                    "uninvited",
                    _("Without a delivered invitation"),
                    lambda row: row.response.invited_at is None,
                ),
            ),
            viewed=Action.RESPONSE_SUBMITTED_LIST_VIEWED,
            exported=Action.RESPONSE_SUBMITTED_LIST_EXPORTED,
            selects=lambda family: family.submitted_at is not None,
        ),
        ResponseList(
            key="started",
            title=_("Families that started but did not submit"),
            description=_("Opened the form, nothing submitted yet"),
            columns=(
                Column(
                    "opened",
                    _("Form opened"),
                    lambda row: row.response.form_opened_at,
                    "instant",
                ),
                Column(
                    "progressed",
                    _("Got past the first step"),
                    lambda row: row.response.progressed_at,
                    "instant",
                    _("Not yet"),
                ),
                FAMILY,
                DUID,
                ENVELOPE,
            ),
            default_sort="family",
            choices=(
                Choice(EVERYONE, _("Everyone"), _keep_all),
                Choice(
                    "progressed",
                    _("Got past the first step"),
                    lambda row: row.response.progressed_at is not None,
                ),
                Choice(
                    "opened",
                    _("Opened the form only"),
                    lambda row: row.response.progressed_at is None,
                ),
            ),
            viewed=Action.RESPONSE_STARTED_LIST_VIEWED,
            exported=Action.RESPONSE_STARTED_LIST_EXPORTED,
            selects=lambda family: (
                family.form_opened_at is not None and family.submitted_at is None
            ),
        ),
        ResponseList(
            key="not-opened",
            title=_("Invited Families that never opened the form"),
            description=_("Invitation delivered, form never opened"),
            columns=(
                Column(
                    "invited",
                    _("Invitation delivered"),
                    lambda row: row.response.invited_at,
                    "instant",
                ),
                Column(
                    "link",
                    _("Link followed"),
                    lambda row: row.response.link_at,
                    "instant",
                    _("No"),
                ),
                FAMILY,
                DUID,
                ENVELOPE,
            ),
            default_sort="family",
            choices=(
                Choice(EVERYONE, _("Everyone"), _keep_all),
                Choice(
                    "followed",
                    _("Link followed"),
                    lambda row: row.response.link_at is not None,
                ),
                Choice(
                    "unfollowed",
                    _("Link not followed"),
                    lambda row: row.response.link_at is None,
                ),
            ),
            viewed=Action.RESPONSE_NOT_OPENED_LIST_VIEWED,
            exported=Action.RESPONSE_NOT_OPENED_LIST_EXPORTED,
            selects=lambda family: (
                family.invited_at is not None and family.form_opened_at is None
            ),
        ),
        ResponseList(
            key="more-than-once",
            title=_("Families that submitted more than once"),
            description=_("More than one submission"),
            columns=(
                SUBMISSIONS,
                FIRST_SUBMITTED,
                Column(
                    "last",
                    _("Last submitted"),
                    lambda row: row.response.last_submitted_at,
                    "instant",
                ),
                FAMILY,
                DUID,
                ENVELOPE,
            ),
            default_sort="-submissions",
            choices=(Choice(EVERYONE, _("Everyone"), _keep_all),),
            viewed=Action.RESPONSE_REPEAT_LIST_VIEWED,
            exported=Action.RESPONSE_REPEAT_LIST_EXPORTED,
            selects=lambda family: family.submissions > 1,
        ),
        ResponseList(
            key="data-quality",
            title=_("ParishSoft data to check"),
            description=_("Blank mailing name or envelope number 0"),
            columns=(
                FAMILY,
                DUID,
                ENVELOPE,
                Column(
                    "mailing",
                    _("Mailing name"),
                    lambda row: row.mailing_name,
                    "text",
                    _("Blank"),
                ),
                Column(
                    "problem",
                    _("What to check"),
                    lambda row: "; ".join(row.problems),
                    "text",
                ),
                FIRST_SUBMITTED,
            ),
            default_sort="family",
            choices=(
                Choice(EVERYONE, _("Everything to check"), _keep_all),
                Choice(
                    "mailing-name",
                    _("Blank mailing name"),
                    lambda row: row.facts is not None and not row.facts.mailing_name,
                ),
                Choice(
                    "envelope",
                    _("Envelope number 0"),
                    lambda row: row.envelope == 0,
                ),
            ),
            viewed=Action.RESPONSE_DATA_QUALITY_LIST_VIEWED,
            exported=Action.RESPONSE_DATA_QUALITY_LIST_EXPORTED,
            needs_facts=True,
            choice_label=_("Check"),
        ),
    )
}


@dataclass(frozen=True)
class ListQuery:
    """A list page's filter state: the system mode and the ``show`` choice.

    Both are closed vocabularies, as are the shared table's sort, size and
    page, so a list's URL never carries anything identifying.
    """

    mode: str = "production"
    show: str = EVERYONE

    @classmethod
    def parse(cls, spec, parameters, *, extra=()):
        """Validate a request's choices; return (query, the other values).

        ``parameters`` is a QueryDict. The table's sort, size and page (and
        any ``extra`` names, such as an export's time zone) are returned as
        single values for their own parsers; anything else is refused.
        """
        allowed = {"mode", "show"} | table_parameters() | set(extra)
        values = filters(parameters, allowed=allowed)
        query = cls(values.pop("mode", "production"), values.pop("show", EVERYONE))
        if query.mode not in MODES:
            raise ValueError("Invalid response list mode.")
        spec.choice(query.show)
        return query, values

    def carried(self):
        """The (name, value) pairs every link and form keeps; defaults left out."""
        values = []
        if self.mode != "production":
            values.append(("mode", self.mode))
        if self.show != EVERYONE:
            values.append(("show", self.show))
        return values

    def url(self, campaign_id, key, **extra):
        """A list's URL with these choices and the non-empty ``extra`` ones."""
        path = reverse("admin:response_list", args=[campaign_id, key])
        values = self.carried() + [(k, v) for k, v in extra.items() if v]
        return path + ("?" + urlencode(values) if values else "")


def list_counts(families):
    """How many Families each funnel-chosen list holds, by key.

    The data-quality list is left out: counting it needs the ParishSoft
    read, and the dashboard reads only the funnel.
    """
    return {
        key: sum(1 for family in families if spec.selects(family))
        for key, spec in LISTS.items()
        if not spec.needs_facts
    }


def candidates(spec, families, active=frozenset()):
    """The funnel rows a list could hold, before its ParishSoft facts are read.

    A funnel list keeps the rows it selects; the data-quality list starts
    from the campaign's active Families (``active``: FamilyCampaign ids), and
    its facts decide.
    """
    if spec.needs_facts:
        return [family for family in families if family.family_id in active]
    return [family for family in families if spec.selects(family)]


def listed(spec, families, facts, choice):
    """Join the candidate rows with their facts and apply the filter choice.

    The rows keep the funnel statement's DUID order, which breaks ties in
    every sort. A data-quality row needs a problem to be listed at all.
    """
    rows = [ListedFamily(family, facts.get(family.family_duid)) for family in families]
    if spec.needs_facts:
        rows = [row for row in rows if row.problems]
    return [row for row in rows if choice.keeps(row)]


def cells(spec, row):
    """One row's cells for the page: (column, value) in column order."""
    return [(column, column.value(row)) for column in spec.columns]


def csv_value(value, zone):
    """A cell's file text: times in ``zone`` as the shared CSV text, missing blank.

    ``web.dates.csv_text`` writes ISO 8601 with a space separator, as every
    other CSV download does.
    """
    if value is None:
        return ""
    if isinstance(value, datetime):
        return csv_text(value.astimezone(zone))
    return str(value)


def list_csv(spec, rows, zone):
    """The list as CSV: UTF-8, a header row, CRLF, every cell neutralized.

    ``rows`` are already filtered and sorted as on the page, every one of
    them, not just a page. A missing value is blank rather than the page's
    words for it.
    """
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow([csv_cell(str(column.heading)) for column in spec.columns])
    for row in rows:
        writer.writerow(
            csv_cell(csv_value(column.value(row), zone)) for column in spec.columns
        )
    return buffer.getvalue().encode("utf-8")


def downloads_paused(campaign_id):
    """Whether the campaign's purge gate is closed, which stops new downloads.

    The reports spec rejects every new export while a purge is being
    prepared or run; reading the lists stays allowed, under the read guard.
    """
    # As export admission (stewardship_export_admitted_v1): any gate not
    # released, a tombstone included, refuses.
    return (
        CampaignWorkGate.objects.filter(campaign_id=campaign_id)
        .exclude(state="released")
        .exists()
    )


def current_snapshot():
    """The current ParishSoft snapshot's id, or None before the first load."""
    return SourceCurrent.objects.values_list("snapshot_id", flat=True).first()


def read_list(spec, scope, as_of, choice, snapshot=None):
    """Read one list's rows at ``as_of``: the funnel rows, then their facts.

    Runs inside the campaign read guard's read-only transaction. One funnel
    statement, the campaign's active Families for the data-quality list, and
    two snapshot queries for the names, envelope numbers and mailing names
    of the candidates only. ``snapshot`` is the ParishSoft snapshot to read
    them from (``current_snapshot()`` when not given); a caller that audits
    the read passes the one it records (#556).
    """
    families = response_families(scope, as_of)
    active = frozenset()
    if spec.needs_facts:
        active = frozenset(
            FamilyCampaign.objects.filter(
                campaign_id=scope.campaign_id, active=True
            ).values_list("pk", flat=True)
        )
    chosen = candidates(spec, families, active)
    if snapshot is None:
        snapshot = current_snapshot()
    facts = snapshot_family_facts(
        snapshot, [family.family_duid for family in chosen], str(_("Family"))
    )
    return listed(spec, chosen, facts, choice)

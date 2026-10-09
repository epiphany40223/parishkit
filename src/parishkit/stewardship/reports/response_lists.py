"""The lists of Families behind the response funnel's counts (#477, PR 5).

Each list is one ``ResponseList``: which per-Family funnel rows it holds
(``response_metrics.FamilyResponse``, the rows behind the dashboard's tiles),
its columns, its closed filter (``show``, where it has one) and its default
order. Every list also takes a private name, DUID or envelope-number
search (``ListQuery.search``, #849/#860). Four
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

Each list downloads as CSV, XLSX or PDF (``list_file``, #850), rendered on
request from the same rows and columns as the page.

Names, envelope numbers and mailing names come from the current source
snapshot (``source.snapshot_names``) in two queries for any number of
Families. Sorting, filtering, searching and paging happen here in memory:
the funnel
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
from parishkit.stewardship.web import dates
from parishkit.stewardship.web.content import bounded_text
from parishkit.stewardship.web.contracts import filters
from parishkit.stewardship.web.dates import csv_text
from parishkit.stewardship.web.exports import csv_cell
from parishkit.stewardship.web.tables import Sorting, table_parameters

from .directory_rendering import draw_pages, fill_pages, table_heading, table_lines
from .information_rendering import information_xlsx
from .response_metrics import MODES, FamilyResponse, response_families

# The filter value that keeps every row of a list; it is left out of URLs.
EVERYONE = "all"
# The download formats (#850), each rendered on request, and their types.
FORMATS = {
    "csv": "text/csv",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "pdf": "application/pdf",
}
PRIVACY = "Sensitive parish report. Share only with authorized recipients."
# PDF column widths in monospaced characters, by column key: each at least
# its heading's length, and every list's row (with two spaces between
# columns) within PDF_LINE, the characters a landscape page holds at 9 pt
# (see directory_rendering.COLUMN_WIDTHS). Longer cells wrap. A compact
# time ("Sep 30, 2026 12:04 PM") is 21 characters.
PDF_WIDTHS = {
    "submitted": 21,
    "opened": 21,
    "progressed": 23,
    "invited": 21,
    "link": 21,
    "last": 21,
    "family": 36,
    "duid": 11,
    "envelope": 15,
    "submissions": 11,
    "mailing": 24,
    "problem": 20,
}
PDF_LINE = 138


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
            # The Administrator found the invitation filter unneeded here
            # (#860): the list shows every Family that submitted.
            choices=(Choice(EVERYONE, _("Everyone"), _keep_all),),
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


@dataclass(frozen=True, repr=False)
class ListQuery:
    """A list page's filter state: the mode, the ``show`` choice, the search.

    The mode and ``show`` are closed vocabularies, as are the shared table's
    sort, size and page, so a list's URL never carries anything identifying.
    ``search`` can name a Family, so it travels only in CSRF-protected POST
    bodies (``private``), never in a URL, a log line or the audit (#849);
    ``repr=False`` keeps it out of tracebacks too.
    """

    mode: str = "production"
    show: str = EVERYONE
    search: str = ""

    @classmethod
    def parse(cls, spec, parameters, *, extra=(), private=False):
        """Validate a request's choices; return (query, the other values).

        ``parameters`` is a QueryDict. The table's sort, size and page (and
        any ``extra`` names, such as an export's time zone) are returned as
        single values for their own parsers; anything else is refused.
        ``private`` marks a POST body, the only place a search may come from.
        """
        allowed = {"mode", "show"} | table_parameters() | set(extra)
        if private:
            allowed.add("search")
        values = filters(parameters, allowed=allowed)
        show = spec.choice(values.pop("show", EVERYONE)).value
        search = bounded_text(values.pop("search", "")).strip()
        query = cls(values.pop("mode", "production"), show, search)
        if query.mode not in MODES:
            raise ValueError("Invalid response list mode.")
        return query, values

    def carried(self):
        """The closed (name, value) pairs links keep; defaults left out."""
        values = []
        if self.mode != "production":
            values.append(("mode", self.mode))
        if self.show != EVERYONE:
            values.append(("show", self.show))
        return values

    def posted(self):
        """What a POST form keeps: the closed choices and any search."""
        return self.carried() + ([("search", self.search)] if self.search else [])

    def matches(self, row):
        """Whether a listed Family matches the search (every row without one).

        As the directory's search matches its Family column: part of the
        shown name (surname, then the heads of household), ignoring case.
        A search of digits only also matches that exact Family DUID or
        envelope number, the numbers staff copy from ParishSoft or a gift; a
        partial number would match far too many Families to be useful.
        """
        text = self.search.casefold()
        if not text:
            return True
        if text.isascii() and text.isdigit() and int(text) in (row.duid, row.envelope):
            return True
        return bool(row.name) and text in row.name.casefold()

    def url(self, key, **extra):
        """A list's URL with these choices and the non-empty ``extra`` ones."""
        path = reverse("admin:response_list", args=[key])
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


def listed(spec, families, facts, query):
    """Join the candidate rows with their facts; apply the filter and search.

    ``query`` is the page's ``ListQuery``. The rows keep the funnel
    statement's DUID order, which breaks ties in every sort. A data-quality
    row needs a problem to be listed at all.
    """
    rows = [ListedFamily(family, facts.get(family.family_duid)) for family in families]
    if spec.needs_facts:
        rows = [row for row in rows if row.problems]
    keeps = spec.choice(query.show).keeps
    return [row for row in rows if keeps(row) and query.matches(row)]


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


@dataclass(frozen=True, repr=False)
class ListDocument:
    """One list's file contents for the shared XLSX and table PDF writers.

    ``metadata`` is the (label, value) details the XLSX "Report information"
    sheet lists and the PDF header draws; ``rows`` hold each format's cell
    values (``xlsx_value`` or ``pdf_text``); ``widths`` maps each heading to
    its PDF column width. ``repr=False`` keeps names out of tracebacks.
    """

    title: str
    metadata: tuple
    headings: tuple
    rows: tuple
    requested_at: datetime
    widths: dict
    sheet_name: str = "Families"


def list_details(spec, query, *, parish, campaign, as_of, zone, count):
    """The file's report details: what was listed, when, and for whom.

    Never the search text, which can name a Family (#849): only whether one
    was applied. ``as_of`` is the list's Counted at instant, shown in the
    download's time zone ``zone`` (a ZoneInfo).
    """
    return (
        ("Report", str(spec.title)),
        ("Parish", parish),
        ("Campaign", campaign),
        ("Responses", "Testing" if query.mode == "testing" else "Production"),
        ("Filter", f"{spec.choice_label}: {spec.choice(query.show).label}"),
        ("Search applied", "Yes" if query.search else "No"),
        ("Counted at", as_of.astimezone(zone)),
        ("Display time zone", zone.key),
        ("Families in this file", f"{count:,}"),
        ("Privacy", PRIVACY),
    )


def xlsx_value(column, value, zone):
    """A cell for the XLSX sheet: a native time in ``zone`` or count, else text.

    A missing value is a truly empty cell (None), blank as in the CSV.
    Identifiers (DUID, envelope number) stay text, as on the page, so they
    are never grouped or summed.
    """
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.astimezone(zone)
    return value if column.kind == "count" else str(value)


def pdf_text(column, value, zone):
    """A cell for the PDF, which people read: the page's words for it.

    A missing value reads as on the page ("Not yet", "No"); a time is the
    parish's compact date format in ``zone`` (the header names the zone);
    a count is grouped, as on the page.
    """
    if value is None or value == "":
        return str(column.missing)
    if isinstance(value, datetime):
        return dates.display_text(value.astimezone(zone), compact=True)
    if column.kind == "count":
        return f"{value:,}"
    return str(value)


def list_pdf(document, output):
    """The list as landscape table pages with its details on every page."""
    details = dict(document.metadata)
    header = (
        " · ".join(
            (
                details["Parish"],
                details["Campaign"],
                f"Counted at {dates.display_text(details['Counted at'])}",
                f"Times in {details['Display time zone']}",
            )
        ),
        f"{details['Families in this file']} "
        f"{'Family' if details['Families in this file'] == '1' else 'Families'} "
        "in this file. "
        f"{details['Responses']} responses. {details['Filter']}."
        + (" Search applied." if details["Search applied"] == "Yes" else ""),
    )
    return draw_pages(
        document,
        output,
        list(fill_pages(list(table_lines(document, document.widths)))),
        header=header,
        heading=table_heading(document.headings, document.widths),
        footer=PRIVACY,
    )


def list_file(spec, rows, zone, format, *, details=(), as_of=None):
    """The list's download in ``format`` (a ``FORMATS`` key), as bytes.

    ``rows`` are already filtered, searched and sorted as on the page, every
    one of them. CSV is exactly the table (``list_csv``). XLSX and PDF add
    the report ``details`` (``list_details``): the XLSX through the shared
    writer, with its "Report information" sheet, and the PDF through the
    shared table pages. Every text cell is neutralized by its writer: CSV
    against formulas, XLSX as literal text, PDF escaping what its font
    cannot draw. ``zone`` is a ZoneInfo; ``as_of`` dates the PDF.
    """
    if format == "csv":
        return list_csv(spec, rows, zone)
    if format not in FORMATS:
        raise ValueError("Unsupported response list format.")
    convert = xlsx_value if format == "xlsx" else pdf_text
    headings = tuple(str(column.heading) for column in spec.columns)
    document = ListDocument(
        title=str(spec.title),
        metadata=details,
        headings=headings,
        rows=tuple(
            tuple(convert(c, c.value(row), zone) for c in spec.columns) for row in rows
        ),
        requested_at=as_of,
        widths={
            heading: PDF_WIDTHS[column.key]
            for heading, column in zip(headings, spec.columns, strict=True)
        },
    )
    output = io.BytesIO()
    (information_xlsx if format == "xlsx" else list_pdf)(document, output)
    return output.getvalue()


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


def read_list(spec, scope, as_of, query, snapshot=None):
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
    return listed(spec, chosen, facts, query)

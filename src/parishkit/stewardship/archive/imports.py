"""Import past-campaign archive workbooks as parish records (#1018, slice 2).

The import follows Review, Apply and Change status (campaign-history spec,
"Import"); the Past campaigns page (slice 4) and ``pk-admin history`` call
these same functions:

- ``review`` checks one workbook's bytes in the isolated helper
  (``archive.check.check_archive``), refuses the current campaign's year,
  and stores the validated aggregates under a ``pending`` archive row: the
  ``daily`` rows, the pledge bands and, when kept, each Family's id system,
  id and first campaign day. It says what Apply would do: **add** the year,
  **replace** the year's active import, or nothing (**already** imported, by
  checksum). A refused file stores nothing.
- ``apply`` makes a preview ``active`` in one transaction, removing the
  year's earlier import first for a replacement, which needs the
  acknowledgement the page asks for. A partial file never replaces a final
  one.
- ``remove`` removes a year's active import, leaving its archive row as a
  tombstone (year, checksum, counts, who and when) without its day, band
  and Family rows.

Only an Administrator (``CONFIGURE`` and ``FINANCIAL_DETAIL``) may do any of
this; SQL checks the actor again (``stewardship_export_authorized_v1``) and
admits only these state changes. Racing Applies are settled by the
one-active-row indexes: the loser is stale. Review is one at a time across
the deployment (a non-blocking advisory lock), since each holds a workbook
and a helper process. A preview lives fifteen minutes; each Review first
deletes expired previews, which is the housekeeping the trigger admits,
skipping any an Apply holds.

Audit events (``campaign_archive_previewed``, ``…_imported``,
``…_replaced`` and ``…_removed``) carry the year, the file's SHA-256, the
row counts and the outcome, never a Family value or note text.
"""

import hashlib
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal

from django.db import IntegrityError, connection, transaction
from django.db.models import Q

from parishkit.stewardship.storage import StaleRecordError

from .check import CHECK_SECONDS, check_archive

# The advisory lock that keeps archive Reviews one at a time (key, slot).
# Its key is used nowhere else (736247 is taken by the Users chain's 0053).
REVIEW_LOCK = (736248, 1)
# How long a Review's preview may be applied: the preview lifetime of
# configuration changes (admin-portal spec, "Preview lifetimes").
PREVIEW_SECONDS = 15 * 60
# A stored file name is cut to this, after dropping any directory part.
FILE_NAME_LIMIT = 255
FOUR_DIGITS = re.compile(r"[0-9]{4}")
HELPER = "archive_check_worker"


class ArchiveRefusal(ValueError):
    """A workbook or Apply the import refuses; nothing was stored or changed.

    ``problems`` are ``{"code", "sheet", "cell", "rule"}`` as the reader
    words them (a cell's place, never its value); ``more`` counts problems
    cut off. ``fields`` is the same list in the command line's error-field
    shape.
    """

    def __init__(self, problems, more=0):
        """Keep the located problems."""
        super().__init__("The campaign archive is refused.")
        self.problems = [dict(problem) for problem in problems]
        self.more = more

    @property
    def fields(self):
        """The problems as ``{"field", "code", "message"}``."""
        return [
            {
                "field": place(problem) or "file",
                "code": problem["code"],
                "message": problem["rule"],
            }
            for problem in self.problems
        ]


def place(problem):
    """A problem's location: ``sheet!cell``, ``sheet`` or "" for the file."""
    if problem.get("sheet") is None:
        return ""
    if problem.get("cell"):
        return f"{problem['sheet']}!{problem['cell']}"
    return problem["sheet"]


def refusal(code, rule):
    """A refusal with one problem about the whole file."""
    return ArchiveRefusal([{"code": code, "sheet": None, "cell": None, "rule": rule}])


class ReviewBusy(Exception):
    """Another archive Review is being checked; try again in a minute."""


class ReplaceNotAcknowledged(ArchiveRefusal):
    """Apply would replace a year's import, which needs the acknowledgement."""

    def __init__(self, year):
        """Name the year the acknowledgement is about."""
        super().__init__(
            [
                {
                    "code": "replace_not_acknowledged",
                    "sheet": None,
                    "cell": None,
                    "rule": f"this replaces the {year} figures already imported; "
                    "acknowledge that to apply it",
                }
            ]
        )


@dataclass(frozen=True)
class Review:
    """A Review's outcome: the pending archive and what Apply would do.

    ``action`` is ``add``, ``replace`` or ``already``; ``current`` is the
    year's active import for ``replace`` and the matching import for
    ``already`` (None for ``add``). ``flagged_notes`` names the free-text
    values the Administrator should check before Apply.
    """

    archive: object
    action: str
    current: object
    flagged_notes: tuple


@dataclass(frozen=True)
class Applied:
    """An Apply's outcome: ``imported``, ``replaced`` or ``already``.

    ``archive`` is the active import (for ``already``, the earlier one);
    ``replaced`` the import it removed, for ``replaced``.
    """

    outcome: str
    archive: object
    replaced: object = None


def authorize(store, actor_id):
    """The current principal, if it may import archives; else PermissionError.

    Administrators only: the page needs ``CONFIGURE`` and
    ``FINANCIAL_DETAIL`` (Family-level giving is in the files).
    """
    from parishkit.stewardship.accounts.policy import (
        Capability,
        allows,
        current_principal,
    )

    principal = current_principal(store, actor_id)
    if not (
        allows(principal, Capability.CONFIGURE)
        and allows(principal, Capability.FINANCIAL_DETAIL)
    ):
        raise PermissionError("Campaign archives are the Administrator's.")
    return principal


def stewardship_year():
    """The current campaign's stewardship year, or None with no campaign.

    The year Families pledge for (``year_from_values``). The start-date
    fallback of ``web.presentation.campaign_year`` is not used: it names the
    year the campaign runs in. When no single year can be resolved, every
    import is refused, since "last year" cannot be told apart.
    """
    from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
    from parishkit.stewardship.campaigns.models import Campaign

    campaign_id = SystemConfiguration.objects.values_list(
        "current_campaign_id", flat=True
    ).first()
    if campaign_id is None:
        return None
    return year_from_values(
        Campaign.objects.select_related("active_configuration")
        .get(pk=campaign_id)
        .active_configuration.values
    )


def year_from_values(values):
    """A campaign configuration's stewardship year: the year Families pledge for.

    The Campaign settings year label decides when it is one four-digit year
    and either there is no financial period or the label is one of the
    period's calendar years (a July-June period starting 2026 may be labelled
    2027). A label outside the period (2026 on a 2027-01-01 to 2027-12-31
    period) names something else, such as the year the campaign runs in, so
    the period decides when it lies in one calendar year. Without a label,
    the period's start year decides. Anything else (a label such as
    "2026-2027", no label and no period, or a label outside a period that
    spans two years) is refused as unresolved.
    """
    label = (values.get("year_label") or "").strip()
    financial = values.get("financial") or {}
    first = str(financial.get("start") or "")[:4]
    last = str(financial.get("end") or "")[:4]
    if not FOUR_DIGITS.fullmatch(last):
        # Saved configurations always have an end; without one, one year.
        last = first
    period = range(int(first), int(last) + 1) if FOUR_DIGITS.fullmatch(first) else None
    if FOUR_DIGITS.fullmatch(label) and (period is None or int(label) in period):
        return int(label)
    if period is not None and (
        not label or (FOUR_DIGITS.fullmatch(label) and len(period) == 1)
    ):
        return period[0]
    raise refusal(
        "year_unresolved",
        "the current campaign's stewardship year cannot be resolved; set "
        "the year label in Campaign settings to the year Families pledge for",
    )


def clean_file_name(name):
    """A stored file name: its last path part, without control characters."""
    name = re.split(r"[\\/]", name or "")[-1]
    name = "".join(ch for ch in name if ch.isprintable())
    return name.strip()[:FILE_NAME_LIMIT]


class ReviewLock:
    """The deployment-wide, non-blocking lock for archive Reviews.

    A session-level advisory lock, so the same connection may take it again
    (PostgreSQL counts the holds): the Past campaigns page takes it before
    it reads the uploaded body, and ``review`` takes it again around the
    check. Each exit releases one hold.
    """

    def __enter__(self):
        """Take the lock, or refuse at once when another Review holds it."""
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_try_advisory_lock(%s,%s)", list(REVIEW_LOCK))
            if not cursor.fetchone()[0]:
                raise ReviewBusy("Another archive is being checked.")
        return self

    def __exit__(self, *exc):
        """Release the lock, whatever happened."""
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_unlock(%s,%s)", list(REVIEW_LOCK))
        return False


def _check(data, check):
    """The helper's document, recording a kill as a durable timeout entry."""
    from parishkit.stewardship.provider_checks import helper_timeout_recorder

    with ReviewLock():
        return check(
            data,
            on_timeout=helper_timeout_recorder(
                HELPER, what="archive_check", seconds=CHECK_SECONDS
            ),
        )


def _audit(action, actor_id, archive_id, context):
    """One import audit event: year, checksum, counts and outcome only."""
    from parishkit.stewardship.audit.schemas import ActorKind
    from parishkit.stewardship.audit.services import record_action

    record_action(
        action,
        actor_kind=ActorKind.PORTAL_USER,
        actor_id=actor_id,
        subject_id=archive_id,
        current_parish=True,
        context=context,
    )


def _archive_context(archive, outcome, **extra):
    """The audit context of one archive row."""
    return {
        "outcome": outcome,
        "archive_year": archive.campaign_year,
        "file_fingerprint": archive.checksum,
        "count": archive.families_responded,
        "archive_days": archive.days,
        "archive_family_rows": archive.family_row_count,
        "version": archive.version,
        **extra,
    }


# The child tables (reports.archive_grants) are deleted before their archive
# row. The records refuse generic ORM deletes, so these are explicit
# statements the guards admit.


def _housekeeping(now):
    """Delete expired previews, children first, as the trigger admits.

    Each expired preview is locked first, skipping any another transaction
    holds: an Apply that locked a preview at the edge of its lifetime may
    be making it active, and deleting its rows then would leave an active
    import without them. ``FOR UPDATE`` re-reads a row an Apply committed
    meanwhile, so the ``pending`` filter drops it. A skipped preview goes at
    a later Review. (The child guard also locks the archive ``FOR SHARE``
    before admitting a delete, so a delete that raced anyway is refused.)
    """
    from parishkit.stewardship.reports.archive_models import CampaignArchive

    expired = list(
        CampaignArchive.objects.select_for_update(skip_locked=True)
        .filter(
            state="pending", created_at__lt=now - timedelta(seconds=PREVIEW_SECONDS)
        )
        .values_list("pk", flat=True)
    )
    if expired:
        _delete_pending(expired)


def _delete_children(archive_ids):
    """Delete the day, band and Family rows of these archives."""
    from parishkit.stewardship.reports.archive_grants import CHILD_TABLES

    with connection.cursor() as cursor:
        for table in CHILD_TABLES:
            cursor.execute(
                f"DELETE FROM {table} WHERE archive_id = ANY(%s)",  # noqa: S608
                [list(archive_ids)],
            )


def _delete_pending(archive_ids):
    """Delete pending previews and their rows (a preview never applied)."""
    _delete_children(archive_ids)
    with connection.cursor() as cursor:
        cursor.execute(
            "DELETE FROM stewardship_campaign_archive "
            "WHERE id = ANY(%s) AND state='pending'",
            [list(archive_ids)],
        )


def _now():
    """The database's current instant."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT statement_timestamp()")
        return cursor.fetchone()[0]


def _stored_notes(document):
    """The ``note.*`` rows and unrecomputed ``count.*`` rows, for data notes."""
    return {**document["notes"], **document["other_counts"]}


def _instant(text):
    """An ISO instant from the helper's document."""
    return datetime.fromisoformat(text)


def _quartiles(document):
    """The helper's quartiles as Decimals, or three Nones."""
    values = document["quartiles"]
    return tuple(Decimal(value) for value in values) if values else (None,) * 3


def review(
    store,
    actor_id,
    data,
    *,
    keep_family_rows=True,
    file_name="",
    check=None,
    record=None,
):
    """Check one workbook and store it as a pending preview; return a Review.

    Raises ``ArchiveRefusal`` for a refused file (its located problems), the
    current campaign's year, an unresolvable current year, or a partial file
    for a year with a final import; ``ReviewBusy`` while another Review
    runs; ``PermissionError`` for anyone but an Administrator. ``record()``,
    when given, runs in the preview's transaction (the command line's own
    audit event).
    """
    from parishkit.stewardship.audit.schemas import Action, Outcome

    authorize(store, actor_id)
    checksum = hashlib.sha256(data).hexdigest()

    def denied(year=None):
        """Record the refused Review, without any Family value or note."""
        context = {"outcome": Outcome.DENIED, "file_fingerprint": checksum}
        if year is not None:
            context["archive_year"] = year
        with transaction.atomic():
            _audit(Action.CAMPAIGN_ARCHIVE_PREVIEWED, actor_id, None, context)

    try:
        current_year = stewardship_year()
    except ArchiveRefusal:
        # year_unresolved: every import is refused, before any check.
        denied()
        raise
    document = _check(data, check or check_archive)
    if not document["valid"]:
        denied()
        raise ArchiveRefusal(document["problems"], document.get("more_problems", 0))
    metadata = document["metadata"]
    year = metadata["campaign_year"]
    if current_year is not None and year == current_year:
        denied(year)
        raise refusal(
            "current_campaign_year",
            "this is the current campaign's year; the report reads the "
            "current campaign live",
        )
    try:
        archive, action, current = _store_preview(
            actor_id,
            data,
            checksum,
            document,
            keep_family_rows=keep_family_rows,
            file_name=file_name,
            record=record,
        )
    except ArchiveRefusal:
        # partial_over_final; the preview's transaction rolled back first.
        denied(year)
        raise
    return Review(archive, action, current, tuple(document["flagged_notes"]))


def _store_preview(
    actor_id, data, checksum, document, *, keep_family_rows, file_name, record
):
    """Store a checked file as a pending preview, in one transaction.

    Returns the preview, what Apply would do and the import it concerns
    (see ``Review``). Raises ``ArchiveRefusal`` (``partial_over_final``)
    with nothing stored.
    """
    from parishkit.stewardship.audit.schemas import Action, Outcome
    from parishkit.stewardship.reports.archive_models import (
        CampaignArchive,
        CampaignArchiveBand,
        CampaignArchiveDay,
        CampaignArchiveFamily,
    )

    metadata = document["metadata"]
    year = metadata["campaign_year"]
    renewal = document["renewal_families"] if keep_family_rows else []
    quartiles = _quartiles(document)
    with transaction.atomic():
        _housekeeping(_now())
        already = CampaignArchive.objects.filter(
            state="active", checksum=checksum
        ).first()
        current = CampaignArchive.objects.filter(
            state="active", campaign_year=year
        ).first()
        if already is not None:
            action, current = "already", already
        elif current is not None:
            if current.is_final and not metadata["is_final"]:
                raise refusal(
                    "partial_over_final",
                    f"a partial file cannot replace the final {year} import",
                )
            action = "replace"
        else:
            action = "add"
        archive = CampaignArchive.objects.create(
            actor_id=actor_id,
            checksum=checksum,
            file_name=clean_file_name(file_name),
            file_size=len(data),
            schema_version=metadata["schema_version"],
            campaign_name=metadata["campaign_name"],
            campaign_year=year,
            is_final=metadata["is_final"],
            as_of=_instant(metadata["as_of"]),
            timezone=metadata["timezone"],
            start_date=date.fromisoformat(metadata["campaign_start_date"]),
            last_submission_date=date.fromisoformat(
                metadata["campaign_last_submission_date"]
            ),
            active_families=metadata["active_families"],
            active_families_provenance=metadata["active_families_provenance"],
            source_system=metadata["source_system"],
            dedup_rule=metadata["dedup_rule"],
            family_id_systems=sorted(document["family_id_systems"]),
            family_rows_kept=keep_family_rows,
            notes=_stored_notes(document),
            days=len(document["daily"]),
            families_responded=document["families"],
            submission_rows=document["submission_rows"],
            unattributed_rows=document["unattributed_rows"],
            ministry_rows=document["ministry_rows"],
            pledging_families=document["pledging_families"],
            annual_pledged=Decimal(document["annual_pledged"]),
            first_quartile=quartiles[0],
            median=quartiles[1],
            third_quartile=quartiles[2],
            band_set_version=document["band_set_version"],
        )
        CampaignArchiveDay.objects.bulk_create(
            CampaignArchiveDay(
                actor_id=actor_id,
                archive=archive,
                day=row["campaign_day"],
                local_date=date.fromisoformat(row["local_date"]),
                first_submissions=row["first_submissions"],
                cumulative_families=row["cumulative_families"],
                cumulative_pledging_families=row["cumulative_pledging_families"],
                cumulative_annual_pledged=Decimal(row["cumulative_annual_pledged"]),
            )
            for row in document["daily"]
        )
        CampaignArchiveBand.objects.bulk_create(
            CampaignArchiveBand(
                actor_id=actor_id,
                archive=archive,
                band_set_version=document["band_set_version"],
                band=band["band"],
                families=band["families"],
                total=Decimal(band["total"]),
            )
            for band in document["bands"]
        )
        CampaignArchiveFamily.objects.bulk_create(
            CampaignArchiveFamily(
                actor_id=actor_id,
                archive=archive,
                family_id_system=system,
                family_id=family_id,
                first_campaign_day=first_day,
            )
            for system, family_id, first_day in renewal
        )
        archive.family_row_count = len(renewal)
        _audit(
            Action.CAMPAIGN_ARCHIVE_PREVIEWED,
            actor_id,
            archive.pk,
            _archive_context(archive, Outcome.SUCCEEDED),
        )
        if record is not None:
            record()
    return archive, action, current


def _family_rows(archive):
    """How many Family rows an archive keeps."""
    from parishkit.stewardship.reports.archive_models import CampaignArchiveFamily

    return CampaignArchiveFamily.objects.filter(archive=archive).count()


def apply(store, actor_id, archive_id, *, replace_acknowledged=False, record=None):
    """Make one preview the year's active import; return an ``Applied``.

    The preview must be the caller's own, still pending and younger than
    fifteen minutes; otherwise it is stale (``StaleRecordError``). A file
    already imported changes no import (``already``); its preview is
    deleted. A replacement needs ``replace_acknowledged``, and a partial file
    never replaces a final one. A racing Apply that won first makes this one
    stale. ``record()`` runs in the Apply's transaction whenever the preview
    was used, ``already`` included.
    """
    from parishkit.stewardship.audit.schemas import Action, Outcome
    from parishkit.stewardship.reports.archive_models import CampaignArchive

    authorize(store, actor_id)
    try:
        with transaction.atomic():
            now = _now()
            pending = (
                CampaignArchive.objects.select_for_update()
                .filter(pk=archive_id, state="pending", actor_id=actor_id)
                .first()
            )
            if pending is None or pending.created_at < now - timedelta(
                seconds=PREVIEW_SECONDS
            ):
                raise StaleRecordError("The preview expired or was applied.")
            current_year = stewardship_year()
            if current_year is not None and pending.campaign_year == current_year:
                raise refusal(
                    "current_campaign_year",
                    "this is the current campaign's year; the report reads the "
                    "current campaign live",
                )
            already = (
                CampaignArchive.objects.select_for_update()
                .filter(state="active", checksum=pending.checksum)
                .first()
            )
            if already is not None:
                # Nothing is imported, but the preview is used up, so the
                # command's own event (admin_cmd_history_confirm) is kept:
                # its token was spent and a repeat is stale.
                _delete_pending([pending.pk])
                if record is not None:
                    record()
                return Applied("already", already)
            current = (
                CampaignArchive.objects.select_for_update()
                .filter(state="active", campaign_year=pending.campaign_year)
                .first()
            )
            if current is not None:
                if current.is_final and not pending.is_final:
                    raise refusal(
                        "partial_over_final",
                        "a partial file cannot replace the final "
                        f"{pending.campaign_year} import",
                    )
                if not replace_acknowledged:
                    raise ReplaceNotAcknowledged(pending.campaign_year)
                current = _mark_removed(current, actor_id, now)
            CampaignArchive.objects.filter(pk=pending.pk, state="pending").update(
                state="active",
                version=pending.version + 1,
                imported_by_id=actor_id,
                imported_at=now,
            )
            pending.refresh_from_db()
            pending.family_row_count = _family_rows(pending)
            if current is not None:
                current.family_row_count = 0
                _audit(
                    Action.CAMPAIGN_ARCHIVE_REPLACED,
                    actor_id,
                    current.pk,
                    _archive_context(current, Outcome.CHANGED),
                )
            _audit(
                Action.CAMPAIGN_ARCHIVE_IMPORTED,
                actor_id,
                pending.pk,
                _archive_context(pending, Outcome.SUCCEEDED),
            )
            if record is not None:
                record()
            return Applied(
                "replaced" if current is not None else "imported", pending, current
            )
    except IntegrityError as error:
        # The expected race: another Apply made the same file or year active
        # first (a one-active-row index), so this preview is stale.
        if getattr(error.__cause__, "sqlstate", None) == "23505":
            raise StaleRecordError("Another import of this year won.") from None
        raise


def _mark_removed(archive, actor_id, now):
    """Remove an active import: tombstone the row, delete its children."""
    from parishkit.stewardship.reports.archive_models import CampaignArchive

    updated = CampaignArchive.objects.filter(
        pk=archive.pk, state="active", version=archive.version
    ).update(
        state="removed",
        version=archive.version + 1,
        removed_by_id=actor_id,
        removed_at=now,
    )
    if updated != 1:
        raise StaleRecordError("The import changed meanwhile.")
    _delete_children([archive.pk])
    archive.refresh_from_db()
    return archive


def remove(store, actor_id, year, *, expected_version, record=None):
    """Remove a year's active import; return its tombstone row.

    ``LookupError`` when the year has no active import; ``StaleRecordError``
    when its version is not ``expected_version``. ``record()`` runs in the
    removal's transaction.
    """
    from parishkit.stewardship.audit.schemas import Action, Outcome
    from parishkit.stewardship.reports.archive_models import CampaignArchive

    authorize(store, actor_id)
    with transaction.atomic():
        archive = (
            CampaignArchive.objects.select_for_update()
            .filter(state="active", campaign_year=year)
            .first()
        )
        if archive is None:
            raise LookupError("No import of that year.")
        if archive.version != expected_version:
            raise StaleRecordError("The import changed since it was read.")
        family_rows = _family_rows(archive)
        archive = _mark_removed(archive, actor_id, _now())
        archive.family_row_count = family_rows
        _audit(
            Action.CAMPAIGN_ARCHIVE_REMOVED,
            actor_id,
            archive.pk,
            _archive_context(archive, Outcome.CHANGED),
        )
        if record is not None:
            record()
    return archive


def archives():
    """Imported and removed archives, newest year first, then newest import.

    Pending previews are never listed or read by the report.
    """
    from django.db.models import Count

    from parishkit.stewardship.reports.archive_models import CampaignArchive

    return list(
        CampaignArchive.objects.filter(~Q(state="pending"))
        .annotate(family_row_count=Count("family_rows"))
        .order_by("-campaign_year", "state", "-created_at")
    )

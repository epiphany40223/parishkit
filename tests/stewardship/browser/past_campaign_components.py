"""Past campaigns (#1018, slice 4), as its view renders it.

Every figure is invented. ``/past-campaigns`` lists a final 2026 and a
partial 2025; its Review posts to the real ``review/`` address, which the
fixture server answers as the view does, with a redirect to the page
naming the preview (a Review card that replaces 2025); its Apply answers
with the page naming what was done (2025 replaced), and its Remove dialog
posts to ``removal/`` and redraws from a page without 2025. ``-refused``
answers its Review with the located problems, ``-year`` has Review
unavailable for an unresolvable year, and ``-empty`` has nothing imported.
``POSTS`` lists the POST answers, filled in by ``components``.
"""

from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID

from django.template.loader import render_to_string
from django.urls import reverse

from parishkit.stewardship.reports import past_campaign_views as views
from parishkit.stewardship.web.tables import whole_table

PAGE = "/past-campaigns"
REVIEW = reverse("admin:past_campaign_review")
APPLY = reverse("admin:past_campaign_import")
REMOVE = reverse("admin:past_campaign_removal")
REFUSED_REVIEW = REVIEW + "?refused"
PREVIEW = UUID(int=1)
APPLIED = UUID(int=2)
ADMIN = UUID(int=3)
CARD = f"{PAGE}?preview={PREVIEW}"
DONE = f"{PAGE}?applied={APPLIED}&outcome=replaced"
REMOVED = f"{PAGE}-removed"
# The fixtures' file limit, so a small test file counts as too large.
MAX_BYTES = 2048
POSTS = {}


def archive(year, *, final=True, pk=None, state="active", **fields):
    """One import as the page reads it."""
    values = {
        "pk": pk or UUID(int=year),
        "campaign_year": year,
        "campaign_name": f"Stewardship {year}",
        "file_name": f"stewardship-{year}.xlsx",
        "checksum": f"{year:x}".rjust(64, "a"),
        "state": state,
        "version": 2,
        "is_final": final,
        "start_date": date(year - 1, 9, 14),
        "last_submission_date": date(year - 1, 11, 30),
        "days": 78,
        "families_responded": 812,
        "annual_pledged": Decimal("1234567.50"),
        "active_families": 1480,
        "active_families_provenance": (
            "Families issued <campaign> codes, from an invented snapshot"
        ),
        "source_system": "An invented old system",
        "dedup_rule": "Each Family's last submission",
        "notes": {"note.01": "Converted from invented exports"},
        "family_rows_kept": True,
        "imported_by_id": ADMIN,
        "imported_at": datetime(2026, 10, 1, 14, 5, tzinfo=UTC),
        "removed_by_id": None,
        "removed_at": None,
        "created_at": datetime(2026, 10, 11, 14, tzinfo=UTC),
    }
    return SimpleNamespace(**(values | fields))


def context_for(active, removed=(), **extra):
    """The template's context for these imports, as ``views._render`` builds it."""
    emails = {ADMIN: "admin@example.org"}
    rows = [views._row(row, emails) for row in active]
    return {
        "table": whole_table(rows, sorting=views.SORTING, sort="-year"),
        "tombstones": [views._tombstone(row, emails) for row in [*active, *removed]],
        "refresh_url": REMOVED,
        "card": None,
        "applied": None,
        "problems": [],
        "more_problems": 0,
        "year_problem": None,
        "max_bytes": MAX_BYTES,
    } | extra


def components(context, admin):
    """The page in each state its tests need."""
    final = archive(2026)
    partial = archive(2025, final=False)
    reviewed = archive(2025, pk=PREVIEW, state="pending", file_name="corrected.xlsx")
    replaced = archive(2025, pk=APPLIED, imported_at=datetime(2026, 10, 11, tzinfo=UTC))
    old = archive(
        2025,
        final=False,
        pk=UUID(int=9),
        state="removed",
        removed_by_id=ADMIN,
        removed_at=datetime(2026, 10, 11, tzinfo=UTC),
    )

    def page(values, action=None):
        """Render the page; ``action`` replaces the Review form's address."""
        html = render_to_string(
            "stewardship/past-campaigns.html",
            context | {"admin_chrome": admin} | values,
        )
        if action:
            html = html.replace(f'action="{REVIEW}#', f'action="{action}#')
        return "text/html", html

    card = {
        "archive": reviewed,
        "action": "replace",
        "current": partial,
        "current_by": "admin@example.org",
        "replace_blocked": False,
        "day_1": "September 14, 2024",
        "last_day": "November 30, 2024",
        "families": "812",
        "pledged": "$1,234,567.50",
        "active": "1,480",
        "notes": views.note_rows(reviewed)
        + [("note.02", "Envelope 12345 was merged into another", True)],
        "family_rows": "790",
        "expires_at": datetime(2026, 10, 11, 14, 15, tzinfo=UTC),
    }
    problems = [
        {
            "code": "formula",
            "place": "families!J2",
            "rule": "a formula is not allowed; enter the value itself",
        },
        {
            "code": "header",
            "place": "daily!A1",
            "rule": "expected the column campaign_day",
        },
    ]
    POSTS.update(
        {
            REVIEW: (303, f"{CARD}#archive-review", ""),
            APPLY: (303, f"{DONE}#archive-review", ""),
            REMOVE: (303, PAGE, ""),
            REFUSED_REVIEW: (
                400,
                None,
                page(context_for([final, partial], problems=problems, more_problems=3))[
                    1
                ],
            ),
        }
    )
    applied = {
        "outcome": "replaced",
        "archive": replaced,
        "imported_by": "admin@example.org",
    }
    return {
        PAGE: page(context_for([final, partial])),
        CARD: page(context_for([final, partial], card=card)),
        DONE: page(context_for([final, replaced], [old], applied=applied)),
        REMOVED: page(context_for([final], [partial])),
        f"{PAGE}-refused": page(context_for([final, partial]), action=REFUSED_REVIEW),
        f"{PAGE}-year": page(
            context_for(
                [final],
                year_problem={"code": "year_unresolved", "rule": "cannot be resolved"},
            )
        ),
        f"{PAGE}-empty": page(context_for([])),
    }

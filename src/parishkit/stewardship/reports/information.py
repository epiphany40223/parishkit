"""Bounded, private staff queue queries over live Family submissions."""

import json
from dataclasses import dataclass
from datetime import date, datetime

from django.core.exceptions import ObjectDoesNotExist
from django.db import connection

from parishkit.stewardship.campaigns.read_guards import ReadUnavailable
from parishkit.stewardship.responses.models import AdditionalInformationRevision
from parishkit.stewardship.web.content import bounded_text
from parishkit.stewardship.web.contracts import PageWindow, filters

from .weekly_presentation import DISPOSITIONS

ORDERS = {
    "newest": "submitted_at DESC,id",
    "oldest": "submitted_at,id",
    "name": "lower(family_name),id",
    "name_desc": "lower(family_name) DESC,id",
}
PAGE_SIZE = 50


@dataclass(frozen=True, repr=False)
class InformationQuery:
    """Search is private POST state, never a query-string or audit payload."""

    search: str = ""
    disposition: str = "current_actionable"
    needed: str = "any"
    completed: str = "any"
    start: str = ""
    end: str = ""
    sort: str = "newest"
    page: int = 1

    @classmethod
    def parse(cls, parameters):
        """Accept single bounded values and canonical parish-local date filters."""
        values = filters(parameters, allowed=set(cls.__dataclass_fields__))
        query = cls(**(values | {"page": parse_page(values.get("page", "1"))}))
        if (
            query.disposition not in {*DISPOSITIONS, "all"}
            or query.needed not in {"any", "yes", "no"}
            or query.completed not in {"any", "yes", "no"}
            or query.sort not in ORDERS
        ):
            raise ValueError("Invalid information filters.")
        bounded_text(query.search)
        for value in (query.start, query.end):
            if value and date.fromisoformat(value).isoformat() != value:
                raise ValueError("Invalid date filter.")
        if query.start and query.end and query.start > query.end:
            raise ValueError("Invalid date interval.")
        return query

    def form_values(self):
        """Return escaped-by-template values for CSRF-protected page navigation."""
        return {
            key: getattr(self, key)
            for key in self.__dataclass_fields__
            if key != "page"
        }


def parse_page(value):
    """Reject alternate integer spellings before the shared bounded page helper."""
    if not value.isascii() or not value.isdecimal() or str(int(value)) != value:
        raise ValueError("Invalid information page.")
    PageWindow(page=int(value))
    return int(value)


PAGE_SQL = """
WITH selected AS MATERIALIZED (
    SELECT c.id,cc.name,cc.timezone,sc.snapshot_id AS source_id,
        ss.generation AS source_generation,ss.promoted_at AS source_as_of
    FROM stewardship_campaign c
    JOIN stewardship_campaign_configuration cc ON cc.id=c.active_configuration_id
    JOIN stewardship_source_current sc ON sc.singleton
    JOIN stewardship_source_snapshot ss ON ss.id=sc.snapshot_id
        AND ss.state='promoted' AND ss.compacted_at IS NULL
    WHERE c.id=%(campaign)s
), history AS MATERIALIZED (
    SELECT stewardship_weekly_history_v1(id,'production',NULL) AS value
    FROM selected
), rows AS MATERIALIZED (
    SELECT i.id,i.version,i.text,i.disposition,i.replacement_id,
        i.follow_up_needed,i.followed_up_at,r.followed_up_by_id,
        coalesce(r.notes,'') AS notes,s.submitted_at,f.family_duid,
        coalesce(nullif(btrim(p.canonical::jsonb->>'mailingName'),''),
            nullif(btrim(concat_ws(' ',
                nullif(btrim(p.canonical::jsonb->>'firstName'),''),
                nullif(btrim(p.canonical::jsonb->>'lastName'),''))),''),
            'Family') AS family_name,
        h.value->'reported' ? i.id::text AS previously_reported,
        h.value->'corrected' @>
            jsonb_build_array(jsonb_build_array(i.id::text,i.disposition))
            AS correction_resolved
    FROM selected x CROSS JOIN history h
    JOIN stewardship_submission s ON s.campaign_id=x.id AND s.mode='live'
    JOIN stewardship_additional_information i ON i.submission_id=s.id
    JOIN stewardship_family_campaign f ON f.id=s.family_id
    LEFT JOIN stewardship_snapshot_family m
        ON m.snapshot_id=x.source_id AND m.source_key=f.family_duid::text
    LEFT JOIN stewardship_source_family p ON p.id=m.payload_id
    LEFT JOIN LATERAL (
        SELECT notes,followed_up_by_id FROM stewardship_information_revision
        WHERE item_id=i.id ORDER BY expected_version DESC LIMIT 1) r ON true
    WHERE (%(item)s::uuid IS NULL OR i.id=%(item)s)
      AND (%(start)s::date IS NULL
          OR (s.submitted_at AT TIME ZONE x.timezone)::date>=%(start)s::date)
      AND (%(end)s::date IS NULL
          OR (s.submitted_at AT TIME ZONE x.timezone)::date<=%(end)s::date)
), filtered AS MATERIALIZED (
    SELECT * FROM rows WHERE (%(disposition)s='all' OR disposition=%(disposition)s)
      AND (%(needed)s='any' OR follow_up_needed=(%(needed)s='yes'))
      AND (%(completed)s='any' OR (followed_up_at IS NOT NULL)=(%(completed)s='yes'))
      AND (%(search)s='' OR position(lower(%(search)s) IN lower(family_name))>0
        OR position(%(search)s IN family_duid::text)>0
        OR position(lower(%(search)s) IN lower(text))>0
        OR position(lower(%(search)s) IN lower(notes))>0)
), page AS (
    SELECT * FROM filtered ORDER BY {order} LIMIT %(size)s OFFSET %(offset)s
)
SELECT jsonb_build_object('metadata',to_jsonb(x),
    'total',(SELECT count(*) FROM filtered),
    'rows',coalesce((SELECT jsonb_agg(to_jsonb(page) ORDER BY {order}) FROM page),
        '[]'::jsonb))::text
FROM selected x
"""


def information_page(campaign_id, query, *, item_id=None):
    """Detach one coherent source/item page under the caller's campaign guard.

    Only a fixed sort expression is interpolated. Every private value remains a
    bound parameter. One MVCC statement prevents mixed source names, workflow
    versions, result counts and page rows during concurrent Staff/Family edits.
    """
    with connection.cursor() as cursor:
        cursor.execute(
            PAGE_SQL.format(order=ORDERS[query.sort]),
            dict(
                campaign=campaign_id,
                item=item_id,
                search=query.search,
                disposition=query.disposition,
                needed=query.needed,
                completed=query.completed,
                start=query.start or None,
                end=query.end or None,
                size=PAGE_SIZE,
                offset=(query.page - 1) * PAGE_SIZE,
            ),
        )
        value = cursor.fetchone()
    if value is None:
        raise ReadUnavailable("Information report inputs are unavailable.")
    result = json.loads(value[0])
    if item_id is not None and not result["rows"]:
        raise ObjectDoesNotExist("Information item is unavailable.")
    for row in result["rows"]:
        row["disposition_label"] = DISPOSITIONS[row["disposition"]]
        for field in ("submitted_at", "followed_up_at"):
            row[field] = datetime.fromisoformat(row[field]) if row[field] else None
    result["metadata"]["source_as_of"] = datetime.fromisoformat(
        result["metadata"]["source_as_of"]
    )
    return result


def information_history(item_id, page, *, version):
    """Bound history to the displayed version, even if another edit now commits."""
    return PageWindow(page=page).rows(
        AdditionalInformationRevision.objects.filter(
            item_id=item_id, expected_version__lt=version
        ).order_by("-expected_version")
    )

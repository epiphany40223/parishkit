"""Record durable Family engagement from the paths that already see it (#477).

Sign-in, form issuance and the presence heartbeat each call this module inside
their own transaction. The write is one ``INSERT ... ON CONFLICT DO UPDATE``
whose update only moves each column the way the funnel needs: a ``first_*``
instant to the earliest known value, the furthest step forward by its rank in
``PRESENCE_SECTIONS``, ``last_seen_at`` to the latest; an update that would
change nothing is skipped (``DO UPDATE ... WHERE``), so replaying the same or
older evidence writes nothing at all. The backfill command relies on that.

Reporting data must never cost a Family its sign-in or form. Sign-in and form
issuance therefore use ``record_engagement_best_effort``: the write runs in a
savepoint with its foreign key checked immediately, so any refusal surfaces
inside the savepoint and is rolled back alone; the failure is kept as a
durable operational event and the request goes on. The heartbeat, which
carries nothing but presence, calls ``record_engagement`` directly and lets a
failure become its ordinary temporary denial.

The engagement foreign key is created DEFERRABLE INITIALLY DEFERRED, as every
Django foreign key here is. Checking it at commit would take the KEY SHARE
lock on the FamilyCampaign row only then, after another transaction may have
locked that row and started waiting on this transaction's uncommitted
engagement row: a deadlock, likeliest during a backfill. Every write here
first sets the constraint IMMEDIATE for its transaction, so the KEY SHARE is
taken with the insert, before any such wait can begin.
"""

from uuid import uuid4

from django.db import DatabaseError, connection, transaction

from parishkit.stewardship.audit.schemas import ContextKind, Outcome
from parishkit.stewardship.audit.services import operational
from parishkit.stewardship.observability import (
    Event,
    current_correlation,
    emit_failure,
)

from .credential_models import PRESENCE_SECTIONS

# Step order of the form; welcome is 1. ``stewardship_engagement_rank_v1`` in
# the schema lists the same names in the same order.
SECTION_RANK = {name: index + 1 for index, name in enumerate(PRESENCE_SECTIONS)}
FIRST_SECTION = PRESENCE_SECTIONS[0]
# Django's generated name for the family foreign key; the schema SQL and the
# frozen migration create it under exactly this name.
FAMILY_CONSTRAINT = "stewardship_family_e_family_id_71a8a34d_fk_stewardsh"

_RANK = "public.stewardship_engagement_rank_v1"
_UPSERT = f"""
INSERT INTO public.stewardship_family_engagement AS e
    (id, actor_id, correlation_id, version, family_id, mode, rehearsal_epoch_id,
     first_link_at, first_form_at, first_progress_at, furthest_section, furthest_at,
     last_seen_at)
VALUES (%(id)s, %(actor_id)s, %(correlation_id)s, 1, %(family_id)s, %(mode)s,
        %(rehearsal_epoch_id)s, %(link_at)s, %(form_at)s, %(progress_at)s,
        %(section)s, %(section_at)s, %(seen_at)s)
ON CONFLICT ON CONSTRAINT family_engagement_identity DO UPDATE SET
    first_link_at=LEAST(e.first_link_at, EXCLUDED.first_link_at),
    first_form_at=LEAST(e.first_form_at, EXCLUDED.first_form_at),
    first_progress_at=LEAST(e.first_progress_at, EXCLUDED.first_progress_at),
    furthest_section=CASE
        WHEN {_RANK}(EXCLUDED.furthest_section) > {_RANK}(e.furthest_section)
        THEN EXCLUDED.furthest_section ELSE e.furthest_section END,
    furthest_at=CASE
        WHEN {_RANK}(EXCLUDED.furthest_section) > {_RANK}(e.furthest_section)
        THEN EXCLUDED.furthest_at ELSE e.furthest_at END,
    last_seen_at=GREATEST(e.last_seen_at, EXCLUDED.last_seen_at),
    version=e.version + 1,
    actor_id=EXCLUDED.actor_id,
    correlation_id=EXCLUDED.correlation_id
WHERE EXCLUDED.first_link_at < e.first_link_at
   OR (e.first_link_at IS NULL AND EXCLUDED.first_link_at IS NOT NULL)
   OR EXCLUDED.first_form_at < e.first_form_at
   OR (e.first_form_at IS NULL AND EXCLUDED.first_form_at IS NOT NULL)
   OR EXCLUDED.first_progress_at < e.first_progress_at
   OR (e.first_progress_at IS NULL AND EXCLUDED.first_progress_at IS NOT NULL)
   OR {_RANK}(EXCLUDED.furthest_section) > {_RANK}(e.furthest_section)
   OR EXCLUDED.last_seen_at > e.last_seen_at
"""


def engagement_mode(session_mode):
    """Spell a Family session's mode as responses do: "test" or "live"."""
    return "test" if session_mode == "testing" else "live"


def engagement_values(
    *,
    family_id,
    mode,
    rehearsal_epoch_id,
    seen_at,
    actor_id=None,
    link_at=None,
    form_at=None,
    section=None,
    section_at=None,
):
    """The upsert's bound values for one observation; no SQL is built from them.

    ``section`` is the form step a heartbeat reported, with ``section_at`` the
    instant it was seen. A step past the first one is progress, so it also
    supplies ``first_progress_at``. Every instant is bounded by ``seen_at``,
    the latest thing known about the Family in this observation.
    """
    if mode not in {"live", "test"}:
        raise ValueError("Engagement mode must be live or test.")
    if (section is None) != (section_at is None):
        raise ValueError("A section and its instant come together.")
    if section is not None and section not in SECTION_RANK:
        raise ValueError("Unknown form section.")
    if link_at is None and form_at is None and section is None:
        raise ValueError("An engagement observation needs some evidence.")
    for instant in (link_at, form_at, section_at):
        if instant is not None and instant > seen_at:
            raise ValueError("Engagement evidence cannot follow the observation.")
    return {
        "id": uuid4(),
        "actor_id": actor_id,
        "correlation_id": current_correlation(),
        "family_id": family_id,
        "mode": mode,
        "rehearsal_epoch_id": rehearsal_epoch_id,
        "link_at": link_at,
        "form_at": form_at,
        "progress_at": (
            section_at if section is not None and section != FIRST_SECTION else None
        ),
        "section": section if section is not None else "",
        "section_at": section_at,
        "seen_at": seen_at,
    }


def record_engagement(**observation):
    """Upsert one observation in the caller's transaction; see ``engagement_values``.

    Returns whether a row was inserted or changed.
    """
    values = engagement_values(**observation)
    with connection.cursor() as cursor:
        cursor.execute(f'SET CONSTRAINTS "{FAMILY_CONSTRAINT}" IMMEDIATE')
        cursor.execute(_UPSERT, values)
        return cursor.rowcount == 1


def record_engagement_best_effort(**observation):
    """Record an observation without ever failing the request that saw it.

    The savepoint confines a refusal to this write; the surrounding sign-in or
    form issuance commits as if the observation had not happened, and the
    durable ``family_engagement_failed`` event (plus the process log) says so.
    Returns whether the observation was recorded.
    """
    try:
        with transaction.atomic():
            record_engagement(**observation)
            return True
    except DatabaseError as error:
        emit_failure(error, event=Event.FAMILY_ENGAGEMENT_FAILED)
        operational(
            Event.FAMILY_ENGAGEMENT_FAILED,
            level="ERROR",
            schema=ContextKind.EXCEPTION,
            context={"outcome": Outcome.FAILED},
        )
        return False

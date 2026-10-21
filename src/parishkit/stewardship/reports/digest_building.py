"""A sent Daily digest's document, rebuilt from its retained inputs (#1834).

The digest pipeline that compiled and sent these is retired; the saved
digest pages still read what each send retained.
"""

from parishkit.stewardship.source.data_age import data_age_at

from .daily_digest import DailyDigestDocument
from .digest_capture import retained_dates, retained_statistics
from .digest_funnel import digest_funnel, report_day_end
from .documents import participation_document
from .facts import FactUnavailable, fact_inputs


def retained_daily_document(snapshot, facts, comparison=None):
    """Load a sent digest's pinned observation for the authorized saved pages.

    ``comparison`` is the retained section the saved page shows (the ready
    row's), never computed again here.
    """
    if facts.state != "ready" or fact_inputs(snapshot) != fact_inputs(facts):
        raise FactUnavailable("Daily report facts do not match their retained input.")
    document = participation_document(
        facts,
        parish_name=snapshot.configuration.parish.name,
        browser_timezone=snapshot.timezone_configuration.timezone,
        requested_at=snapshot.observed_at,
    )
    # The ParishSoft data age and connection as known at the observation
    # (#510), for the email and the saved report page alike. The compiled
    # email body is retained, so later refreshes never rewrite a sent digest.
    covered = retained_dates(snapshot)
    mode = snapshot.preparation.mode
    # The response funnel at the end of the report day (#477), counted from
    # durable timestamps here and again on the saved page, so both agree.
    funnel = digest_funnel(
        snapshot.campaign_id,
        mode,
        report_day_end(covered[-1], document.campaign_timezone, snapshot.observed_at),
    )
    return DailyDigestDocument(
        snapshot.pk,
        document,
        retained_statistics(snapshot),
        covered,
        snapshot.configuration.parish.date_format,
        source_age=data_age_at(snapshot.observed_at),
        funnel=funnel,
        mode=mode,
        comparison=comparison,
    )

"""``report data-to-fix``: Data to fix in ParishSoft's counts (ADM-11, #1011).

The command reads the page (``accounts.data_to_fix_views``) through the
page's own functions: its admission (``_principal``: Administrators and
Staff, anyone who may open at least one section's detail page), its read of
the configuration and current campaign, ``read_sections`` (each section's
existing read, only for the sections the reader's role may see), and its
audit (``data_to_fix_viewed`` with the total the reader saw).

It prints counts only, never a name, address or value: ``campaign_id`` and,
under ``sections``, one member per section the role may see (a JSON
object, so printed in key order). Each is ``{"count": n}`` (with
``families`` for refused addresses), or ``{"unavailable": reason}`` with
the page's words when that section cannot count now. A section that cannot
count never fails the command, as it never fails the page.
"""

from dataclasses import dataclass
from uuid import UUID

from .admin_reads import ReadModel, Unavailable, _held, _recheck
from .admin_report_reads import _admit


@dataclass(frozen=True)
class DataToFixReport(ReadModel):
    """The page's section counts.

    ``campaign_id`` is the current campaign, or None without one.
    ``sections`` maps each visible section's key (``form_cannot_open``,
    ``refused_addresses``, ``unreachable_families``, ``census_changes``,
    ``roster_changes``) to its count, or to ``unavailable`` with the page's
    reason.
    """

    campaign_id: UUID | None
    sections: dict


def data_to_fix_model(campaign, rows):
    """The command's projection of the page's rows: counts and reasons only."""
    sections = {}
    for row in rows:
        if "count" in row:
            sections[row["key"]] = {
                name: row[name] for name in ("count", "families") if name in row
            }
        else:
            sections[row["key"]] = {"unavailable": str(row["reason"])}
    return DataToFixReport(
        campaign_id=campaign.pk if campaign else None, sections=sections
    )


def read_data_to_fix(caller, service):
    """``report data-to-fix``: the page's counts for the current campaign.

    Admits passively with the page's admission, so a session of any scope
    reads and a Ministry leader is ``denied``. A restore under review is
    ``unavailable``, as the page's 503. The session is rechecked before
    anything is reported, then the page's ``data_to_fix_viewed`` is
    recorded with the total the reader saw.
    """
    from .accounts.data_to_fix_views import (
        _principal,
        audit,
        current,
        read_sections,
        total,
    )
    from .audit.schemas import Outcome

    def step():
        """Admit, count each section, recheck and audit, as the page."""
        actor = _admit(caller, service, _principal)
        found = current(service.store)
        if found is None:
            raise Unavailable("Data to fix cannot be read now.")
        configuration, campaign = found
        rows = read_sections(actor, configuration, campaign)
        _recheck(caller, service.store, actor)
        audit(actor, Outcome.SUCCEEDED, total(rows))
        return data_to_fix_model(campaign, rows)

    return _held(step)

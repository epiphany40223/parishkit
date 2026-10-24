"""Exact source-read scope, independent of unrelated campaign content edits."""

from dataclasses import dataclass
from datetime import date
from uuid import UUID

from parishkit.stewardship.campaigns.campaign_families import stored_rule
from parishkit.stewardship.campaigns.configuration import campaign_values

from .canonical import InvalidSourcePayload, canonical_payload


@dataclass(frozen=True)
class GivingPeriod:
    """One inclusive civil-date period and its explicitly selected source funds."""

    start: date
    end: date
    funds: tuple[int, ...]

    def document(self):
        """Dates remain civil dates, not midnight instants with a guessed offset."""
        return {
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "funds": list(self.funds),
        }


@dataclass(frozen=True)
class RefreshWindow:
    """Immutable campaign binding; no archive may expand the live source window.

    ``families`` is the campaign's Families rule (#1393), which decides the
    load's Portal and email eligibility; ``None`` is the legacy rule.
    """

    campaign_id: UUID | None
    periods: tuple[GivingPeriod, ...]
    families: dict | None = None

    def document(self):
        """Serialize only query-relevant values for durable optimistic admission.

        The Families rule joins the document only when the campaign has one,
        so a legacy campaign's window, and its digest, stay exactly as they
        were before rules existed (as does SQL's campaign-less digest).
        """
        document = {
            "campaign_id": str(self.campaign_id) if self.campaign_id else None,
            "periods": [period.document() for period in self.periods],
        }
        if self.families is not None:
            document["campaign_families"] = self.families
        return document

    @property
    def digest(self):
        """Content/template/name-only edits cannot invalidate a source request."""
        return canonical_payload(self.document())[1]


def refresh_window(*, campaign_id, state, values):
    """Select giving for the sole current draft/scheduled/active/closed campaign.

    The current pointer may retain an archived campaign until Return to Testing;
    ordinary census refresh can continue, but archived giving remains bound to
    its retained immutable source. A null current pointer requests no giving.
    The source request owner must compare this binding again before promotion.
    """
    if campaign_id is None:
        if state is not None or values is not None:
            raise InvalidSourcePayload(
                "A source window has inconsistent campaign input."
            )
        return RefreshWindow(None, ())
    if not isinstance(campaign_id, UUID) or state not in {
        "draft",
        "scheduled",
        "active",
        "closed",
        "archived",
    }:
        raise InvalidSourcePayload("The source campaign window is unavailable.")
    campaign_values(values)
    families = stored_rule(values)
    financial = values["financial"]
    if state == "archived" or "financial" not in values["modules"] or financial is None:
        return RefreshWindow(campaign_id, (), families)
    periods = tuple(
        GivingPeriod(
            date.fromisoformat(financial[prefix + "start"]),
            date.fromisoformat(financial[prefix + "end"]),
            tuple(financial[prefix + "fund_duids"]),
        )
        for prefix in ("", "comparison_")
    )
    return RefreshWindow(campaign_id, periods, families)

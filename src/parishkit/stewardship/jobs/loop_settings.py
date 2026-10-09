"""The scheduler loop's runtime settings, read once per loop and without locks.

Every scheduler loop runs about twenty producers. Before #715 each one read
the runtime singletons again (the system configuration row, the current
campaign and its credential row), most of them inside a global work-order
lock transaction, on every loop, even when there was nothing to do. A
producer now first asks "is there anything to do?" without the lock, and
that read takes these rows from one LoopSettings object built per loop:
each row is read at most once per loop, and only if a producer asks for it.

These rows are hints for that read, never authority. A producer that finds
work takes the work-order lock and reads everything again, with its usual
row locks and admission checks, and the SQL guards stay authoritative. So a
stale row can only delay work, never admit it: a change committed after a
row was read here (a pause, a configuration change) is seen by the next
loop, two seconds later, which is also when a change committed just after
a locked read is seen. The clock is never cached here: a producer's read
asks the database for the current instant, so clock-driven work (a due
reminder, a boundary, a refresh slot) is found on the loop it falls due.
"""

from functools import cached_property

from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.campaigns.credential_models import CampaignCredentialState
from parishkit.stewardship.campaigns.models import (
    ActivationCatchUpDemand,
    Campaign,
    CampaignWorkGate,
)


class LoopSettings:
    """Lazily read, per-loop copies of the runtime singletons (no locks)."""

    @cached_property
    def runtime(self):
        """The system configuration row, or None before initial setup."""
        return SystemConfiguration.objects.first()

    @cached_property
    def campaign_id(self):
        """The current campaign's identity, or None."""
        return None if self.runtime is None else self.runtime.current_campaign_id

    @cached_property
    def campaign(self):
        """The current campaign with its active configuration, or None."""
        if self.campaign_id is None:
            return None
        return (
            Campaign.objects.select_related("active_configuration")
            .filter(pk=self.campaign_id)
            .first()
        )

    @cached_property
    def credentials(self):
        """The current campaign's credential row outside its go-live gate, or None."""
        if self.campaign_id is None:
            return None
        return CampaignCredentialState.objects.filter(
            campaign_id=self.campaign_id, go_live_gate=False
        ).first()

    @cached_property
    def gated(self):
        """Whether the current campaign has an unreleased work gate."""
        if self.campaign_id is None:
            return False
        return (
            CampaignWorkGate.objects.filter(campaign_id=self.campaign_id)
            .exclude(state="released")
            .exists()
        )

    @cached_property
    def catching_up(self):
        """Whether the current campaign's activation catch-up is unfinished."""
        if self.campaign_id is None:
            return False
        return ActivationCatchUpDemand.objects.filter(
            campaign_id=self.campaign_id, completed_at__isnull=True
        ).exists()

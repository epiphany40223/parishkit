"""The compiled shape of an Administrator-routed mail owner.

Operational incident alerts and login-policy security alerts share one
delivery contract: a SQL-created immutable source row, a frozen per-source
recipient cohort captured by a fenced preparation Task, one outbox message
per address, and a private MAIL helper that recompiles fixed content. What
differs is closed here, per owner, so neither can borrow the other's source,
recipients, content or SQL admission.
"""

from collections.abc import Callable
from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True)
class AlertOwner:
    """Closed per-purpose facts and callables; never a caller-supplied choice."""

    purpose: str
    label: str
    task_type: str
    namespace: UUID
    source_field: str
    cohort_model: type
    recipient_model: type
    cohort_table: str
    recipient_table: str
    configured: Callable[[], bool]
    pending_sources: Callable[[int], tuple]
    source_exists: Callable[[UUID], bool]
    capture: Callable[..., object]
    alert: Callable[..., object]
    mail_render: Callable[..., tuple]
    current_mail: Callable[..., tuple]
    recipient_current: Callable[..., bool]
    submit: Callable[..., object]

    def __post_init__(self):
        """Reject an owner assembled from anything but closed, typed parts."""
        if (
            self.purpose not in ("operational", "security_event")
            or not isinstance(self.namespace, UUID)
            or not all(
                callable(getattr(self, name))
                for name in (
                    "configured",
                    "pending_sources",
                    "source_exists",
                    "capture",
                    "alert",
                    "mail_render",
                    "current_mail",
                    "recipient_current",
                    "submit",
                )
            )
        ):
            raise TypeError("An alert owner requires closed, typed parts.")

    def source_id(self, cohort):
        """The opaque source identity this cohort was captured for."""
        return getattr(cohort, self.source_field)

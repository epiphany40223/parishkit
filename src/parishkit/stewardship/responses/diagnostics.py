"""Value-free durable diagnostics for unusable, already Family-scoped source."""

from parishkit.stewardship.audit.schemas import ContextKind
from parishkit.stewardship.audit.services import operational
from parishkit.stewardship.observability import Event


def record_unusable_source(error):
    """Identify the source correction needed without recording any census value."""
    operational(
        Event.SOURCE_MEMBER_UNUSABLE,
        level="WARNING",
        schema=ContextKind.MEMBER_SOURCE,
        context=error.context,
    )

"""The report email block registry (report-emails spec, "Block registry").

One module per block; ``REGISTRY`` maps each block's stable key to its
declaration, in the order the spec lists them. The compiler
(``compiler.compile_report``) reads only this registry.
"""

from . import (
    additional_information,
    campaign_totals,
    compared_with_past_campaigns,
    day_by_day,
    participation_charts,
    response_funnel,
)

REGISTRY = {
    module.BLOCK.key: module.BLOCK
    for module in (
        additional_information,
        campaign_totals,
        response_funnel,
        participation_charts,
        day_by_day,
        compared_with_past_campaigns,
    )
}

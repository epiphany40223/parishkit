"""The setup draft's content step names, kept for the frozen step CHECK.

The wizard collected first-campaign pages and emails before #142, one draft
step per content slot. No setup page saves them any more, but the
``setup_public_step`` CHECK on ``SetupDraftSection`` (``setup_models``)
still names every step, and changing it needs a migration, so the step list
stays here. A draft saved before #142 may still hold such steps; setup
never reads them (``setup_preview.SYSTEM_SECTIONS``).
"""

from .content_schema import EMAIL_SLOTS, PAGE_SLOTS

CONTENT_STEPS = tuple(
    f"{kind}_{slot}"
    for kind, slots in (("page", PAGE_SLOTS), ("email", EMAIL_SLOTS))
    for slot in sorted(slots)
)

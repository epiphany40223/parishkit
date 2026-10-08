"""The shared button: one definition, a portal rendering and an email rendering.

Portal templates use the ``{% button %}`` tag (templatetags/stewardship.py),
which renders a ``<button>`` (or, with ``href``, a link styled as one) with
the stylesheet's classes; ``test_button_guard.py`` keeps templates on it.
Emails cannot load the stylesheet, so :func:`email_button` builds the same
button from the design tokens with inline styles (#732).
"""

from html import escape
from urllib.parse import urlsplit

from .design_tokens import BUTTON, COLORS, EMAIL_SANS, RADII, hex6, px

# Each variant's portal class. A primary <button> needs no class: the
# stylesheet styles every button as primary unless a class says otherwise.
VARIANTS = {
    "primary": "",
    "secondary": "button-secondary",
    "large": "button-large",
    "link": "link-button",
}
# A link styled as a button always carries "button"; it cannot look like a
# plain link (use a plain <a> for that).
LINK_VARIANTS = {"primary", "secondary", "large"}


def portal_classes(variant, extra="", *, link=False):
    """The class attribute value for one portal button, or "" for none.

    ``extra`` holds a template's own classes (layout hooks such as
    ``sort-link``), kept after the variant's class.
    """
    if variant not in (LINK_VARIANTS if link else VARIANTS):
        raise ValueError(f"unknown button variant: {variant!r}")
    names = ["button"] if link else []
    names += [VARIANTS[variant], *str(extra or "").split()]
    return " ".join(name for name in names if name)


def _email_colors(variant):
    """(background, text) for an email button variant."""
    if variant == "primary":
        return hex6(COLORS["accent"]), hex6(COLORS["paper"])
    if variant == "secondary":
        return hex6(COLORS["paper"]), hex6(COLORS["accent"])
    raise ValueError(f"unknown email button variant: {variant!r}")


def email_button(label, href, *, variant="primary"):
    """One bulletproof email button: a link in a one-cell presentation table.

    Mail programs keep inline styles only, so every value is inlined from
    the design tokens. The cell carries the colour, border and corner radius
    (``bgcolor`` too, for clients that drop CSS backgrounds) and
    ``mso-padding-alt`` gives Outlook for Windows, which ignores padding on
    links, the same size; there the corners are square but the button keeps
    its colour, size and real-text label. There is no VML: its fixed pixel
    width cannot fit a label of unknown length. The label is real text, so
    the button reads with images off, and the light-on-dark (or dark-on-
    light) pair stays legible when a dark mode inverts both colours.
    """
    parts = urlsplit(href)
    # A host is required too: "https:foo" and "http:///x" parse with an
    # http(s) scheme but are not absolute addresses any mail program opens.
    if parts.scheme not in ("https", "http") or not parts.hostname:
        raise ValueError("an email button must link to an absolute http(s) address")
    background, text = _email_colors(variant)
    border = f"{BUTTON['button-border-width']} solid {hex6(COLORS['accent'])}"
    radius = f"{px(RADII['radius-small'])}px"
    block, inline = (f"{px(part)}px" for part in BUTTON["button-padding"].split())
    # The 44px minimum height: two borders, two paddings and the line.
    line = px(BUTTON["button-min-height"]) - 2 * (
        px(block) + px(BUTTON["button-border-width"])
    )
    cell = (
        f"background-color:{background};border:{border};border-radius:{radius};"
        f"mso-padding-alt:{block} {inline};"
    )
    link = (
        f"display:inline-block;padding:{block} {inline};color:{text};"
        f"font-family:{EMAIL_SANS};font-size:{px('1rem')}px;font-weight:bold;"
        f"line-height:{line}px;text-decoration:none;border-radius:{radius};"
    )
    return (
        '<table role="presentation" cellpadding="0" cellspacing="0" border="0" '
        'style="margin:16px 0;"><tr>'
        f'<td align="center" bgcolor="{background}" style="{cell}">'
        f'<a href="{escape(href)}" style="{link}">{escape(label, quote=False)}</a>'
        "</td></tr></table>"
    )

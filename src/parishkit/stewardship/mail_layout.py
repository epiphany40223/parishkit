"""The one email-client-safe HTML document around every stewardship email.

Stored and rendered email bodies are sanitized fragments with bare tags. On
their own they display in each mail program's default (often serif, full
width) style. Senders wrap the finished, already sanitized fragment here, just
before MIME construction, so authored content is never re-interpreted and the
retained content, digests and canonical checks stay unchanged.

Mail programs support CSS unevenly: Gmail keeps only a <style> block in the
head and Outlook for Windows ignores max-width, so the layout uses a centered
presentation table (with an Outlook-only fixed-width table), inline styles on
the container and on each bare sanitized tag, and a matching <style> block.

Every email, Admin and Family alike, uses the Admin portal's colours and
shapes from the shared design tokens (:data:`SHARED`, #732). Family mail also
draws its call to action, a paragraph holding only one bold link, as the
shared email button (:func:`web.buttons.email_button`). Every sender says
whether it sends Family mail, so a new Family-facing sender cannot lose that
button by default.
"""

import re
from dataclasses import dataclass
from html import escape, unescape

from .web.buttons import email_button
from .web.design_tokens import COLORS, EMAIL_SANS, RADII, hex6, px

FONT = EMAIL_SANS


@dataclass(frozen=True)
class Look:
    """The colours and shapes one email look inlines (all six-digit hex)."""

    text: str
    page: str
    card_border: str
    card_radius: str
    heading: str
    link: str
    quote_rule: str
    quote_text: str
    notice: str

    def tag_styles(self):
        """Inline styles for the bare tags that sanitized content contains."""
        return {
            "p": "margin:0 0 16px;",
            "h2": (
                "margin:24px 0 12px;font-size:20px;line-height:1.3;"
                f"color:{self.heading};"
            ),
            "h3": (
                "margin:20px 0 8px;font-size:17px;line-height:1.35;"
                f"color:{self.heading};"
            ),
            "ul": "margin:0 0 16px;padding:0 0 0 24px;",
            "ol": "margin:0 0 16px;padding:0 0 0 24px;",
            "li": "margin:0 0 6px;",
            "blockquote": (
                f"margin:0 0 16px;padding:0 0 0 14px;border-left:3px solid "
                f"{self.quote_rule};color:{self.quote_text};"
            ),
        }

    def link_style(self):
        """The inline style of every link that has none of its own."""
        return f"color:{self.link};text-decoration:underline;"


def _token(name):
    """One design-token colour as the six-digit hex older mail programs need."""
    return hex6(COLORS[name])


# Every email: the portal's ink, page, panel border and corner radius,
# link colour and warning notice (the portal's .notice, with its thicker
# leading edge), all from web/design_tokens.py.
SHARED = Look(
    text=_token("ink"),
    page=_token("page"),
    card_border=_token("border"),
    card_radius=f"{px(RADII['radius'])}px",
    heading=_token("ink"),
    link=_token("link"),
    quote_rule=_token("border"),
    quote_text=_token("muted"),
    notice=(
        f"background-color:{_token('warning-bg')};"
        f"border:1px solid {_token('warning-border')};"
        f"border-left-width:{px('.35rem')}px;"
        f"border-radius:{px(RADII['radius-small'])}px;"
        f"padding:8px 12px;font-size:14px;line-height:1.4;"
        f"color:{_token('warning-ink')};"
    ),
)

# The shared look's styles under their established names.
TAG_STYLES = SHARED.tag_styles()
LINK_STYLE = SHARED.link_style()
NOTICE_STYLE = SHARED.notice
_BARE_TAG = re.compile("<(" + "|".join(TAG_STYLES) + ")>")
_UNSTYLED_LINK = re.compile(r"<a (?![^>]*\bstyle=)")
# Hosted images (#346) fit narrow phone mail clients; the campaign banner
# already carries its own style.
_UNSTYLED_IMAGE = re.compile(
    r'<img (?=[^>]*\bsrc="[^"]*/files/[A-Za-z0-9_-]{43}")(?![^>]*\bstyle=)'
)
IMAGE_STYLE = "max-width:100%;height:auto;border:0;"
# The mandatory Testing banner that routing prepends to retained Family,
# receipt and digest mail (and that the database checks), shown as a notice.
_TEST_BANNER = re.compile(r"\A<h2>TEST</h2><p>([^<]*)</p>")
# A Family email's call to action: a paragraph holding only one bold link, in
# the sanitizer's canonical form. The default invitation and reminder mark
# "Begin your household's renewal" this way. Inline links never match.
_CALL_TO_ACTION = re.compile(
    r'<p><strong><a href="(https?://[^"]+)" rel="noopener noreferrer">'
    r"([^<]+)</a></strong></p>"
)


def _buttons(html):
    """Draw each call-to-action paragraph as the shared email button.

    The label and link are the sanitized content's own, unescaped once
    because email_button() escapes them again; nothing else in the body
    changes. A link email_button() refuses (no host), or one whose label is
    only spaces (which would draw an empty coloured box), stays a plain link.
    """

    def button(match):
        label = unescape(match[2])
        if not label.strip():
            return match[0]
        try:
            return email_button(label, unescape(match[1]))
        except ValueError:
            return match[0]

    return _CALL_TO_ACTION.sub(button, html)


def _style_block():
    """The <style> block for mail programs that keep one, matching the inline styles."""
    return (
        f"body{{margin:0;padding:0;background-color:{SHARED.page};}}"
        f".pk-body{{font-family:{FONT};font-size:16px;line-height:1.5;color:{SHARED.text};}}"
        ".pk-body p{margin:0 0 16px;}"
        ".pk-body h2{margin:24px 0 12px;font-size:20px;line-height:1.3;}"
        ".pk-body h3{margin:20px 0 8px;font-size:17px;line-height:1.35;}"
        ".pk-body ul,.pk-body ol{margin:0 0 16px;padding:0 0 0 24px;}"
        ".pk-body li{margin:0 0 6px;}"
        f".pk-body a{{color:{SHARED.link};text-decoration:underline;}}"
        ".pk-body img{max-width:100%;height:auto;border:0;}"
        "@media only screen and (max-width:620px){"
        ".pk-card{padding:20px 16px !important;}}"
    )


def _styled(html):
    """Add the inline style of each bare sanitized tag, unstyled link and image."""
    styles = SHARED.tag_styles()
    html = _BARE_TAG.sub(lambda match: f'<{match[1]} style="{styles[match[1]]}">', html)
    html = _UNSTYLED_IMAGE.sub(f'<img style="{IMAGE_STYLE}" ', html)
    return _UNSTYLED_LINK.sub(f'<a style="{SHARED.link_style()}" ', html)


def notice_html(text):
    """One small line, in the shared warning style, at the top of a test message."""
    return (
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        'border="0" style="margin:0 0 20px;"><tr><td style="'
        + SHARED.notice
        + '">'
        + escape(text, quote=False)
        + "</td></tr></table>"
    )


# Family and operational mail keep the common 600px column. The Admin report
# emails are read at a desk and lead with charts and bars, so they use this
# desktop width instead (#720); a narrower window still shrinks them to fit.
REPORT_WIDTH = 960


def email_document(html, *, family, notice=None, width=600):
    """Return the complete HTML part for one already sanitized email body.

    ``width`` is the column's largest width in CSS pixels, and Outlook for
    Windows' fixed width. ``notice`` is plain text for a small test notice
    above the content. A
    leading routed Testing banner (``<h2>TEST</h2><p>…</p>``) becomes that
    notice instead of a large heading. Every email uses the shared design
    tokens (:data:`SHARED`). ``family`` is required: True for Family mail
    and its readiness and test-send samples, whose call-to-action paragraphs
    become the shared email button, and False for Admin mail, which is left
    as compiled. The plain-text alternative is built separately by each
    sender and is not affected.
    """
    if family:
        html = _buttons(html)
    banner = _TEST_BANNER.match(html)
    if banner is not None:
        html = html[banner.end() :]
        notice = unescape(banner[1]) if notice is None else notice
    top = notice_html(notice) if notice else ""
    return (
        "<!DOCTYPE html>"
        '<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<meta name="x-apple-disable-message-reformatting">'
        f"<style>{_style_block()}</style></head>"
        f'<body style="margin:0;padding:0;background-color:{SHARED.page};">'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        f'border="0" style="background-color:{SHARED.page};"><tr>'
        '<td align="center" style="padding:24px 12px;">'
        # Outlook for Windows ignores max-width; fix its width instead.
        f'<!--[if mso]><table role="presentation" width="{width}" cellpadding="0" '
        'cellspacing="0" border="0"><tr><td><![endif]-->'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        f'border="0" style="max-width:{width}px;background-color:#ffffff;'
        f"border:1px solid {SHARED.card_border};border-radius:{SHARED.card_radius};"
        '"><tr>'
        '<td class="pk-body pk-card" style="padding:28px 32px;text-align:left;'
        f"font-family:{FONT};font-size:16px;line-height:1.5;color:{SHARED.text};"
        '">' + top + _styled(html) + "</td></tr></table>"
        "<!--[if mso]></td></tr></table><![endif]-->"
        "</td></tr></table></body></html>"
    )

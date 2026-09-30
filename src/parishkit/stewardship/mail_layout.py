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
"""

import re
from html import escape, unescape

FONT = "Arial, Helvetica, sans-serif"
TEXT = "#1f2933"
PAGE = "#f4f5f7"
LINK = "#1a56db"

# Inline styles for the bare tags that sanitized content contains. Only an
# attribute-free opening tag is styled, so compiler-owned digest markup that
# carries its own attributes is left alone.
TAG_STYLES = {
    "p": "margin:0 0 16px;",
    "h2": "margin:24px 0 12px;font-size:20px;line-height:1.3;color:#111827;",
    "h3": "margin:20px 0 8px;font-size:17px;line-height:1.35;color:#111827;",
    "ul": "margin:0 0 16px;padding:0 0 0 24px;",
    "ol": "margin:0 0 16px;padding:0 0 0 24px;",
    "li": "margin:0 0 6px;",
    "blockquote": (
        "margin:0 0 16px;padding:0 0 0 14px;border-left:3px solid #d0d5dd;"
        "color:#475467;"
    ),
}
LINK_STYLE = f"color:{LINK};text-decoration:underline;"
NOTICE_STYLE = (
    "background-color:#fff8db;border:1px solid #f0d77a;border-radius:4px;"
    "padding:8px 12px;font-size:13px;line-height:1.4;color:#5c4a00;"
)
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

_STYLE_BLOCK = (
    f"body{{margin:0;padding:0;background-color:{PAGE};}}"
    f".pk-body{{font-family:{FONT};font-size:16px;line-height:1.5;color:{TEXT};}}"
    ".pk-body p{margin:0 0 16px;}"
    ".pk-body h2{margin:24px 0 12px;font-size:20px;line-height:1.3;}"
    ".pk-body h3{margin:20px 0 8px;font-size:17px;line-height:1.35;}"
    ".pk-body ul,.pk-body ol{margin:0 0 16px;padding:0 0 0 24px;}"
    ".pk-body li{margin:0 0 6px;}"
    f".pk-body a{{color:{LINK};text-decoration:underline;}}"
    ".pk-body img{max-width:100%;height:auto;border:0;}"
    "@media only screen and (max-width:620px){"
    ".pk-card{padding:20px 16px !important;}}"
)


def _styled(html):
    """Add the inline style of each bare sanitized tag, unstyled link and image."""
    html = _BARE_TAG.sub(
        lambda match: f'<{match[1]} style="{TAG_STYLES[match[1]]}">', html
    )
    html = _UNSTYLED_IMAGE.sub(f'<img style="{IMAGE_STYLE}" ', html)
    return _UNSTYLED_LINK.sub(f'<a style="{LINK_STYLE}" ', html)


def notice_html(text):
    """One small, clearly styled line at the top of a test message."""
    return (
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        'border="0" style="margin:0 0 20px;"><tr><td style="'
        + NOTICE_STYLE
        + '">'
        + escape(text, quote=False)
        + "</td></tr></table>"
    )


def email_document(html, *, notice=None):
    """Return the complete HTML part for one already sanitized email body.

    ``notice`` is plain text for a small test notice above the content. A
    leading routed Testing banner (``<h2>TEST</h2><p>…</p>``) becomes that
    notice instead of a large heading. The plain-text alternative is built
    separately by each sender and is not affected.
    """
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
        f"<style>{_STYLE_BLOCK}</style></head>"
        f'<body style="margin:0;padding:0;background-color:{PAGE};">'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        f'border="0" style="background-color:{PAGE};"><tr>'
        '<td align="center" style="padding:24px 12px;">'
        # Outlook for Windows ignores max-width; fix its width at 600px.
        '<!--[if mso]><table role="presentation" width="600" cellpadding="0" '
        'cellspacing="0" border="0"><tr><td><![endif]-->'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        'border="0" style="max-width:600px;background-color:#ffffff;'
        'border:1px solid #e4e7ec;border-radius:8px;"><tr>'
        '<td class="pk-body pk-card" style="padding:28px 32px;text-align:left;'
        f"font-family:{FONT};font-size:16px;line-height:1.5;color:{TEXT};"
        '">' + top + _styled(html) + "</td></tr></table>"
        "<!--[if mso]></td></tr></table><![endif]-->"
        "</td></tr></table></body></html>"
    )

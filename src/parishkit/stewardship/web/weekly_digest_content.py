"""Closed no-image weekly report markup for the isolated mail boundary."""

import nh3

from .content import TAGS

# A reference parish can submit thousands of distinct requests in one interval.
# This bound is separate from the daily chart and ordinary authored prose limits.
MAX_WEEKLY_BODY_BYTES = 8 * 1024 * 1024


def validate_weekly_body(html, text):
    """Validate compiled bodies without repairing, interpreting, or logging them."""
    try:
        if any(
            type(value) is not str
            or not value.strip()
            or "\x00" in value
            or len(value.encode("utf-8")) > MAX_WEEKLY_BODY_BYTES
            for value in (html, text)
        ):
            raise ValueError
        clean = nh3.clean(
            html,
            tags=TAGS,
            attributes={"a": {"href", "title"}},
            url_schemes={"https", "http", "mailto", "tel"},
            link_rel="noopener noreferrer",
            strip_comments=True,
        )
        if clean != html:
            raise ValueError
    except (ValueError, TypeError, UnicodeError):
        raise ValueError("Invalid compiled weekly digest content.") from None

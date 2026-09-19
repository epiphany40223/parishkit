"""Exact post-close release exceptions, never a generic bypass of live pause."""

from django.db import connection


def message_released(message):
    """Read a decision for this message and the campaign's current pause version."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT stewardship_delivery_message_released_v1(%s)", [message.pk]
        )
        return cursor.fetchone()[0]

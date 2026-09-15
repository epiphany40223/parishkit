"""Bounded schedule-level impact counts without reading Family or email content."""

import hashlib
import json

from django.db import connection


def work_summary(campaign_id):
    """Capture aggregate monotonic versions and exact current-revision work counts.

    Every occurrence/task mutation increments its version. Count plus version
    sums therefore invalidates a preview when that retained corpus changes,
    without materializing per-Family rows. Purge is separately fenced by the
    campaign work scope; ordinary schedule work cannot delete these records.
    The database's counts-only boundary shares the exact reconciliation rules
    without exposing recipients, renders or substitutions to the web process.
    Outbox and complete retry-chain versions also invalidate stale previews.
    """
    rows = {}
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT definition_id, revision, definition_version, occurrences, "
            "versions, task_versions, outbox_versions, outboxes, blocking, "
            "cancellable, failed, delivered, covered "
            "FROM public.stewardship_schedule_work_summary WHERE campaign_id=%s",
            [campaign_id],
        )
        columns = [column[0] for column in cursor.description][2:]
        for identifier, revision, *counts in cursor.fetchall():
            rows[str(identifier)] = {
                "revision": str(revision),
                **dict(zip(columns, map(int, counts), strict=True)),
            }
    return rows


def fingerprint(domain_fingerprint, summary):
    """A signed preview binds domain scope and the exact aggregate work generation."""
    return hashlib.sha256(
        json.dumps([domain_fingerprint, summary], sort_keys=True).encode()
    ).hexdigest()

"""Report emails in the configuration: the ``campaign-series-v12`` schema (#1786).

A report email is one ``report`` record of the ``schedules`` section (the
report-emails spec, "Report send records"). The schema is every rule of the
users form and its remembered reminder series (``campaign-series-v11``,
whose section stays optional) for the other records, plus each report
record's own values (``campaigns.report_values``) and the rules that span a
campaign's reports. It admits no Daily or Weekly digest record: the
conversion (``reports_upgrade``) turns each into a report.

The schema is chosen by content (``configuration_schema.schema_for``): a
users-form document holding a report record is this schema. A document
without one keeps its earlier schema, whose rules it already meets, and
none may gain a digest record again (``campaigns.configuration``'s
``validate_campaign_change``), so the digest kinds never return.
"""

from parishkit.stewardship.campaigns.report_values import (
    report_values,
    validate_reports,
)
from parishkit.stewardship.schema_primitives import invalid

from . import series_schema, users_schema

SCHEMA = users_schema.REPORTS_SCHEMA
# The offline conversion of the Daily and Weekly digests (#1786).
UPGRADE_REQUEST_SCHEMA = "operator-upgrade-reports-v12"
DIGEST_KINDS = frozenset({"daily_digest", "weekly_digest"})


def _kind(record):
    """A schedule record's kind, or None when its values are malformed."""
    values = record.get("values") if isinstance(record, dict) else None
    return values.get("kind") if isinstance(values, dict) else None


def uses_reports(document):
    """True when the document holds at least one report email record."""
    return any(
        _kind(record) == "report"
        for record in document["sections"].get("schedules", [])
    )


def has_digests(document):
    """True when the document still holds a Daily or Weekly digest record."""
    return any(
        _kind(record) in DIGEST_KINDS
        for record in document["sections"].get("schedules", [])
    )


def validate_report_records(document):
    """Each report belongs to a campaign of the document and is valid alone.

    Then each campaign's reports together: distinct names and at most the
    limit (``report_values.validate_reports``).
    """
    sections = document["sections"]
    campaigns = {row["id"] for row in sections.get("campaigns", [])}
    owned = {}
    for row in sections.get("schedules", []):
        if _kind(row) != "report":
            continue
        values = report_values(row["values"])
        if values["campaign_id"] not in campaigns:
            invalid()
        owned.setdefault(values["campaign_id"], []).append(values)
    for records in owned.values():
        validate_reports(records)


def validate_sections(document):
    """Every series-schema rule for the other records, then the reports.

    The other records are validated exactly as ``campaign-series-v11``
    validates them (its series section may be absent), so nothing about
    them changes meaning; a digest record is refused outright.
    """
    sections = document["sections"]
    if has_digests(document):
        invalid()
    others = dict(sections)
    if "schedules" in sections:
        others["schedules"] = [
            row for row in sections["schedules"] if _kind(row) != "report"
        ]
    series_schema.validate_sections(document | {"sections": others})
    validate_report_records(document)

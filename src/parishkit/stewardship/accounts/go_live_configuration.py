"""Resolve draft references before treating an applied configuration as ready.

Canonical draft validation deliberately permits unfinished selections. This
additional read-only check resolves the selected campaign's content, financial
mapping and provider/recipient prerequisites. It does not prove provider access,
source freshness, public-origin reachability or permission to change modes.
"""

from dataclasses import dataclass

from .integration_selection import integration_records


@dataclass(frozen=True)
class ConfigurationReadiness:
    """Closed checklist codes and selected Family-mail revisions, without bodies."""

    problems: tuple[str, ...]
    family_templates: tuple[str, ...]
    admin_recipients: tuple[str, ...]

    @property
    def ready(self):
        """A validated but unfinished draft must never appear ready to go live."""
        return not self.problems


def configuration_readiness(document, campaign_id, *, ministries, funds):
    """Resolve only this campaign against the caller's same-tenant source catalog.

    The caller obtains the canonical document through coherent configuration
    validation and verifies source freshness separately. Optional page blocks
    may be absent; a selected but missing block is not equivalent to omission.
    """
    sections = document["sections"]
    owner = str(campaign_id)
    campaign = next(
        row["values"] for row in sections["campaigns"] if row["id"] == owner
    )
    content = {
        row["id"]: row["values"]
        for row in sections.get("content", [])
        if row["values"]["campaign_id"] == owner
    }
    schedules = [
        row["values"]
        for row in sections.get("schedules", [])
        if row["values"]["campaign_id"] == owner
    ]
    problems = []
    if sum(row["kind"] == "initial" for row in schedules) != 1:
        problems.append("initial_schedule_required")
    if any(
        reference not in content
        or content[reference]["kind"] != "page"
        or content[reference]["slot"] != slot
        for slot, reference in campaign["content_versions"].items()
    ):
        problems.append("page_reference_unavailable")
    if any(not _template_matches(row, content) for row in schedules):
        problems.append("mail_template_unavailable")
    if set(campaign["ministry_duids"]) - ministries:
        problems.append("ministry_mapping_unavailable")
    financial = campaign["financial"]
    if "financial" in campaign["modules"] and (
        financial is None
        or not campaign["share_options"]
        or any(
            not financial[name] or set(financial[name]) - funds
            for name in ("fund_duids", "comparison_fund_duids")
        )
    ):
        problems.append("financial_mapping_incomplete")
    records = integration_records(document)
    if (
        any(
            kind not in records
            or records[kind]["values"]["credential_fingerprint"] is None
            # Google login is deployment-owned and was already authenticated;
            # initial setup need not duplicate it in campaign integrations.
            for kind in ("parishsoft", "google_workspace")
        )
        or "email" not in records
    ):
        problems.append("integration_configuration_incomplete")
    admins = tuple(
        sorted(
            row["values"]["email"]
            for row in sections.get("login_rules", [])
            if row["values"]["kind"] == "address"
            and "administrator" in row["values"]["roles"]
        )
    )
    if not admins:
        problems.append("admin_recipient_required")
    templates = tuple(
        sorted(
            {
                row["template_version"]
                for row in schedules
                if row["kind"] in {"initial", "reminder"}
                and _template_matches(row, content)
            }
        )
    )
    return ConfigurationReadiness(tuple(problems), templates, admins)


def _template_matches(schedule, content):
    """A UUID alone is not a selected, matching, usable email revision."""
    row = content.get(schedule["template_version"])
    return row is not None and (row["kind"], row["slot"], row["subject"]) == (
        "email",
        schedule["kind"],
        schedule["subject"],
    )

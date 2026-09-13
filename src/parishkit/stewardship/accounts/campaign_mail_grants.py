"""Public test-mail metadata and journal writes, never Family data or key handoffs."""


def add_campaign_mail_grants(tables, columns, *, scheduler=False):
    """Limit scheduler recovery and mail writes to their fenced journal owners."""
    tables["stewardship_campaign_mail_test"] = {"SELECT", "UPDATE"}
    for table in ("stewardship_applied_integration", "stewardship_content_version"):
        tables[table] = {"SELECT"}
    if not scheduler:
        columns["stewardship_campaign"] = {"SELECT": {"id", "state"}}
        columns["stewardship_campaign_work_gate"] = {"SELECT": {"state"}}
        columns["stewardship_campaign_credentials"] = {"SELECT": {"go_live_gate"}}

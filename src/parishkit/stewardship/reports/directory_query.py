"""The installed selection kernel owns both bounded pages and complete exports.

``stewardship_directory_report_v2`` (migration 0043, #933) is v1 plus the
Response and ParishSoft data to check filters, the response columns and
their sort orders; v1 stays installed but nothing calls it.
"""

DIRECTORY = "SELECT stewardship_directory_report_v2(%s,%s::jsonb,%s)::text"

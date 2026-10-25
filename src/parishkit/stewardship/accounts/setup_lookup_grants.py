"""Grants for the setup wizard's ParishSoft organization lookup (#1554).

The web requests, reads, chooses and discards a lookup; only the isolated
ParishSoft installer, which alone can open the staged key, answers it. The
end-of-setup scrub is a definer trigger, so no other login is granted the
table. The guard in frozen migration 0174 admits each move for its owner.
"""

TABLE = "stewardship_setup_parishsoft_lookup"
# What the web may change: the choice, the discard and their attribution.
WEB_UPDATE_COLUMNS = frozenset(
    {
        "state",
        "chosen_id",
        "chosen_name",
        "organizations",
        "error_code",
        "scrubbed_at",
        "actor_id",
        "correlation_id",
        "version",
    }
)


def add_web_lookup_grants(tables, columns):
    """The web wizard requests and reads lookups and records the choice."""
    tables[TABLE] = {"SELECT", "INSERT"}
    columns[TABLE] = {"UPDATE": set(WEB_UPDATE_COLUMNS)}


def extend_lookup_permissions(tables):
    """The ParishSoft installer claims and answers lookups."""
    tables[TABLE] = {"SELECT", "UPDATE"}

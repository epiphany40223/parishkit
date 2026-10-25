"""Grants for the setup wizard's quick parish data check (#2201).

The web requests, reads and discards a check; only the isolated ParishSoft
installer, which alone can open the staged key, claims and answers it. The
end-of-setup discard is a definer trigger, so no other login is granted the
table, except the credential installers' read of a check's binding columns
for their readiness check (``setup_install_grants``). The guard in frozen
migration 0183 admits each move for its owner.
"""

TABLE = "stewardship_setup_source_check"
# What the web may change: the discard and its attribution.
WEB_UPDATE_COLUMNS = frozenset({"state", "actor_id", "correlation_id", "version"})
# What every credential installer reads of a check: its credential binding,
# which stewardship_setup_install_selected_v1 compares with the key it
# installs.
BINDING_COLUMNS = frozenset(
    {"id", "credential_id", "credential_version", "fingerprint"}
)


def add_web_check_grants(tables, columns):
    """The web wizard requests, reads and discards parish data checks."""
    tables[TABLE] = {"SELECT", "INSERT"}
    columns[TABLE] = {"UPDATE": set(WEB_UPDATE_COLUMNS)}


def extend_check_permissions(tables):
    """The ParishSoft installer claims and answers parish data checks."""
    tables[TABLE] = {"SELECT", "UPDATE"}

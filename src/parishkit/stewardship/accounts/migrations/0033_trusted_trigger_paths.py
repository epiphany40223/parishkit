"""Pin legacy emitters/guards before granting operational database access.

Historical migrations stay unchanged. These functions already exist at the
accounts dependency below; no optional audit projection migration is assumed.
Public precedes explicit pg_temp, so temporary relations cannot divert an audit
INSERT or an activation/checkpoint dependency. Schema CREATE privilege must
also remain unavailable to runtime roles (the provisioning owner's contract).
"""

from django.db import migrations

# Frozen identities, not a catalog-wide ALTER that could change unrelated code.
FUNCTIONS = (
    "stewardship_request_checkpoint_v1",
    "stewardship_request_stage_v1",
    "stewardship_request_audit_v1",
    "stewardship_request_checkpoint_v2",
    "stewardship_runtime_guard_v1",
    "stewardship_runtime_activation_required_v1",
    "stewardship_activation_guard_v1",
    "stewardship_activation_effects_v1",
    "stewardship_secret_state_v1",
    "stewardship_secret_checkpoint_v1",
    "stewardship_secret_history_v1",
)


class Migration(migrations.Migration):
    """Retain this defense on downgrade; older migrations own function removal."""

    dependencies = [("stewardship_accounts", "0032_complete_credential_consumers")]
    operations = [
        migrations.RunSQL(
            "\n".join(
                f"ALTER FUNCTION public.{name}() "
                "SET search_path = pg_catalog, public, pg_temp;"
                for name in FUNCTIONS
            ),
            reverse_sql=migrations.RunSQL.noop,
        ),
    ]

"""Expose only current Chairperson identity evidence to policy reconciliation.

The view uses its schema owner's base-table privileges. Consumers receive only
SELECT on this security-barrier view, not on Member/contact census payloads.
All joins use the one current snapshot; staged and historical relationships
cannot accidentally supply a missing part of a current relationship.
"""

from django.db import migrations

FORWARD = """
CREATE VIEW stewardship_current_chair WITH (security_barrier=true) AS
SELECT cur.snapshot_id, cur.generation, cur.organization_id,
       member.source_key::bigint AS member_duid,
       ministry.source_key::bigint AS ministry_duid,
       email.value->>'value' AS email,
       (contact.canonical::jsonb->>'publish_email')::boolean AS publish_email,
       roster.source_key AS roster_key
FROM public.stewardship_source_current cur
JOIN public.stewardship_snapshot_roster rm ON rm.snapshot_id=cur.snapshot_id
JOIN public.stewardship_source_roster roster ON roster.id=rm.payload_id
JOIN public.stewardship_snapshot_member mm ON mm.snapshot_id=cur.snapshot_id
    AND mm.source_key=roster.member_key
JOIN public.stewardship_source_member member ON member.id=mm.payload_id
JOIN public.stewardship_snapshot_ministry tm ON tm.snapshot_id=cur.snapshot_id
    AND tm.source_key=roster.ministry_key
JOIN public.stewardship_source_ministry ministry ON ministry.id=tm.payload_id
JOIN public.stewardship_snapshot_contact cm ON cm.snapshot_id=cur.snapshot_id
    AND cm.source_key='member:'||member.source_key
JOIN public.stewardship_source_contact contact ON contact.id=cm.payload_id
CROSS JOIN LATERAL jsonb_array_elements(contact.canonical::jsonb->'emails') email
WHERE member.canonical::jsonb->'schema_version'='1'::jsonb
    AND contact.canonical::jsonb->'schema_version'='1'::jsonb
    AND ministry.canonical::jsonb->'schema_version'='1'::jsonb
    AND roster.canonical::jsonb->'schema_version'='1'::jsonb
    AND member.canonical::jsonb->'active'='true'::jsonb
    AND ministry.canonical::jsonb->'catalog_present'='true'::jsonb
    AND roster.canonical::jsonb->'current'='true'::jsonb
    AND translate(roster.canonical::jsonb->>'ministryRoleName',
                  'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz')
        ='chairperson'
    AND email.value->'valid'='true'::jsonb;
"""


class Migration(migrations.Migration):
    dependencies = [("stewardship_source", "0016_refresh_tick_guards")]
    operations = [
        migrations.RunSQL(FORWARD, "DROP VIEW stewardship_current_chair;"),
    ]

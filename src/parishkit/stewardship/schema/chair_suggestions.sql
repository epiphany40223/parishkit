-- Chairperson suggestions for the Administrator's review.
--
-- The current source relationships the narrow projection already proves,
-- with the names the page shows and the active Members who use each login
-- address, so a shared address is shown as ambiguous rather than guessed.
-- Names come from the promoted canonical payloads of the current snapshot
-- only. Nothing here is an authorization: a row is a fact about the parish
-- source, and only the Administrator-only Portal users page reads it. The
-- narrow projection beneath it is the one definition of a current
-- Chairperson, shared with the reconciliation owners; the restricted web
-- role is granted this view and not that projection, so the page can never
-- redefine "current Chairperson" from the source tables it reads elsewhere.
CREATE VIEW stewardship_chair_suggestion WITH (security_barrier=true) AS
SELECT chair.snapshot_id,
       chair.generation,
       chair.organization_id,
       chair.member_duid,
       concat_ws(' ',
           NULLIF(member.canonical::jsonb->>'firstName', ''),
           NULLIF(member.canonical::jsonb->>'middleName', ''),
           NULLIF(member.canonical::jsonb->>'lastName', '')) AS member_name,
       chair.ministry_duid,
       ministry.canonical::jsonb->>'name' AS ministry_name,
       chair.email,
       chair.publish_email,
       chair.roster_key,
       -- Every active Member of this snapshot whose valid address this is;
       -- more than one, or one other than the Chairperson, is an ambiguity.
       (SELECT array_agg(DISTINCT (other.source_key)::bigint
                         ORDER BY (other.source_key)::bigint)
          FROM stewardship_snapshot_member om
          JOIN stewardship_source_member other ON other.id=om.payload_id
          JOIN stewardship_snapshot_contact oc
            ON oc.snapshot_id=om.snapshot_id
           AND oc.source_key='member:'||other.source_key
          JOIN stewardship_source_contact contact ON contact.id=oc.payload_id
          CROSS JOIN LATERAL jsonb_array_elements(
              contact.canonical::jsonb->'emails') email(value)
         WHERE om.snapshot_id=chair.snapshot_id
           AND other.canonical::jsonb->'schema_version'='1'::jsonb
           AND contact.canonical::jsonb->'schema_version'='1'::jsonb
           AND other.canonical::jsonb->'active'='true'::jsonb
           AND email.value->'valid'='true'::jsonb
           AND email.value->>'value'=chair.email) AS address_members
  FROM stewardship_current_chair chair
  JOIN stewardship_snapshot_member mm
    ON mm.snapshot_id=chair.snapshot_id
   AND mm.source_key=chair.member_duid::text
  JOIN stewardship_source_member member ON member.id=mm.payload_id
  JOIN stewardship_snapshot_ministry tm
    ON tm.snapshot_id=chair.snapshot_id
   AND tm.source_key=chair.ministry_duid::text
  JOIN stewardship_source_ministry ministry ON ministry.id=tm.payload_id;

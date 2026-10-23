-- A successful interval requires its entire original cohort, not one accepted
-- child. Empty captures advance the watermark without inventing provider mail.
CREATE VIEW stewardship_weekly_digest_completion_ready AS
    SELECT p.id AS preparation_id,
      CASE WHEN EXISTS(SELECT 1 FROM stewardship_weekly_digest_recipient r
        LEFT JOIN stewardship_outbox_message m ON m.id=r.outbox_id
        WHERE r.snapshot_id=s.id AND (m.state='delivered'
          OR (r.outbox_id IS NULL AND jsonb_array_length(r.covered_messages)>0)))
        THEN 'delivered' ELSE 'empty' END AS disposition,
      CASE WHEN s.information='[]'::jsonb AND s.corrections='[]'::jsonb
        THEN 'weekly_digest_empty' ELSE 'weekly_digest_no_current_recipients' END AS empty_reason
    FROM stewardship_weekly_digest_preparation p
    JOIN stewardship_weekly_digest_snapshot s ON s.preparation_id=p.id
    WHERE p.phase='complete' AND (
      (s.information='[]'::jsonb AND s.corrections='[]'::jsonb)
      OR NOT EXISTS(SELECT 1 FROM jsonb_array_elements_text(s.recipients) wanted(address)
        WHERE NOT EXISTS(SELECT 1 FROM stewardship_weekly_digest_recipient r
          LEFT JOIN stewardship_outbox_message m ON m.id=r.outbox_id
          WHERE r.snapshot_id=s.id AND r.address=wanted.address
            AND (m.state='delivered' OR (m.state='cancelled' AND m.reason='recipient_revoked')
              -- A failed report to a recipient who is not currently an
              -- Administrator settles like a recipient_revoked cancellation,
              -- whatever ended it; the roster is read live (see the daily
              -- proof), and the metadata finalizer completes the occurrence.
              OR (m.state='permanent_failure' AND NOT EXISTS(
                SELECT 1 FROM stewardship_system_configuration runtime
                JOIN stewardship_address_rule admin
                  ON admin.configuration_id=runtime.active_configuration_id
                WHERE admin.roles @> '["administrator"]'::jsonb AND admin.email=r.address))
              OR (r.outbox_id IS NULL AND r.information='[]'::jsonb AND r.corrections='[]'::jsonb
                AND jsonb_array_length(r.covered_messages)>0))
            AND NOT EXISTS(SELECT 1 FROM jsonb_array_elements_text(r.covered_messages) proof(message_id)
              WHERE NOT EXISTS(SELECT 1 FROM stewardship_outbox_message accepted
                WHERE accepted.id=proof.message_id::uuid AND accepted.state='delivered')))));
REVOKE ALL ON stewardship_weekly_digest_completion_ready FROM PUBLIC;

-- Frozen forward migration file 0073 (the repository-wide file sequence):
-- mail dispatch reads one Family link token per claimed message, never the
-- whole token table (#389 L1). It is installed by the Django migration that
-- names it in FROZEN_SQL and must never change once released;
-- tests/stewardship/test_schema_migration_files.py pins its digest. It
-- replaces nothing, so the fresh-install baseline is unchanged: a fresh
-- install runs the baseline, the earlier files and then this.
--
-- Until this release the mail-dispatch login held column SELECT on every
-- Family's link-token ciphertext (stewardship_family_token.ciphertext) and
-- every rehearsal token ciphertext
-- (stewardship_rehearsal_credential.token_ciphertext). It also holds the
-- token private key, so a compromise of that login could decrypt every
-- Family's link at once. Dispatch only ever needs the one token of the
-- message it is about to send. This file:
--
--   adds stewardship_dispatch_family_token_v1(uuid, uuid, uuid, bigint, uuid)
--                                            the ciphertext and digest of one
--                                            message's own token, only while
--                                            the caller's delivery task for
--                                            that message is live (definer;
--                                            mail dispatch)
--   revokes mail dispatch's column SELECT on both ciphertext columns
--
-- The definer answers one row only when the message is an unsent
-- ('pending' or 'retry_wait') initial, reminder or Family-test message, and
-- the given run is that message's outbox_delivery task (its root is the
-- message's task and its domain request is the message), running, with the
-- given fence and worker and an unexpired lease: what
-- jobs/ownership.lock_task_claim and jobs/family_mail_dispatch.bound_dispatch
-- check in Python. Production answers the message's own Family, campaign
-- and token generation's undestroyed token; Testing answers the message's
-- own Family and rehearsal epoch's credential. Anything else answers no row.
-- The generation and epoch holds stay in Python, as before.
--
-- What this holds against a compromised mail-dispatch login: it can no
-- longer read every Family's ciphertext, only the token of a Family with an
-- unsent message under a live delivery claim. It reads only; no token, code
-- or link changes, so every emailed code and link stays valid.
--
-- The role is created by database-roles, not by the schema, so a database
-- migrated before its roles exist has nothing to revoke. Mail dispatch gets
-- EXECUTE through `database-grants`
-- (runtime_grants.DISPATCH_TOKEN_FUNCTION). Reversing needs its own forward
-- migration.
SET LOCAL check_function_bodies = false;
SET LOCAL search_path = public;

CREATE FUNCTION public.stewardship_dispatch_family_token_v1(wanted_message uuid, wanted_token uuid, claim_run uuid, claim_fence bigint, claim_worker uuid)
    RETURNS TABLE(ciphertext text, digest text)
    LANGUAGE sql VOLATILE SECURITY DEFINER
    SET search_path TO pg_catalog, public, pg_temp
AS $$
    -- The message is unsent Family mail, and the caller's run is its live
    -- delivery claim (lease checked against the wall clock, after any lock
    -- wait, as lock_task_claim does).
    WITH claimed AS (
        SELECT m.id, m.mode, m.family_id, m.campaign_id, m.token_generation_id,
            m.rehearsal_epoch_id
        FROM public.stewardship_outbox_message m
        JOIN public.stewardship_task_run r ON r.id=claim_run
        WHERE m.id=wanted_message AND m.state IN ('pending','retry_wait')
          AND m.purpose IN ('initial','reminder','family_test')
          AND r.root_id=m.task_id AND r.domain_request_id=m.id
          AND r.task_type='outbox_delivery' AND r.state='running'
          AND r.fence=claim_fence AND r.worker_id=claim_worker
          AND r.lease_expires_at>clock_timestamp()
    )
    SELECT t.ciphertext, t.digest::text
    FROM claimed c
    JOIN public.stewardship_family_token t ON t.id=wanted_token
    WHERE c.mode='production' AND t.family_id=c.family_id
      AND t.campaign_id=c.campaign_id AND t.generation_id=c.token_generation_id
      AND t.destroyed_at IS NULL
    UNION ALL
    SELECT rc.token_ciphertext, rc.token_digest::text
    FROM claimed c
    JOIN public.stewardship_rehearsal_credential rc ON rc.id=wanted_token
    WHERE c.mode='testing' AND rc.family_id=c.family_id
      AND rc.epoch_id=c.rehearsal_epoch_id
$$;
ALTER FUNCTION public.stewardship_dispatch_family_token_v1(uuid, uuid, uuid, bigint, uuid) SECURITY DEFINER;
REVOKE ALL ON FUNCTION public.stewardship_dispatch_family_token_v1(uuid, uuid, uuid, bigint, uuid) FROM PUBLIC;

-- Mail dispatch's whole-table reads of both ciphertext columns end here,
-- before database-grants runs in the same upgrade (database-grants never
-- revokes: it refuses a login holding more than it declares).
DO $revoke$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles
               WHERE rolname='pk_stewardship_mail_dispatch') THEN
        REVOKE SELECT (ciphertext) ON public.stewardship_family_token
            FROM pk_stewardship_mail_dispatch;
        REVOKE SELECT (token_ciphertext) ON public.stewardship_rehearsal_credential
            FROM pk_stewardship_mail_dispatch;
    END IF;
END
$revoke$;

-- Refuse to commit unless everything above is installed as declared.
DO $check$
BEGIN
    -- The one definer: SECURITY DEFINER, the fixed search_path, no EXECUTE
    -- for PUBLIC, its five arguments and two-column answer, the unsent,
    -- purpose and live-claim checks, both credential branches with their
    -- mode and Family gates, and no other overload.
    IF NOT EXISTS (SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
            WHERE n.nspname='public' AND p.proname='stewardship_dispatch_family_token_v1'
              AND p.prosecdef AND p.proretset AND p.provolatile='v'
              AND p.proconfig=ARRAY['search_path=pg_catalog, public, pg_temp']
              AND NOT has_function_privilege('public', p.oid, 'EXECUTE')
              AND pg_get_function_identity_arguments(p.oid)
                  ='wanted_message uuid, wanted_token uuid, claim_run uuid, claim_fence bigint, claim_worker uuid'
              AND pg_get_function_result(p.oid)='TABLE(ciphertext text, digest text)'
              AND p.prosrc LIKE '%WHERE m.id=wanted_message AND m.state IN (''pending'',''retry_wait'')%'
              AND p.prosrc LIKE '%AND m.purpose IN (''initial'',''reminder'',''family_test'')%'
              AND p.prosrc LIKE '%AND r.root_id=m.task_id AND r.domain_request_id=m.id%'
              AND p.prosrc LIKE '%AND r.task_type=''outbox_delivery'' AND r.state=''running''%'
              AND p.prosrc LIKE '%AND r.fence=claim_fence AND r.worker_id=claim_worker%'
              AND p.prosrc LIKE '%AND r.lease_expires_at>clock_timestamp()%'
              AND p.prosrc LIKE '%WHERE c.mode=''production'' AND t.family_id=c.family_id%'
              AND p.prosrc LIKE '%AND t.campaign_id=c.campaign_id AND t.generation_id=c.token_generation_id%'
              AND p.prosrc LIKE '%AND t.destroyed_at IS NULL%'
              AND p.prosrc LIKE '%WHERE c.mode=''testing'' AND rc.family_id=c.family_id%'
              AND p.prosrc LIKE '%AND rc.epoch_id=c.rehearsal_epoch_id%')
       OR (SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
           WHERE n.nspname='public' AND p.proname='stewardship_dispatch_family_token_v1')<>1 THEN
        RAISE EXCEPTION 'Migration 0073: the dispatch token definer is not installed as declared';
    END IF;
    -- Mail dispatch, when it exists, reads neither ciphertext column, by a
    -- column grant or a table grant.
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='pk_stewardship_mail_dispatch')
       AND (has_column_privilege('pk_stewardship_mail_dispatch',
                'public.stewardship_family_token', 'ciphertext', 'SELECT')
            OR has_column_privilege('pk_stewardship_mail_dispatch',
                'public.stewardship_rehearsal_credential', 'token_ciphertext', 'SELECT')) THEN
        RAISE EXCEPTION 'Migration 0073: mail dispatch can still read token ciphertext';
    END IF;
END
$check$;

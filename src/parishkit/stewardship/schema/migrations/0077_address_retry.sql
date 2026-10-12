-- Frozen forward migration file 0077 (the repository-wide file sequence):
-- a Family email that the provider accepted for some addresses but refused
-- for others only for now is sent again to just those addresses (#806,
-- slice 2). This file is installed by its Django migration in the jobs app
-- and must never change once released; tests/stewardship/test_schema_migration_files.py
-- pins its digest. Reversing needs its own forward migration.
--
-- One email goes to all of a Family's addresses at once. When the provider
-- accepts it but refuses some addresses temporarily (an SMTP 4xx at RCPT),
-- the email is delivered and never sent again, so until now those addresses
-- never got it. The email is not prepared or rendered again and its
-- credentials are untouched: the follow-up sends the very same rendering,
-- code and link, and only to the refused addresses.
--
-- stewardship_outbox_address_retry: one row per accepted attempt that
-- refused some addresses for now (unique on that attempt's event). Positions
-- index the attempt's rendering's routed recipients. ``positions`` is fixed
-- (the attempt's refused-for-now positions); ``remaining`` (still to send)
-- only shrinks and ``refused`` (refused permanently) only grows, both
-- inside ``positions``, so an address that accepted the email, in the
-- original attempt or a follow-up, is never in a later envelope. It keeps a
-- copy of the message's sealed code-and-link reference, taken before the
-- acceptance scrubbed the message's, and scrubs it when it ends.
--
-- stewardship_address_retry_open_v1 (SECURITY DEFINER, mail dispatch only)
-- creates the row and its family_address_retry task in the transaction
-- that records the acceptance, under the caller's live claim on the
-- message's delivery task, only for a delivered Production invitation or
-- reminder, and only for exactly the refused-for-now positions of that
-- accepted attempt's validated evidence. Mail dispatch gets no task INSERT
-- grant: the task row is inserted here, as the schema owner, which the task
-- login guard exempts.
--
-- stewardship_address_retry_guard_v1 checks every change: only this
-- function (as the schema owner) inserts; mail dispatch updates only under
-- the work-order lock and a live exact claim of the row's own task (or, as
-- for the message, recovery of an abandoned task after the provider
-- deadline, which records "unknown" and never resends); the moves are
-- waiting -> submitting (one more attempt, envelope unchanged, before
-- expiry, only in the scope the message itself would need to be sent, never
-- for a row restored from a backup or superseded by a later accepted email
-- to the Family), submitting -> waiting/finished/failed/unknown with an outcome
-- consistent with the move, and waiting -> failed/cancelled. Nothing is
-- ever deleted, and an ended row never changes.
--
-- stewardship_address_retry_token_v1 (SECURITY DEFINER, mail dispatch only)
-- reads the Family's link token for a waiting follow-up under that live
-- claim, so the follow-up never needs a table grant on token ciphertext.
--
-- stewardship_delivery_address_retries_v1 (SECURITY DEFINER, web only, the
-- same caller checks as 0068's outcomes read) returns, for the attempt
-- 0068 reads, each followed-up position with what the follow-up did:
-- delivered, refused permanently, being retried, not sure it arrived, or
-- given up (refused for now). Positions and words only, never an address.
--
-- The task type is added to the execution map (stewardship_task_type_login_v1,
-- 0004's body plus one name, run by mail dispatch) and to the creator map
-- (stewardship_task_type_creators_v1, 0041's body plus one entry naming no
-- login, since only the definer above creates it).
SET LOCAL check_function_bodies = false;
SET LOCAL search_path = public;

CREATE TABLE "stewardship_outbox_address_retry" ("id" uuid NOT NULL PRIMARY KEY, "created_at" timestamp with time zone DEFAULT (STATEMENT_TIMESTAMP()) NOT NULL, "actor_id" uuid NULL, "correlation_id" uuid NOT NULL, "updated_at" timestamp with time zone DEFAULT (STATEMENT_TIMESTAMP()) NOT NULL, "version" bigint NOT NULL CHECK ("version" >= 0), "message_id" uuid NOT NULL, "source_event_id" uuid NOT NULL UNIQUE, "render_id" uuid NOT NULL, "task_id" uuid NOT NULL UNIQUE, "positions" jsonb NOT NULL, "remaining" jsonb NOT NULL, "refused" jsonb NOT NULL, "outcome" jsonb NULL, "state" varchar(16) DEFAULT 'waiting' NOT NULL, "attempt" integer DEFAULT 0 NOT NULL CHECK ("attempt" >= 0), "spared" integer DEFAULT 0 NOT NULL CHECK ("spared" >= 0), "not_before" timestamp with time zone NOT NULL, "expires_at" timestamp with time zone NOT NULL, "run_id" uuid NULL, "task_fence" bigint NULL CHECK ("task_fence" >= 0), "worker_id" uuid NULL, "submitted_at" timestamp with time zone NULL, "provider_deadline" timestamp with time zone NULL, "finished_at" timestamp with time zone NULL, "reason" varchar(32) DEFAULT '' NOT NULL, "sealed_substitutions" text NULL, "sealed_key_id" varchar(48) NULL, "token_generation_id" uuid NOT NULL, "credential_epoch_id" uuid NOT NULL);
ALTER TABLE "stewardship_outbox_address_retry" ADD CONSTRAINT "stewardship_outbox_a_message_id_2b415eb5_fk_stewardsh" FOREIGN KEY ("message_id") REFERENCES "stewardship_outbox_message" ("id") DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE "stewardship_outbox_address_retry" ADD CONSTRAINT "stewardship_outbox_a_source_event_id_80273f33_fk_stewardsh" FOREIGN KEY ("source_event_id") REFERENCES "stewardship_outbox_event" ("id") DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE "stewardship_outbox_address_retry" ADD CONSTRAINT "stewardship_outbox_a_render_id_3cbabd48_fk_stewardsh" FOREIGN KEY ("render_id") REFERENCES "stewardship_outbox_render" ("id") DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE "stewardship_outbox_address_retry" ADD CONSTRAINT "stewardship_outbox_a_task_id_7b46aab5_fk_stewardsh" FOREIGN KEY ("task_id") REFERENCES "stewardship_task_run" ("id") DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE "stewardship_outbox_address_retry" ADD CONSTRAINT "stewardship_jobs_addressretry_positive_version" CHECK ("version" >= 1);
ALTER TABLE "stewardship_outbox_address_retry" ADD CONSTRAINT "address_retry_state" CHECK (((state)::text = ANY (ARRAY[('waiting'::character varying)::text, ('submitting'::character varying)::text, ('finished'::character varying)::text, ('unknown'::character varying)::text, ('failed'::character varying)::text, ('cancelled'::character varying)::text])));
ALTER TABLE "stewardship_outbox_address_retry" ADD CONSTRAINT "address_retry_reason" CHECK (("reason" = '' OR "reason"::text ~ '^[a-z][a-z0-9_]{0,31}$'));
ALTER TABLE "stewardship_outbox_address_retry" ADD CONSTRAINT "address_retry_scrub" CHECK ((("finished_at" IS NOT NULL AND "sealed_key_id" IS NULL AND "sealed_substitutions" IS NULL AND (state)::text = ANY (ARRAY[('finished'::character varying)::text, ('unknown'::character varying)::text, ('failed'::character varying)::text, ('cancelled'::character varying)::text])) OR (NOT ((state)::text = ANY (ARRAY[('finished'::character varying)::text, ('unknown'::character varying)::text, ('failed'::character varying)::text, ('cancelled'::character varying)::text])) AND "finished_at" IS NULL AND "sealed_key_id" IS NOT NULL AND "sealed_substitutions" IS NOT NULL)));
ALTER TABLE "stewardship_outbox_address_retry" ADD CONSTRAINT "address_retry_attempt_shape" CHECK ((("attempt" = 0 AND "provider_deadline" IS NULL AND "run_id" IS NULL AND "spared" = 0 AND "submitted_at" IS NULL AND "task_fence" IS NULL AND "worker_id" IS NULL) OR ("attempt" >= 1 AND "provider_deadline" >= ("submitted_at") AND "provider_deadline" IS NOT NULL AND "run_id" IS NOT NULL AND "spared" <= ("attempt") AND "submitted_at" IS NOT NULL AND "task_fence" >= 1 AND "task_fence" IS NOT NULL AND "worker_id" IS NOT NULL)));
CREATE INDEX "stewardship_outbox_address_retry_correlation_id_c7862334" ON "stewardship_outbox_address_retry" ("correlation_id");
CREATE INDEX "stewardship_outbox_address_retry_message_id_2b415eb5" ON "stewardship_outbox_address_retry" ("message_id");
CREATE INDEX "stewardship_outbox_address_retry_render_id_3cbabd48" ON "stewardship_outbox_address_retry" ("render_id");

-- A stored position list as a sorted integer array: distinct envelope
-- positions in increasing order, each below ``bound`` (the rendering's
-- routed recipient count). NULL when the value is anything else.
CREATE FUNCTION public.stewardship_address_positions_v1(value jsonb, bound integer) RETURNS integer[]
LANGUAGE plpgsql IMMUTABLE SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE result integer[]:=ARRAY[]::integer[]; item jsonb; previous integer:=-1; current integer;
BEGIN
    IF value IS NULL OR bound IS NULL OR jsonb_typeof(value)<>'array' THEN RETURN NULL; END IF;
    FOR item IN SELECT element FROM jsonb_array_elements(value) WITH ORDINALITY AS list(element,n) ORDER BY n LOOP
        IF jsonb_typeof(item)<>'number' OR item::text!~'^[0-9]{1,3}$' THEN RETURN NULL; END IF;
        current:=item::text::integer;
        IF current<=previous OR current>=bound THEN RETURN NULL; END IF;
        result:=result||current;
        previous:=current;
    END LOOP;
    RETURN result;
END $$;

CREATE FUNCTION public.stewardship_address_retry_guard_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE
    owner boolean:=pg_has_role(current_user,
        (SELECT nspowner FROM pg_namespace WHERE nspname='public'),'USAGE');
    instant timestamptz:=clock_timestamp();
    bound integer;
    positions integer[]; remaining integer[]; refused integer[];
    old_remaining integer[]; old_refused integer[];
    permanent integer[]; transient integer[]; status text;
    open_states text[]:=ARRAY['waiting','submitting'];
    mutable text[]:=ARRAY['version','updated_at','actor_id','correlation_id','remaining',
        'refused','outcome','state','attempt','spared','not_before','run_id','task_fence',
        'worker_id','submitted_at','provider_deadline','finished_at','reason',
        'sealed_substitutions','sealed_key_id'];
    recovery boolean:=false;
    t public.stewardship_task_run%ROWTYPE;
BEGIN
    IF TG_OP='DELETE' THEN
        RAISE EXCEPTION 'An address follow-up is never deleted' USING ERRCODE='23514';
    END IF;
    SELECT jsonb_array_length(r.routed_recipients) INTO bound
      FROM public.stewardship_outbox_render r WHERE r.id=NEW.render_id;
    positions:=public.stewardship_address_positions_v1(NEW.positions,bound);
    remaining:=public.stewardship_address_positions_v1(NEW.remaining,bound);
    refused:=public.stewardship_address_positions_v1(NEW.refused,bound);
    IF positions IS NULL OR remaining IS NULL OR refused IS NULL OR cardinality(positions)=0
       OR NOT remaining<@positions OR NOT refused<@positions OR remaining&&refused THEN
        RAISE EXCEPTION 'Address follow-up positions are invalid' USING ERRCODE='23514';
    END IF;
    IF TG_OP='INSERT' THEN
        -- Only stewardship_address_retry_open_v1, which runs as the schema
        -- owner, opens a follow-up; it has checked the claim and evidence.
        IF NOT owner THEN
            RAISE EXCEPTION 'An address follow-up is opened only by its definer' USING ERRCODE='42501';
        END IF;
        IF NEW.remaining<>NEW.positions OR NEW.refused<>'[]'::jsonb OR NEW.outcome IS NOT NULL
           OR NEW.state<>'waiting' OR NEW.attempt<>0 OR NEW.spared<>0 OR NEW.version<>1
           OR NEW.reason<>'' THEN
            RAISE EXCEPTION 'Invalid initial address follow-up' USING ERRCODE='23514';
        END IF;
        NEW.created_at:=statement_timestamp();
        NEW.updated_at:=NEW.created_at;
        RETURN NEW;
    END IF;
    IF (to_jsonb(NEW)-mutable) IS DISTINCT FROM (to_jsonb(OLD)-mutable) THEN
        RAISE EXCEPTION 'Address follow-up identity is immutable' USING ERRCODE='23514';
    END IF;
    IF NEW.version IS DISTINCT FROM OLD.version+1 THEN
        RAISE EXCEPTION 'Every update must advance the record version' USING ERRCODE='23514';
    END IF;
    IF OLD.state<>ALL(open_states) THEN
        RAISE EXCEPTION 'An ended address follow-up never changes' USING ERRCODE='23514';
    END IF;
    -- Positions only shrink: nothing returns to the envelope, and a newly
    -- refused position comes from the envelope just sent.
    old_remaining:=public.stewardship_address_positions_v1(OLD.remaining,bound);
    old_refused:=public.stewardship_address_positions_v1(OLD.refused,bound);
    IF NOT remaining<@old_remaining OR NOT old_refused<@refused
       OR NOT ARRAY(SELECT x FROM unnest(refused) x WHERE x<>ALL(old_refused))<@old_remaining THEN
        RAISE EXCEPTION 'Address follow-up positions only shrink' USING ERRCODE='23514';
    END IF;
    IF NEW.state=ANY(open_states) AND (NEW.sealed_substitutions IS DISTINCT FROM OLD.sealed_substitutions
       OR NEW.sealed_key_id IS DISTINCT FROM OLD.sealed_key_id) THEN
        RAISE EXCEPTION 'An open address follow-up keeps its sealed reference' USING ERRCODE='23514';
    END IF;
    NEW.updated_at:=statement_timestamp();
    IF NEW.state<>ALL(open_states) THEN NEW.finished_at:=statement_timestamp(); END IF;
    IF OLD.state='submitting' THEN
        -- The attempt itself is unchanged; at most one spared outcome.
        IF NEW.attempt<>OLD.attempt OR NEW.run_id IS DISTINCT FROM OLD.run_id
           OR NEW.task_fence IS DISTINCT FROM OLD.task_fence OR NEW.worker_id IS DISTINCT FROM OLD.worker_id
           OR NEW.submitted_at IS DISTINCT FROM OLD.submitted_at
           OR NEW.provider_deadline IS DISTINCT FROM OLD.provider_deadline
           OR NEW.spared NOT IN (OLD.spared,OLD.spared+1) THEN
            RAISE EXCEPTION 'Address follow-up attempt differs' USING ERRCODE='23514';
        END IF;
        recovery:=NEW.state='unknown' AND NEW.reason='recovery_unknown'
            AND NEW.remaining=OLD.remaining AND NEW.refused=OLD.refused
            AND NEW.outcome IS NOT DISTINCT FROM OLD.outcome AND NEW.spared=OLD.spared;
        IF NOT recovery THEN
            -- An outcome: its status and the positions of the envelope just
            -- sent (OLD.remaining) that it refused for good and for now.
            status:=NEW.outcome->>'status';
            permanent:=public.stewardship_address_positions_v1(NEW.outcome->'permanent',bound);
            transient:=public.stewardship_address_positions_v1(NEW.outcome->'transient',bound);
            IF jsonb_typeof(NEW.outcome) IS DISTINCT FROM 'object'
               OR (SELECT array_agg(k ORDER BY k) FROM jsonb_object_keys(NEW.outcome) k)
                  IS DISTINCT FROM ARRAY['permanent','status','transient']
               OR status IS NULL OR status NOT IN ('accepted','transient','unavailable','permanent',
                   'delivery_unknown','systemic')
               OR permanent IS NULL OR transient IS NULL OR NOT permanent<@old_remaining
               OR NOT transient<@old_remaining OR permanent&&transient
               OR refused<>ARRAY(SELECT DISTINCT x FROM unnest(old_refused||permanent) x ORDER BY x)
               OR (NEW.spared=OLD.spared+1 AND status NOT IN ('transient','unavailable')) THEN
                RAISE EXCEPTION 'Address follow-up outcome is invalid' USING ERRCODE='23514';
            END IF;
            IF (status='accepted' AND (cardinality(permanent)+cardinality(transient)>=cardinality(old_remaining)
                    OR remaining<>transient))
               OR (status<>'accepted'
                    AND remaining<>ARRAY(SELECT x FROM unnest(old_remaining) x WHERE x<>ALL(permanent) ORDER BY x))
               OR ((status='delivery_unknown')<>(NEW.state='unknown'))
               OR (status<>'delivery_unknown' AND (cardinality(remaining)=0)<>(NEW.state='finished'))
               OR (NEW.state='waiting' AND NEW.not_before<=instant)
               OR NEW.state='submitting' THEN
                RAISE EXCEPTION 'Address follow-up outcome does not match its move' USING ERRCODE='23514';
            END IF;
        END IF;
    ELSIF NEW.state='submitting' THEN
        -- One more attempt to the unchanged envelope, before expiry.
        NEW.submitted_at:=instant;
        IF NEW.attempt<>OLD.attempt+1 OR NEW.spared<>OLD.spared
           OR NEW.remaining<>OLD.remaining OR NEW.refused<>OLD.refused
           OR NEW.outcome IS DISTINCT FROM OLD.outcome OR OLD.not_before>instant
           OR instant>=OLD.expires_at OR NEW.provider_deadline IS NULL
           OR NEW.provider_deadline<=instant OR NEW.provider_deadline>instant+interval '5 minutes' THEN
            RAISE EXCEPTION 'Address follow-up submission is not admitted' USING ERRCODE='23514';
        END IF;
        -- The scope the message itself needs to be sent (family_dispatch.sql's
        -- live check): the current Production campaign, open and not paused,
        -- no restore review, go-live or purge gate, current source, a Family
        -- still eligible and deliverable with no other email whose provider
        -- outcome is unresolved, the same production cycle. And
        -- never a follow-up restored from a backup (it may have been sent
        -- after that backup), nor one a later accepted email for the Family
        -- has superseded.
        IF NOT EXISTS (
            SELECT 1 FROM public.stewardship_outbox_message m
            JOIN public.stewardship_outbox_event source_event ON source_event.id=OLD.source_event_id
            JOIN public.stewardship_schedule_occurrence o ON o.id=m.semantic_key AND o.outbox_id=m.id
            JOIN public.stewardship_campaign c ON c.id=m.campaign_id
            JOIN public.stewardship_system_configuration r ON r.current_campaign_id=c.id
            JOIN public.stewardship_campaign_configuration p ON p.id=c.active_configuration_id
            JOIN public.stewardship_campaign_credentials k ON k.campaign_id=c.id
            JOIN public.stewardship_family_campaign f ON f.id=m.family_id AND f.campaign_id=c.id
            JOIN public.stewardship_source_current source ON source.singleton
            WHERE m.id=OLD.message_id AND m.mode='production' AND r.mode='production'
              AND NOT r.restore_review_required
              AND (r.restore_activated_at IS NULL OR OLD.created_at>r.restore_activated_at)
              AND c.state IN ('scheduled','active') AND NOT c.delivery_paused
              AND public.stewardship_campaign_now_v1()>=p.starts_at
              AND public.stewardship_campaign_now_v1()<p.ends_at
              AND o.production_cycle=c.production_cycle
              AND NOT k.go_live_gate AND NOT k.population_dirty
              AND k.source_snapshot_id=source.snapshot_id AND k.source_generation=source.generation
              AND f.source_generation=source.generation AND f.active AND f.email_eligible
              AND f.email_deliverable
              AND NOT EXISTS (SELECT 1 FROM public.stewardship_campaign_work_gate gate
                  WHERE gate.campaign_id=c.id AND gate.state<>'released')
              AND NOT EXISTS (SELECT 1 FROM public.stewardship_outbox_message unresolved
                  WHERE unresolved.family_id=m.family_id AND unresolved.campaign_id=c.id
                    AND unresolved.mode=m.mode AND unresolved.id<>m.id
                    AND unresolved.state IN ('submitting','delivery_unknown'))
              AND NOT EXISTS (SELECT 1 FROM public.stewardship_outbox_event later
                  JOIN public.stewardship_outbox_message other ON other.id=later.message_id
                  WHERE other.family_id=m.family_id AND other.campaign_id=c.id
                    AND other.mode=m.mode AND other.id<>m.id
                    AND other.purpose IN ('initial','reminder')
                    AND later.previous_state='submitting' AND later.state='delivered'
                    AND later.created_at>source_event.created_at)) THEN
            RAISE EXCEPTION 'Address follow-up scope does not admit a send' USING ERRCODE='23514';
        END IF;
    ELSE
        -- Waiting: deferred, or ended without a send.
        IF NEW.attempt<>OLD.attempt OR NEW.spared<>OLD.spared
           OR NEW.remaining<>OLD.remaining OR NEW.refused<>OLD.refused
           OR NEW.outcome IS DISTINCT FROM OLD.outcome
           OR NEW.run_id IS DISTINCT FROM OLD.run_id OR NEW.task_fence IS DISTINCT FROM OLD.task_fence
           OR NEW.worker_id IS DISTINCT FROM OLD.worker_id
           OR NEW.submitted_at IS DISTINCT FROM OLD.submitted_at
           OR NEW.provider_deadline IS DISTINCT FROM OLD.provider_deadline
           OR NEW.state NOT IN ('waiting','failed','cancelled')
           OR (NEW.state<>'waiting' AND NEW.reason='') THEN
            RAISE EXCEPTION 'Address follow-up change is not admitted' USING ERRCODE='23514';
        END IF;
    END IF;
    IF owner THEN RETURN NEW; END IF;
    IF current_user<>'pk_stewardship_mail_dispatch' THEN
        RAISE EXCEPTION 'Only mail dispatch changes an address follow-up' USING ERRCODE='42501';
    END IF;
    IF NOT EXISTS(SELECT 1 FROM pg_locks WHERE pid=pg_backend_pid()
        AND locktype='advisory' AND classid=736220 AND objid=1 AND objsubid=2
        AND mode='ExclusiveLock' AND granted) THEN
        RAISE EXCEPTION 'Address follow-up requires work ownership' USING ERRCODE='23514';
    END IF;
    SELECT * INTO t FROM public.stewardship_task_run WHERE id=NEW.correlation_id;
    IF t.id IS NULL OR t.task_type<>'family_address_retry' OR t.root_id<>OLD.task_id
       OR t.domain_request_id IS DISTINCT FROM OLD.id OR NEW.actor_id IS NULL THEN
        RAISE EXCEPTION 'Address follow-up requires its own task' USING ERRCODE='23514';
    END IF;
    IF recovery THEN
        -- An abandoned attempt past its provider deadline: uncertain, never resent.
        IF t.state<>'abandoned' OR t.id<>OLD.run_id OR t.fence<OLD.task_fence
           OR OLD.provider_deadline>instant THEN
            RAISE EXCEPTION 'Address follow-up recovery is not admitted' USING ERRCODE='23514';
        END IF;
        RETURN NEW;
    END IF;
    IF t.state<>'running' OR t.worker_id IS DISTINCT FROM NEW.actor_id
       OR t.lease_expires_at<=instant THEN
        RAISE EXCEPTION 'Address follow-up requires a live exact claim' USING ERRCODE='23514';
    END IF;
    IF (OLD.state='submitting' AND (OLD.run_id<>t.id OR OLD.task_fence<>t.fence
            OR OLD.worker_id IS DISTINCT FROM t.worker_id))
       OR (NEW.state='submitting' AND (NEW.run_id IS DISTINCT FROM t.id
            OR NEW.task_fence IS DISTINCT FROM t.fence OR NEW.worker_id IS DISTINCT FROM t.worker_id)) THEN
        RAISE EXCEPTION 'Address follow-up claim differs' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER stewardship_address_retry_guard BEFORE INSERT OR UPDATE OR DELETE
ON public.stewardship_outbox_address_retry
FOR EACH ROW EXECUTE FUNCTION public.stewardship_address_retry_guard_v1();

CREATE FUNCTION public.stewardship_address_retry_open_v1(source_event uuid, claim_run uuid,
    claim_fence bigint, claim_worker uuid, sealed text, sealed_key text, wait_seconds integer)
RETURNS uuid
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE
    t public.stewardship_task_run%ROWTYPE;
    m public.stewardship_outbox_message%ROWTYPE;
    e public.stewardship_outbox_event%ROWTYPE;
    result jsonb;
    retry_uuid uuid:=gen_random_uuid();
    task_uuid uuid:=gen_random_uuid();
    due timestamptz:=clock_timestamp()+make_interval(secs=>wait_seconds);
BEGIN
    IF session_user<>'pk_stewardship_mail_dispatch' AND NOT pg_has_role(session_user,
        (SELECT nspowner FROM pg_namespace WHERE nspname='public'),'USAGE') THEN
        RAISE EXCEPTION 'Only mail dispatch opens an address follow-up' USING ERRCODE='42501';
    END IF;
    IF NOT EXISTS(SELECT 1 FROM pg_locks WHERE pid=pg_backend_pid()
        AND locktype='advisory' AND classid=736220 AND objid=1 AND objsubid=2
        AND mode='ExclusiveLock' AND granted) THEN
        RAISE EXCEPTION 'Address follow-up requires work ownership' USING ERRCODE='23514';
    END IF;
    -- The caller's live claim on the message's own delivery task.
    SELECT * INTO t FROM public.stewardship_task_run WHERE id=claim_run FOR UPDATE;
    SELECT * INTO m FROM public.stewardship_outbox_message
     WHERE id=t.domain_request_id AND task_id=t.root_id;
    SELECT * INTO e FROM public.stewardship_outbox_event WHERE id=source_event;
    IF t.id IS NULL OR t.task_type<>'outbox_delivery' OR t.state<>'running'
       OR t.fence IS DISTINCT FROM claim_fence OR t.worker_id IS DISTINCT FROM claim_worker
       OR t.lease_expires_at<=clock_timestamp() OR m.id IS NULL THEN
        RAISE EXCEPTION 'Address follow-up requires a live delivery claim' USING ERRCODE='23514';
    END IF;
    -- A delivered Production invitation or reminder, accepted by exactly
    -- this attempt of this claim, with its credential generation.
    IF m.mode<>'production' OR m.routing<>'production' OR m.purpose NOT IN ('initial','reminder')
       OR m.credential_namespace<>'production' OR m.family_id IS NULL
       OR m.state<>'delivered' OR m.run_id IS DISTINCT FROM t.id
       OR m.task_fence IS DISTINCT FROM t.fence OR m.worker_id IS DISTINCT FROM t.worker_id
       OR m.token_generation_id IS NULL OR m.credential_epoch_id IS NULL
       OR e.id IS NULL OR e.message_id<>m.id OR e.previous_state<>'submitting'
       OR e.state<>'delivered' OR e.render_id<>m.render_id OR e.attempt<>m.attempt
       OR e.run_id IS DISTINCT FROM t.id OR e.task_fence IS DISTINCT FROM t.fence THEN
        RAISE EXCEPTION 'Address follow-up requires its accepted attempt' USING ERRCODE='23514';
    END IF;
    result:=public.stewardship_family_smtp_result_v1(e.id);
    IF result IS NULL OR result->>'status'<>'accepted'
       OR jsonb_typeof(result->'transient')<>'array'
       OR jsonb_array_length(result->'transient')=0 THEN
        RAISE EXCEPTION 'Address follow-up requires addresses refused for now' USING ERRCODE='23514';
    END IF;
    IF sealed IS NULL OR length(sealed) NOT BETWEEN 1 AND 32768
       OR sealed_key IS NULL OR length(sealed_key) NOT BETWEEN 1 AND 48
       OR wait_seconds IS NULL OR wait_seconds NOT BETWEEN 1 AND 86400 THEN
        RAISE EXCEPTION 'Address follow-up values are invalid' USING ERRCODE='23514';
    END IF;
    INSERT INTO public.stewardship_task_run(id,root_id,version,retry_sequence,task_type,
        idempotency_key,domain_request_id,initiated_by_id,actor_id,correlation_id,not_before)
    VALUES(task_uuid,task_uuid,1,0,'family_address_retry',retry_uuid::text,retry_uuid,
        claim_worker,claim_worker,claim_run,due);
    INSERT INTO public.stewardship_outbox_address_retry(id,actor_id,correlation_id,version,
        message_id,source_event_id,render_id,task_id,positions,remaining,refused,state,
        attempt,spared,not_before,expires_at,reason,sealed_substitutions,sealed_key_id,
        token_generation_id,credential_epoch_id)
    VALUES(retry_uuid,claim_worker,claim_run,1,m.id,e.id,e.render_id,task_uuid,
        result->'transient',result->'transient','[]'::jsonb,'waiting',0,0,due,
        e.created_at+interval '7 days','',sealed,sealed_key,
        m.token_generation_id,m.credential_epoch_id);
    RETURN retry_uuid;
END $$;
ALTER FUNCTION public.stewardship_address_retry_open_v1(uuid, uuid, bigint, uuid, text, text, integer) SECURITY DEFINER;
REVOKE ALL ON FUNCTION public.stewardship_address_retry_open_v1(uuid, uuid, bigint, uuid, text, text, integer) FROM PUBLIC;

CREATE FUNCTION public.stewardship_address_retry_token_v1(retry_uuid uuid, token_uuid uuid,
    claim_run uuid, claim_fence bigint, claim_worker uuid)
RETURNS TABLE(ciphertext text, digest text)
LANGUAGE sql VOLATILE SECURITY DEFINER SET search_path TO pg_catalog,public,pg_temp AS $$
    -- A waiting follow-up under its own task's live claim, and the token of
    -- its Family and campaign in the generation it was opened with.
    SELECT token.ciphertext::text, token.digest::text
      FROM public.stewardship_outbox_address_retry a
      JOIN public.stewardship_task_run r ON r.id=claim_run
      JOIN public.stewardship_outbox_message m ON m.id=a.message_id
      JOIN public.stewardship_family_token token ON token.id=token_uuid
     WHERE a.id=retry_uuid AND a.state='waiting'
       AND r.root_id=a.task_id AND r.domain_request_id=a.id
       AND r.task_type='family_address_retry' AND r.state='running'
       AND r.fence=claim_fence AND r.worker_id=claim_worker
       AND r.lease_expires_at>clock_timestamp()
       AND m.mode='production' AND token.family_id=m.family_id
       AND token.campaign_id=m.campaign_id AND token.generation_id=a.token_generation_id
       AND token.destroyed_at IS NULL
$$;
ALTER FUNCTION public.stewardship_address_retry_token_v1(uuid, uuid, uuid, bigint, uuid) SECURITY DEFINER;
REVOKE ALL ON FUNCTION public.stewardship_address_retry_token_v1(uuid, uuid, uuid, bigint, uuid) FROM PUBLIC;

CREATE FUNCTION public.stewardship_delivery_address_retries_v1(message_uuid uuid, actor_uuid uuid)
RETURNS TABLE(envelope_position integer, outcome text)
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE newest public.stewardship_outbox_render%ROWTYPE; latest uuid;
    follow public.stewardship_outbox_address_retry%ROWTYPE;
BEGIN
    IF session_user<>'pk_stewardship_web' AND NOT pg_has_role(session_user,
        (SELECT nspowner FROM pg_namespace WHERE nspname='public'),'USAGE') THEN
        RAISE EXCEPTION 'Email address follow-ups are read only by the web login' USING ERRCODE='42501';
    END IF;
    IF actor_uuid IS NULL OR public.stewardship_export_authorized_v1(actor_uuid,true) IS NOT TRUE THEN
        RAISE EXCEPTION 'Email address follow-ups require an Administrator' USING ERRCODE='42501';
    END IF;
    -- The attempt 0068's outcomes read reads: the newest rendering's
    -- latest finished attempt.
    SELECT * INTO newest FROM public.stewardship_outbox_render r
     WHERE r.message_id=message_uuid ORDER BY r.created_at DESC, r.id DESC LIMIT 1;
    IF newest.id IS NULL THEN RETURN; END IF;
    SELECT e.id INTO latest FROM public.stewardship_outbox_event e
     WHERE e.render_id=newest.id AND e.message_id=message_uuid
       AND e.previous_state='submitting'
     ORDER BY e.version DESC LIMIT 1;
    IF latest IS NULL THEN RETURN; END IF;
    SELECT * INTO follow FROM public.stewardship_outbox_address_retry a
     WHERE a.source_event_id=latest AND a.message_id=message_uuid AND a.render_id=newest.id;
    IF follow.id IS NULL THEN RETURN; END IF;
    RETURN QUERY
        SELECT item.value::integer,
               CASE WHEN follow.refused @> item.value::jsonb THEN 'refused_permanently'
                    WHEN NOT follow.remaining @> item.value::jsonb THEN 'delivered'
                    WHEN follow.state IN ('waiting','submitting') THEN 'retrying'
                    WHEN follow.state='unknown' THEN 'retry_unknown'
                    ELSE 'refused_for_now' END
          FROM jsonb_array_elements_text(follow.positions) AS item(value)
         ORDER BY 1;
END $$;
ALTER FUNCTION public.stewardship_delivery_address_retries_v1(uuid, uuid) SECURITY DEFINER;
REVOKE ALL ON FUNCTION public.stewardship_delivery_address_retries_v1(uuid, uuid) FROM PUBLIC;

-- 0004's execution map with family_address_retry run by mail dispatch.
CREATE OR REPLACE FUNCTION public.stewardship_task_type_login_v1(task_type text) RETURNS text
    LANGUAGE sql IMMUTABLE STRICT
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$ SELECT CASE
        -- The login whose compiled registry executes each task type
        -- (runtime_background.py): general-queue types run in the worker,
        -- mail-queue types in mail dispatch. NULL is an unknown type.
        WHEN task_type IN (
            'activation_catchup',
            'automation_maintenance',
            'branding_cleanup',
            'campaign_boundary',
            'daily_digest_finalize',
            'daily_digest_prepare',
            'family_mail_prepare',
            'family_mail_test',
            'operational_collect',
            'operational_prepare',
            'operational_slack',
            'production_cleanup',
            'production_token_cleanup',
            'production_tokens',
            'report_exact_export',
            'report_export',
            'report_export_cleanup',
            'report_fact_verification',
            'report_facts',
            'security_prepare',
            'setup_finalize',
            'setup_source_cleanup',
            'setup_source_load',
            'source_refresh',
            'weekly_digest_finalize',
            'weekly_digest_prepare')
        THEN 'pk_stewardship_worker'
        WHEN task_type IN (
            'campaign_mail_test',
            'family_address_retry',
            'outbox_delivery',
            'setup_mail_test')
        THEN 'pk_stewardship_mail_dispatch'
    END $$;

-- 0041's creator map with family_address_retry created by no login: only
-- stewardship_address_retry_open_v1, as the schema owner, inserts it.
CREATE OR REPLACE FUNCTION public.stewardship_task_type_creators_v1(task_type text) RETURNS text[]
    LANGUAGE sql IMMUTABLE STRICT
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$ SELECT CASE task_type
        -- The logins whose code creates each type, by enqueue or explicit
        -- retry (tests/stewardship/test_task_creators.py pins every call
        -- site). NULL is an unknown type.
        WHEN 'activation_catchup' THEN ARRAY['pk_stewardship_web']
        WHEN 'automation_maintenance' THEN ARRAY['pk_stewardship_scheduler']
        WHEN 'branding_cleanup' THEN ARRAY['pk_stewardship_scheduler']
        WHEN 'campaign_boundary' THEN ARRAY['pk_stewardship_scheduler']
        WHEN 'campaign_mail_test' THEN ARRAY['pk_stewardship_web']
        WHEN 'daily_digest_finalize' THEN ARRAY['pk_stewardship_scheduler','pk_stewardship_web']
        WHEN 'daily_digest_prepare' THEN ARRAY['pk_stewardship_scheduler','pk_stewardship_web']
        WHEN 'family_address_retry' THEN ARRAY[]::text[]
        WHEN 'family_mail_prepare' THEN ARRAY['pk_stewardship_scheduler','pk_stewardship_web']
        WHEN 'family_mail_test' THEN ARRAY['pk_stewardship_web']
        WHEN 'operational_collect' THEN ARRAY['pk_stewardship_scheduler']
        WHEN 'operational_prepare' THEN ARRAY['pk_stewardship_scheduler']
        WHEN 'operational_slack' THEN ARRAY['pk_stewardship_scheduler']
        WHEN 'outbox_delivery' THEN ARRAY['pk_stewardship_web','pk_stewardship_worker']
        WHEN 'production_cleanup' THEN ARRAY['pk_stewardship_web']
        WHEN 'production_token_cleanup' THEN ARRAY['pk_stewardship_scheduler','pk_stewardship_web']
        WHEN 'production_tokens' THEN ARRAY['pk_stewardship_scheduler','pk_stewardship_web']
        WHEN 'report_exact_export' THEN ARRAY['pk_stewardship_web']
        WHEN 'report_export' THEN ARRAY['pk_stewardship_web','pk_stewardship_worker']
        WHEN 'report_export_cleanup' THEN ARRAY['pk_stewardship_scheduler','pk_stewardship_web']
        WHEN 'report_fact_verification' THEN ARRAY['pk_stewardship_scheduler']
        WHEN 'report_facts' THEN ARRAY['pk_stewardship_scheduler']
        WHEN 'security_prepare' THEN ARRAY['pk_stewardship_scheduler']
        WHEN 'setup_finalize' THEN ARRAY['pk_stewardship_scheduler']
        WHEN 'setup_mail_test' THEN ARRAY['pk_stewardship_web']
        WHEN 'setup_source_cleanup' THEN ARRAY['pk_stewardship_scheduler']
        WHEN 'setup_source_load' THEN ARRAY['pk_stewardship_web']
        WHEN 'source_refresh' THEN ARRAY['pk_stewardship_scheduler','pk_stewardship_web','pk_stewardship_worker']
        WHEN 'weekly_digest_finalize' THEN ARRAY['pk_stewardship_scheduler','pk_stewardship_web']
        WHEN 'weekly_digest_prepare' THEN ARRAY['pk_stewardship_scheduler','pk_stewardship_web']
    END $$;

-- Refuse to commit unless everything above is installed as declared. It
-- reads only the catalog and the two IMMUTABLE maps, and writes nothing.
-- Missing functions are checked first, on their own, because PostgreSQL does
-- not short-circuit OR and a ::regprocedure cast would fail with its own
-- error instead of this file's.
DO $check$
BEGIN
    IF to_regclass('public.stewardship_outbox_address_retry') IS NULL
       OR to_regprocedure('public.stewardship_address_positions_v1(jsonb,integer)') IS NULL
       OR to_regprocedure('public.stewardship_address_retry_guard_v1()') IS NULL
       OR to_regprocedure('public.stewardship_address_retry_open_v1(uuid,uuid,bigint,uuid,text,text,integer)') IS NULL
       OR to_regprocedure('public.stewardship_address_retry_token_v1(uuid,uuid,uuid,bigint,uuid)') IS NULL
       OR to_regprocedure('public.stewardship_delivery_address_retries_v1(uuid,uuid)') IS NULL THEN
        RAISE EXCEPTION 'Migration 0077 (address follow-ups) is not installed as declared';
    END IF;
    -- The three definers: SECURITY DEFINER, the fixed search_path, no
    -- EXECUTE for PUBLIC, and the checks that bind them.
    IF (SELECT count(*) FROM pg_proc p
        WHERE p.prosecdef AND p.proconfig=ARRAY['search_path=pg_catalog, public, pg_temp']
          AND NOT has_function_privilege('public',p.oid,'EXECUTE')
          AND ((p.oid='public.stewardship_address_retry_open_v1(uuid,uuid,bigint,uuid,text,text,integer)'::regprocedure
                AND p.prosrc LIKE '%public.stewardship_family_smtp_result_v1(e.id)%'
                AND p.prosrc LIKE '%t.task_type<>''outbox_delivery''%'
                AND p.prosrc LIKE '%''family_address_retry''%')
            OR (p.oid='public.stewardship_address_retry_token_v1(uuid,uuid,uuid,bigint,uuid)'::regprocedure
                AND p.prosrc LIKE '%r.task_type=''family_address_retry''%'
                AND p.prosrc LIKE '%a.state=''waiting''%')
            OR (p.oid='public.stewardship_delivery_address_retries_v1(uuid,uuid)'::regprocedure
                AND p.provolatile='s'
                AND p.prosrc LIKE '%stewardship_export_authorized_v1(actor_uuid,true)%')))<>3 THEN
        RAISE EXCEPTION 'Migration 0077 (address follow-ups) is not installed as declared';
    END IF;
    -- The guard and the position check are invoker's rights, the guard
    -- holds a send to the message's own scope and refuses a restored row,
    -- and it is attached before INSERT, UPDATE and DELETE.
    IF EXISTS (SELECT 1 FROM pg_proc p
               WHERE p.oid IN ('public.stewardship_address_positions_v1(jsonb,integer)'::regprocedure,
                               'public.stewardship_address_retry_guard_v1()'::regprocedure)
                 AND (p.prosecdef OR p.proconfig IS DISTINCT FROM ARRAY['search_path=pg_catalog, public, pg_temp']))
       OR NOT EXISTS (SELECT 1 FROM pg_proc p
               WHERE p.oid='public.stewardship_address_retry_guard_v1()'::regprocedure
                 AND p.prosrc LIKE '%OLD.created_at>r.restore_activated_at%'
                 AND p.prosrc LIKE '%NOT r.restore_review_required%'
                 AND p.prosrc LIKE '%f.email_deliverable%'
                 AND p.prosrc LIKE '%Address follow-up scope does not admit a send%')
       OR NOT EXISTS (SELECT 1 FROM pg_trigger t
               WHERE t.tgrelid='public.stewardship_outbox_address_retry'::regclass
                 AND t.tgname='stewardship_address_retry_guard' AND NOT t.tgisinternal
                 AND t.tgenabled='O' AND t.tgtype=31
                 AND t.tgfoid='public.stewardship_address_retry_guard_v1()'::regprocedure) THEN
        RAISE EXCEPTION 'Migration 0077 (address follow-ups) is not installed as declared';
    END IF;
    -- The two maps: the new type runs in mail dispatch and no login
    -- creates it, and a sample of the others is unchanged.
    IF public.stewardship_task_type_login_v1('family_address_retry') IS DISTINCT FROM 'pk_stewardship_mail_dispatch'
       OR public.stewardship_task_type_login_v1('outbox_delivery') IS DISTINCT FROM 'pk_stewardship_mail_dispatch'
       OR public.stewardship_task_type_login_v1('report_export') IS DISTINCT FROM 'pk_stewardship_worker'
       OR public.stewardship_task_type_login_v1('unknown_type') IS NOT NULL
       OR public.stewardship_task_type_creators_v1('family_address_retry') IS DISTINCT FROM ARRAY[]::text[]
       OR public.stewardship_task_type_creators_v1('source_refresh') IS DISTINCT FROM
          ARRAY['pk_stewardship_scheduler','pk_stewardship_web','pk_stewardship_worker']
       OR public.stewardship_task_type_creators_v1('unknown_type') IS NOT NULL
       OR (SELECT count(*) FROM pg_proc p
           WHERE p.oid IN ('public.stewardship_task_type_login_v1(text)'::regprocedure,
                           'public.stewardship_task_type_creators_v1(text)'::regprocedure)
             AND NOT p.prosecdef AND p.provolatile='i' AND p.proisstrict
             AND p.proconfig=ARRAY['search_path=pg_catalog, public, pg_temp'])<>2 THEN
        RAISE EXCEPTION 'Migration 0077 (address follow-ups) is not installed as declared';
    END IF;
END
$check$;

-- Frozen forward migration file 0048 (the repository-wide file sequence):
-- settle held emails after a restore review is released (#757). This file is
-- installed by its Django migration in the campaigns app and must never
-- change once released; tests/stewardship/test_schema_migration_files.py
-- pins its digest and checks that its copy of each replaced function still
-- equals the fresh-install baseline's (functions.sql and
-- family_dispatch.sql). A fresh install runs the baseline, the earlier
-- forward files and then this, and ends in the same catalog as an upgraded
-- database.
--
-- Before this file the held-email decision guard refused every decision once
-- a restore review was released, so a hold nobody decided during the review
-- kept its email back for good, and an undecided invitation kept back every
-- reminder of its Family for the rest of the campaign. This file replaces two
-- functions, each copied whole from its latest definition with only these
-- changes:
--
--   * stewardship_restore_resolution_v1 (latest in frozen file 0017): the
--     "not after release" refusal is dropped, and a hold whose campaign is no
--     longer the current one is refused instead, because the planner never
--     acts on another campaign's holds. "Send again" on a held reminder is
--     also refused once a later reminder for the same Family (removed since
--     or not) was delivered, is on its way or its delivery is unknown: it
--     would arrive out of order. The rule lives here, so the web login's
--     direct insert of a decision cannot skip it.
--   * stewardship_family_dispatch_live_v1 (latest in the baseline,
--     family_dispatch.sql): a reminder sent again after a restore is not live
--     once a later reminder for the same Family was delivered or is on its
--     way, which covers a later reminder that was in flight when "send
--     again" was decided.
--
-- Every other rule stays: only an undecided hold is decided (each decision
-- is final), only "assumed sent" or "send again", a resend is refused while
-- another restore's hold keeps the same email back, the Administrator must
-- have signed in with Google within five minutes, and each decision stays
-- append-only, versioned and audited (stewardship_restore_resolution_effect_v1,
-- unchanged). Nothing here creates, replaces or cancels a Family code or
-- link: "send again" only lets the ordinary planner send the restored link.
--
-- No table, column or grant change. Both functions are SECURITY INVOKER,
-- before and after; CREATE OR REPLACE keeps their owners, grants and
-- attributes, and the DO block at the end refuses to commit unless the new
-- bodies are installed with them. No temporary objects: the migration login
-- has no TEMP privilege.
SET LOCAL check_function_bodies = false;
SET LOCAL search_path = public;

-- The replaced functions' owners and grants before the change, for the DO
-- block. Custom settings hold them, not a temporary table: the migration
-- login has no TEMP privilege.
SELECT set_config('stewardship.restore_resolution_acl', (
    SELECT coalesce(proacl::text,'default')||':'||pg_get_userbyid(proowner)
    FROM pg_proc WHERE oid='public.stewardship_restore_resolution_v1()'::regprocedure), true);
SELECT set_config('stewardship.family_dispatch_live_acl', (
    SELECT coalesce(proacl::text,'default')||':'||pg_get_userbyid(proowner)
    FROM pg_proc WHERE oid='public.stewardship_family_dispatch_live_v1(uuid)'::regprocedure), true);

-- stewardship_restore_resolution_v1, from functions.sql: undecided holds of
-- the current campaign may be decided after release; no resend of a reminder
-- a later one superseded.
CREATE OR REPLACE FUNCTION public.stewardship_restore_resolution_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
DECLARE h stewardship_restore_delivery_hold%ROWTYPE;
BEGIN
    PERFORM pg_advisory_xact_lock(736220,1);
    SELECT * INTO h FROM stewardship_restore_delivery_hold WHERE id=NEW.hold_id FOR UPDATE;
    -- Two decisions (migration 0017, #537): assume it was sent, or send it
    -- again. Each is final: an assumption may already have cancelled the
    -- unsent copy, so turning it into a resend later would send nothing.
    -- (not_applicable is no longer decided: it would have released the
    -- email like "send again" without saying so.)
    IF h.id IS NULL OR NEW.version<>h.version+1 OR NEW.actor_id IS NULL OR btrim(NEW.evidence)=''
       OR NEW.state NOT IN ('assumed_delivered','resend_authorized')
       OR h.state<>'unreviewed'
       OR (NEW.state='resend_authorized' AND NEW.recovery_occurrence_id IS NOT NULL AND NOT EXISTS(
           SELECT 1 FROM stewardship_schedule_occurrence o WHERE o.id=NEW.recovery_occurrence_id
           AND o.definition_id=h.definition_id AND o.mode=h.mode AND o.target=h.target AND o.slot=h.slot AND o.state='pending'
       )) OR (NEW.state<>'resend_authorized' AND NEW.recovery_occurrence_id IS NOT NULL)
       -- An undecided hold is still decided after release (#757), but only
       -- for the current campaign: the planner never acts on another
       -- campaign's holds, so a decision there would mean nothing.
       OR NOT EXISTS(SELECT 1 FROM stewardship_schedule_definition held
           JOIN stewardship_system_configuration current_runtime ON current_runtime.current_campaign_id=held.campaign_id
           WHERE held.id=h.definition_id)
       -- Another (earlier restore's) hold still keeps this email back, so a
       -- resend would silently do nothing: settle that one first.
       OR (NEW.state='resend_authorized' AND EXISTS(SELECT 1 FROM stewardship_restore_delivery_hold other
           WHERE other.id<>h.id AND other.definition_id=h.definition_id AND other.mode=h.mode
             AND other.target=h.target AND other.slot=h.slot AND other.state IN ('unreviewed','assumed_delivered'))) THEN
        RAISE EXCEPTION 'Invalid restore hold review or resend binding' USING ERRCODE='23514'; END IF;
    -- "Send again" on a held reminder is refused once a later reminder for
    -- the same Family (removed since or not) was delivered, is on its way or
    -- its delivery is unknown: it would arrive out of order (#757). The
    -- Family dispatch check refuses the same thing at send time.
    IF NEW.state='resend_authorized' AND EXISTS(SELECT 1 FROM stewardship_schedule_definition earlier
        JOIN stewardship_schedule_revision earlier_revision ON earlier_revision.id=earlier.current_revision_id
        JOIN stewardship_schedule_definition later_definition ON later_definition.campaign_id=earlier.campaign_id
        JOIN stewardship_schedule_occurrence later ON later.definition_id=later_definition.id
        WHERE earlier.id=h.definition_id AND earlier.kind='reminder' AND later_definition.kind='reminder'
          AND later_definition.id<>earlier.id AND later.mode=h.mode AND later.target=h.target
          AND later.due_at>earlier_revision.due_at
          AND (EXISTS(SELECT 1 FROM stewardship_outbox_message later_message
                  WHERE later_message.id=later.outbox_id
                    AND later_message.state IN ('submitting','delivery_unknown','delivered'))
              OR EXISTS(SELECT 1 FROM stewardship_schedule_fulfillment later_sent
                  WHERE later_sent.occurrence_id=later.id AND later_sent.disposition='delivered'))) THEN
        RAISE EXCEPTION 'A later reminder reached this Family or is on its way; assume this one sent'
            USING ERRCODE='23514'; END IF;
    -- Settling a held email is an Administrator's decision made within five
    -- minutes of a Google sign-in (migration 0017, #537).
    IF NOT public.stewardship_restore_decision_admitted_v1(NEW.actor_id,NEW.session_id,NEW.authenticated_at) THEN
        RAISE EXCEPTION 'A restore decision requires fresh Administrator authentication' USING ERRCODE='42501'; END IF;
    RETURN NEW;
END $$;

-- stewardship_family_dispatch_live_v1, from family_dispatch.sql: a resent
-- held reminder never goes out after a later reminder.
CREATE OR REPLACE FUNCTION public.stewardship_family_dispatch_live_v1(message uuid)
RETURNS boolean LANGUAGE sql STABLE SET search_path TO pg_catalog,public,pg_temp AS $$
    SELECT public.stewardship_receipt_dispatch_live_v1(message)
        OR public.stewardship_family_test_dispatch_live_v1(message) OR EXISTS (
        SELECT 1 FROM public.stewardship_outbox_message m
        JOIN public.stewardship_schedule_occurrence o ON o.id=m.semantic_key AND o.outbox_id=m.id
        JOIN public.stewardship_schedule_definition d ON d.id=o.definition_id
        JOIN public.stewardship_campaign c ON c.id=m.campaign_id
        JOIN public.stewardship_system_configuration r ON r.current_campaign_id=c.id
        JOIN public.stewardship_campaign_configuration p ON p.id=c.active_configuration_id
        JOIN public.stewardship_campaign_credentials k ON k.campaign_id=c.id
        JOIN public.stewardship_family_campaign f ON f.id=m.family_id AND f.campaign_id=c.id
        JOIN public.stewardship_source_current source ON source.singleton
        WHERE m.id=message AND d.kind=m.purpose AND d.campaign_id=c.id
          AND m.purpose IN ('initial','reminder') AND o.target='family:'||f.id::text
          AND o.mode=m.mode AND o.routing=m.routing AND r.mode=m.mode
          AND o.revision_id=d.current_revision_id AND o.state IN ('pending','running')
          AND o.due_at<=public.stewardship_campaign_now_v1()
          AND public.stewardship_campaign_now_v1()>=p.starts_at
          AND public.stewardship_campaign_now_v1()<p.ends_at
          AND NOT r.restore_review_required AND NOT k.go_live_gate AND NOT k.population_dirty
          AND k.source_snapshot_id=source.snapshot_id AND k.source_generation=source.generation
          AND f.source_generation=source.generation AND f.active AND f.email_eligible AND f.email_deliverable
          AND NOT EXISTS (SELECT 1 FROM public.stewardship_campaign_work_gate gate
              WHERE gate.campaign_id=c.id AND gate.state<>'released')
          AND NOT EXISTS (SELECT 1 FROM public.stewardship_restore_delivery_hold hold
              WHERE hold.definition_id=d.id AND hold.mode=m.mode AND hold.target=o.target
                AND hold.slot=o.slot AND hold.state IN ('unreviewed','assumed_delivered'))
          AND NOT EXISTS (SELECT 1 FROM public.stewardship_schedule_fulfillment covered
              WHERE covered.definition_id=d.id AND covered.mode=m.mode
                AND covered.target=o.target AND covered.slot=o.slot)
          AND NOT EXISTS (SELECT 1 FROM public.stewardship_outbox_message unresolved
              WHERE unresolved.family_id=f.id AND unresolved.campaign_id=c.id AND unresolved.mode=m.mode
                AND unresolved.id<>m.id AND unresolved.state IN ('submitting','delivery_unknown'))
          AND (m.purpose<>'reminder' OR NOT EXISTS (
              SELECT 1 FROM public.stewardship_restore_delivery_hold initial_hold
              JOIN public.stewardship_schedule_definition initial ON initial.id=initial_hold.definition_id
              WHERE initial.campaign_id=c.id AND initial.kind='initial' AND initial_hold.mode=m.mode
                AND initial_hold.target=o.target AND initial_hold.slot='once' AND initial_hold.state='unreviewed'))
          -- A held reminder sent again after a restore (#757) never goes out
          -- once a later reminder (removed since or not) reached the Family
          -- or is on its way: it would arrive out of order.
          AND (m.purpose<>'reminder' OR NOT EXISTS (
              SELECT 1 FROM public.stewardship_restore_delivery_hold resent
              WHERE resent.definition_id=d.id AND resent.mode=m.mode AND resent.target=o.target
                AND resent.slot=o.slot AND resent.state='resend_authorized')
            OR NOT EXISTS (
              SELECT 1 FROM public.stewardship_schedule_occurrence later
              JOIN public.stewardship_schedule_definition later_definition ON later_definition.id=later.definition_id
              WHERE later_definition.campaign_id=c.id AND later_definition.kind='reminder'
                AND later_definition.id<>d.id AND later.mode=m.mode AND later.target=o.target
                AND later.due_at>o.due_at
                AND (EXISTS (SELECT 1 FROM public.stewardship_outbox_message later_message
                        WHERE later_message.id=later.outbox_id
                          AND later_message.state IN ('submitting','delivery_unknown','delivered'))
                    OR EXISTS (SELECT 1 FROM public.stewardship_schedule_fulfillment later_sent
                        WHERE later_sent.occurrence_id=later.id AND later_sent.disposition='delivered'))))
          AND ((m.mode='testing' AND c.state='draft' AND m.credential_namespace='rehearsal'
                AND m.rehearsal_epoch_id=k.rehearsal_epoch_id
                AND EXISTS (SELECT 1 FROM public.stewardship_rehearsal_epoch e
                    WHERE e.id=m.rehearsal_epoch_id AND e.state='active')
                AND NOT EXISTS (SELECT 1 FROM public.stewardship_submission sub
                    WHERE sub.family_id=f.id AND sub.mode='test' AND sub.rehearsal_epoch_id=m.rehearsal_epoch_id))
            OR (m.mode='production' AND c.state IN ('scheduled','active') AND NOT c.delivery_paused
                AND f.effective_submission_id IS NULL AND m.credential_namespace='production'
                AND m.token_generation_id=c.active_token_generation_id
                AND EXISTS (SELECT 1 FROM public.stewardship_family_token_generation generation
                    JOIN public.stewardship_credential_deployment deployment
                      ON deployment.family_link_epoch=generation.credential_epoch
                    WHERE generation.id=m.token_generation_id AND generation.state='active'
                      AND generation.credential_epoch=m.credential_epoch_id)
                AND NOT EXISTS (SELECT 1 FROM public.stewardship_activation_catchup demand
                    WHERE demand.campaign_id=c.id AND demand.completed_at IS NULL)))
    )
$$;

DO $check$
BEGIN
    IF NOT EXISTS(SELECT 1 FROM pg_proc WHERE oid='public.stewardship_restore_resolution_v1()'::regprocedure
        AND NOT prosecdef AND provolatile='v' AND prorettype='trigger'::regtype
        AND proconfig @> ARRAY['search_path=pg_catalog, public, pg_temp']
        AND prosrc LIKE '%stewardship_restore_decision_admitted_v1(NEW.actor_id,NEW.session_id,NEW.authenticated_at)%'
        AND prosrc LIKE '%NEW.recovery_occurrence_id IS NOT NULL AND NOT EXISTS%'
        AND prosrc LIKE '%other.id<>h.id%' AND prosrc LIKE '%h.state<>''unreviewed''%'
        AND prosrc LIKE '%current_runtime.current_campaign_id=held.campaign_id%'
        AND prosrc LIKE '%later.due_at>earlier_revision.due_at%'
        AND prosrc LIKE '%later_sent.disposition=''delivered''%'
        AND prosrc NOT LIKE '%restore_released_at%'
        AND prosrc NOT LIKE '%''not_applicable''%') THEN
        RAISE EXCEPTION 'The held-email decision guard is not the one this file installs';
    END IF;
    IF NOT EXISTS(SELECT 1 FROM pg_trigger WHERE tgname='stewardship_restore_resolution_v1'
        AND tgrelid='public.stewardship_restore_hold_resolution'::regclass
        AND tgfoid='public.stewardship_restore_resolution_v1()'::regprocedure AND tgenabled='O') THEN
        RAISE EXCEPTION 'The held-email decision guard is not attached';
    END IF;
    IF NOT EXISTS(SELECT 1 FROM pg_proc WHERE oid='public.stewardship_family_dispatch_live_v1(uuid)'::regprocedure
        AND NOT prosecdef AND provolatile='s' AND prorettype='boolean'::regtype
        AND proconfig @> ARRAY['search_path=pg_catalog, public, pg_temp']
        AND prosrc LIKE '%resent.state=''resend_authorized''%'
        AND prosrc LIKE '%later.due_at>o.due_at%'
        AND prosrc LIKE '%later_sent.disposition=''delivered''%'
        AND prosrc LIKE '%initial_hold.state=''unreviewed''%'
        AND prosrc LIKE '%unresolved.state IN (''submitting'',''delivery_unknown'')%') THEN
        RAISE EXCEPTION 'The Family dispatch check is not the one this file installs';
    END IF;
    -- CREATE OR REPLACE keeps the owners and grants; prove it.
    IF current_setting('stewardship.restore_resolution_acl', true) IS DISTINCT FROM (
        SELECT coalesce(proacl::text,'default')||':'||pg_get_userbyid(proowner)
        FROM pg_proc WHERE oid='public.stewardship_restore_resolution_v1()'::regprocedure) THEN
        RAISE EXCEPTION 'The held-email decision guard''s owner or grants changed';
    END IF;
    IF current_setting('stewardship.family_dispatch_live_acl', true) IS DISTINCT FROM (
        SELECT coalesce(proacl::text,'default')||':'||pg_get_userbyid(proowner)
        FROM pg_proc WHERE oid='public.stewardship_family_dispatch_live_v1(uuid)'::regprocedure) THEN
        RAISE EXCEPTION 'The Family dispatch check''s owner or grants changed';
    END IF;
END
$check$;

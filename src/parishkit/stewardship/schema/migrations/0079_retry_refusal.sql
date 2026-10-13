-- Frozen forward migration file 0079 (the repository-wide file sequence): an
-- address that a Family email's follow-up finds refused permanently becomes
-- a recipient refusal (#806). This file is installed by its Django migration
-- in the jobs app and must never change once released;
-- tests/stewardship/test_schema_migration_files.py pins its digest.
-- Reversing needs its own forward migration.
--
-- 0077's follow-up sends a delivered Production invitation or reminder again
-- to the addresses one accepted attempt refused only for now. Until now an
-- address the follow-up saw refused permanently was only shown on the Mail
-- message page; it stayed one of the Family's recipients, so the next
-- reminder was addressed to it again. Now the transaction that records the
-- follow-up's outcome also appends a stewardship_recipient_refusal row for
-- it, exactly as an accepted attempt's own permanent refusal does, so it is
-- suppressed, listed on Refused addresses and cleared the same way.
--
-- The refusal names the accepted attempt's event (the follow-up's
-- source_event_id), so Outgoing mail and Refused addresses point at the
-- email the address was refused for. (event_id, address) stays unique: the
-- follow-up's addresses are that attempt's refused-for-now ones, never its
-- refused-permanently ones.
--
-- stewardship_recipient_refusal_guard_v1 (baseline delivery.sql) is
-- replaced with that file's final definition: it keeps the event's own
-- evidence unchanged and adds the follow-up's, admitted only when a
-- follow-up of that attempt, for the same message and rendering, lists the
-- address's position as refused permanently in its latest outcome and the
-- refusal's actor and run are the follow-up's (the worker and task run that
-- just recorded the outcome): the row is settled (not submitting, not a
-- recovery), its run_id, the run that sent the envelope, is the refusal's
-- run, and that task run's fence is still the one that submitted
-- (task_fence), since an end without a send, a new submission or a
-- recovery rewrites the row's actor and run but keeps the old outcome, and
-- a retry keeps the task run's id and only advances its fence. The
-- Production, Family, DUID, organization and routed/intended recipient
-- checks are unchanged. The guard is invoker's
-- rights (only mail dispatch inserts refusals, and it reads follow-ups), so
-- there is no SECURITY DEFINER to repeat. Nothing is sent, rendered or
-- re-sealed, and no credential changes.
SET LOCAL check_function_bodies = false;
SET LOCAL search_path = public;

CREATE OR REPLACE FUNCTION public.stewardship_recipient_refusal_guard_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
BEGIN
    PERFORM pg_advisory_xact_lock(736220,1);
    -- The evidence is either the event's own definitive refusal, attributed
    -- to the event's actor, or (#806, migration 0079) the latest outcome of
    -- the follow-up that this accepted attempt opened for the addresses it
    -- refused only for now: the refusal names the attempt's event, and its
    -- actor and run are the ones that just recorded that outcome. An end
    -- without a send, a new submission or a recovery rewrites the row's
    -- actor and run but keeps the outcome, so the outcome counts only in
    -- the settled row of the run that sent it (run_id), never while
    -- submitting, never after a recovery, and only until the task is
    -- claimed again: a retry keeps the task run's id and advances its fence,
    -- which then no longer equals the fence that submitted (task_fence).
    IF NEW.address<>lower(NEW.address) OR NOT EXISTS (
        SELECT 1 FROM public.stewardship_outbox_event e
        JOIN public.stewardship_outbox_message m ON m.id=e.message_id
        JOIN public.stewardship_outbox_render r ON r.id=e.render_id
        JOIN public.stewardship_family_campaign f ON f.id=m.family_id
        JOIN public.stewardship_campaign_credentials k ON k.campaign_id=f.campaign_id
        JOIN public.stewardship_source_snapshot s ON s.id=k.source_snapshot_id
        WHERE e.id=NEW.event_id AND m.family_id=NEW.family_id
          AND f.family_duid=NEW.family_duid AND s.organization_id=NEW.organization_id
          AND m.mode='production' AND m.routing='production'
          AND ((e.actor_id=NEW.actor_id AND ((e.state='permanent_failure' AND e.reason='recipient_refused') OR EXISTS (
              SELECT 1 FROM jsonb_array_elements(
                  public.stewardship_family_smtp_result_v1(e.id)->'permanent') item
              WHERE r.routed_recipients->>(item::text::integer)=NEW.address
          ))) OR EXISTS (
              SELECT 1 FROM public.stewardship_outbox_address_retry a
              JOIN public.stewardship_task_run t ON t.id=a.run_id AND t.fence=a.task_fence
              CROSS JOIN LATERAL jsonb_array_elements(a.outcome->'permanent') item
              WHERE a.source_event_id=e.id AND a.message_id=m.id AND a.render_id=r.id
                AND a.actor_id=NEW.actor_id AND a.correlation_id=NEW.correlation_id
                AND a.state<>'submitting' AND a.run_id=NEW.correlation_id
                AND a.reason<>'recovery_unknown'
                AND jsonb_typeof(item)='number'
                AND r.routed_recipients->>(item::text::integer)=NEW.address
          ))
          AND r.routed_recipients ? NEW.address AND r.intended_recipients ? NEW.address
    ) THEN RAISE EXCEPTION 'Refusal requires exact Production recipient evidence'
        USING ERRCODE='23514'; END IF;
    RETURN NEW;
END $$;

-- Refuse to commit unless the replaced guard is installed as declared: both
-- kinds of evidence, the follow-up's bound to the run that recorded it, the
-- routed and intended recipient checks, invoker's rights with the fixed
-- search_path, and still attached BEFORE INSERT FOR EACH ROW (tgtype 7).
-- It reads only the catalog.
DO $check$
BEGIN
    IF to_regprocedure('public.stewardship_recipient_refusal_guard_v1()') IS NULL
       OR NOT EXISTS (SELECT 1 FROM pg_proc p
           WHERE p.oid='public.stewardship_recipient_refusal_guard_v1()'::regprocedure
             AND NOT p.prosecdef
             AND p.proconfig=ARRAY['search_path=pg_catalog, public, pg_temp']
             AND p.prosrc LIKE '%public.stewardship_family_smtp_result_v1(e.id)->''permanent''%'
             AND p.prosrc LIKE '%e.state=''permanent_failure'' AND e.reason=''recipient_refused''%'
             AND p.prosrc LIKE '%e.actor_id=NEW.actor_id AND%'
             AND p.prosrc LIKE '%FROM public.stewardship_outbox_address_retry a%'
             AND p.prosrc LIKE '%a.source_event_id=e.id AND a.message_id=m.id AND a.render_id=r.id%'
             AND p.prosrc LIKE '%a.actor_id=NEW.actor_id AND a.correlation_id=NEW.correlation_id%'
             AND p.prosrc LIKE '%a.state<>''submitting'' AND a.run_id=NEW.correlation_id%'
             AND p.prosrc LIKE '%a.reason<>''recovery_unknown''%'
             AND p.prosrc LIKE '%t.id=a.run_id AND t.fence=a.task_fence%'
             AND p.prosrc LIKE '%r.routed_recipients ? NEW.address%'
             AND p.prosrc LIKE '%r.intended_recipients ? NEW.address%')
       OR NOT EXISTS (SELECT 1 FROM pg_trigger t
           WHERE t.tgrelid='public.stewardship_recipient_refusal'::regclass
             AND t.tgname='stewardship_recipient_refusal_guard' AND NOT t.tgisinternal
             AND t.tgenabled='O' AND t.tgtype=7
             AND t.tgfoid='public.stewardship_recipient_refusal_guard_v1()'::regprocedure) THEN
        RAISE EXCEPTION 'Migration 0079 (follow-up recipient refusals) is not installed as declared';
    END IF;
END
$check$;

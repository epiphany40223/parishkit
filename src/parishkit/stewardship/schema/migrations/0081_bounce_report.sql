-- Frozen forward migration file 0081 (the repository-wide file sequence): an
-- Administrator reports that a delivered Family email bounced at one of its
-- addresses (#806 slice 4). This file is installed by its Django migration in
-- the jobs app and must never change once released;
-- tests/stewardship/test_schema_migration_files.py pins its digest and checks
-- that its copy of the replaced guard equals the fresh-install baseline's
-- (schema/delivery.sql). Reversing needs its own forward migration.
--
-- The provider can accept an email for an address whose mailbox later sends
-- a non-delivery report (a bounce) to the sending mailbox. The system never
-- reads that mailbox; an Administrator who sees the bounce records it on the
-- email's Mail message page. The report is a stewardship_recipient_refusal
-- row of a new kind, so it sets the address aside for that Family exactly as
-- a provider's permanent refusal does, and every reader of refusals (the
-- next rendering's recipients, deliverability, Outgoing mail's mark, Refused
-- addresses, clearing and the ParishSoft-correction resolution) treats it
-- the same without change.
--
-- stewardship_recipient_refusal gains two columns: kind ('provider' for
-- every existing row and every refusal mail dispatch records, or
-- 'reported_bounce') and evidence_note (empty for a provider refusal; for a
-- report, the Administrator's note, more than spaces, at most 2000
-- characters). The recipient_refusal_kind check ties them together. The
-- table stays immutable (recipient_immutable) and web keeps only SELECT,
-- which covers the new columns; mail dispatch's and the source owner's
-- table-level grants are unchanged.
--
-- stewardship_recipient_refusal_guard_v1 (baseline delivery.sql, 0079's
-- body) is replaced with that file's final definition: the provider branch
-- is unchanged and a report is admitted only from the web login, for an
-- enabled Administrator actor, with a note, naming a delivered event of a
-- Production-routed email of a Family in the current campaign (not archived,
-- export admitted, as Clear verified refusal requires), for an address in
-- that event's routed and intended recipients that the Family has no
-- unresolved refusal of. The guard stays invoker's rights; the report's only
-- writer is the definer below, so it runs with the owner's reads.
--
-- stewardship_report_recipient_bounce_v1(report, message, address, actor,
-- note) is the report's only entry point, a SECURITY DEFINER that only the
-- web login may call (database-grants grants it EXECUTE; PUBLIC has none).
-- It names the email's newest delivered event, inserts the refusal with id
-- report (the form's command id, so a repeated command finds it) and, in the
-- same transaction, a recipient_bounce_reported audit event naming it. The
-- refusal's existing AFTER trigger (stewardship_refusal_family_effect_v1)
-- recalculates the Family's email_deliverable under the guard's work-order
-- lock, as for a provider refusal.
--
-- No Family code, link, token or rendering is created, replaced or
-- cancelled, and nothing is sent. The DO block at the end refuses to commit
-- unless the columns, the check, the guard and the definer are installed as
-- described. No temporary objects: the migration login has no TEMP
-- privilege.
SET LOCAL check_function_bodies = false;
SET LOCAL search_path = public;

ALTER TABLE public.stewardship_recipient_refusal
    ADD COLUMN kind varchar(32) NOT NULL DEFAULT 'provider';
ALTER TABLE public.stewardship_recipient_refusal
    ADD COLUMN evidence_note varchar(2000) NOT NULL DEFAULT '';
ALTER TABLE public.stewardship_recipient_refusal
    ADD CONSTRAINT recipient_refusal_kind CHECK (
        (evidence_note='' AND kind='provider') OR
        (actor_id IS NOT NULL AND kind='reported_bounce' AND NOT evidence_note=''));

CREATE OR REPLACE FUNCTION public.stewardship_recipient_refusal_guard_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
BEGIN
    PERFORM pg_advisory_xact_lock(736220,1);
    -- An Administrator's bounce report (#806 slice 4, migration 0081),
    -- written only by stewardship_report_recipient_bounce_v1 for the web
    -- login: the actor is an enabled Administrator now, the note is more
    -- than spaces, the event is a delivered attempt of a Production-routed
    -- email of a Family in the current campaign, which is not archived and
    -- admits Admin changes (as Clear verified refusal requires), the
    -- address is one of that attempt's routed and intended recipients, and
    -- the Family has no unresolved refusal of it already.
    IF NEW.kind='reported_bounce' THEN
        IF session_user<>'pk_stewardship_web' OR NEW.actor_id IS NULL
           OR public.stewardship_export_authorized_v1(NEW.actor_id,true) IS NOT TRUE
           OR NEW.address<>lower(NEW.address) OR btrim(NEW.evidence_note)=''
           OR NOT EXISTS (
            SELECT 1 FROM public.stewardship_outbox_event e
            JOIN public.stewardship_outbox_message m ON m.id=e.message_id
            JOIN public.stewardship_outbox_render r ON r.id=e.render_id
            JOIN public.stewardship_family_campaign f ON f.id=m.family_id
            JOIN public.stewardship_system_configuration cfg ON cfg.current_campaign_id=f.campaign_id
            JOIN public.stewardship_campaign c ON c.id=f.campaign_id
            JOIN public.stewardship_campaign_credentials k ON k.campaign_id=f.campaign_id
            JOIN public.stewardship_source_snapshot s ON s.id=k.source_snapshot_id
            WHERE e.id=NEW.event_id AND m.family_id=NEW.family_id
              AND f.family_duid=NEW.family_duid AND s.organization_id=NEW.organization_id
              AND m.mode='production' AND m.routing='production' AND e.state='delivered'
              AND c.state<>'archived' AND public.stewardship_export_admitted_v1(c.id,true)
              AND r.routed_recipients ? NEW.address AND r.intended_recipients ? NEW.address
           ) OR EXISTS (
            SELECT 1 FROM public.stewardship_recipient_refusal refusal
            WHERE refusal.organization_id=NEW.organization_id
              AND refusal.family_duid=NEW.family_duid AND refusal.address=NEW.address
              AND NOT EXISTS (SELECT 1 FROM public.stewardship_recipient_resolution resolution
                  WHERE resolution.refusal_id=refusal.id)
           ) THEN RAISE EXCEPTION 'Bounce report requires a delivered Production address'
            USING ERRCODE='23514'; END IF;
        RETURN NEW;
    END IF;
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

-- The report's only writer. The caller checks repeat 0068's, but for the web
-- login alone: the guard admits a report only from it. The email's newest
-- delivered event is the evidence the refusal names; an email that was
-- never delivered, or is no Family's, is refused here (23514) before the
-- guard checks the rest.
CREATE FUNCTION public.stewardship_report_recipient_bounce_v1(
    report uuid, message uuid, reported_address text, actor uuid, note text
) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE delivered uuid; family public.stewardship_family_campaign%ROWTYPE;
    organization bigint; correlation uuid:=gen_random_uuid();
BEGIN
    IF session_user<>'pk_stewardship_web' THEN
        RAISE EXCEPTION 'Bounce reports are recorded only by the web login' USING ERRCODE='42501';
    END IF;
    IF actor IS NULL OR public.stewardship_export_authorized_v1(actor,true) IS NOT TRUE THEN
        RAISE EXCEPTION 'Bounce reports require an Administrator' USING ERRCODE='42501';
    END IF;
    IF report IS NULL OR reported_address IS NULL OR note IS NULL
       OR btrim(note)='' OR length(note)>2000 THEN
        RAISE EXCEPTION 'Bounce report requires an address and a note' USING ERRCODE='23514';
    END IF;
    SELECT e.id INTO delivered FROM public.stewardship_outbox_event e
     WHERE e.message_id=message AND e.state='delivered'
     ORDER BY e.version DESC LIMIT 1;
    SELECT f.* INTO family FROM public.stewardship_outbox_message m
      JOIN public.stewardship_family_campaign f ON f.id=m.family_id
     WHERE m.id=message;
    SELECT s.organization_id INTO organization
      FROM public.stewardship_campaign_credentials k
      JOIN public.stewardship_source_snapshot s ON s.id=k.source_snapshot_id
     WHERE k.campaign_id=family.campaign_id;
    IF delivered IS NULL OR family.id IS NULL OR organization IS NULL THEN
        RAISE EXCEPTION 'Bounce report requires a delivered Family email' USING ERRCODE='23514';
    END IF;
    INSERT INTO public.stewardship_recipient_refusal
        (id,actor_id,correlation_id,family_id,event_id,address,organization_id,
         family_duid,kind,evidence_note)
    VALUES(report,actor,correlation,family.id,delivered,reported_address,organization,
        family.family_duid,'reported_bounce',note);
    INSERT INTO public.stewardship_audit_event
        (id,actor_id,correlation_id,event_type,subject_id)
    VALUES(gen_random_uuid(),actor,correlation,'recipient_bounce_reported',report);
    RETURN report;
END $$;
ALTER FUNCTION public.stewardship_report_recipient_bounce_v1(uuid, uuid, text, uuid, text) SECURITY DEFINER;
REVOKE ALL ON FUNCTION public.stewardship_report_recipient_bounce_v1(uuid, uuid, text, uuid, text) FROM PUBLIC;

-- Refuse to commit unless everything is installed as declared: the two
-- columns with their defaults, the kind check, the guard with both branches
-- (invoker's rights, the fixed search_path, still attached BEFORE INSERT FOR
-- EACH ROW, tgtype 7) and the definer, which PUBLIC cannot execute. It reads
-- only the catalog. The missing function is checked first, on its own,
-- because PostgreSQL does not short-circuit OR and the ::regprocedure casts
-- below would otherwise fail with their own error instead of this file's.
DO $check$
BEGIN
    IF to_regprocedure('public.stewardship_report_recipient_bounce_v1(uuid,uuid,text,uuid,text)') IS NULL
       OR to_regprocedure('public.stewardship_recipient_refusal_guard_v1()') IS NULL THEN
        RAISE EXCEPTION 'Migration 0081 (bounce reports) is not installed as declared';
    END IF;
    IF (SELECT count(*) FROM pg_attribute a
         WHERE a.attrelid='public.stewardship_recipient_refusal'::regclass
           AND NOT a.attisdropped AND a.attnotnull
           AND ((a.attname='kind' AND format_type(a.atttypid,a.atttypmod)='character varying(32)'
                 AND pg_get_expr((SELECT d.adbin FROM pg_attrdef d
                     WHERE d.adrelid=a.attrelid AND d.adnum=a.attnum),a.attrelid)
                     ='''provider''::character varying')
             OR (a.attname='evidence_note' AND format_type(a.atttypid,a.atttypmod)='character varying(2000)'
                 AND pg_get_expr((SELECT d.adbin FROM pg_attrdef d
                     WHERE d.adrelid=a.attrelid AND d.adnum=a.attnum),a.attrelid)
                     ='''''::character varying')))<>2
       OR NOT EXISTS (SELECT 1 FROM pg_constraint c
           WHERE c.conrelid='public.stewardship_recipient_refusal'::regclass
             AND c.conname='recipient_refusal_kind' AND c.contype='c' AND c.convalidated
             AND pg_get_constraintdef(c.oid) LIKE '%reported_bounce%')
       OR NOT EXISTS (SELECT 1 FROM pg_proc p
           WHERE p.oid='public.stewardship_recipient_refusal_guard_v1()'::regprocedure
             AND NOT p.prosecdef
             AND p.proconfig=ARRAY['search_path=pg_catalog, public, pg_temp']
             AND p.prosrc LIKE '%IF NEW.kind=''reported_bounce'' THEN%'
             AND p.prosrc LIKE '%session_user<>''pk_stewardship_web''%'
             AND p.prosrc LIKE '%public.stewardship_export_authorized_v1(NEW.actor_id,true)%'
             AND p.prosrc LIKE '%e.state=''delivered''%'
             AND p.prosrc LIKE '%public.stewardship_export_admitted_v1(c.id,true)%'
             AND p.prosrc LIKE '%public.stewardship_family_smtp_result_v1(e.id)->''permanent''%'
             AND p.prosrc LIKE '%e.state=''permanent_failure'' AND e.reason=''recipient_refused''%'
             AND p.prosrc LIKE '%FROM public.stewardship_outbox_address_retry a%'
             AND p.prosrc LIKE '%t.id=a.run_id AND t.fence=a.task_fence%'
             AND p.prosrc LIKE '%r.intended_recipients ? NEW.address%')
       OR NOT EXISTS (SELECT 1 FROM pg_trigger t
           WHERE t.tgrelid='public.stewardship_recipient_refusal'::regclass
             AND t.tgname='stewardship_recipient_refusal_guard' AND NOT t.tgisinternal
             AND t.tgenabled='O' AND t.tgtype=7
             AND t.tgfoid='public.stewardship_recipient_refusal_guard_v1()'::regprocedure)
       OR NOT EXISTS (SELECT 1 FROM pg_proc p
           WHERE p.oid='public.stewardship_report_recipient_bounce_v1(uuid,uuid,text,uuid,text)'::regprocedure
             AND p.prosecdef AND p.provolatile='v' AND NOT p.proretset
             AND p.prorettype='uuid'::regtype
             AND p.proconfig=ARRAY['search_path=pg_catalog, public, pg_temp']
             AND p.prosrc LIKE '%''reported_bounce'',note%'
             AND p.prosrc LIKE '%''recipient_bounce_reported''%')
       OR has_function_privilege('public',
           'public.stewardship_report_recipient_bounce_v1(uuid,uuid,text,uuid,text)', 'EXECUTE') THEN
        RAISE EXCEPTION 'Migration 0081 (bounce reports) is not installed as declared';
    END IF;
END
$check$;

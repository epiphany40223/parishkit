-- Frozen forward migration file 0204: an Administrator grant names the
-- Google account of a standing verified-address match (#2363; data spec
-- "Verified-address match records", admin-portal spec "User change checks
-- and alerts"). This file is installed by its Django migration in the
-- accounts app and must never change once released;
-- tests/stewardship/test_schema_migration_files.py pins its digest.
-- Reversing needs its own forward migration. (Prefix 0204 in the
-- repository-wide sequence; the train renumbers this file if the sequence
-- moved.)
--
-- When a matched user is raised to Administrator, the grant's notice named
-- only the user's address, though the new Administrator signs in through
-- another Google account. This file:
--
--   replaces the policy_security_event_account check (0200): an
--        administrator_granted row may carry account_email (trimmed lower
--        case), still with no address_match_id or replaced account; the
--        address_matched rule and every other kind are unchanged;
--   creates stewardship_grant_match_v1, a SECURITY DEFINER BEFORE INSERT
--        trigger on stewardship_policy_security_event that sets an
--        administrator_granted row's account_email to the sign_in_email of
--        the standing match of its target and user record, or to null,
--        whatever the insert supplied. It completes the row the activation
--        trigger writes, as stewardship_recovery_recipient_v1 does, so the
--        activation function is not replaced. A definer: only the web login is granted the
--        match table, and activation also runs from the command line;
--   replaces stewardship_security_content_v2 (0200's body) with a "Signs in
--        through Google account" row after Target when a grant carries
--        account_email, the twin of jobs/security_content.py.
--
-- No match is made while the alternate_address_sign_in setting is off, so
-- while none was ever made (Production today) every grant row and alert is
-- unchanged. A match that still stands is named even when it cannot sign
-- in at that moment (the setting turned off later, or its 30-day
-- confirmation lapsed): deliberately, because it signs in again once the
-- setting is back on or it is re-checked, so the Administrators are told
-- of the link. Re-running the file (the users-swap tests do, after 0200's)
-- is safe: the check is dropped if present, and the function and trigger
-- are made again only after the swap test dropped them. The file ends
-- with a DO block that refuses to commit unless all of it is installed as
-- declared.
SET LOCAL check_function_bodies = false;
SET LOCAL search_path = public;

-- Adding the check validates every row; no existing row carries an
-- account_email except address_matched ones, which the rule keeps.
ALTER TABLE public.stewardship_policy_security_event
    DROP CONSTRAINT IF EXISTS policy_security_event_account;
ALTER TABLE "stewardship_policy_security_event" ADD CONSTRAINT "policy_security_event_account" CHECK ((("account_email" IS NOT NULL AND "address_match_id" IS NOT NULL AND "kind" = 'address_matched' AND "account_email" = (LOWER(TRIM("account_email")))) OR ("address_match_id" IS NULL AND "kind" = 'administrator_granted' AND "replaced_account_email" IS NULL AND ("account_email" IS NULL OR "account_email" = (LOWER(TRIM("account_email"))))) OR (NOT ("kind" = 'address_matched') AND NOT ("kind" = 'administrator_granted') AND "account_email" IS NULL AND "address_match_id" IS NULL AND "replaced_account_email" IS NULL)));

-- The grant's matched account. The match's email and sign_in_email are
-- both stored trimmed lower case (address_match_normalized), as the grant's
-- target is, so plain equality finds it; at most one match of an address
-- stands (address_match_one_per_address). The match must also be of the
-- granted user record: nothing ends a match when its user is removed (it
-- ends as user_gone at the account's next sign-in), so a user removed and
-- added again under a new record ID must not name the old account.
-- Always assigned, so no caller can name an account the match table does
-- not.
CREATE FUNCTION public.stewardship_grant_match_v1() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path TO pg_catalog,public,pg_temp AS $$
BEGIN
    IF NEW.kind='administrator_granted' THEN
        NEW.account_email := (SELECT m.sign_in_email FROM public.stewardship_address_match m
                              WHERE m.email=NEW.target AND m.user_record_id=NEW.rule_record_id
                                AND m.ended_at IS NULL);
    END IF;
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION public.stewardship_grant_match_v1() FROM PUBLIC;
CREATE TRIGGER stewardship_grant_match_v1
    BEFORE INSERT ON public.stewardship_policy_security_event
    FOR EACH ROW EXECUTE FUNCTION public.stewardship_grant_match_v1();

-- stewardship_security_content_v2, from 0200_address_match.sql: an
-- Administrator grant's Signs in through Google account row (the Python
-- twin is jobs/security_content.py). Still a STABLE invoker with the fixed
-- search_path, so no ALTER follows.
CREATE OR REPLACE FUNCTION public.stewardship_security_content_v2(event uuid, deployment_mode text,
    configuration uuid)
RETURNS jsonb LANGUAGE plpgsql STABLE SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE e stewardship_policy_security_event%ROWTYPE; title text; actor text;
    instruction text; labels text[]; vals text[]; body text; html text; i integer;
BEGIN
    IF deployment_mode IS NULL OR deployment_mode NOT IN ('testing','production') THEN
      RAISE EXCEPTION 'Security alert mode is invalid' USING ERRCODE='23514'; END IF;
    SELECT * INTO STRICT e FROM stewardship_policy_security_event WHERE id=event;
    title:=CASE e.kind
      WHEN 'administrator_granted' THEN 'Administrator added to an exact address'
      WHEN 'domain_created' THEN 'Hosted-domain rule created'
      WHEN 'domain_staff_granted' THEN 'Staff added to a hosted-domain rule'
      WHEN 'backup_key_replaced' THEN 'Backup encryption key replaced'
      WHEN 'address_matched' THEN 'Google account matched through another address'
    END;
    IF title IS NULL THEN RAISE EXCEPTION 'Security alert kind is invalid' USING ERRCODE='23514'; END IF;
    SELECT email INTO actor FROM stewardship_portal_user WHERE id=e.actor_id;
    IF e.kind='backup_key_replaced' THEN
      instruction:='An Administrator replaced the public key that backups are encrypted '
        'with. From the next backup on, only the matching private key can open them. '
        'If the parish key holder did not plan this, replace the key again with the '
        'parish key and ask the server operator to check the newest backup. If you '
        'can still sign in as an Administrator, acknowledge it on the Admin dashboard; '
        'it stays there until an Administrator other than the one who made the change '
        'does. If you no longer can, contact the parish or its other Administrators.';
      labels:=ARRAY['Key before','Key after','By','When','Deployment mode',
        'Event reference'];
      vals:=ARRAY[COALESCE(e.before_roles->>0,'not known'),e.after_roles->>0,
        COALESCE(actor,'Operator recovery'),
        stewardship_alert_time_v1(e.created_at,configuration),
        initcap(deployment_mode),e.id::text];
    ELSIF e.kind='address_matched' THEN
      -- A new verified-address match (#2343): the matched Google account,
      -- the user it signs in as and that user's role, and, after a
      -- takeover, the Google account it replaced. No By row: the account
      -- itself is the actor.
      instruction:='A Google account signed in to the Admin portal as a user, through '
        'another address of that account that Google confirmed belongs to it. If this '
        'was not expected, end the match on the Edit page of that user, or delete the '
        'user if the address itself is not safe. Acknowledge it on the Admin dashboard; '
        'it stays there until an Administrator other than the matched person does.';
      labels:=ARRAY['Account','User','Role'];
      vals:=ARRAY[e.account_email,e.target,stewardship_security_role_words_v1(e.after_roles)];
      IF e.replaced_account_email IS NOT NULL THEN
        labels:=labels||ARRAY['Replaced Google account'];
        vals:=vals||ARRAY[e.replaced_account_email||CASE WHEN e.replaced_login_at IS NULL
          THEN ', never signed in'
          ELSE ', last signed in '||stewardship_alert_time_v1(e.replaced_login_at,configuration)
          END];
      END IF;
      labels:=labels||ARRAY['When','Deployment mode','Event reference'];
      vals:=vals||ARRAY[stewardship_alert_time_v1(e.created_at,configuration),
        initcap(deployment_mode),e.id::text];
    ELSE
    instruction:='This change to who may sign in took effect on activation. Acknowledge it '
      'on the Admin dashboard; it stays there until an Administrator who existed '
      'before the change does.';
    labels:=ARRAY['Target'];
    vals:=ARRAY[e.target];
    -- An Administrator grant to an address a Google account signs in
    -- through by a standing match (#2363); only a grant carries it.
    IF e.account_email IS NOT NULL THEN
      labels:=labels||ARRAY['Signs in through Google account'];
      vals:=vals||ARRAY[e.account_email];
    END IF;
    labels:=labels||ARRAY['Roles before','Roles after','By','When','Deployment mode',
      'Event reference'];
    vals:=vals||ARRAY[stewardship_security_role_words_v1(e.before_roles),
      stewardship_security_role_words_v1(e.after_roles),COALESCE(actor,'Operator recovery'),
      stewardship_alert_time_v1(e.created_at,configuration),
      initcap(deployment_mode),e.id::text];
    END IF;
    body:=title||E'\n\n'||instruction||E'\n\n';
    html:='<h2>'||title||'</h2><p>'||instruction||'</p><dl>';
    FOR i IN 1..array_length(labels,1) LOOP
      body:=body||CASE WHEN i>1 THEN E'\n' ELSE '' END||labels[i]||': '||vals[i];
      html:=html||'<dt>'||labels[i]||'</dt><dd>'||stewardship_security_escape_v1(vals[i])||'</dd>';
    END LOOP;
    RETURN jsonb_build_object('subject','['||upper(deployment_mode)||'] SECURITY: '||title,
      'html',html||'</dl>','text',body);
END $$;

DO $check$
DECLARE pinned text[] := ARRAY['search_path=pg_catalog, public, pg_temp'];
BEGIN
    -- The replaced check admits a grant's account.
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
        WHERE conrelid='public.stewardship_policy_security_event'::regclass
          AND contype='c' AND conname='policy_security_event_account'
          AND strpos(pg_get_constraintdef(oid),'administrator_granted')>0) THEN
        RAISE EXCEPTION 'Migration 0204: the security event account check is not installed';
    END IF;
    -- The trigger: an enabled row trigger, before insert only (1+2+4), on
    -- the definer.
    IF NOT EXISTS (SELECT 1 FROM pg_trigger t
        WHERE t.tgrelid='public.stewardship_policy_security_event'::regclass
          AND t.tgname='stewardship_grant_match_v1' AND NOT t.tgisinternal
          AND t.tgenabled='O' AND t.tgtype=7
          AND t.tgfoid='public.stewardship_grant_match_v1()'::regprocedure) THEN
        RAISE EXCEPTION 'Migration 0204: the grant match trigger is not installed';
    END IF;
    -- The definer is a definer with no PUBLIC EXECUTE and the replaced
    -- compiler an invoker naming the new row; both pin exactly the
    -- search_path (CREATE OR REPLACE drops a SET clause the new text
    -- leaves out).
    IF (SELECT count(*) FROM pg_proc p
        WHERE p.proconfig=pinned
          AND ((p.oid=to_regprocedure('public.stewardship_grant_match_v1()')
                AND p.prosecdef
                AND strpos(p.prosrc,'m.email=NEW.target AND m.user_record_id=NEW.rule_record_id')>0)
            OR (p.oid=to_regprocedure('public.stewardship_security_content_v2(uuid,text,uuid)')
                AND NOT p.prosecdef AND p.provolatile='s'
                AND strpos(p.prosrc,'Signs in through Google account')>0
                AND strpos(p.prosrc,'e.replaced_account_email')>0)))<>2
       OR has_function_privilege('public',
          'public.stewardship_grant_match_v1()','EXECUTE') THEN
        RAISE EXCEPTION 'Migration 0204: a grant match function is not installed as declared';
    END IF;
END
$check$;

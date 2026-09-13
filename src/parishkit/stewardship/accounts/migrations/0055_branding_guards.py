"""Normalized asset receipts and retained YAML references fence media cleanup."""

from django.db import migrations

from parishkit.stewardship.storage_migrations import (
    immutable_guard_v1,
    mutable_guard_v1,
)

FORWARD = """
CREATE FUNCTION public.stewardship_branding_bundle_guard_v1()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    PERFORM pg_advisory_xact_lock(736230,1);
    IF TG_OP='INSERT' THEN
        NEW.created_at := clock_timestamp();
        NEW.updated_at := NEW.created_at;
        IF NEW.state<>'writing' OR NEW.version<>1
           OR NEW.actor_id IS DISTINCT FROM NEW.owner_id
           OR NEW.expires_at<=NEW.created_at
           OR NEW.expires_at>NEW.created_at+interval '24 hours' THEN
            RAISE EXCEPTION 'Invalid branding intake' USING ERRCODE='23514';
        END IF;
        RETURN NEW;
    END IF;
    IF NOT ((OLD.state='writing' AND NEW.state IN('ready','cleanup_pending'))
        OR (OLD.state='ready' AND NEW.state='cleanup_pending')
        OR (OLD.state='cleanup_pending' AND NEW.state='scrubbed')) THEN
        RAISE EXCEPTION 'Invalid branding transition' USING ERRCODE='23514';
    END IF;
    IF NEW.state='ready' AND (NEW.expires_at<=clock_timestamp()
       OR (SELECT count(*) FROM public.stewardship_branding_asset
           WHERE bundle_id=NEW.id)<>4) THEN
        RAISE EXCEPTION 'Branding bundle is incomplete or expired'
            USING ERRCODE='23514';
    END IF;
    IF NEW.state IN('cleanup_pending','scrubbed') THEN
        IF EXISTS (SELECT 1 FROM public.stewardship_parish p
            JOIN public.stewardship_branding_asset a ON
                a.id IN(p.large_logo_id,p.menu_logo_id,p.icon_logo_id,p.favicon_id)
            WHERE a.bundle_id=NEW.id) THEN
            RAISE EXCEPTION 'Retained configuration pins branding'
                USING ERRCODE='23514';
        END IF;
        IF EXISTS (SELECT 1 FROM public.stewardship_config_request r
            WHERE coalesce((SELECT c.state FROM public.stewardship_config_checkpoint c
                WHERE c.request_id=r.id ORDER BY c.sequence DESC LIMIT 1),'pending')
                NOT IN('applied','failed','cancelled')) THEN
            RAISE EXCEPTION 'Pending configuration blocks branding cleanup'
                USING ERRCODE='23514';
        END IF;
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER stewardship_branding_bundle_guard_v1
BEFORE INSERT OR UPDATE ON public.stewardship_branding_bundle
FOR EACH ROW EXECUTE FUNCTION public.stewardship_branding_bundle_guard_v1();

CREATE FUNCTION public.stewardship_branding_asset_insert_v1()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE selected public.stewardship_branding_bundle%ROWTYPE;
        maximum integer;
BEGIN
    PERFORM pg_advisory_xact_lock(736230,1);
    SELECT * INTO selected FROM public.stewardship_branding_bundle
        WHERE id=NEW.bundle_id;
    maximum := CASE NEW.label
        WHEN 'large' THEN 1024 WHEN 'favicon' THEN 32 ELSE 128 END;
    IF NOT FOUND OR selected.state<>'writing'
       OR selected.expires_at<=clock_timestamp()
       OR NEW.actor_id IS DISTINCT FROM selected.owner_id
       OR NEW.width>maximum OR NEW.height>maximum THEN
        RAISE EXCEPTION 'Invalid branding asset receipt' USING ERRCODE='23514';
    END IF;
    NEW.created_at := clock_timestamp();
    RETURN NEW;
END;
$$;
CREATE TRIGGER stewardship_branding_asset_insert_v1
BEFORE INSERT ON public.stewardship_branding_asset
FOR EACH ROW EXECUTE FUNCTION public.stewardship_branding_asset_insert_v1();

CREATE FUNCTION public.stewardship_parish_branding_v1()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE predecessor uuid; previous public.stewardship_parish%ROWTYPE;
        selected public.stewardship_branding_bundle%ROWTYPE;
        bundle_count integer; asset_count integer;
BEGIN
    SELECT predecessor_id INTO predecessor
        FROM public.stewardship_configuration_version WHERE id=NEW.configuration_id;
    -- Pre-feature root snapshots retain their historical UUID vocabulary.
    -- Operational bootstrap is separately restricted to bootstrap-policy-v1.
    IF predecessor IS NULL THEN RETURN NEW; END IF;
    SELECT * INTO previous FROM public.stewardship_parish
        WHERE configuration_id=predecessor;
    IF FOUND AND (NEW.large_logo_id,NEW.menu_logo_id,NEW.icon_logo_id,NEW.favicon_id)
        IS NOT DISTINCT FROM
        (previous.large_logo_id,previous.menu_logo_id,previous.icon_logo_id,
            previous.favicon_id) THEN RETURN NEW; END IF;
    PERFORM pg_advisory_xact_lock(736230,1);
    SELECT count(*),count(DISTINCT bundle_id) INTO asset_count,bundle_count
        FROM public.stewardship_branding_asset WHERE
            (id=NEW.large_logo_id AND label='large')
            OR (id=NEW.menu_logo_id AND label='menu')
            OR (id=NEW.icon_logo_id AND label='icon')
            OR (id=NEW.favicon_id AND label='favicon');
    IF asset_count<>4 OR bundle_count<>1 THEN
        RAISE EXCEPTION 'Parish branding requires a complete normalized bundle'
            USING ERRCODE='23514';
    END IF;
    SELECT b.* INTO selected FROM public.stewardship_branding_bundle b
        JOIN public.stewardship_branding_asset a ON a.bundle_id=b.id
        WHERE a.id=NEW.large_logo_id;
    IF selected.state<>'ready' THEN
        RAISE EXCEPTION 'Branding is not ready' USING ERRCODE='23514';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM public.stewardship_parish p
        JOIN public.stewardship_config_activation c
            ON c.configuration_id=p.configuration_id
        WHERE p.large_logo_id=NEW.large_logo_id) AND
        (selected.expires_at<=clock_timestamp() OR selected.base_id<>predecessor
         OR selected.owner_id IS DISTINCT FROM NEW.actor_id) THEN
        RAISE EXCEPTION 'Staged branding ownership or configuration changed'
            USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER stewardship_parish_branding_v1
BEFORE INSERT ON public.stewardship_parish
FOR EACH ROW EXECUTE FUNCTION public.stewardship_parish_branding_v1();
"""

BACKWARD = """
LOCK TABLE public.stewardship_branding_bundle IN ACCESS EXCLUSIVE MODE;
DO $$ BEGIN
    IF EXISTS(SELECT 1 FROM public.stewardship_branding_bundle) THEN
        RAISE EXCEPTION 'Branding history prevents downgrade' USING ERRCODE='23514';
    END IF;
END $$;
DROP TRIGGER stewardship_parish_branding_v1 ON public.stewardship_parish;
DROP FUNCTION public.stewardship_parish_branding_v1();
DROP TRIGGER stewardship_branding_asset_insert_v1 ON public.stewardship_branding_asset;
DROP FUNCTION public.stewardship_branding_asset_insert_v1();
DROP TRIGGER stewardship_branding_bundle_guard_v1 ON public.stewardship_branding_bundle;
DROP FUNCTION public.stewardship_branding_bundle_guard_v1();
"""


class Migration(migrations.Migration):
    dependencies = [
        ("stewardship_accounts", "0054_brandingbundle_brandingasset_and_more")
    ]
    operations = [
        mutable_guard_v1(
            "stewardship_branding_bundle",
            frozen_fields=(
                "owner_id",
                "session_id",
                "setup_attempt_id",
                "base_id",
                "expires_at",
            ),
        ),
        immutable_guard_v1("stewardship_branding_asset"),
        migrations.RunSQL(FORWARD, BACKWARD),
    ]

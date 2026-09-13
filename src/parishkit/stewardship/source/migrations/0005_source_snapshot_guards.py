"""Enforce permanent manifests, one current corpus and safe protection changes."""

from django.db import migrations

from parishkit.stewardship.storage_migrations import mutable_guard_v1


def seed_current(apps, schema_editor):
    """The initial current pointer is empty, not an invented source snapshot."""
    apps.get_model("stewardship_source", "SourceCurrent").objects.get_or_create(
        singleton=True
    )


FORWARD = """
CREATE FUNCTION stewardship_source_corpus_evidence(snapshot_id uuid)
RETURNS jsonb LANGUAGE plpgsql AS $$
DECLARE kind text;
        items jsonb;
        item_count bigint;
        manifest jsonb := '{}'::jsonb;
        counts jsonb := '{}'::jsonb;
        relation text[];
        invalid boolean;
BEGIN
    FOREACH kind IN ARRAY ARRAY['family','member','contact','address','ministry',
        'roster','fund','pledge','contribution'] LOOP
        EXECUTE format('SELECT COALESCE(jsonb_object_agg(m.source_key,p.digest),'
            '''{}''::jsonb),count(*) FROM %I m JOIN %I p ON p.id=m.payload_id '
            'WHERE m.snapshot_id=$1', 'stewardship_snapshot_'||kind,
            'stewardship_source_'||kind) INTO items,item_count USING snapshot_id;
        manifest := manifest || jsonb_build_object(kind,items);
        counts := counts || jsonb_build_object(kind,item_count);
    END LOOP;
    FOREACH relation SLICE 1 IN ARRAY ARRAY[
        ['member','family_key','family'], ['roster','member_key','member'],
        ['roster','ministry_key','ministry'], ['pledge','family_key','family'],
        ['pledge','fund_key','fund'], ['contribution','family_key','family'],
        ['contribution','fund_key','fund']
    ] LOOP
        EXECUTE format('SELECT EXISTS(SELECT 1 FROM %I m '
            'JOIN %I p ON p.id=m.payload_id '
            'WHERE m.snapshot_id=$1 AND NOT EXISTS(SELECT 1 FROM %I parent '
            'WHERE parent.snapshot_id=$1 AND parent.source_key=p.%I))',
            'stewardship_snapshot_'||relation[1], 'stewardship_source_'||relation[1],
            'stewardship_snapshot_'||relation[3], relation[2])
            INTO invalid USING snapshot_id;
        IF invalid THEN
            RAISE EXCEPTION 'Source corpus has unresolved relationships'
                USING ERRCODE='23514';
        END IF;
    END LOOP;
    FOREACH kind IN ARRAY ARRAY['contact','address'] LOOP
        EXECUTE format('SELECT EXISTS(SELECT 1 FROM %I m '
            'JOIN %I p ON p.id=m.payload_id '
            'LEFT JOIN stewardship_snapshot_family f ON f.snapshot_id=$1 '
            'AND f.source_key=p.owner_key AND p.owner_kind=''family'' '
            'LEFT JOIN stewardship_snapshot_member b ON b.snapshot_id=$1 '
            'AND b.source_key=p.owner_key AND p.owner_kind=''member'' '
            'WHERE m.snapshot_id=$1 AND f.id IS NULL AND b.id IS NULL)',
            'stewardship_snapshot_'||kind,'stewardship_source_'||kind)
            INTO invalid USING snapshot_id;
        IF invalid THEN
            RAISE EXCEPTION 'Source corpus has an unresolved contact owner'
                USING ERRCODE='23514';
        END IF;
    END LOOP;
    RETURN jsonb_build_object('counts', counts, 'digest', encode(sha256(convert_to(
        stewardship_source_canonical(manifest),'UTF8')),'hex'));
END;
$$;

CREATE FUNCTION stewardship_source_snapshot_guard()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE current_source stewardship_source_current%ROWTYPE;
        evidence jsonb;
BEGIN
    IF TG_OP='DELETE' THEN
        RAISE EXCEPTION 'Source manifests are permanent' USING ERRCODE='23514';
    END IF;
    IF TG_OP='INSERT' THEN
        IF NEW.state <> 'staging' OR NEW.completed_at IS NOT NULL
           OR NEW.content_digest <> '' OR NEW.compacted_at IS NOT NULL THEN
            RAISE EXCEPTION 'Source snapshots must begin in staging'
                USING ERRCODE='23514';
        END IF;
    ELSIF OLD.state='promoted' THEN
        IF (to_jsonb(NEW)-ARRAY['version','updated_at','actor_id','correlation_id',
                'compacted_at']) IS DISTINCT FROM
           (to_jsonb(OLD)-ARRAY['version','updated_at','actor_id','correlation_id',
                'compacted_at']) OR OLD.compacted_at IS NOT NULL
           OR NEW.compacted_at IS NULL THEN
            RAISE EXCEPTION 'Promoted source manifests are immutable'
                USING ERRCODE='23514';
        END IF;
        IF EXISTS (SELECT 1 FROM stewardship_source_current WHERE snapshot_id=OLD.id)
           OR EXISTS (SELECT 1 FROM stewardship_source_pin WHERE snapshot_id=OLD.id
                AND (expires_at IS NULL OR expires_at > clock_timestamp()))
           OR NOT EXISTS (SELECT 1 FROM stewardship_source_lease l
                JOIN stewardship_task_run t ON t.id=l.owner_id
                WHERE l.phase='compaction' AND l.expires_at > clock_timestamp()
                  AND t.state='running' AND t.fence=l.task_fence
                  AND t.worker_id=l.worker_id
                  AND t.lease_expires_at > clock_timestamp())
        THEN
            RAISE EXCEPTION 'Source snapshot is protected from compaction'
                USING ERRCODE='23514';
        END IF;
        RETURN NEW;
    ELSIF NOT ((OLD.state='staging' AND NEW.state IN ('staging','ready','rejected'))
        OR (OLD.state='ready' AND NEW.state IN ('promoted','rejected'))) THEN
        RAISE EXCEPTION 'Source snapshot transition is invalid' USING ERRCODE='23514';
    END IF;
    IF TG_OP='UPDATE' AND OLD.state='ready' AND (
        NEW.counts IS DISTINCT FROM OLD.counts
        OR NEW.content_digest IS DISTINCT FROM OLD.content_digest
        OR NEW.cursor IS DISTINCT FROM OLD.cursor
        OR NEW.validation IS DISTINCT FROM OLD.validation
    ) THEN
        RAISE EXCEPTION 'Validated source evidence is immutable' USING ERRCODE='23514';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM stewardship_source_lease l
        JOIN stewardship_task_run t ON t.id=l.owner_id
        WHERE l.owner_id=NEW.task_id AND l.fence=NEW.source_fence AND l.phase=NEW.kind
          AND l.expires_at > clock_timestamp() AND t.state='running'
          AND t.fence=l.task_fence AND t.worker_id=l.worker_id
          AND t.lease_expires_at > clock_timestamp()) THEN
        RAISE EXCEPTION 'Source snapshot requires its live fenced owner'
            USING ERRCODE='23514';
    END IF;
    IF NEW.state IN ('ready','promoted') AND (
        NEW.completed_at IS NULL OR NEW.completed_at < NEW.started_at
        OR NEW.completed_at > clock_timestamp() OR NEW.content_digest=''
        OR NEW.validation->>'schema' IS DISTINCT FROM 'source-corpus-v1'
        OR NEW.validation->'complete' IS DISTINCT FROM 'true'::jsonb
    ) THEN
        RAISE EXCEPTION 'Source snapshot is not validated' USING ERRCODE='23514';
    END IF;
    IF NEW.state='promoted' THEN
        SELECT * INTO current_source FROM stewardship_source_current
            WHERE singleton FOR UPDATE;
        IF NOT FOUND OR NEW.generation <> current_source.generation+1
           OR NEW.base_id IS DISTINCT FROM current_source.snapshot_id
           OR (current_source.organization_id IS NOT NULL AND
               NEW.organization_id <> current_source.organization_id)
           OR NEW.promoted_at < NEW.completed_at OR NEW.promoted_at > clock_timestamp()
        THEN
            RAISE EXCEPTION 'Source promotion has a stale or inconsistent base'
                USING ERRCODE='23514';
        END IF;
    END IF;
    IF NEW.state='ready' THEN
        evidence := stewardship_source_corpus_evidence(NEW.id);
        IF NEW.counts IS DISTINCT FROM evidence->'counts'
           OR NEW.content_digest IS DISTINCT FROM evidence->>'digest' THEN
            RAISE EXCEPTION 'Source evidence differs from its complete corpus'
                USING ERRCODE='23514';
        END IF;
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER source_snapshot_guard BEFORE INSERT OR UPDATE OR DELETE
ON stewardship_source_snapshot FOR EACH ROW
EXECUTE FUNCTION stewardship_source_snapshot_guard();

CREATE FUNCTION stewardship_source_current_guard()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP='INSERT' THEN
        IF NEW.snapshot_id IS NOT NULL THEN
            RAISE EXCEPTION 'Source current pointer must start empty'
                USING ERRCODE='23514';
        END IF;
        RETURN NEW;
    END IF;
    IF TG_OP='DELETE' THEN
        RAISE EXCEPTION 'Source current pointer cannot be deleted'
            USING ERRCODE='23514';
    END IF;
    IF NEW.generation <> OLD.generation+1
       OR (OLD.organization_id IS NOT NULL AND
           NEW.organization_id IS DISTINCT FROM OLD.organization_id)
       OR NOT EXISTS (SELECT 1 FROM stewardship_source_snapshot s
            WHERE s.id=NEW.snapshot_id AND s.state='promoted' AND s.compacted_at IS NULL
              AND s.generation=NEW.generation AND s.organization_id=NEW.organization_id
              AND s.base_id IS NOT DISTINCT FROM OLD.snapshot_id) THEN
        RAISE EXCEPTION 'Source pointer requires the next coherent promotion'
            USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER source_current_guard BEFORE INSERT OR UPDATE OR DELETE
ON stewardship_source_current FOR EACH ROW
EXECUTE FUNCTION stewardship_source_current_guard();

CREATE FUNCTION stewardship_source_promotion_pair_guard()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.state <> 'promoted' AND NEW.state='promoted' AND NOT EXISTS (
        SELECT 1 FROM stewardship_source_current
        WHERE snapshot_id=NEW.id AND generation=NEW.generation
    ) THEN
        RAISE EXCEPTION 'Source promotion and pointer must commit together'
            USING ERRCODE='23514';
    END IF;
    RETURN NULL;
END;
$$;
CREATE CONSTRAINT TRIGGER source_promotion_pair_guard
AFTER UPDATE ON stewardship_source_snapshot DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION stewardship_source_promotion_pair_guard();

CREATE FUNCTION stewardship_source_pin_guard()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE snapshot stewardship_source_snapshot%ROWTYPE;
BEGIN
    IF TG_OP='DELETE' THEN
        PERFORM 1 FROM stewardship_source_snapshot WHERE id=OLD.snapshot_id FOR UPDATE;
        RETURN OLD;
    END IF;
    SELECT * INTO snapshot FROM stewardship_source_snapshot WHERE id=NEW.snapshot_id
        FOR UPDATE;
    IF NOT FOUND OR snapshot.state <> 'promoted'
       OR snapshot.compacted_at IS NOT NULL THEN
        RAISE EXCEPTION 'Only reconstructable promoted snapshots can be protected'
            USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER source_pin_guard BEFORE INSERT OR UPDATE OR DELETE
ON stewardship_source_pin FOR EACH ROW EXECUTE FUNCTION stewardship_source_pin_guard();
"""

REVERSE = """
DROP TRIGGER source_pin_guard ON stewardship_source_pin;
DROP FUNCTION stewardship_source_pin_guard();
DROP TRIGGER source_promotion_pair_guard ON stewardship_source_snapshot;
DROP FUNCTION stewardship_source_promotion_pair_guard();
DROP TRIGGER source_current_guard ON stewardship_source_current;
DROP FUNCTION stewardship_source_current_guard();
DROP TRIGGER source_snapshot_guard ON stewardship_source_snapshot;
DROP FUNCTION stewardship_source_snapshot_guard();
DROP FUNCTION stewardship_source_corpus_evidence(uuid);
"""


class Migration(migrations.Migration):
    dependencies = [("stewardship_source", "0004_source_payload_guards")]
    operations = [
        mutable_guard_v1(
            "stewardship_source_snapshot",
            frozen_fields=(
                "organization_id",
                "kind",
                "task_id",
                "source_fence",
                "base_id",
                "started_at",
            ),
            write_once_fields=(
                "completed_at",
                "promoted_at",
                "generation",
                "compacted_at",
            ),
        ),
        mutable_guard_v1("stewardship_source_current", frozen_fields=("singleton",)),
        mutable_guard_v1(
            "stewardship_source_pin",
            frozen_fields=("snapshot_id", "parent_kind", "parent_id"),
        ),
        migrations.RunSQL(FORWARD, REVERSE),
        migrations.RunPython(seed_current, migrations.RunPython.noop),
    ]

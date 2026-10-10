-- stewardship_source_corpus_evidence as functions.sql defined it before
-- migration 0044 (#629), for tests that compare the two definitions. Test
-- data only: never installed by a migration.
CREATE FUNCTION public.stewardship_source_corpus_evidence(snapshot_id uuid) RETURNS jsonb
    LANGUAGE plpgsql
    AS $_$
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
$_$;

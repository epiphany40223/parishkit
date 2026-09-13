"""Read-only, value-free PostgreSQL schema fingerprints for baseline validation."""

import hashlib
import json

QUERIES = {
    "relations": """
        SELECT c.relname, c.relkind, c.relrowsecurity, c.relforcerowsecurity,
               c.relpersistence, c.reloptions,
               CASE WHEN c.relkind IN ('v','m') THEN pg_get_viewdef(c.oid, true) END,
               pg_get_userbyid(c.relowner),
               COALESCE(c.relacl, acldefault(
                   CASE WHEN c.relkind='S' THEN 'S'::"char" ELSE 'r'::"char" END,
                   c.relowner))::text,
               s.seqtypid::regtype::text, s.seqstart, s.seqincrement, s.seqmax,
               s.seqmin, s.seqcache, s.seqcycle
        FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        LEFT JOIN pg_sequence s ON s.seqrelid=c.oid
        WHERE n.nspname='public' AND c.relname LIKE 'stewardship_%'
              AND c.relkind IN ('r','v','S','p','m','f')
    """,
    "columns": """
        SELECT c.relname || '.' || a.attname,
               format_type(a.atttypid,a.atttypmod), a.attnotnull,
               a.attidentity, a.attgenerated, co.collname,
               pg_get_expr(d.adbin,d.adrelid),
               row_number() OVER (PARTITION BY c.oid ORDER BY a.attnum)
        FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid
        JOIN pg_namespace n ON n.oid=c.relnamespace
        LEFT JOIN pg_attrdef d ON d.adrelid=c.oid AND d.adnum=a.attnum
        LEFT JOIN pg_collation co ON co.oid=a.attcollation
        WHERE n.nspname='public' AND c.relname LIKE 'stewardship_%'
          AND c.relkind IN ('r','v','p','m','f')
          AND a.attnum>0 AND NOT a.attisdropped
    """,
    "constraints": """
        SELECT c.relname || '.' || k.conname, k.contype, k.convalidated,
               k.condeferrable, k.condeferred, pg_get_constraintdef(k.oid, true)
        FROM pg_constraint k JOIN pg_class c ON c.oid=k.conrelid
        JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='public' AND c.relname LIKE 'stewardship_%'
    """,
    "indexes": """
        SELECT i.relname, pg_get_indexdef(i.oid), x.indisvalid, x.indisready
        FROM pg_index x JOIN pg_class i ON i.oid=x.indexrelid
        JOIN pg_class c ON c.oid=x.indrelid
        JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='public' AND c.relname LIKE 'stewardship_%'
    """,
    "functions": """
        SELECT p.proname || '(' || pg_get_function_identity_arguments(p.oid) || ')',
               pg_get_functiondef(p.oid), pg_get_userbyid(p.proowner),
               COALESCE(p.proacl,acldefault('f',p.proowner))::text,
               EXISTS (SELECT 1 FROM aclexplode(
                   COALESCE(p.proacl,acldefault('f',p.proowner))) a
                   WHERE a.grantee=0 AND a.privilege_type='EXECUTE')
        FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='public' AND p.proname LIKE 'stewardship_%'
    """,
    "triggers": """
        SELECT c.relname || '.' || t.tgname, t.tgenabled, pg_get_triggerdef(t.oid, true)
        FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid
        JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='public' AND c.relname LIKE 'stewardship_%'
          AND NOT t.tgisinternal
    """,
    "policies": """
        SELECT c.relname || '.' || p.polname, p.polcmd, p.polpermissive,
               ARRAY(SELECT CASE WHEN role=0 THEN 'PUBLIC'
                     ELSE pg_get_userbyid(role)::text END
                     FROM unnest(p.polroles) role ORDER BY 1),
               pg_get_expr(p.polqual,p.polrelid),
               pg_get_expr(p.polwithcheck,p.polrelid)
        FROM pg_policy p JOIN pg_class c ON c.oid=p.polrelid
        JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='public' AND c.relname LIKE 'stewardship_%'
    """,
}


def inventory(connection):
    """Fingerprint definitions, never table contents, credentials or object OIDs."""
    result = {}
    with connection.cursor() as cursor:
        for kind, query in QUERIES.items():
            cursor.execute(query)
            objects = {}
            for name, *definition in cursor.fetchall():
                if name in objects:
                    raise ValueError("Duplicate schema inventory identity")
                encoded = json.dumps(definition, separators=(",", ":")).encode()
                objects[name] = hashlib.sha256(encoded).hexdigest()
            result[kind] = dict(sorted(objects.items()))
    return result

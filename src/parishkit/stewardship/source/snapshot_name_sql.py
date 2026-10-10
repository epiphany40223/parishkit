"""The shown Family name ("Squyres, Tracy and Jeff") built in SQL, to sort by.

``snapshot_names.snapshot_family_names`` names the Families of one page in
Python. A table that sorts its whole inventory by that name in the database
uses ``SnapshotFamilyName`` instead: the same string, per row, as a Django
expression. ``schema/directory_reports.sql`` builds it the same way for the
Family codes directory.
"""

from django.db.models import Func, TextField

from .family_names import WHITESPACE

# The SQL twin of snapshot_names.snapshot_family_names for one Family: the
# surname (family_display_name's lastName, mailingName, "first last", then
# default choice), then the active heads in DUID order as family_heads_name
# joins them. It is the same expression directory_reports.sql uses to order
# the directory. An active_head_duids that is not a JSON array counts as no
# heads instead of failing the whole query. The placeholders are, in order:
# the WHITESPACE sets ({shown} holds two when it is the whole name, then the
# surname four), the default, the snapshot id, then the compiled Family DUID
# expression's own parameters.
FAMILY_NAME_SQL = """(SELECT {shown} FROM (
    SELECT sf.snapshot_id,d.document,coalesce(
        nullif(btrim(d.document->>'lastName',%s),''),
        nullif(btrim(d.document->>'mailingName',%s),''),
        nullif(concat_ws(' ',nullif(btrim(d.document->>'firstName',%s),''),
            nullif(btrim(d.document->>'lastName',%s),'')),''),
        %s) AS surname
    FROM stewardship_snapshot_family sf
    JOIN stewardship_source_family fp ON fp.id=sf.payload_id
    CROSS JOIN LATERAL (SELECT fp.canonical::jsonb) d(document)
    WHERE sf.snapshot_id=%s AND sf.source_key=({duid})::text
) f)"""

HEADS_SQL = """f.surname||coalesce(', '||(
    SELECT CASE WHEN cardinality(n.parts)<3 THEN array_to_string(n.parts,' and ')
        ELSE array_to_string(n.parts[1:cardinality(n.parts)-1],', ')
            ||' and '||n.parts[cardinality(n.parts)] END
    FROM (SELECT array_agg(p.part ORDER BY h.head::bigint)
            FILTER (WHERE p.part<>'') AS parts
        FROM jsonb_array_elements_text(CASE
            WHEN jsonb_typeof(f.document->'active_head_duids')='array'
            THEN f.document->'active_head_duids' ELSE '[]'::jsonb END) h(head)
        JOIN stewardship_snapshot_member sm
            ON sm.snapshot_id=f.snapshot_id AND sm.source_key=h.head
        JOIN stewardship_source_member mp ON mp.id=sm.payload_id
        CROSS JOIN LATERAL (SELECT mp.canonical::jsonb) m(value)
        CROSS JOIN LATERAL (SELECT
            btrim(coalesce(m.value->>'firstName',''),%s),
            btrim(coalesce(m.value->>'lastName',''),%s)) t(first,last)
        CROSS JOIN LATERAL (SELECT CASE WHEN t.last=f.surname THEN t.first
            ELSE concat_ws(' ',nullif(t.first,''),nullif(t.last,'')) END) p(part)
        WHERE m.value->'active'='true'::jsonb) n),'')"""


class SnapshotFamilyName(Func):
    """One Family's name from a snapshot, built in SQL to search or sort by.

    ``duid`` is the Family DUID column (or expression); the result is the
    string ``snapshot_family_names`` shows ("Squyres, Tracy and Jeff"), or
    with ``surname_only`` just its leading surname, so a table can order by
    surname and then by the whole name as the Family codes directory does.
    ``default`` stands in for a blank surname, as in ``snapshot_family_names``,
    and is bound as a parameter. A DUID missing from the snapshot gives NULL.
    """

    output_field = TextField()

    def __init__(self, snapshot_id, duid, *, default="", surname_only=False):
        super().__init__(duid)
        self.snapshot_id = snapshot_id
        self.default = default
        self.surname_only = surname_only

    def as_sql(self, compiler, connection, **extra_context):
        """Compile the DUID, then bind the trim sets, default and snapshot id."""
        duid, params = compiler.compile(self.get_source_expressions()[0])
        shown = "f.surname" if self.surname_only else HEADS_SQL
        sql = FAMILY_NAME_SQL.format(shown=shown, duid=duid)
        # The surname source trims four values; the heads part two more.
        trims = 4 if self.surname_only else 6
        return sql, (*[WHITESPACE] * trims, self.default, self.snapshot_id, *params)

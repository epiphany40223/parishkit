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
# joins them, through the shared functions directory_reports.sql also uses
# (stewardship_family_head_part_v1 and stewardship_family_heads_name_v1,
# migration 0194). An active_head_duids that is not a JSON array counts as no
# heads instead of failing the whole query. The placeholders are, in order:
# the surname's four WHITESPACE sets, the default, the snapshot id, then the
# compiled Family DUID expression's own parameters.
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

HEADS_SQL = """stewardship_family_heads_name_v1(f.surname,ARRAY(
    SELECT stewardship_family_head_part_v1(mp.canonical::jsonb,f.surname)
    FROM jsonb_array_elements_text(CASE
        WHEN jsonb_typeof(f.document->'active_head_duids')='array'
        THEN f.document->'active_head_duids' END) h(head)
    JOIN stewardship_snapshot_member sm
        ON sm.snapshot_id=f.snapshot_id AND sm.source_key=h.head
    JOIN stewardship_source_member mp ON mp.id=sm.payload_id
    ORDER BY h.head::bigint))"""


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
        # The surname source trims four values; the shared head functions
        # trim with their own set (stewardship_name_strip_v1).
        return sql, (*[WHITESPACE] * 4, self.default, self.snapshot_id, *params)

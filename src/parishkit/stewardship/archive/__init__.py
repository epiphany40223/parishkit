"""Campaign archive workbooks: format, reader, checks and the offline command (#1018).

``workbook`` owns the format table, the strict reader and the minimal
writer; ``package`` is the zip and XML safety layer the reader applies first;
``check`` runs the reader in an isolated helper process (``check_worker``)
and implements ``pk-stewardship archive-check``.
"""

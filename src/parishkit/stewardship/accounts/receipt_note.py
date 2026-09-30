"""The retired receipt closing note (#260), folded into the confirmation email.

A receipt email used to be the confirmation email followed by a separately
edited ``submission_confirmation`` page block, so Admins edited one email in
two places. The confirmation email body now holds all of it, and no new block
can be authored (see ``content_schema``).

Configurations applied before the change may still carry a block. Such a
block stays structurally valid, because applied history must keep verifying,
and receipts keep appending it exactly as before, so no Admin text is lost or
changed. The confirmation email editor opens with the block already folded
into the body; saving that email (or cloning the campaign) stores the folded
body and drops the block. The storage kind and slot names stay allowed in the
schema, which is frozen for launch.
"""

RETIRED = ("page", "submission_confirmation")


def legacy_note(records, campaign_id):
    """The campaign's retired closing-note record, or None.

    ``records`` are canonical content records (``{"id", "values"}``).
    """
    return next(
        (
            row
            for row in records
            if row["values"]["campaign_id"] == str(campaign_id)
            and (row["values"]["kind"], row["values"]["slot"]) == RETIRED
        ),
        None,
    )


def fold(email, note, *, campaign_id):
    """Confirmation email values with the closing note appended.

    Joins the parts with ``fold_parts``, exactly as ``render_receipt`` renders
    an email with a note, so a folded email sends the same receipt the pair
    did. Without a confirmation email, the note follows the built-in receipt
    template that receipts fall back to.
    """
    from parishkit.stewardship.jobs.receipt_content import ReceiptTemplate, fold_parts

    if email is None:
        fallback = ReceiptTemplate()
        email = {
            "campaign_id": str(campaign_id),
            "kind": "email",
            "slot": "confirmation",
            "subject": fallback.subject,
            "html": fallback.html,
            "text": fallback.text,
        }
    html, text = fold_parts(email["html"], email["text"], note["html"], note["text"])
    return email | {"html": html, "text": text}

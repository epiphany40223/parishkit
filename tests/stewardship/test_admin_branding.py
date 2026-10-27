"""Parish logos commands: ``branding preview`` and ``branding confirm`` (#1865).

Pure tests: the printed sizes, built through the projection the command
uses from a stand-in review; the page's form as the command binds it; the
standard-input reader; the catalog entries; the ledger; and usage errors.
The commands against a real database are in
database/test_admin_branding_cli_postgresql.py.
"""

import io
import json
from types import SimpleNamespace
from uuid import UUID

import pytest

from parishkit.stewardship import admin_branding, admin_cli
from parishkit.stewardship.accounts.branding_slots import SLOTS
from parishkit.stewardship.admin_parity import LEDGER
from parishkit.stewardship.audit.log_descriptions import DESCRIPTIONS
from parishkit.stewardship.web.content import MAX_IMAGE_BYTES

from .test_admin_reads import FORBIDDEN

VERSION = "b" * 64
BUNDLE = "00000000-0000-4000-8000-0000000000b1"


def test_the_spelled_out_constants_are_the_pages():
    """The parser's sizes and the reader's limit stay the page's own."""
    assert tuple(SLOTS) == admin_branding.SIZES
    assert admin_branding.MAX_IMAGE_BYTES == MAX_IMAGE_BYTES


def test_logo_fields_print_every_size_in_the_pages_order():
    """Each staged size's stored facts, its label and whether it changes."""
    assets = [
        SimpleNamespace(
            label=label,
            pk=UUID(int=index + 1),
            width=10 * (index + 1),
            height=5,
            size=100 + index,
            sha256="c" * 64,
            changed=label == "menu",
        )
        # The page's review lists the assets by label; the command prints
        # them in the page's own size order.
        for index, label in enumerate(sorted(SLOTS))
    ]
    logos = admin_branding.logo_fields({"assets": assets})
    assert [logo["slot"] for logo in logos] == list(SLOTS)
    by_slot = {asset.label: asset for asset in assets}
    for logo in logos:
        asset = by_slot[logo["slot"]]
        assert logo == {
            "slot": asset.label,
            "name": str(SLOTS[asset.label].title),
            "id": asset.pk,
            "width": asset.width,
            "height": asset.height,
            "size": asset.size,
            "sha256": asset.sha256,
            "changed": asset.label == "menu",
        }


@pytest.mark.parametrize("size", [None, "favicon"])
def test_logo_form_binds_the_pages_own_post(size):
    """Basic posts the image as ``logo``; Advanced under its own size."""
    form = admin_branding.logo_form(b"image", size, VERSION)
    assert form.data == {
        "base_digest": VERSION,
        "editor_mode": "basic" if size is None else "advanced",
    }
    [(name, upload)] = form.files.items()
    assert name == (size or "logo")
    # The name is server-owned; the bytes are exactly what was read.
    assert upload.name == "upload" and upload.read() == b"image"


def test_logo_form_refuses_a_large_image_in_the_pages_words():
    """More than the limit reaches the form, which refuses it as the page."""
    data = admin_branding.read_image(io.BytesIO(b"x" * (MAX_IMAGE_BYTES + 10)))
    assert len(data) == MAX_IMAGE_BYTES + 1
    form = admin_branding.logo_form(data, None, VERSION)
    assert not form.is_valid()
    assert form.has_error("logo", "too_large")


def test_read_image_refuses_empty_input():
    """No bytes after the preamble is invalid input, never a blank upload."""
    with pytest.raises(ValueError):
        admin_branding.read_image(io.BytesIO(b""))


def test_the_catalog_entries():
    """Full scope and state-changing, as the page's upload and Apply; no
    prompt and no recent sign-in. Each records its own described event."""
    entries = {entry["name"]: entry for entry in admin_cli.catalog()}
    preview, confirm = entries["branding preview"], entries["branding confirm"]
    for entry in (preview, confirm):
        assert entry["scope"] == "full" and entry["pr"] == 10
        assert entry["changes_state"]
        assert not entry["prompts"] and not entry["fresh_gated"]
    assert preview["expected_version"] and not confirm["expected_version"]
    assert preview["arguments"] == ["-"] and confirm["arguments"] == []
    assert preview["audit_event"] == "admin_cmd_branding_preview"
    assert confirm["audit_event"] == "admin_cmd_branding_confirm"
    for event in (preview["audit_event"], confirm["audit_event"]):
        assert event in DESCRIPTIONS
    assert preview["result_fields"] == list(
        admin_branding.BrandingPreview.field_names()
    )
    assert confirm["result_fields"] == list(
        admin_branding.BrandingConfirm.field_names()
    )
    options = {option["name"]: option for option in preview["options"]}
    assert options["--expected-version"]["required"]
    assert not options["--size"]["required"]
    options = {option["name"]: option for option in confirm["options"]}
    assert options["--bundle"]["required"] and options["--token"]["required"]


def test_the_documents_print_only_allowed_names():
    """The preview prints stored facts and the token, never image bytes."""
    names = set(admin_branding.BrandingPreview.field_names())
    assert names == {"version", "bundle", "mode", "logos", "preview"}
    logo = {"slot", "name", "id", "width", "height", "size", "sha256", "changed"}
    for name in names | logo | set(admin_branding.BrandingConfirm.field_names()):
        assert FORBIDDEN.search(name) is None, name


def test_the_ledger_covers_the_page():
    """Parish logos is covered by its read, its review and its Apply; the
    image bytes stay a permanent exemption."""
    parity = LEDGER["branding_settings"]
    assert parity.kind == "command" and parity.owed is None
    assert parity.detail == ("branding show", "branding preview", "branding confirm")
    assert LEDGER["branding_asset"].kind == "permanent"


@pytest.mark.parametrize(
    "argv",
    [
        ["branding", "preview", "--expected-version", VERSION],
        ["branding", "preview", "logo.png", "--expected-version", VERSION],
        ["branding", "preview", "-"],
        ["branding", "preview", "-", "--expected-version", "nope"],
        ["branding", "preview", "-", "--size", "banner", "--expected-version", VERSION],
        ["branding", "confirm", "--token", "-"],
        ["branding", "confirm", "--bundle", BUNDLE],
        ["branding", "confirm", "--bundle", "nope", "--token", "-"],
    ],
)
def test_malformed_commands_are_usage_errors(argv):
    """The preview needs ``-``, a known size and the version; the confirm a
    bundle id and the token."""
    out = io.StringIO()
    code = admin_cli.main(
        [*argv, "--config", "web.yaml", "--session-stdin"],
        stdin=io.BytesIO(b""),
        stdout=out,
        stderr=out,
    )
    assert code == 2 and json.loads(out.getvalue())["error"]["code"] == "usage"


def staging(monkeypatch, effect):
    """Run ``preview_branding`` with the page's staging replaced by ``effect``.

    ``effect(record)`` stands in for ``stage_upload``; admission and the
    audit write are stand-ins, so only the command's own mapping runs.
    Returns the command's ``context``.
    """
    from parishkit.stewardship import admin_changes
    from parishkit.stewardship.accounts import branding_views
    from parishkit.stewardship.audit import services

    monkeypatch.setattr(
        admin_changes, "_admit_change", lambda *a, **k: SimpleNamespace(identity=1)
    )
    monkeypatch.setattr(services, "record_action", lambda *a, **k: None)
    monkeypatch.setattr(
        branding_views,
        "stage_upload",
        lambda caller, service, form, *, record: effect(record),
    )
    return {}


def test_a_database_failure_after_the_event_may_have_committed(monkeypatch):
    """Once the event was written, a database failure is outcome unknown."""
    from django.db import DatabaseError

    def effect(record):
        """Write the event, then fail as a commit could."""
        record()
        raise DatabaseError("commit")

    context = staging(monkeypatch, effect)
    caller = SimpleNamespace(automation_session_id=UUID(BUNDLE))
    with pytest.raises(DatabaseError):
        admin_branding.preview_branding(
            caller, None, b"x", size=None, expected_version=VERSION, context=context
        )
    assert context["committed"] is True


@pytest.mark.parametrize("failure", [OSError("disk"), "config"])
def test_unusable_storage_or_configuration_is_unavailable(monkeypatch, failure):
    """Media that cannot be used, or a configuration that cannot be read,
    is ``unavailable`` (the page's 503), with nothing committed."""
    from parishkit.config import ConfigError
    from parishkit.stewardship.admin_reads import Unavailable

    error = ConfigError("media") if failure == "config" else failure

    def effect(record):
        """Fail before anything is staged."""
        raise error

    context = staging(monkeypatch, effect)
    with pytest.raises(Unavailable):
        admin_branding.preview_branding(
            SimpleNamespace(),
            None,
            b"x",
            size=None,
            expected_version=VERSION,
            context=context,
        )
    assert "committed" not in context


def test_logo_frames_are_one_per_size_in_size_order():
    """Parish logos draws one frame per size, in size order, whatever is
    saved (#2279): a missing size has no asset and an unknown label is
    ignored, so the current logos keep one height."""
    from parishkit.stewardship.accounts.branding_views import logo_frames

    menu = SimpleNamespace(label="menu")
    frames = logo_frames([SimpleNamespace(label="other"), menu])
    assert [frame["title"] for frame in frames] == [s.title for s in SLOTS.values()]
    assert [frame["asset"] for frame in frames] == [None, None, menu, None]
    assert all(frame["asset"] is None for frame in logo_frames([]))


def test_logo_sizes_run_smallest_to_largest_whatever_the_row_order():
    """The four sizes run favicon, icon, menu logo, large logo, by their
    LOGO_SIZES bounds (#2288), and the current logos' frames keep that
    order whatever order the saved rows come in."""
    from parishkit.stewardship.accounts.branding_views import logo_frames
    from parishkit.stewardship.web.content import LOGO_SIZES

    assert list(SLOTS) == ["favicon", "icon", "menu", "large"]
    assert list(SLOTS) == sorted(
        LOGO_SIZES, key=lambda name: LOGO_SIZES[name][0] * LOGO_SIZES[name][1]
    )
    rows = [SimpleNamespace(label=name) for name in ("large", "menu", "icon")]
    rows.append(SimpleNamespace(label="favicon"))
    for ordered in (rows, rows[::-1], sorted(rows, key=lambda row: row.label)):
        frames = logo_frames(ordered)
        assert [str(frame["title"]) for frame in frames] == [
            "Favicon",
            "Icon",
            "Menu logo",
            "Large logo",
        ]
        assert [frame["asset"].label for frame in frames] == list(SLOTS)


def test_every_logo_field_names_its_own_preview_box():
    """Basic's one field and each Advanced size field name the chosen-image
    box drawn above them, by the field's id plus "_preview" (#2279)."""
    from parishkit.stewardship.accounts.branding_views import LogoUploadForm

    form = LogoUploadForm()
    for name in ("logo", *SLOTS):
        widget = form.fields[name].widget
        assert widget.attrs["data-preview-target"] == f"id_{name}_preview"

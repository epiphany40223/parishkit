"""Large weekly report envelopes do not expand any other delivery contract."""

from dataclasses import asdict, replace
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from parishkit.stewardship.jobs.digest_content import (
    DigestTemplate,
    render_digest_envelope,
)
from parishkit.stewardship.jobs.outbox_validation import (
    DeliveryIdentity,
    RenderInput,
    WeeklyRenderInput,
    validate_render_purpose,
)
from parishkit.stewardship.reports.weekly_digest import (
    WeeklyDigestDocument,
    WeeklyInformation,
    render_weekly_digest,
)
from parishkit.stewardship.web.weekly_digest_content import MAX_WEEKLY_BODY_BYTES

from .test_outbox_validation import rendering


def identity(purpose="weekly_digest", *, testing=False):
    """Only the intended purpose chooses the larger compiled report boundary."""
    campaign = uuid4()
    return DeliveryIdentity(
        scope_id=campaign,
        campaign_id=campaign,
        semantic_key=uuid4(),
        mode="testing" if testing else "production",
        routing="operational"
        if purpose in {"operational", "security_event"}
        else "testing_override"
        if testing
        else "production",
        purpose=purpose,
        family_id=uuid4() if purpose in {"initial", "reminder", "receipt"} else None,
    )


@pytest.mark.parametrize("testing", [False, True])
def test_five_thousand_family_report_survives_the_actual_journal_envelope(testing):
    selected = identity(testing=testing)
    instant = datetime(2026, 9, 17, tzinfo=UTC)
    document = WeeklyDigestDocument(
        uuid4(),
        selected.campaign_id,
        "Parish",
        "Campaign",
        "America/New_York",
        instant,
        tuple(
            WeeklyInformation(
                UUID(int=i),
                i,
                f"Household {i}",
                instant,
                "Please call about this request. " * 10,
            )
            for i in range(1, 5001)
        ),
        (),
    )
    compiled = render_weekly_digest(document, public_origin="https://parish.example")
    assert len(compiled.html.encode()) > 1_048_576
    result = render_digest_envelope(
        identity=selected,
        configuration_id=uuid4(),
        template_id=None,
        template=DigestTemplate("Weekly campaign report"),
        content=compiled,
        values={},
        sender="parish@example.org",
        recipient="admin@example.org",
        testing_recipient="test@example.org" if testing else None,
    )
    assert type(result) is WeeklyRenderInput
    assert result.html.endswith(compiled.html) and result.text.endswith(compiled.text)
    assert "Household 5000" in result.text
    assert len(result.html.encode()) <= MAX_WEEKLY_BODY_BYTES
    assert result.routed_recipients == (
        ("test@example.org",) if testing else ("admin@example.org",)
    )
    validate_render_purpose(selected, result)
    assert set(result.fields()) == set(rendering().fields())


@pytest.mark.parametrize("field", ["html", "text"])
def test_weekly_capacity_is_bounded_in_utf8_bytes(field):
    arguments = asdict(rendering())
    value = WeeklyRenderInput(
        **(arguments | {field: "é" * (MAX_WEEKLY_BODY_BYTES // 2)})
    )
    assert len(getattr(value, field).encode()) == MAX_WEEKLY_BODY_BYTES
    with pytest.raises(ValueError, match="delivery body"):
        replace(value, **{field: getattr(value, field) + "a"})
    with pytest.raises(ValueError, match="delivery body"):
        RenderInput(**(arguments | {field: "x" * 1_048_577}))


@pytest.mark.parametrize(
    "purpose",
    ["initial", "reminder", "receipt", "daily_digest", "operational", "security_event"],
)
def test_weekly_type_cannot_bind_to_another_purpose(purpose):
    value = WeeklyRenderInput(**asdict(rendering()))
    with pytest.raises(ValueError, match="weekly delivery identity"):
        validate_render_purpose(identity(purpose), value)


@pytest.mark.parametrize("render_type", [RenderInput, WeeklyRenderInput])
def test_private_render_fields_do_not_appear_in_repr(render_type):
    value = render_type(**asdict(rendering(text="PRIVATE-REPORT-CANARY")))
    assert "PRIVATE-REPORT-CANARY" not in repr(value)
    assert "member@example.org" not in repr(value)

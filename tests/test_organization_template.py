from pathlib import Path

import pytest

from calendar_pedagoga.organization_template import (
    ORG_TEMPLATE_UNSUPPORTED_MESSAGE,
    CalendarTemplateSource,
    OrganizationTemplateError,
    select_calendar_template,
    validate_organization_template,
)


REFERENCES = Path(__file__).resolve().parents[1] / "references"
VALID_SAMPLE = REFERENCES / "Календарный план Образец.docx"
FILLED_SAMPLE = REFERENCES / "календарный план ТП 2 г.об. 4 ч.docx"


def test_standard_template_is_selected_when_upload_is_absent() -> None:
    selection = select_calendar_template()
    assert selection.source is CalendarTemplateSource.STANDARD
    assert selection.filename is None
    assert selection.content is None
    assert not selection.uses_organization_template


def test_compatible_empty_organization_template_is_accepted() -> None:
    content = VALID_SAMPLE.read_bytes()
    selection = select_calendar_template("Организация.docx", content)
    assert selection.source is CalendarTemplateSource.ORGANIZATION
    assert selection.filename == "Организация.docx"
    assert selection.content == content
    assert selection.uses_organization_template


def test_filled_organization_template_is_rejected() -> None:
    with pytest.raises(OrganizationTemplateError, match="не поддерживается"):
        select_calendar_template(
            "filled.docx",
            FILLED_SAMPLE.read_bytes(),
        )


def test_validate_organization_template_message_is_user_friendly() -> None:
    with pytest.raises(OrganizationTemplateError) as error:
        validate_organization_template(FILLED_SAMPLE.read_bytes())
    assert error.value.args[0] == ORG_TEMPLATE_UNSUPPORTED_MESSAGE


@pytest.mark.parametrize(
    ("filename", "content_kind"),
    [
        ("Организация.docx", "missing"),
        (None, "content_only"),
        ("Организация.pdf", "valid"),
        ("Организация.docx", "empty"),
    ],
)
def test_invalid_organization_template_upload_is_rejected(
    filename: str | None, content_kind: str
) -> None:
    if content_kind == "missing":
        content = None
    elif content_kind == "content_only":
        content = VALID_SAMPLE.read_bytes()
    elif content_kind == "valid":
        content = VALID_SAMPLE.read_bytes()
    else:
        content = b""
    with pytest.raises((ValueError, OrganizationTemplateError)):
        select_calendar_template(filename, content)

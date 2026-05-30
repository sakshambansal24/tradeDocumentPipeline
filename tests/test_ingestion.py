import base64
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from nova.ingestion import DocumentLoader, UnreadableDocumentError


def test_load_clean_pdf_returns_prepared_pages(tmp_path: Path) -> None:
    pdf_path = tmp_path / "invoice.pdf"
    image = Image.new("RGB", (900, 1200), "white")
    draw = ImageDraw.Draw(image)
    draw.text((80, 100), "Commercial Invoice", fill="black")
    draw.text((80, 160), "Invoice No: INV-001", fill="black")
    image.save(pdf_path, "PDF", resolution=100)

    loaded = DocumentLoader(dpi=150).load(pdf_path)

    assert loaded.source_filename == "invoice.pdf"
    assert loaded.page_count == 1
    assert len(loaded.pages) == 1
    assert loaded.original_bytes_hash
    assert loaded.pages[0].page_number == 1
    assert loaded.pages[0].width > 0
    assert loaded.pages[0].height > 0
    assert loaded.pages[0].dpi == 150
    assert loaded.pages[0].quality_score >= 0.0
    assert base64.b64decode(loaded.pages[0].base64_image)


def test_load_rotated_image_corrects_orientation_and_warns(tmp_path: Path) -> None:
    image_path = tmp_path / "rotated.png"
    image = Image.new("RGB", (1600, 1000), "white")
    draw = ImageDraw.Draw(image)
    draw.text((80, 80), "Bill of Lading", fill="black")
    image.save(image_path)

    loaded = DocumentLoader().load(image_path)
    page = loaded.pages[0]

    assert page.height > page.width
    assert page.rotation_applied_deg == 90
    assert "landscape_page_rotated_to_portrait" in page.warnings


def test_zero_byte_file_raises_unreadable_document_error(tmp_path: Path) -> None:
    empty_path = tmp_path / "empty.pdf"
    empty_path.write_bytes(b"")

    with pytest.raises(UnreadableDocumentError, match="empty"):
        DocumentLoader().load(empty_path)

"""Tests for build_batch_receipt_prompt (services/receipts.py)."""

import pytest

from app import config as config_mod
from app.services import receipts


@pytest.mark.asyncio
async def test_batch_prompt_has_n_labeled_sections(monkeypatch, tmp_path):
    monkeypatch.setattr(config_mod.config, "data_dir", tmp_path)

    async def fake_extract(image_bytes, mime):
        return f"OCR:{mime}"
    monkeypatch.setattr(receipts.vision, "extract_text", fake_extract)

    files = [
        (b"\xff\xd8\xff", "image/jpeg"),
        (b"\x89PNG", "image/png"),
        (b"\xff\xd8\xff", "image/jpeg"),
    ]
    prompt = await receipts.build_batch_receipt_prompt("trip receipts", files)

    assert "receipt 1" in prompt
    assert "receipt 2" in prompt
    assert "receipt 3" in prompt
    assert "OCR:image/jpeg" in prompt
    assert "OCR:image/png" in prompt
    assert "trip receipts" in prompt
    # three distinct files saved to disk
    saved = list((tmp_path / "receipts").glob("receipt-*"))
    assert len(saved) == 3


@pytest.mark.asyncio
async def test_batch_prompt_ocr_failure_is_inline_not_fatal(monkeypatch, tmp_path):
    monkeypatch.setattr(config_mod.config, "data_dir", tmp_path)

    calls = {"n": 0}

    async def flaky_extract(image_bytes, mime):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("OCR service down")
        return "Coffee 4.50"
    monkeypatch.setattr(receipts.vision, "extract_text", flaky_extract)

    files = [(b"\xff\xd8\xff", "image/jpeg"), (b"\xff\xd8\xff", "image/jpeg")]
    prompt = await receipts.build_batch_receipt_prompt("", files)

    assert "OCR failed" in prompt
    assert "OCR service down" in prompt
    assert "Coffee 4.50" in prompt
    # both files still saved despite the first OCR failure
    saved = list((tmp_path / "receipts").glob("receipt-*"))
    assert len(saved) == 2


@pytest.mark.asyncio
async def test_batch_prompt_instructs_single_confirm_then_per_item_record(monkeypatch, tmp_path):
    monkeypatch.setattr(config_mod.config, "data_dir", tmp_path)

    async def fake_extract(image_bytes, mime):
        return "text"
    monkeypatch.setattr(receipts.vision, "extract_text", fake_extract)

    prompt = await receipts.build_batch_receipt_prompt(
        "", [(b"\xff\xd8\xff", "image/jpeg")])

    assert "record_transaction" in prompt
    assert "confirm_duplicate" in prompt


@pytest.mark.asyncio
async def test_batch_prompt_pdf_file_renders_pages(monkeypatch, tmp_path):
    import fitz

    monkeypatch.setattr(config_mod.config, "data_dir", tmp_path)

    async def fake_extract(image_bytes, mime):
        return "PDF page text"
    monkeypatch.setattr(receipts.vision, "extract_text", fake_extract)

    doc = fitz.open()
    doc.new_page()
    pdf_bytes = doc.tobytes()
    doc.close()

    prompt = await receipts.build_batch_receipt_prompt(
        "", [(pdf_bytes, "application/pdf")])

    assert "PDF page text" in prompt
    previews = list((tmp_path / "receipts").glob("*.preview.png"))
    assert len(previews) == 1

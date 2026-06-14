import fitz  # PyMuPDF

from app.services import receipts


def _one_page_pdf(text="Coffee $4.50") -> bytes:
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), text)
    data = doc.tobytes()
    doc.close()
    return data


def test_pdf_to_page_images_returns_png_per_page():
    pages = receipts._pdf_to_page_images(_one_page_pdf())
    assert len(pages) == 1
    assert pages[0][:8] == b"\x89PNG\r\n\x1a\n"   # PNG magic


import pytest

from app import config as config_mod


@pytest.mark.asyncio
async def test_pdf_receipt_ocrs_pages_and_stores_preview(monkeypatch, tmp_path):
    monkeypatch.setattr(config_mod.config, "data_dir", tmp_path)

    async def fake_extract(image_bytes, mime):
        return "Coffee 4.50"
    monkeypatch.setattr(receipts.vision, "extract_text", fake_extract)

    prompt = await receipts.build_receipt_prompt("", _one_page_pdf(), "application/pdf")

    pdfs = list(tmp_path.glob("receipts/*.pdf"))
    assert len(pdfs) == 1
    previews = list(tmp_path.glob("receipts/*.preview.png"))
    assert len(previews) == 1
    assert "Coffee 4.50" in prompt
    assert str(pdfs[0]) in prompt

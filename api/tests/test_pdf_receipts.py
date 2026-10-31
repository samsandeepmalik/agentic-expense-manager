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


def _n_page_pdf(n: int) -> bytes:
    doc = fitz.open()
    for i in range(n):
        page = doc.new_page()
        page.insert_text((72, 72), f"page {i + 1}")
    data = doc.tobytes()
    doc.close()
    return data


@pytest.mark.asyncio
async def test_pdf_pages_are_ocrd_concurrently_not_sequentially(monkeypatch, tmp_path):
    """A multi-page PDF used to OCR pages one at a time — a 10-page PDF took
    10x one page's latency. Pages must be sent to the OCR provider in
    parallel (bounded), so wall time stays close to ONE page's latency."""
    import asyncio

    monkeypatch.setattr(config_mod.config, "data_dir", tmp_path)

    async def slow_extract(image_bytes, mime):
        await asyncio.sleep(0.2)
        return "text"
    monkeypatch.setattr(receipts.vision, "extract_text", slow_extract)

    start = asyncio.get_event_loop().time()
    await receipts.save_and_ocr(_n_page_pdf(6), "application/pdf")
    elapsed = asyncio.get_event_loop().time() - start

    # Sequential would take ~1.2s (6 x 0.2s); concurrent stays near 0.2-0.4s.
    assert elapsed < 0.8


@pytest.mark.asyncio
async def test_pdf_page_ocr_concurrency_is_bounded(monkeypatch, tmp_path):
    """Pages OCR concurrently but not unboundedly — cap peak concurrent OCR
    calls so a many-page PDF doesn't hammer the provider's rate limit."""
    import asyncio

    monkeypatch.setattr(config_mod.config, "data_dir", tmp_path)

    current = 0
    peak = 0

    async def tracked_extract(image_bytes, mime):
        nonlocal current, peak
        current += 1
        peak = max(peak, current)
        await asyncio.sleep(0.05)
        current -= 1
        return "text"
    monkeypatch.setattr(receipts.vision, "extract_text", tracked_extract)

    await receipts.save_and_ocr(_n_page_pdf(10), "application/pdf")

    assert peak <= receipts._MAX_CONCURRENT_OCR
    assert peak > 1  # actually ran concurrently, not serialized down to 1

import pytest
import pymupdf as fitz

from backend.app.services.pdf_extractor import (
    PdfCorruptedError,
    PdfExtractorService,
    PdfPasswordProtectedError,
    PdfScannedError,
    PdfValidationError,
)


def _generate_test_pdf(pages_text: list[str]) -> bytes:
    """Helper to generate a clean multi-page PDF in-memory."""
    doc = fitz.open()
    for text in pages_text:
        page = doc.new_page()
        if text:
            page.insert_text((50, 72), text)
    pdf_bytes = doc.tobytes()
    doc.close()
    return pdf_bytes


def test_extract_valid_multipage_pdf():
    """Verify multi-page PDF extraction with 1-based page numbering and order preservation."""
    page_texts = [
        "First page content with important facts.",
        "Second page content with methodology details.",
        "Third page content with concluding results.",
    ]
    pdf_bytes = _generate_test_pdf(page_texts)

    result = PdfExtractorService.extract_text(pdf_bytes)

    assert result.total_pages == 3
    assert len(result.pages) == 3
    assert result.is_scanned is False

    # Check 1-based page numbers and content
    for idx, page in enumerate(result.pages):
        assert page.page_number == idx + 1
        assert page.is_blank is False
        assert page.char_count > 0
        assert page_texts[idx] in page.text


def test_extract_pdf_with_blank_page():
    """Verify handling of blank pages without crashing."""
    page_texts = [
        "First page has text.",
        "",  # Blank page
        "Third page has text.",
    ]
    pdf_bytes = _generate_test_pdf(page_texts)

    result = PdfExtractorService.extract_text(pdf_bytes)

    assert result.total_pages == 3
    assert result.pages[0].is_blank is False
    assert result.pages[1].is_blank is True
    assert result.pages[1].char_count == 0
    assert result.pages[2].is_blank is False


def test_extract_scanned_image_only_pdf_fails():
    """Verify scanned / image-only PDFs with zero extractable text are flagged as PdfScannedError."""
    # A PDF with 2 completely blank pages simulates a scanned document without OCR
    pdf_bytes = _generate_test_pdf(["", ""])

    with pytest.raises(PdfScannedError) as exc_info:
        PdfExtractorService.extract_text(pdf_bytes)

    assert "Scanned image-only PDFs without an embedded text layer are not supported" in str(exc_info.value)


def test_extract_empty_bytes_fails():
    """Verify empty input fails with PdfValidationError."""
    with pytest.raises(PdfValidationError) as exc_info:
        PdfExtractorService.extract_text(b"")

    assert "File is empty" in str(exc_info.value)


def test_extract_invalid_magic_bytes_fails():
    """Verify files without %PDF- magic signature fail validation."""
    with pytest.raises(PdfValidationError) as exc_info:
        PdfExtractorService.extract_text(b"NOT_A_PDF_FILE_HEADER")

    assert "Invalid PDF file signature" in str(exc_info.value)


def test_extract_corrupted_pdf_structure_fails():
    """Verify corrupted PDF bytes fail with PdfCorruptedError."""
    with pytest.raises(PdfCorruptedError):
        PdfExtractorService.extract_text(b"%PDF-1.4 but completely garbage binary content \x00\xff\xfe\x12")


def test_extract_password_protected_pdf_fails():
    """Verify encrypted or password-protected PDFs fail with PdfPasswordProtectedError."""
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((50, 72), "Confidential encrypted text.")
    # Encrypt PDF
    encrypted_bytes = doc.tobytes(
        encryption=fitz.PDF_ENCRYPT_AES_256,
        owner_pw="secret_owner",
        user_pw="secret_user",
    )
    doc.close()

    with pytest.raises(PdfPasswordProtectedError) as exc_info:
        PdfExtractorService.extract_text(encrypted_bytes)

    assert "Encrypted or password-protected PDFs are not supported" in str(exc_info.value)


def test_extraction_determinism():
    """Verify identical input bytes produce strictly identical extraction results."""
    text = "ProofLens AI determinism test.\n\nEnsuring consistent outputs for all extraction runs."
    pdf_bytes = _generate_test_pdf([text])

    res1 = PdfExtractorService.extract_text(pdf_bytes)
    res2 = PdfExtractorService.extract_text(pdf_bytes)

    assert res1.total_pages == res2.total_pages
    assert res1.total_characters == res2.total_characters
    assert [p.text for p in res1.pages] == [p.text for p in res2.pages]


def test_text_normalization():
    """Verify whitespace and line ending normalization preserves paragraph breaks."""
    raw = "Line 1\r\nLine 2\rLine 3   \n\n\n\nParagraph 2\x0c"
    normalized = PdfExtractorService.normalize_text(raw)

    assert "\r" not in normalized
    assert "\x0c" not in normalized
    assert "Line 1\nLine 2\nLine 3\n\nParagraph 2" == normalized

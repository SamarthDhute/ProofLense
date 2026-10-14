import re
from dataclasses import dataclass
from typing import List
import pymupdf as fitz

from backend.app.core.config import settings


class PdfExtractionException(Exception):
    """Base exception for PDF extraction failures."""
    pass


class PdfValidationError(PdfExtractionException):
    """Raised when PDF file structure or constraints fail validation."""
    pass


class PdfPasswordProtectedError(PdfExtractionException):
    """Raised when PDF is encrypted or requires a password."""
    pass


class PdfCorruptedError(PdfExtractionException):
    """Raised when PDF bytes cannot be parsed or are malformed."""
    pass


class PdfScannedError(PdfExtractionException):
    """Raised when PDF contains only scanned images with no extractable text."""
    pass


@dataclass(frozen=True)
class ExtractedPage:
    page_number: int  # 1-based index
    text: str
    char_count: int
    word_count: int
    is_blank: bool


@dataclass(frozen=True)
class ExtractionResult:
    total_pages: int
    pages: List[ExtractedPage]
    total_characters: int
    total_words: int
    is_scanned: bool


class PdfExtractorService:
    """Deterministic, resource-safe PDF text extraction service using PyMuPDF."""

    @staticmethod
    def normalize_text(raw_text: str) -> str:
        """Normalize line endings, whitespace artifacts, and paragraph boundaries without altering semantic content."""
        if not raw_text:
            return ""

        # Normalize carriage returns and form feeds
        normalized = raw_text.replace("\r\n", "\n").replace("\r", "\n").replace("\x0c", "\n")

        # Strip non-breaking spaces or replace with normal space
        normalized = normalized.replace("\u00a0", " ").replace("\u200b", "")

        # Clean trailing whitespace on every line
        lines = [line.rstrip() for line in normalized.split("\n")]

        # Join and collapse excessive consecutive blank lines (allow max 2 newlines for paragraph breaks)
        cleaned = "\n".join(lines)
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)

        return cleaned.strip()

    @classmethod
    def validate_pdf_bytes(cls, pdf_bytes: bytes) -> None:
        """Validate PDF header, non-emptiness, and basic structure before parsing."""
        if not pdf_bytes or len(pdf_bytes) == 0:
            raise PdfValidationError("File is empty (0 bytes).")

        # Validate magic byte signature
        if not pdf_bytes.startswith(b"%PDF-"):
            # Check if %PDF- exists in first 1024 bytes (some files have slight leading bytes)
            first_chunk = pdf_bytes[:1024]
            if b"%PDF-" not in first_chunk:
                raise PdfValidationError("Invalid PDF file signature. Expected '%PDF-' magic header.")

    @classmethod
    def extract_text(cls, pdf_bytes: bytes) -> ExtractionResult:
        """Extract text page-by-page from raw PDF bytes deterministically.
        
        Guarantees:
        - 1-based page numbers matching original PDF.
        - Preserves page ordering.
        - Closes all file handles reliably.
        - Never logs document contents.
        - Detects scanned / image-only PDFs without OCR.
        """
        cls.validate_pdf_bytes(pdf_bytes)

        doc: fitz.Document = None
        try:
            try:
                doc = fitz.open(stream=pdf_bytes, filetype="pdf")
            except Exception as exc:
                raise PdfCorruptedError(f"Malformed or corrupted PDF structure: {str(exc)}") from exc

            # Validate encryption / password protection
            if doc.is_encrypted or doc.needs_pass:
                raise PdfPasswordProtectedError("Encrypted or password-protected PDFs are not supported.")

            total_pages = doc.page_count
            if total_pages == 0:
                raise PdfValidationError("PDF document contains 0 pages.")

            if total_pages > settings.MAX_EXTRACTION_PAGES:
                raise PdfValidationError(
                    f"PDF page count ({total_pages}) exceeds allowed limit ({settings.MAX_EXTRACTION_PAGES})."
                )

            extracted_pages: List[ExtractedPage] = []
            total_chars = 0
            total_words = 0

            for page_idx in range(total_pages):
                page = doc.load_page(page_idx)
                raw_page_text = page.get_text("text")

                normalized = cls.normalize_text(raw_page_text)
                char_count = len(normalized)
                words = normalized.split() if normalized else []
                word_count = len(words)
                is_blank = char_count == 0

                page_number = page_idx + 1  # 1-based

                extracted_pages.append(
                    ExtractedPage(
                        page_number=page_number,
                        text=normalized,
                        char_count=char_count,
                        word_count=word_count,
                        is_blank=is_blank,
                    )
                )

                total_chars += char_count
                total_words += word_count

            # Check if document has no extractable text (scanned PDF without OCR)
            if total_chars == 0:
                raise PdfScannedError(
                    "Document contains no extractable text across all pages. "
                    "Scanned image-only PDFs without an embedded text layer are not supported."
                )

            return ExtractionResult(
                total_pages=total_pages,
                pages=extracted_pages,
                total_characters=total_chars,
                total_words=total_words,
                is_scanned=False,
            )

        finally:
            if doc is not None:
                try:
                    doc.close()
                except Exception:
                    pass

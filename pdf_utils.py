# Place at: utils/pdf_utils.py
"""
utils/pdf_utils.py
PDF text extraction with PyMuPDF table reconstruction.

WHY THIS EXISTS:
  PyMuPDF get_text() explodes table columns into separate lines:
    Haemoglobin          ← test_name
    G%                   ← unit
    13.00 - 17.00        ← reference_range
    15.7                 ← test_value  ← SEPARATED FROM ITS NAME

  This is completely unusable for AI structured extraction.
  We use page.find_tables() to reconstruct rows as readable DataFrames
  BEFORE sending to Gemini.

  Result modes per page:
    "table+text"  → find_tables() succeeded → structured rows + plain text
    "text"        → enough plain text, no tables detected
    "ocr"         → page is scanned image → Tesseract OCR
"""
from __future__ import annotations

import io
import logging
from typing import List, Tuple

import fitz
from PIL import Image

logger = logging.getLogger(__name__)

_MIN_TEXT_CHARS = 60  # below this → treat as scanned, run OCR


def _reconstruct_page_tables(page: fitz.Page) -> str:
    """
    Use PyMuPDF's built-in table finder to reconstruct table rows.
    Returns markdown-style string, or "" if no tables found.
    """
    parts: List[str] = []
    try:
        finder = page.find_tables()
        if not (finder and finder.tables):
            return ""
        for tbl in finder.tables:
            try:
                df = tbl.to_pandas().fillna("").astype(str)
                # Drop rows that are entirely empty
                df = df.loc[~(df == "").all(axis=1)]
                if not df.empty:
                    parts.append(df.to_string(index=False))
                    logger.debug(
                        "Table reconstructed: %d rows × %d cols",
                        len(df), len(df.columns)
                    )
            except Exception as e:
                logger.debug("Table parse skipped: %s", e)
    except Exception as e:
        logger.debug("find_tables failed on this page: %s", e)
    return "\n".join(parts)


def extract_pdf_text(raw_bytes: bytes) -> Tuple[str, List[str]]:
    """
    Extract full text from PDF bytes with table reconstruction.

    Returns:
        (combined_text, modes_per_page)

    Logs:
        - Total pages
        - Mode used per page (table+text / text / ocr)
        - Total extracted text length
        - How many pages used each mode
    """
    from utils.ocr_utils import ocr_pil_image

    doc = fitz.open(stream=raw_bytes, filetype="pdf")
    pages: List[str] = []
    modes: List[str] = []

    logger.info("PDF extraction START: %d pages", len(doc))

    for i in range(len(doc)):
        page = doc[i]
        plain = (page.get_text() or "").strip()
        tables = _reconstruct_page_tables(page)

        if tables:
            # Best case: structured table rows + supplementary plain text
            combined = f"--- Page {i+1} ---\n{tables}\n\n{plain}"
            pages.append(combined)
            modes.append("table+text")
            logger.debug("Page %d: table+text (%d table chars, %d plain chars)",
                         i+1, len(tables), len(plain))

        elif len(plain) >= _MIN_TEXT_CHARS:
            # Good plain text, no tables
            pages.append(f"--- Page {i+1} ---\n{plain}")
            modes.append("text")
            logger.debug("Page %d: text (%d chars)", i+1, len(plain))

        else:
            # Scanned page — rasterize at 2× and OCR
            logger.info("Page %d: SCANNED — triggering OCR (plain text only %d chars)",
                        i+1, len(plain))
            pix = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
            img = Image.open(io.BytesIO(pix.tobytes("png")))
            ocr_text = ocr_pil_image(img)
            pages.append(f"--- Page {i+1} ---\n{ocr_text}")
            modes.append("ocr")
            logger.debug("Page %d: OCR produced %d chars", i+1, len(ocr_text))

    doc.close()
    combined = "\n\n".join(pages).strip()

    mode_counts = {m: modes.count(m) for m in set(modes)}
    logger.info(
        "PDF extraction DONE: total_chars=%d pages=%d mode_counts=%s",
        len(combined), len(modes), mode_counts
    )
    return combined, modes

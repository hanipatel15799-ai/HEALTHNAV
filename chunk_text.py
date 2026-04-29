from __future__ import annotations

import hashlib
import logging
import os
import re
from pathlib import Path
from typing import Dict, List

import fitz  # PyMuPDF
import pandas as pd

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO").upper(), format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

PDF_FOLDER = Path(os.getenv("PDF_FOLDER", "data/medical_pdfs"))
OUTPUT_FILE = Path(os.getenv("CHUNKS_OUTPUT_FILE", "data/chunks.csv"))
CHUNK_SIZE = int(os.getenv("CHUNK_SIZE", "1200"))
OVERLAP = int(os.getenv("CHUNK_OVERLAP", "150"))
MIN_CHUNK_CHARS = int(os.getenv("MIN_CHUNK_CHARS", "80"))


def clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def extract_pages(file_path: Path) -> List[Dict[str, object]]:
    doc = fitz.open(file_path)
    pages: List[Dict[str, object]] = []
    try:
        for page_no, page in enumerate(doc, start=1):
            text = clean_text(page.get_text("text"))
            if text:
                pages.append({"page_number": page_no, "text": text})
    finally:
        doc.close()
    return pages


def chunk_page_text(text: str) -> List[str]:
    if len(text) <= CHUNK_SIZE:
        return [text] if len(text) >= MIN_CHUNK_CHARS else []

    chunks: List[str] = []
    start = 0
    while start < len(text):
        target_end = min(start + CHUNK_SIZE, len(text))
        if target_end < len(text):
            window = text[start:min(target_end + 120, len(text))]
            sentence_breaks = [m.end() for m in re.finditer(r"[.!?]\s+", window)]
            if sentence_breaks:
                target_end = start + sentence_breaks[-1]

        chunk = clean_text(text[start:target_end])
        if len(chunk) >= MIN_CHUNK_CHARS:
            chunks.append(chunk)

        if target_end >= len(text):
            break
        start = max(target_end - OVERLAP, start + 1)

    return chunks


def chunk_id(source_file: str, page_number: int, chunk_index: int, chunk_text: str) -> str:
    payload = f"{source_file}|{page_number}|{chunk_index}|{chunk_text}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def main() -> None:
    if not PDF_FOLDER.exists():
        raise FileNotFoundError(f"PDF folder not found: {PDF_FOLDER}")

    rows: List[Dict[str, object]] = []
    pdf_files = sorted(PDF_FOLDER.glob("*.pdf"))
    if not pdf_files:
        raise FileNotFoundError(f"No PDFs found in {PDF_FOLDER}")

    for pdf_path in pdf_files:
        logger.info("Processing %s", pdf_path.name)
        for page in extract_pages(pdf_path):
            page_chunks = chunk_page_text(str(page["text"]))
            for idx, chunk in enumerate(page_chunks):
                rows.append(
                    {
                        "chunk_id": chunk_id(pdf_path.name, int(page["page_number"]), idx, chunk),
                        "source_file": pdf_path.name,
                        "page_number": int(page["page_number"]),
                        "chunk_index": idx,
                        "chunk_text": chunk,
                        "char_count": len(chunk),
                    }
                )

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(OUTPUT_FILE, index=False)
    logger.info("Saved %s chunks to %s", len(rows), OUTPUT_FILE)


if __name__ == "__main__":
    main()

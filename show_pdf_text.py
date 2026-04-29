"""
show_pdf_text.py
================
Run this in your NEW HEALTH folder:
    python show_pdf_text.py

It will print the EXACT text your PDF produces so we can fix the parser.
Copy the full output and share it.
"""
import sys, os, re
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

# Find the PDF
upload_dir = Path("Uploads")
pdf_path = None

if upload_dir.exists():
    for folder in sorted(upload_dir.iterdir(), reverse=True):
        if folder.is_dir():
            for f in folder.iterdir():
                if f.suffix.lower() == ".pdf" and "blood" in f.name.lower():
                    pdf_path = f
                    break
        if pdf_path:
            break

    if not pdf_path:
        for folder in sorted(upload_dir.iterdir(), reverse=True):
            if folder.is_dir():
                for f in folder.iterdir():
                    if f.suffix.lower() == ".pdf":
                        pdf_path = f
                        break
            if pdf_path:
                break

if not pdf_path:
    pdfs = list(Path(".").glob("*.pdf"))
    if pdfs:
        pdf_path = pdfs[0]

if not pdf_path:
    print("No PDF found. Enter path manually:")
    path_str = input("Path: ").strip().strip('"')
    pdf_path = Path(path_str)

print(f"\nPDF: {pdf_path}")
print(f"Size: {pdf_path.stat().st_size:,} bytes\n")

import fitz
raw = pdf_path.read_bytes()
doc = fitz.open(stream=raw, filetype="pdf")
print(f"Pages: {len(doc)}\n")
print("=" * 70)
print("RAW TEXT (repr — shows EXACT characters including spaces):")
print("=" * 70)

full = []
for i in range(min(len(doc), 5)):  # first 5 pages
    text = doc[i].get_text("text") or ""
    full.append(text)
    print(f"\n--- PAGE {i+1} ({len(text)} chars) ---")
    # Show each line with its exact content
    for j, line in enumerate(text.split('\n')[:60]):
        if line.strip():
            # Count leading spaces
            spaces = len(line) - len(line.lstrip())
            print(f"  L{j:02d}|{line}|")

doc.close()

print("\n" + "=" * 70)
print("LINES WITH NUMBERS (potential lab rows):")
print("=" * 70)
for page_text in full:
    for line in page_text.split('\n'):
        ls = line.strip()
        if ls and any(c.isdigit() for c in ls) and len(ls) > 5:
            # Show with exact spacing
            print(f"  |{ls}|")

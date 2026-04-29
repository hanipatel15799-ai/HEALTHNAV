"""
diagnose_extraction.py
======================
Run this directly on your blood report PDF to see EXACTLY what's happening.

Usage:
    cd "C:\\Users\\hani1\\Downloads\\NEW HEALTH"
    python diagnose_extraction.py

It will:
  1. Extract text from the PDF
  2. Show you the EXACT text sent to Gemini
  3. Show you Gemini's EXACT raw response
  4. Show what the regex pre-parser finds
  5. Tell you precisely why 0 labs are extracted

No server restart needed.
"""

import sys
import os
import json
import re
from pathlib import Path

# Self-heal path
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from dotenv import load_dotenv
load_dotenv()

print("\n" + "="*65)
print("  HealthNav Extraction Diagnostics")
print("="*65)

# ── Find the PDF ──────────────────────────────────────────────────────────────

# Look for blood report in Uploads folder
pdf_path = None
uploads_dir = _HERE / "Uploads"

# Search all patient subfolders
if uploads_dir.exists():
    for folder in uploads_dir.iterdir():
        if folder.is_dir():
            for f in folder.iterdir():
                if f.suffix.lower() == ".pdf":
                    pdf_path = f
                    print(f"\nFound PDF: {pdf_path}")
                    break
        if pdf_path:
            break

if not pdf_path:
    # Try current folder
    pdfs = list(_HERE.glob("*.pdf"))
    if pdfs:
        pdf_path = pdfs[0]
        print(f"\nFound PDF in root: {pdf_path}")

if not pdf_path:
    print("\nNo PDF found automatically.")
    path_input = input("Enter full path to your blood report PDF: ").strip().strip('"')
    pdf_path = Path(path_input)
    if not pdf_path.exists():
        print(f"File not found: {pdf_path}")
        sys.exit(1)

raw_bytes = pdf_path.read_bytes()
print(f"File size: {len(raw_bytes):,} bytes")

# ── Stage 1: Extract text ─────────────────────────────────────────────────────
print("\n" + "-"*65)
print("STAGE 1: PDF TEXT EXTRACTION")
print("-"*65)

import fitz
doc = fitz.open(stream=raw_bytes, filetype="pdf")
print(f"Pages: {len(doc)}")

all_text = []
for i in range(len(doc)):
    page = doc[i]
    text = (page.get_text("text") or "").strip()
    print(f"\n  Page {i+1}: {len(text)} chars")
    if len(text) > 0:
        print(f"  First 3 lines:")
        for line in text.split('\n')[:3]:
            if line.strip():
                print(f"    |{line}|")
    all_text.append(text)

doc.close()
full_text = "\n\n".join(f"--- Page {i+1} ---\n{t}" for i,t in enumerate(all_text))
total_chars = len(full_text)
print(f"\nTotal extracted text: {total_chars:,} chars")

print("\n" + "="*65)
print("EXTRACTED TEXT (first 2000 chars):")
print("="*65)
print(full_text[:2000])
print("...")

# ── Stage 2: Date detection ───────────────────────────────────────────────────
print("\n" + "-"*65)
print("STAGE 2: DATE DETECTION")
print("-"*65)

date_patterns = [
    r"(?:Sample\s*Date|Report\s*Date|Date)[^0-9\n]{0,15}(\d{1,2}-[A-Za-z]{3}-\d{4})",
    r"(\d{1,2}-[A-Za-z]{3}-\d{4})",
    r"(\d{4}-\d{2}-\d{2})",
]
fallback_date = None
for pat in date_patterns:
    m = re.search(pat, full_text, re.IGNORECASE)
    if m:
        from datetime import datetime
        for fmt in ["%d-%b-%Y", "%Y-%m-%d"]:
            try:
                fallback_date = datetime.strptime(m.group(1), fmt).strftime("%Y-%m-%d")
                print(f"  Detected date: {fallback_date} (from pattern: {pat[:40]})")
                break
            except ValueError:
                continue
        if fallback_date:
            break

if not fallback_date:
    fallback_date = "2024-11-23"
    print(f"  No date found — using default: {fallback_date}")

# ── Stage 3: Regex pre-parser ─────────────────────────────────────────────────
print("\n" + "-"*65)
print("STAGE 3: REGEX PRE-PARSER (space-aligned columns)")
print("-"*65)

LAB_LINE = re.compile(
    r"^(?P<name>[A-Za-z][A-Za-z0-9 ,\-\(\)\/\.%]{2,60?}?)"
    r"\s{2,}"
    r"(?P<value>[<>≤≥]?\s*\d[\d,\.\s]*)"
    r"\s*(?P<flag>\*?[HhLl]{1,2}\*?)?"
    r"\s{1,}"
    r"(?P<unit>[A-Za-z%µΜ\/\.\*]{1,20})"
    r"(?:\s{2,}(?P<ref>[\d\.,<>≤≥\s\-–]+))?",
    re.MULTILINE,
)

SKIP = {"test name", "investigation", "parameter", "analyte", "examination",
        "test", "result", "unit", "reference", "bio ref", "bio. ref", "status"}

regex_rows = []
print("\n  Lines matched by regex:")
for line in full_text.split("\n"):
    line_s = line.strip()
    if not line_s or len(line_s) < 8:
        continue
    if line_s.lower().strip() in SKIP:
        continue
    m = LAB_LINE.match(line_s)
    if m:
        name = m.group("name").strip()
        value = m.group("value").strip().replace(",", ".")
        flag = (m.group("flag") or "").replace("*", "").upper()
        unit = m.group("unit").strip()
        ref = (m.group("ref") or "").strip()
        is_abn = flag in {"H", "HH", "L", "LL"}
        row = {"test_name": name, "test_value": value, "unit": unit,
               "reference_range": ref, "is_abnormal": is_abn,
               "test_date": fallback_date}
        regex_rows.append(row)
        print(f"    ✓ {name:<35} = {value:<10} {unit:<15} [{ref}] {'⚠ ABN' if is_abn else ''}")

print(f"\n  Regex found: {len(regex_rows)} lab rows")

if len(regex_rows) == 0:
    print("\n  ⚠ REGEX FOUND NOTHING. Showing raw lines to understand format:")
    print("  (Look for lines that have test names and values)")
    count = 0
    for line in full_text.split("\n"):
        line_s = line.strip()
        if line_s and len(line_s) > 10 and any(c.isdigit() for c in line_s):
            print(f"    |{line_s}|")
            count += 1
            if count > 30:
                break

# ── Stage 4: Gemini call ──────────────────────────────────────────────────────
print("\n" + "-"*65)
print("STAGE 4: GEMINI AI EXTRACTION")
print("-"*65)

date_hint = f'Where test_date is not shown, use "{fallback_date}".'

# Build the prompt we'll actually send
regex_hint = ""
if regex_rows:
    sample = regex_rows[:5]
    regex_hint = f"""
STRUCTURAL HINTS (pre-parsed from text):
{json.dumps(sample, indent=2)}
{len(regex_rows)} rows identified above. Extract all of them plus any you find.
"""

prompt = f"""You are a medical data extraction specialist.

FORMAT: This lab report uses SPACE-ALIGNED COLUMNS — each line is ONE complete result:
  TEST NAME [spaces] RESULT [spaces] UNIT [spaces] REFERENCE RANGE

Examples:
  Haemoglobin                  15.7      G%           13.00 - 17.00
  Total WBC Count              10.31 H   Thou/uL      4.00 - 11.00
  Vitamin D (25-OH)             7.40 LL  ng/mL        30.00 - 100.00

H=high abnormal, L=low abnormal, HH/LL=critically abnormal.
{date_hint}
{regex_hint}

Return ONLY valid JSON:
{{
  "report_type": "lab_report",
  "report_date": "{fallback_date}",
  "confidence": "high|medium|low",
  "labs": [
    {{
      "test_date": "{fallback_date}",
      "test_name": "exact test name",
      "test_value": "numeric result",
      "unit": "unit",
      "reference_range": "reference range",
      "is_abnormal": false,
      "lab_name": "lab facility name"
    }}
  ],
  "visits": [],
  "medications": []
}}

Rules:
- Extract EVERY lab test — aim for 100% coverage
- is_abnormal=true for H/HH/L/LL flags or out-of-range values
- lab_name = facility name from report header
- Never null, use empty string "" for missing fields

Document text:
{full_text[:25000]}
"""

print(f"\n  Prompt length: {len(prompt):,} chars")
print(f"  Calling Gemini (model: {os.getenv('VERTEX_MODEL','gemini-2.5-flash')})...")
print("  This may take 20-45 seconds...\n")

try:
    import threading
    result = [None, None]

    def call_vertex():
        try:
            from vertex_client import get_vertex_client, get_vertex_model_name
            client = get_vertex_client()
            resp = client.models.generate_content(
                model=get_vertex_model_name(), contents=prompt
            )
            result[0] = getattr(resp, "text", "") or ""
        except Exception as e:
            result[1] = e

    t = threading.Thread(target=call_vertex, daemon=True)
    t.start()
    t.join(timeout=90)

    if t.is_alive():
        print("  ✗ GEMINI TIMED OUT after 90 seconds")
        print("  → Check GOOGLE_APPLICATION_CREDENTIALS in .env")
        print("  → Check internet/firewall connectivity to Google Cloud")
        sys.exit(1)

    if result[1]:
        print(f"  ✗ GEMINI ERROR: {result[1]}")
        sys.exit(1)

    raw_response = result[0] or ""
    print(f"  ✓ Gemini responded ({len(raw_response)} chars)")

    print("\n" + "="*65)
    print("GEMINI RAW RESPONSE:")
    print("="*65)
    print(raw_response[:3000])

    # Parse the JSON
    clean = re.sub(r"```json|```", "", raw_response).strip()
    try:
        parsed = json.loads(clean)
        labs = parsed.get("labs", [])
        print(f"\n{'='*65}")
        print(f"PARSED RESULT: {len(labs)} lab rows")
        print(f"Confidence: {parsed.get('confidence')}")
        print(f"Report date: {parsed.get('report_date')}")
        print(f"{'='*65}")
        if labs:
            print(f"\nFirst 5 labs:")
            for lb in labs[:5]:
                print(f"  {lb.get('test_name','?'):<35} = {lb.get('test_value','?'):<10} {lb.get('unit','?')}")
        else:
            print("\n⚠ NO LABS IN GEMINI RESPONSE")
            print("  This means Gemini couldn't match the text format")
            print("  → The regex pre-parser will be used as fallback")
            if regex_rows:
                print(f"  → {len(regex_rows)} rows from regex will be inserted instead")
    except json.JSONDecodeError as e:
        print(f"\n✗ JSON PARSE FAILED: {e}")
        print("Raw response was not valid JSON")

except Exception as e:
    print(f"\n✗ UNEXPECTED ERROR: {e}")
    import traceback
    traceback.print_exc()

print("\n" + "="*65)
print("DIAGNOSIS COMPLETE")
print("="*65)
print("\nShare the output above to identify exactly why labs=0")
print()

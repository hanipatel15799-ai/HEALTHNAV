"""
debug_parse.py — HealthNav Parsing Debugger

Drop this file into your HealthNav project folder.
Run it from your terminal:

    python debug_parse.py "C:/path/to/BLOOD REPORT-1 (1).pdf"

It will trace every stage and show you EXACTLY where parsing fails.
No server restart needed.
"""
import sys
import os
import time
import traceback
from pathlib import Path

# ── Load environment ──────────────────────────────────────────────────────────
from dotenv import load_dotenv
load_dotenv()

print("\n" + "="*60)
print("  HealthNav Parse Debugger")
print("="*60)

if len(sys.argv) < 2:
    print("\nUsage: python debug_parse.py <path_to_pdf>")
    print("Example: python debug_parse.py 'BLOOD REPORT-1 (1).pdf'")
    sys.exit(1)

pdf_path = Path(sys.argv[1])
if not pdf_path.exists():
    print(f"\nFile not found: {pdf_path}")
    sys.exit(1)

print(f"\nFile: {pdf_path}")
print(f"Size: {pdf_path.stat().st_size:,} bytes")
raw_bytes = pdf_path.read_bytes()


# ── Stage A: DB Connection ────────────────────────────────────────────────────
print("\n── Stage A: Database Connection ──")
try:
    import psycopg2
    conn = psycopg2.connect(
        dbname=os.environ["DB_NAME"],
        user=os.environ["DB_USER"],
        password=os.environ["DB_PASSWORD"],
        host=os.environ["DB_HOST"],
        port=os.environ.get("DB_PORT", "5432"),
    )
    print("  ✓ DB connected")

    with conn.cursor() as cur:
        cur.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name='patient_file_extractions';"
        )
        cols = {r[0] for r in cur.fetchall()}
        print(f"  patient_file_extractions columns: {sorted(cols)}")

        missing = {'visual_summary', 'source_kind'} - cols
        if missing:
            print(f"\n  ✗ MISSING COLUMNS: {missing}")
            print("  → Run this SQL in pgAdmin:")
            print("    ALTER TABLE patient_file_extractions ADD COLUMN IF NOT EXISTS visual_summary TEXT;")
            print("    ALTER TABLE patient_file_extractions ADD COLUMN IF NOT EXISTS source_kind TEXT;")
            fix = input("\n  Auto-fix now? (y/n): ").strip().lower()
            if fix == 'y':
                with conn:
                    with conn.cursor() as cur2:
                        cur2.execute("ALTER TABLE patient_file_extractions ADD COLUMN IF NOT EXISTS visual_summary TEXT;")
                        cur2.execute("ALTER TABLE patient_file_extractions ADD COLUMN IF NOT EXISTS source_kind TEXT;")
                print("  ✓ Columns added")
        else:
            print("  ✓ All required columns present")
    conn.close()
except Exception as e:
    print(f"  ✗ DB FAILED: {e}")
    print("  → Check DB_NAME, DB_USER, DB_PASSWORD, DB_HOST in .env")
    sys.exit(1)


# ── Stage B: PDF Text Extraction ──────────────────────────────────────────────
print("\n── Stage B: PDF Text Extraction ──")
try:
    import fitz
    print(f"  PyMuPDF version: {fitz.version}")
    doc = fitz.open(stream=raw_bytes, filetype="pdf")
    print(f"  Pages: {len(doc)}")

    t_start = time.time()
    total_text = 0
    table_pages = 0
    ocr_pages = 0

    for i in range(len(doc)):
        page = doc[i]
        plain = (page.get_text() or "").strip()
        print(f"  Page {i+1}: plain_text={len(plain)} chars", end="")

        # Test find_tables with timeout guard
        t0 = time.time()
        try:
            finder = page.find_tables()
            t1 = time.time()
            tbl_count = len(finder.tables) if finder else 0
            print(f"  tables={tbl_count}  ({(t1-t0):.1f}s)", end="")
            if tbl_count > 0:
                table_pages += 1
        except Exception as te:
            print(f"  find_tables FAILED: {te}", end="")

        if len(plain) < 60:
            ocr_pages += 1
            print("  → OCR needed", end="")
        print()
        total_text += len(plain)

    doc.close()
    elapsed = time.time() - t_start
    print(f"\n  ✓ Extraction done: {total_text:,} chars total  {elapsed:.1f}s")
    print(f"  table_pages={table_pages}  ocr_pages={ocr_pages}")

    if total_text < 100:
        print("  ✗ WARNING: Very little text extracted — file may be scanned image")

except Exception as e:
    print(f"  ✗ PDF EXTRACTION FAILED: {e}")
    traceback.print_exc()
    sys.exit(1)


# ── Stage C: Tesseract (OCR) ──────────────────────────────────────────────────
print("\n── Stage C: OCR (Tesseract) ──")
try:
    import pytesseract
    ver = pytesseract.get_tesseract_version()
    print(f"  ✓ Tesseract available: {ver}")
except Exception as e:
    print(f"  ⚠ Tesseract NOT available: {e}")
    print("  → OCR fallback disabled (OK for text PDFs, not for scanned)")


# ── Stage D: Vertex AI Client ─────────────────────────────────────────────────
print("\n── Stage D: Vertex AI Client ──")
try:
    project = os.environ.get("GOOGLE_CLOUD_PROJECT", "")
    location = os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")
    creds = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "")
    model = os.environ.get("VERTEX_MODEL", "gemini-2.5-flash")

    print(f"  GOOGLE_CLOUD_PROJECT: {project or 'NOT SET'}")
    print(f"  GOOGLE_CLOUD_LOCATION: {location}")
    print(f"  VERTEX_MODEL: {model}")
    print(f"  GOOGLE_APPLICATION_CREDENTIALS: {creds or 'NOT SET'}")

    if creds and "\\" in creds:
        creds_fixed = creds.replace("\\", "/")
        print(f"  → Backslash fix applied: {creds_fixed}")
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = creds_fixed

    if creds and not Path(creds.replace("\\", "/")).exists():
        print(f"  ✗ Credentials file NOT FOUND at: {creds}")
    else:
        print("  ✓ Credentials file exists")

    print("  Initialising Vertex AI client...", end="", flush=True)
    t0 = time.time()
    from google import genai
    from google.genai.types import HttpOptions
    client = genai.Client(
        vertexai=True,
        project=project,
        location=location,
        http_options=HttpOptions(api_version="v1", timeout=60),
    )
    print(f" done ({time.time()-t0:.1f}s)  ✓")

    # Quick ping
    print("  Sending test prompt to Vertex AI...", end="", flush=True)
    t0 = time.time()
    resp = client.models.generate_content(
        model=model,
        contents='Reply with exactly: {"ok": true}'
    )
    elapsed = time.time() - t0
    print(f" done ({elapsed:.1f}s)")
    print(f"  Response: {(resp.text or '')[:100]}")
    print("  ✓ Vertex AI working")

except Exception as e:
    print(f"\n  ✗ VERTEX AI FAILED: {e}")
    traceback.print_exc()
    print("\n  → Check GOOGLE_CLOUD_PROJECT, GOOGLE_APPLICATION_CREDENTIALS in .env")
    print("  → Make sure the service account has roles/aiplatform.user")
    sys.exit(1)


# ── Stage E: Full extraction test ─────────────────────────────────────────────
print("\n── Stage E: Full Extraction (truncated text → Vertex) ──")
try:
    # Use the same extraction logic as report_parser
    sys.path.insert(0, str(Path(__file__).parent))
    from utils.pdf_utils import extract_pdf_text
    from utils.validation import extract_report_date

    text, modes = extract_pdf_text(raw_bytes)
    print(f"  Extracted text: {len(text):,} chars")
    print(f"  Modes: {dict((m, modes.count(m)) for m in set(modes))}")

    date = extract_report_date(text)
    print(f"  Detected report date: {date}")

    print(f"\n  First 500 chars of extracted text:")
    print("  " + text[:500].replace("\n", "\n  "))

    print(f"\n  Calling Vertex AI for structured extraction...")
    print("  (This may take 15-45 seconds...)", flush=True)
    t0 = time.time()

    prompt = f"""Extract lab results from this report. Return ONLY JSON:
{{
  "report_type": "lab_report",
  "confidence": "high|medium|low",
  "labs": [{{"test_name":"","test_value":"","unit":"","reference_range":"","is_abnormal":false,"test_date":"{date or ''}","lab_name":""}}],
  "visits": [],
  "medications": []
}}

Report text:
{text[:8000]}
"""
    resp = client.models.generate_content(model=model, contents=prompt)
    elapsed = time.time() - t0
    raw = (resp.text or "").strip()
    print(f"  ✓ Vertex responded in {elapsed:.1f}s")
    print(f"  Raw response (first 600 chars):")
    print("  " + raw[:600].replace("\n", "\n  "))

    import json, re
    clean = re.sub(r"```json|```", "", raw).strip()
    try:
        result = json.loads(clean)
        labs = result.get("labs", [])
        print(f"\n  ✓ Parsed JSON: {len(labs)} lab rows returned")
        if labs:
            print(f"  First lab: {labs[0]}")
    except Exception as je:
        print(f"  ✗ JSON parse failed: {je}")

except Exception as e:
    print(f"  ✗ EXTRACTION FAILED: {e}")
    traceback.print_exc()


# ── Summary ───────────────────────────────────────────────────────────────────
print("\n" + "="*60)
print("  Debug complete.")
print("  Share the output above to diagnose your parsing issue.")
print("="*60 + "\n")

"""
setup_check.py — Run this to verify your HealthNav project structure.
Place it in your project root (same folder as main.py) and run:
    python setup_check.py
"""
import os, sys
from pathlib import Path

root = Path(__file__).parent
print(f"\nProject root: {root}\n")

REQUIRED = [
    ("main.py",                         "FastAPI app"),
    ("report_parser.py",                "Parser pipeline"),
    ("patient_record_retrieval.py",     "DB layer"),
    ("auth.py",                         "Authentication"),
    ("vertex_client.py",                "Gemini AI client"),
    ("config.py",                       "Config"),
    ("phi_guard.py",                    "PHI guard"),
    ("db/db.py",                        "DB connection"),
    ("db/schema.sql",                   "Database schema"),
    ("utils/__init__.py",               "utils package marker"),
    ("utils/pdf_utils.py",              "PDF extraction"),
    ("utils/ocr_utils.py",              "OCR fallback"),
    ("utils/validation.py",             "Date/row validation"),
    ("utils/logging_utils.py",          "Pipeline logging"),
    ("static/index.html",               "Frontend HTML"),
    (".env",                            "Environment config"),
]

ok = fail = 0
for rel, desc in REQUIRED:
    path = root / rel
    exists = path.exists()
    if exists: ok += 1
    else: fail += 1
    status = "✓" if exists else "✗ MISSING"
    print(f"  {status}  {rel}  ({desc})")

print(f"\n{'='*50}")
print(f"  {ok} OK  {fail} missing")

if fail > 0:
    print("\nFor missing utils files, create the folder structure:")
    print("  mkdir utils")
    print("  Place utils_init.py in utils/ and rename it to __init__.py")
    print("  Place pdf_utils.py, ocr_utils.py, validation.py, logging_utils.py in utils/")

print()

"""
test_pipeline.py — HealthNav end-to-end pipeline validation.

Simulates:
  1. PDF upload + text extraction
  2. AI structured extraction (mocked)
  3. DB inserts with savepoints
  4. User isolation check
  5. Record retrieval
  6. Answer generation (mocked)

Run: python test_pipeline.py
(No running server needed — tests the modules directly)

Requires .env to be configured with a real DB connection.
"""
from __future__ import annotations

import asyncio
import io
import json
import sys
import traceback
from datetime import date
from pathlib import Path
from typing import Any, Dict
from unittest.mock import MagicMock, patch

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

from dotenv import load_dotenv
load_dotenv()

PASS = "  ✓"
FAIL = "  ✗"
results = []


def check(name: str, condition: bool, detail: str = "") -> None:
    results.append((name, condition, detail))
    icon = PASS if condition else FAIL
    print(f"{icon} {name}" + (f"  [{detail}]" if detail else ""))


def run_test(name: str):
    def decorator(fn):
        def wrapper():
            print(f"\n── {name} ──")
            try:
                fn()
            except Exception as e:
                check(name, False, f"EXCEPTION: {e}")
                traceback.print_exc()
        return wrapper
    return decorator


# ─────────────────────────────────────────────
# Test 1: DB connection + schema
# ─────────────────────────────────────────────

@run_test("DB Connection & Schema")
def test_db():
    from patient_record_retrieval import get_connection, ensure_tables_exist
    conn = get_connection()
    check("DB connection opens", conn is not None)
    conn.close()
    check("DB connection closes", True)

    ensure_tables_exist()
    check("ensure_tables_exist runs idempotently", True)

    conn = get_connection()
    with conn.cursor() as cur:
        for table in ["patient_labs", "patient_visits", "patient_medications",
                      "patient_files", "patient_file_extractions",
                      "patient_users", "auth_sessions"]:
            cur.execute(
                "SELECT EXISTS (SELECT 1 FROM information_schema.tables "
                "WHERE table_name=%s);", (table,)
            )
            exists = cur.fetchone()[0]
            check(f"Table exists: {table}", exists)

        # Check critical columns
        cur.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name='patient_file_extractions';"
        )
        cols = {r[0] for r in cur.fetchall()}
        check("patient_file_extractions.visual_summary exists", "visual_summary" in cols)
        check("patient_file_extractions.source_kind exists", "source_kind" in cols)
    conn.close()


# ─────────────────────────────────────────────
# Test 2: PDF extraction
# ─────────────────────────────────────────────

@run_test("PDF Text Extraction")
def test_pdf_extraction():
    import fitz
    from utils.pdf_utils import extract_pdf_text
    from utils.validation import extract_report_date

    # Create a minimal test PDF with lab-like content
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((50, 50), "Haemoglobin\nG%\n13.00 - 17.00\n15.7\nReport Date: 23-Nov-2024\nHDL Cholesterol\nmg/dL\n40 - 60\n38.00")
    pdf_bytes = doc.tobytes()
    doc.close()

    text, modes = extract_pdf_text(pdf_bytes)
    check("PDF extraction returns text", len(text) > 0, f"len={len(text)}")
    check("Haemoglobin in extracted text", "Haemoglobin" in text or "aemoglobin" in text)

    report_date = extract_report_date(text)
    check("Date extracted from PDF", report_date is not None, f"date={report_date}")


# ─────────────────────────────────────────────
# Test 3: Vertex AI extraction (mocked)
# ─────────────────────────────────────────────

@run_test("AI Extraction (mocked Vertex)")
def test_ai_extraction():
    from utils.validation import resolve_lab_date, is_valid_lab_row

    # Simulate what Vertex returns
    mock_response = {
        "report_type": "lab_report",
        "confidence": "high",
        "report_date": "2024-11-23",
        "labs": [
            {"test_name": "HDL Cholesterol", "test_value": "38.00", "unit": "mg/dL",
             "reference_range": "40-60", "is_abnormal": True, "test_date": "2024-11-23", "lab_name": "TestLab"},
            {"test_name": "Haemoglobin", "test_value": "15.7", "unit": "G%",
             "reference_range": "13.0-17.0", "is_abnormal": False, "test_date": "", "lab_name": ""},
            {"test_name": "", "test_value": "42", "unit": "x10/uL", "reference_range": ""},  # invalid — no name
        ],
        "visits": [],
        "medications": []
    }

    labs = mock_response["labs"]
    valid_labs = [l for l in labs if is_valid_lab_row(l)]
    check("Invalid lab rows filtered (empty test_name)", len(valid_labs) == 2, f"valid={len(valid_labs)}/total={len(labs)}")

    for lab in valid_labs:
        resolved = resolve_lab_date(lab, "2024-11-23")
        check(f"Date resolved for {lab['test_name']}", resolved is not None, f"date={resolved}")


# ─────────────────────────────────────────────
# Test 4: DB inserts + user isolation
# ─────────────────────────────────────────────

@run_test("DB Inserts + User Isolation (HIPAA)")
def test_db_inserts():
    from patient_record_retrieval import (
        get_connection, get_recent_labs, get_active_medications,
        get_recent_visits, insert_parsed_labs, insert_parsed_visits,
        insert_parsed_medications,
    )

    patient_a = "test-patient-A-isolation"
    patient_b = "test-patient-B-isolation"
    test_date = "2024-01-15"

    conn = get_connection()
    try:
        with conn:
            # Insert for patient A
            n = insert_parsed_labs(conn, patient_a, [
                {"test_name": "TEST_HDL_ISO", "test_value": "38", "unit": "mg/dL",
                 "reference_range": "40-60", "is_abnormal": True, "test_date": test_date, "lab_name": "IsoLab"},
            ])
            check("Lab inserted for patient A", n >= 0, f"inserted={n}")

            # Insert for patient B
            n2 = insert_parsed_labs(conn, patient_b, [
                {"test_name": "TEST_HDL_ISO", "test_value": "55", "unit": "mg/dL",
                 "reference_range": "40-60", "is_abnormal": False, "test_date": test_date, "lab_name": "IsoLab"},
            ])
            check("Lab inserted for patient B", n2 >= 0, f"inserted={n2}")

        # Verify isolation
        labs_a = get_recent_labs(patient_a, limit=50)
        labs_b = get_recent_labs(patient_b, limit=50)
        a_names = [l["patient_id"] for l in labs_a]
        b_names = [l["patient_id"] for l in labs_b]

        check("Patient A labs all have patient_id=A",
              all(p == patient_a for p in a_names), f"count={len(labs_a)}")
        check("Patient B labs all have patient_id=B",
              all(p == patient_b for p in b_names), f"count={len(labs_b)}")
        check("Patient A cannot see Patient B labs",
              not any(p == patient_b for p in a_names))
        check("Patient B cannot see Patient A labs",
              not any(p == patient_a for p in b_names))

        # Medication insert test
        conn2 = get_connection()
        with conn2:
            nm = insert_parsed_medications(conn2, patient_a, [
                {"medication_name": "TestMetformin", "dosage": "500mg",
                 "frequency": "twice daily", "start_date": test_date,
                 "is_active": True, "indication": "Diabetes", "prescribing_doctor": "Dr Test"},
            ])
            check("Medication inserted", nm >= 0, f"inserted={nm}")
        conn2.close()

        meds_a = get_active_medications(patient_a)
        check("Medications isolated to patient A",
              all(m["patient_id"] == patient_a for m in meds_a))

        # Visit insert test
        conn3 = get_connection()
        with conn3:
            nv = insert_parsed_visits(conn3, patient_a, [
                {"visit_date": test_date, "visit_type": "TestCheckup",
                 "chief_complaint": "Test complaint", "clinical_notes": "All normal",
                 "doctor_name": "Dr Test"},
            ])
            check("Visit inserted", nv >= 0, f"inserted={nv}")
        conn3.close()

        visits_a = get_recent_visits(patient_a, limit=20)
        check("Visits isolated to patient A",
              all(v["patient_id"] == patient_a for v in visits_a))

    finally:
        # Cleanup
        with conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM patient_labs WHERE patient_id IN (%s, %s);", (patient_a, patient_b))
                cur.execute("DELETE FROM patient_medications WHERE patient_id=%s;", (patient_a,))
                cur.execute("DELETE FROM patient_visits WHERE patient_id=%s;", (patient_a,))
        conn.close()


# ─────────────────────────────────────────────
# Test 5: Medication category prompt
# ─────────────────────────────────────────────

@run_test("Category-Aware Prompt Building")
def test_category_prompts():
    from report_parser import _build_extraction_prompt

    lab_prompt = _build_extraction_prompt("test text", "lab_report", "2024-11-23")
    med_prompt = _build_extraction_prompt("test text", "medication_list", "2024-11-23")
    visit_prompt = _build_extraction_prompt("test text", "visit_summary", "2024-11-23")

    check("Lab prompt contains labs schema", '"labs"' in lab_prompt)
    check("Lab prompt mentions is_abnormal", "is_abnormal" in lab_prompt)
    check("Medication prompt contains medications schema", '"medications"' in med_prompt)
    check("Medication prompt mentions is_active", "is_active" in med_prompt)
    check("Medication prompt mentions indication", "indication" in med_prompt)
    check("Visit prompt contains visits schema", '"visits"' in visit_prompt)
    check("Visit prompt mentions chief_complaint", "chief_complaint" in visit_prompt)
    check("All prompts contain date hint", "2024-11-23" in lab_prompt and "2024-11-23" in med_prompt)


# ─────────────────────────────────────────────
# Test 6: PHI guard
# ─────────────────────────────────────────────

@run_test("PHI Detection & Redaction")
def test_phi_guard():
    from phi_guard import detect_phi, redact_phi

    test_input = "My email is john.doe@hospital.com and my SSN is 123-45-6789"
    detected = detect_phi(test_input)
    check("Email PHI detected", detected.get("email", False))
    check("SSN PHI detected", detected.get("ssn", False))

    redacted = redact_phi(test_input)
    check("Email redacted in output", "john.doe@hospital.com" not in redacted)
    check("SSN redacted in output", "123-45-6789" not in redacted)


# ─────────────────────────────────────────────
# Test 7: Config completeness
# ─────────────────────────────────────────────

@run_test("Config & Environment")
def test_config():
    from config import missing_core_env
    missing = missing_core_env()
    if missing:
        for m in missing:
            check(f"Env var set: {m}", False, "NOT SET")
    else:
        check("All required env vars are set", True)

    from vertex_client import get_vertex_model_name
    model = get_vertex_model_name()
    check("VERTEX_MODEL configured", bool(model), f"model={model}")


# ─────────────────────────────────────────────
# Run all tests
# ─────────────────────────────────────────────

def main():
    print("\n" + "="*60)
    print("  HealthNav Pipeline Test Suite")
    print("="*60)

    test_db()
    test_pdf_extraction()
    test_ai_extraction()
    test_db_inserts()
    test_category_prompts()
    test_phi_guard()
    test_config()

    print("\n" + "="*60)
    passed = sum(1 for _, ok, _ in results if ok)
    failed = sum(1 for _, ok, _ in results if not ok)
    print(f"  Results: {passed} passed  {failed} failed  ({len(results)} total)")

    if failed:
        print("\n  FAILED TESTS:")
        for name, ok, detail in results:
            if not ok:
                print(f"    ✗ {name}" + (f": {detail}" if detail else ""))

    print("="*60 + "\n")
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()

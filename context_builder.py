"""
HealthNav context_builder.py
─────────────────────────────
Builds the minimum-necessary patient context for AI calls.

Intent-scoped: a cholesterol question fetches only lipid labs + relevant
meds. Never sends the entire patient chart to the model.

Public API:
  build_minimum_necessary_context(patient_id, question) → Dict[str, Any]

Return shape:
  {
    "focus":       str        detected intent label
    "labs":        list[dict] relevant lab rows from patient_labs
    "visits":      list[dict] relevant visit rows (general / BP only)
    "medications": list[dict] filtered or all active medications
  }
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List

from patient_record_retrieval import (
    get_abnormal_labs,
    get_active_medications,
    get_lab_by_name,
    get_recent_labs,
    get_recent_visits,
)

logger = logging.getLogger(__name__)

# ── Intent keyword maps ────────────────────────────────────────────────────────

_INTENT_MAP: List[tuple[str, List[str]]] = [
    ("lipids",         ["cholesterol", "ldl", "hdl", "triglyceride", "lipid", "statin"]),
    ("diabetes",       ["glucose", "diabetes", "a1c", "hba1c", "sugar", "insulin", "metformin", "glp"]),
    ("thyroid",        ["thyroid", "tsh", "t3", "t4", "hypothyroid", "hyperthyroid"]),
    ("kidney",         ["creatinine", "kidney", "renal", "gfr", "urea", "bun", "uric acid"]),
    ("liver",          ["liver", "alt", "ast", "bilirubin", "albumin", "hepatic"]),
    ("blood_count",    ["hemoglobin", "hematocrit", "wbc", "rbc", "platelet", "anemia", "blood count", "cbc"]),
    ("blood_pressure", ["blood pressure", "hypertension", "amlodipine", "lisinopril", "systolic", "diastolic"]),
]

# Lab search terms per intent
_INTENT_LABS: Dict[str, List[str]] = {
    "lipids":         ["ldl", "hdl", "cholesterol", "triglyceride"],
    "diabetes":       ["glucose", "hba1c", "a1c"],
    "thyroid":        ["tsh", "t3", "t4", "thyroid"],
    "kidney":         ["creatinine", "gfr", "urea", "bun", "uric acid", "potassium"],
    "liver":          ["alt", "ast", "bilirubin", "albumin", "alkaline phosphatase", "ggt"],
    "blood_count":    ["hemoglobin", "hematocrit", "wbc", "rbc", "platelet", "mch", "mcv", "mchc"],
    "blood_pressure": ["potassium", "sodium", "creatinine"],
}

# Medication name substrings that are relevant per intent
_INTENT_MEDS: Dict[str, List[str]] = {
    "lipids":         ["statin", "atorvastatin", "rosuvastatin", "simvastatin", "ezetimibe", "fibrate", "fenofibrate"],
    "diabetes":       ["metformin", "insulin", "glp", "sglt", "dpp", "glipizide", "sitagliptin", "empagliflozin", "dulaglutide"],
    "thyroid":        ["levothyroxine", "thyroxine", "methimazole", "propylthiouracil", "liothyronine"],
    "blood_pressure": ["amlodipine", "lisinopril", "losartan", "ramipril", "atenolol", "bisoprolol", "hydrochlorothiazide", "valsartan"],
}


def _detect_intent(question: str) -> str:
    q = question.lower()
    for intent, keywords in _INTENT_MAP:
        if any(kw in q for kw in keywords):
            return intent
    return "general"


def _filter_meds(meds: List[Dict], intent: str) -> List[Dict]:
    keywords = _INTENT_MEDS.get(intent)
    if not keywords:
        return meds[:5]
    relevant = [
        m for m in meds
        if any(kw in (m.get("medication_name") or "").lower() for kw in keywords)
    ]
    return relevant if relevant else meds[:5]


# ── Public API ─────────────────────────────────────────────────────────────────

def build_minimum_necessary_context(patient_id: str, question: str) -> Dict[str, Any]:
    """
    Return only the patient data relevant to the question intent.
    Never returns the full chart.

    Called by:
      - main.py _safe_patient_context_text() before every AI call
      - Can also be called directly for debugging/testing
    """
    intent = _detect_intent(question)
    logger.info("context_builder patient=%.12s intent=%s", patient_id, intent)

    all_meds = get_active_medications(patient_id)

    # ── Specific intent paths ──────────────────────────────────────────────────

    if intent in _INTENT_LABS:
        lab_terms = _INTENT_LABS[intent]
        limit = 8 if intent == "blood_count" else 5
        labs = get_lab_by_name(patient_id, lab_terms, limit=limit)
        meds = _filter_meds(all_meds, intent)
        # Blood pressure also wants recent visits
        visits = get_recent_visits(patient_id, limit=2) if intent == "blood_pressure" else []
        return {"focus": intent, "labs": labs, "medications": meds, "visits": visits}

    # ── General fallback ───────────────────────────────────────────────────────

    recent = get_recent_labs(patient_id, limit=5)
    abnormal = get_abnormal_labs(patient_id, limit=5)

    # Merge without duplicates (recent + any abnormal not already included)
    seen = {
        (str(r.get("test_name", "")).lower(), str(r.get("test_date", "")))
        for r in recent
    }
    for row in abnormal:
        key = (str(row.get("test_name", "")).lower(), str(row.get("test_date", "")))
        if key not in seen:
            recent.append(row)
            seen.add(key)

    visits = get_recent_visits(patient_id, limit=2)
    return {
        "focus": "general",
        "labs": recent,
        "medications": all_meds[:5],
        "visits": visits,
    }

"""
Detect and redact PHI from user input.
"""
from __future__ import annotations

import re
from typing import Dict, Tuple

PHI_PATTERNS = {
    "email": r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b",
    "phone": r"\b(?:\+?1[-.\s]?)?(?:\(?\d{3}\)?[-.\s]?)\d{3}[-.\s]?\d{4}\b",
    "dob": r"\b(?:dob|date of birth)\s*[:\-]?\s*\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b",
    "mrn": r"\b(?:mrn|medical record number|record\s*#|record\s*no\.?)\s*[:\-]?\s*[A-Za-z0-9\-]+\b",
    "ssn": r"\b\d{3}-\d{2}-\d{4}\b",
    "address": (
        r"\b\d{1,5}\s+(?:[A-Za-z]+\s+){1,4}"
        r"(?:street|st|avenue|ave|road|rd|drive|dr|lane|ln|boulevard|blvd|court|ct|place|pl|way|circle|cir|highway|hwy|parkway|pkwy|terrace|ter|trail|trl)\b"
        r"(?:\s*,?\s*(?:apt|unit|suite|ste|#)\s*[A-Za-z0-9\-]+)?"
    ),
    # BUG FIX: Previous pattern matched ANY 5-digit number (e.g. lab values like "WBC 12345 U/L").
    # Now requires an explicit zip/postal keyword so only genuine zip codes are caught.
    "zipcode": r"\b(?:zip(?:\s*code)?|postal\s*code)\s*[:\-]?\s*\d{5}(?:-\d{4})?\b",
    "po_box": r"\bP\.?\s*O\.?\s*Box\s+\d+\b",
    "patient_name": r"\b(?:name|patient|pt)\s*[:\-]\s*[A-Z][a-z]+(?:\s+[A-Z][a-z]+)+",
}


def detect_phi(text: str) -> Dict[str, bool]:
    return {
        label: bool(re.search(pattern, text, flags=re.IGNORECASE))
        for label, pattern in PHI_PATTERNS.items()
    }


def redact_phi(text: str) -> str:
    redacted = text
    for label, pattern in PHI_PATTERNS.items():
        redacted = re.sub(pattern, f"[{label.upper()} REDACTED]", redacted, flags=re.IGNORECASE)
    return redacted


def detect_and_redact_phi(text: str) -> Tuple[Dict[str, bool], str]:
    found = detect_phi(text)
    return found, redact_phi(text) if any(found.values()) else text

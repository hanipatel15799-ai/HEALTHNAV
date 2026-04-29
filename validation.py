# Place at: utils/validation.py
"""
utils/validation.py
Date normalisation and lab row validation used by report_parser.
"""
from __future__ import annotations

import logging
import re
from datetime import date, datetime
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_DATE_FORMATS = [
    "%Y-%m-%d",    # 2024-11-23
    "%d-%b-%Y",    # 23-Nov-2024
    "%d/%m/%Y",    # 23/11/2024
    "%m/%d/%Y",    # 11/23/2024
    "%d-%m-%Y",    # 23-11-2024
    "%d %b %Y",    # 23 Nov 2024
    "%B %d, %Y",   # November 23, 2024
    "%d-%b-%y",    # 23-Nov-24
]


def parse_date(raw: Any) -> Optional[str]:
    """Try to parse any date-like value → ISO YYYY-MM-DD. Returns None on failure."""
    if raw is None:
        return None
    if isinstance(raw, (date, datetime)):
        return (raw.date() if isinstance(raw, datetime) else raw).strftime("%Y-%m-%d")
    s = str(raw).strip()
    if not s:
        return None
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def extract_report_date(text: str) -> Optional[str]:
    """
    Pull the most likely report date from free-form PDF text.
    Searches label-prefixed dates first, then any date pattern.
    """
    label_pattern = (
        r"(?:Sample\s*Date|Report\s*Date|Collected|Reported|Printed\s*On|Date)"
        r"[^0-9\n]{0,20}"
        r"(\d{1,2}[-/\s][A-Za-z]{3,9}[-/\s]\d{4}|\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{4})"
    )
    m = re.search(label_pattern, text, re.IGNORECASE)
    if m:
        parsed = parse_date(m.group(1))
        if parsed:
            logger.debug("Report date from label: %s", parsed)
            return parsed

    # Fallback: first unambiguous date in text
    for pattern in [
        r"(\d{1,2}-[A-Za-z]{3}-\d{4})",   # 23-Nov-2024
        r"(\d{4}-\d{2}-\d{2})",             # 2024-11-23
        r"(\d{1,2}/\d{1,2}/\d{4})",         # 23/11/2024
    ]:
        m2 = re.search(pattern, text)
        if m2:
            parsed = parse_date(m2.group(1))
            if parsed:
                logger.debug("Report date from pattern: %s", parsed)
                return parsed
    logger.warning("Could not extract report date from text")
    return None


def resolve_lab_date(lab: Dict[str, Any], fallback: Optional[str]) -> Optional[str]:
    """
    Return a resolved ISO date for a lab row.
    Priority: lab['test_date'] → fallback → today
    """
    from_lab = parse_date(lab.get("test_date"))
    if from_lab:
        return from_lab
    if fallback:
        return fallback
    today = date.today().isoformat()
    logger.debug("Using today as lab date fallback: %s", today)
    return today


def is_valid_lab_row(lab: Dict[str, Any]) -> bool:
    """
    Minimal validity: requires non-empty test_name.
    Intentionally loose — partial rows with missing values are kept.
    """
    name = (lab.get("test_name") or "").strip()
    return bool(name)

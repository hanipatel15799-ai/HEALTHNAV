from __future__ import annotations

from typing import Dict, List, Optional


def _to_float(value) -> Optional[float]:
    try:
        return float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def analyze_lab_trend(records: List[Dict]) -> Dict:
    numeric = []
    for rec in records:
        v = _to_float(rec.get("test_value"))
        if v is not None:
            numeric.append(v)

    count = len(numeric)
    if count == 0:
        return {"trend": "insufficient_data", "note": "No numeric values available."}
    if count == 1:
        return {"trend": "insufficient_data", "note": "Only one result available.", "latest": numeric[0]}

    latest = numeric[0]
    previous = numeric[1]
    pct_change = 0.0 if previous == 0 else ((latest - previous) / abs(previous)) * 100
    threshold = 5.0

    if pct_change > threshold:
        trend = "rising"
        note = f"{records[0].get('test_name', 'result')} is rising ({previous} → {latest})."
    elif pct_change < -threshold:
        trend = "falling"
        note = f"{records[0].get('test_name', 'result')} is falling ({previous} → {latest})."
    else:
        trend = "stable"
        note = f"{records[0].get('test_name', 'result')} is stable ({previous} → {latest})."

    return {
        "trend": trend,
        "direction": f"{pct_change:+.1f}%",
        "latest": latest,
        "previous": previous,
        "count": count,
        "all_values": numeric,
        "note": note,
    }


def analyze_all_labs(labs: List[Dict]) -> Dict[str, Dict]:
    grouped: Dict[str, List[Dict]] = {}
    for lab in labs:
        name = str(lab.get("test_name", "unknown")).strip()
        grouped.setdefault(name, []).append(lab)

    for name in grouped:
        grouped[name].sort(key=lambda r: str(r.get("test_date", "")), reverse=True)

    return {name: analyze_lab_trend(records) for name, records in grouped.items()}


def format_trend_summary(trend_data: Dict[str, Dict]) -> str:
    if not trend_data:
        return ""
    lines = ["TREND SUMMARY:"]
    for name, info in sorted(trend_data.items()):
        lines.append(f"- {name}: {info.get('note', 'No trend note available.')}")
    return "\n".join(lines)

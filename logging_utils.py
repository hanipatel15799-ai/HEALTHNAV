# Place at: utils/logging_utils.py
"""
utils/logging_utils.py
Structured logging setup for HealthNav.

Features:
  - JSON-structured logs for production (easy CloudWatch/Splunk ingestion)
  - Human-readable logs for development
  - PHI-safe: patient_id is always hashed before logging
  - Pipeline stage markers for easy grep/filter
  - Log level controlled by LOG_LEVEL env var
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import sys
from datetime import datetime, timezone
from typing import Any, Optional


# ─────────────────────────────────────────────
# PHI-safe ID hashing
# ─────────────────────────────────────────────

def safe_patient_ref(patient_id: Optional[str]) -> str:
    """Return a short non-reversible reference for logs — never raw patient_id."""
    if not patient_id:
        return "unknown"
    return "p-" + hashlib.sha256(patient_id.encode()).hexdigest()[:8]


# ─────────────────────────────────────────────
# Pipeline stage logger
# ─────────────────────────────────────────────

class PipelineLogger:
    """
    Structured logger for the parse pipeline.
    Each stage call emits a consistently-formatted log entry.

    Usage:
        pl = PipelineLogger(file_id=42, patient_id="p-abc123")
        pl.stage("extract", "PDF extracted", text_len=23689, modes=["table+text"])
        pl.stage("vertex", "AI response received", labs_returned=42)
        pl.stage("insert", "Labs inserted", inserted=38, skipped=4)
    """

    def __init__(self, file_id: Any, patient_id: str):
        self.file_id = file_id
        self.patient_ref = safe_patient_ref(patient_id)
        self.logger = logging.getLogger("healthnav.pipeline")

    def stage(self, stage_name: str, message: str, **kwargs) -> None:
        extra = {
            "file_id": self.file_id,
            "patient_ref": self.patient_ref,
            "stage": stage_name,
            **kwargs,
        }
        parts = [f"[{stage_name.upper()}] {message}"]
        for k, v in kwargs.items():
            parts.append(f"{k}={v!r}")
        self.logger.info("  ".join(parts))

    def warn(self, stage_name: str, message: str, **kwargs) -> None:
        parts = [f"[{stage_name.upper()}] WARN: {message}"]
        for k, v in kwargs.items():
            parts.append(f"{k}={v!r}")
        self.logger.warning("  ".join(parts))

    def error(self, stage_name: str, message: str, exc: Optional[Exception] = None, **kwargs) -> None:
        parts = [f"[{stage_name.upper()}] ERROR: {message}"]
        for k, v in kwargs.items():
            parts.append(f"{k}={v!r}")
        if exc:
            self.logger.exception("  ".join(parts))
        else:
            self.logger.error("  ".join(parts))


# ─────────────────────────────────────────────
# JSON formatter for production
# ─────────────────────────────────────────────

class JsonFormatter(logging.Formatter):
    """
    Emit JSON log lines for structured log ingestion.
    Suitable for CloudWatch, Splunk, Datadog.
    """

    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "timestamp": datetime.fromtimestamp(
                record.created, tz=timezone.utc
            ).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(entry)


# ─────────────────────────────────────────────
# App-wide logging configuration
# ─────────────────────────────────────────────

def configure_logging() -> None:
    """
    Call once at app startup (in main.py lifespan).
    - Development: coloured human-readable console output
    - Production: JSON lines to stdout (LOG_LEVEL=INFO recommended)
    """
    log_level_str = os.getenv("LOG_LEVEL", "INFO").upper()
    log_level = getattr(logging, log_level_str, logging.INFO)
    environment = os.getenv("ENVIRONMENT", "development").lower()

    root = logging.getLogger()
    root.setLevel(log_level)

    # Remove existing handlers (avoids duplicate logs on reload)
    for h in root.handlers[:]:
        root.removeHandler(h)

    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(log_level)

    if environment in ("production", "prod", "staging"):
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
                datefmt="%H:%M:%S",
            )
        )

    root.addHandler(handler)

    # Quiet noisy third-party loggers
    for noisy in ("uvicorn.access", "httpx", "httpcore", "google"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    logging.getLogger("healthnav").setLevel(log_level)
    logging.info("Logging configured: level=%s env=%s", log_level_str, environment)

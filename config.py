"""
config.py — central configuration for HealthNav.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Dict, List

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class DatabaseConfig:
    dbname: str
    user: str
    password: str
    host: str
    port: str

    def as_psycopg_kwargs(self) -> Dict[str, str]:
        return {
            "dbname": self.dbname,
            "user": self.user,
            "password": self.password,
            "host": self.host,
            "port": self.port,
        }


@dataclass(frozen=True)
class AppConfig:
    log_level: str
    frontend_origin: str
    app_secret: str
    max_question_len: int
    rate_limit_requests: int
    rate_limit_window_secs: int


def _require(key: str) -> str:
    v = os.getenv(key)
    if not v:
        raise EnvironmentError(f"Missing required env var: {key}")
    return v


def get_database_config() -> DatabaseConfig:
    return DatabaseConfig(
        dbname=_require("DB_NAME"),
        user=_require("DB_USER"),
        password=_require("DB_PASSWORD"),
        host=_require("DB_HOST"),
        port=os.getenv("DB_PORT", "5432"),
    )


def get_app_config() -> AppConfig:
    return AppConfig(
        log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
        frontend_origin=os.getenv("FRONTEND_ORIGIN", "http://localhost:8000"),
        app_secret=_require("APP_SECRET"),
        max_question_len=int(os.getenv("MAX_QUESTION_LEN", "1000")),
        rate_limit_requests=int(os.getenv("RATE_LIMIT_REQUESTS", "20")),
        rate_limit_window_secs=int(os.getenv("RATE_LIMIT_WINDOW_SECS", "60")),
    )


def missing_core_env() -> List[str]:
    required = [
        "APP_SECRET", "DB_NAME", "DB_USER", "DB_PASSWORD",
        "DB_HOST", "DB_PORT", "GOOGLE_CLOUD_PROJECT",
        "GOOGLE_CLOUD_LOCATION", "VERTEX_MODEL",
    ]
    return [k for k in required if not os.getenv(k)]

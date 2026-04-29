import os
from dotenv import load_dotenv

load_dotenv()

SENSITIVE_KEYS = ["GOOGLE_API_KEY", "DB_PASSWORD"]
NON_SENSITIVE_KEYS = ["DB_NAME", "DB_USER", "DB_HOST", "DB_PORT"]


def masked(value: str | None) -> str:
    if not value:
        return "NOT SET"
    if len(value) <= 6:
        return "***SET***"
    return f"{value[:2]}***{value[-2:]}"


for key in SENSITIVE_KEYS:
    print(f"{key}: {masked(os.getenv(key))}")

for key in NON_SENSITIVE_KEYS:
    print(f"{key}: {os.getenv(key, 'NOT SET')}")

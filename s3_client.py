"""
s3_client.py — HealthNav AWS S3 singleton client.

ALL file I/O goes through this module. No file is ever permanently
stored on the local filesystem.

S3 key structure:
    uploads/{patient_id}/{uuid}{ext}

.env variables required:
    S3_BUCKET_NAME          your bucket name
    AWS_REGION              e.g. us-east-1 (default: us-east-1)
    AWS_ACCESS_KEY_ID       only needed if not using IAM role
    AWS_SECRET_ACCESS_KEY   only needed if not using IAM role

boto3 uses the standard AWS credential chain:
    1. Environment variables (AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY)
    2. IAM instance role (EC2 / ECS / Lambda) ← recommended for production
    3. ~/.aws/credentials (local dev)
"""
from __future__ import annotations

import logging
import os
import threading
from typing import Optional

from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

_lock = threading.Lock()
_s3 = None

S3_BUCKET = os.getenv("S3_BUCKET_NAME") or os.getenv("S3_BUCKET", "")
AWS_REGION = os.getenv("AWS_REGION", "us-east-1")


def get_s3_client():
    """Return a singleton boto3 S3 client."""
    global _s3
    if _s3 is not None:
        return _s3
    with _lock:
        if _s3 is not None:
            return _s3
        import boto3
        _s3 = boto3.client("s3", region_name=AWS_REGION)
        logger.info("S3 client ready (bucket=%s region=%s)", S3_BUCKET, AWS_REGION)
        return _s3


def _bucket() -> str:
    if not S3_BUCKET:
        raise EnvironmentError("S3_BUCKET_NAME is not set in .env")
    return S3_BUCKET


def build_s3_key(patient_id: str, stored_filename: str) -> str:
    """
    Returns the S3 object key for a given patient file.
    Format: uploads/{patient_id}/{stored_filename}
    """
    return f"uploads/{patient_id}/{stored_filename}"


def upload_bytes_to_s3(
    raw_bytes: bytes,
    patient_id: str,
    stored_filename: str,
    content_type: str = "application/octet-stream",
) -> str:
    """
    Upload raw bytes to S3. Returns the S3 key.
    Raises on failure — caller must handle and return 500.
    """
    key = build_s3_key(patient_id, stored_filename)
    get_s3_client().put_object(
        Bucket=_bucket(),
        Key=key,
        Body=raw_bytes,
        ContentType=content_type,
        # Server-side encryption — AES256 is free and always on for HIPAA
        ServerSideEncryption="AES256",
        # Tag for lifecycle rules / audit. Do NOT put patient_id in S3 tags.
        Tagging="service=healthnav",
    )
    logger.info("S3 upload OK: key=%s size=%d", key, len(raw_bytes))
    return key


def download_bytes_from_s3(s3_key: str) -> bytes:
    """
    Download a file from S3 by key. Returns raw bytes.
    Raises botocore.exceptions.ClientError if key does not exist.
    """
    response = get_s3_client().get_object(Bucket=_bucket(), Key=s3_key)
    data = response["Body"].read()
    logger.info("S3 download OK: key=%s size=%d", s3_key, len(data))
    return data


def delete_from_s3(s3_key: str) -> None:
    """Delete a file from S3. Non-fatal — logs on error."""
    try:
        get_s3_client().delete_object(Bucket=_bucket(), Key=s3_key)
        logger.info("S3 delete OK: key=%s", s3_key)
    except Exception as exc:
        logger.error("S3 delete FAILED: key=%s err=%s", s3_key, exc)


def generate_presigned_url(s3_key: str, expires_in: int = 3600) -> Optional[str]:
    """
    Generate a pre-signed URL for direct browser download.
    expires_in: seconds (default 1 hour).
    Returns None on failure.
    """
    try:
        url = get_s3_client().generate_presigned_url(
            "get_object",
            Params={"Bucket": _bucket(), "Key": s3_key},
            ExpiresIn=expires_in,
        )
        return url
    except Exception as exc:
        logger.error("S3 presign FAILED: key=%s err=%s", s3_key, exc)
        return None

"""
vertex_client.py — AWS Bedrock drop-in replacement for the Gemini client.

ZERO changes needed in any other file. This module keeps the same public API:
    get_vertex_client()      → returns a client with .models.generate_content()
    get_vertex_model_name()  → returns the model ID string from .env

.env variables (replace old Google ones):
    AWS_ACCESS_KEY_ID        your AWS access key
    AWS_SECRET_ACCESS_KEY    your AWS secret key
    AWS_REGION               e.g. us-east-1  (default: us-east-1)
    BEDROCK_MODEL            e.g. anthropic.claude-3-5-sonnet-20241022-v2:0
    VERTEX_TIMEOUT_SECONDS   request timeout in seconds (default: 600)

Supported model families (auto-detected from model ID):
    anthropic.claude-*       → Anthropic Messages API format
    amazon.titan-*           → Amazon Titan format
    meta.llama*              → Meta Llama format
    mistral.*                → Mistral format
"""

from __future__ import annotations

import json
import logging
import os
import threading
from typing import Any

from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

_lock = threading.Lock()
_client = None


# ─────────────────────────────────────────────────────────────────────────────
# Response wrapper — makes Bedrock response look like Gemini's resp.text
# ─────────────────────────────────────────────────────────────────────────────

class _BedrockResponse:
    """Wraps a Bedrock response so callers can do `resp.text` just like Gemini."""

    def __init__(self, text: str):
        self.text = text

    def __repr__(self):
        preview = (self.text or "")[:80].replace("\n", " ")
        return f"<BedrockResponse text={preview!r}>"


# ─────────────────────────────────────────────────────────────────────────────
# Models shim — wraps boto3 bedrock-runtime so callers can do:
#   client.models.generate_content(model=model_id, contents=prompt_str)
# ─────────────────────────────────────────────────────────────────────────────

class _ModelsShim:
    def __init__(self, boto_client, timeout: int):
        self._boto = boto_client
        self._timeout = timeout

    def generate_content(self, model: str, contents: str) -> _BedrockResponse:
        """
        Unified call that auto-formats the request body based on model family.
        `contents` is always a plain string (matching existing Gemini usage).
        """
        body = _build_request_body(model, contents)

        response = self._boto.invoke_model(
            modelId=model,
            body=json.dumps(body),
            contentType="application/json",
            accept="application/json",
        )

        raw = json.loads(response["body"].read())
        text = _extract_text(model, raw)
        return _BedrockResponse(text)


# ─────────────────────────────────────────────────────────────────────────────
# Request body builders per model family
# ─────────────────────────────────────────────────────────────────────────────

def _build_request_body(model_id: str, prompt: str) -> dict:
    m = model_id.lower()

    if "anthropic.claude" in m:
        # Claude on Bedrock uses the Messages API
        max_tokens = int(os.getenv("BEDROCK_MAX_TOKENS", "4096"))
        return {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}],
        }

    if "amazon.titan" in m:
        max_tokens = int(os.getenv("BEDROCK_MAX_TOKENS", "4096"))
        return {
            "inputText": prompt,
            "textGenerationConfig": {
                "maxTokenCount": max_tokens,
                "temperature": 0.2,
                "topP": 0.9,
            },
        }

    if "meta.llama" in m:
        max_tokens = int(os.getenv("BEDROCK_MAX_TOKENS", "4096"))
        return {
            "prompt": prompt,
            "max_gen_len": max_tokens,
            "temperature": 0.2,
            "top_p": 0.9,
        }

    if "mistral" in m:
        max_tokens = int(os.getenv("BEDROCK_MAX_TOKENS", "4096"))
        return {
            "prompt": f"<s>[INST]{prompt}[/INST]",
            "max_tokens": max_tokens,
            "temperature": 0.2,
            "top_p": 0.9,
        }

    # Generic fallback — try Titan format
    logger.warning("Unknown model family for %s — using Titan format as fallback", model_id)
    return {
        "inputText": prompt,
        "textGenerationConfig": {"maxTokenCount": 4096},
    }


def _extract_text(model_id: str, raw: dict) -> str:
    """Extract the text string from a Bedrock response body."""
    m = model_id.lower()

    try:
        if "anthropic.claude" in m:
            # {"content": [{"type": "text", "text": "..."}]}
            return raw["content"][0]["text"]

        if "amazon.titan" in m:
            # {"results": [{"outputText": "..."}]}
            return raw["results"][0]["outputText"]

        if "meta.llama" in m:
            # {"generation": "..."}
            return raw.get("generation", "")

        if "mistral" in m:
            # {"outputs": [{"text": "..."}]}
            return raw["outputs"][0]["text"]

    except (KeyError, IndexError) as exc:
        logger.error("Failed to extract text from Bedrock response: %s | raw=%s", exc, str(raw)[:300])

    # Last resort: dump the whole response as string
    return json.dumps(raw)


# ─────────────────────────────────────────────────────────────────────────────
# Bedrock client wrapper — the object returned by get_vertex_client()
# ─────────────────────────────────────────────────────────────────────────────

class _BedrockClient:
    """
    Top-level client. Callers use:
        client.models.generate_content(model=..., contents=...)
    """
    def __init__(self, boto_client, timeout: int):
        self.models = _ModelsShim(boto_client, timeout)


# ─────────────────────────────────────────────────────────────────────────────
# Public API — identical signatures to the old vertex_client.py
# ─────────────────────────────────────────────────────────────────────────────

def get_vertex_client() -> _BedrockClient:
    """
    Returns a singleton Bedrock client.
    Thread-safe double-checked locking (same pattern as original vertex_client.py).
    """
    global _client

    if _client is not None:
        return _client

    with _lock:
        if _client is not None:
            return _client

        import boto3

        region = os.getenv("AWS_REGION", "us-east-1")
        timeout = int(os.getenv("VERTEX_TIMEOUT_SECONDS", "600"))

        # boto3 picks up AWS_ACCESS_KEY_ID + AWS_SECRET_ACCESS_KEY from env automatically.
        # You can also use an IAM role (recommended for EC2/ECS/Cloud Run on AWS).
        boto_client = boto3.client(
            service_name="bedrock-runtime",
            region_name=region,
            # Explicit credentials only needed if not using IAM role / env vars:
            # aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
            # aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
        )

        _client = _BedrockClient(boto_client, timeout)

        logger.info("AWS Bedrock client ready (region=%s)", region)
        logger.info("Using model: %s", get_vertex_model_name())

        return _client


def get_vertex_model_name() -> str:
    """
    Returns the Bedrock model ID from .env.
    Falls back to Claude 3.5 Sonnet if not set.

    Recommended values:
        anthropic.claude-3-5-sonnet-20241022-v2:0   ← best quality
        anthropic.claude-3-haiku-20240307-v1:0      ← fastest / cheapest
        amazon.titan-text-express-v1                ← AWS native
    """
    return os.getenv(
        "BEDROCK_MODEL",
        os.getenv("VERTEX_MODEL", "anthropic.claude-3-5-sonnet-20241022-v2:0"),
    )

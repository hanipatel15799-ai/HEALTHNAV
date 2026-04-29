from __future__ import annotations

import json
import logging
import re

from vertex_client import get_vertex_client, get_vertex_model_name

logger = logging.getLogger(__name__)


def expand_query_for_retrieval(user_question: str) -> dict:
    client = get_vertex_client()
    model_name = get_vertex_model_name()
    prompt = f"""You are a medical information retrieval expert.

A patient asked: "{user_question}"

Identify the core medical concepts needed for retrieval.

Return ONLY valid JSON:
{{
  "core_concept": "main mechanism",
  "retrieval_queries": ["query1", "query2"],
  "clinical_terms": ["term1", "term2"],
  "physiological_concepts": ["concept1"],
  "related_topics": ["topic1"]
}}"""
    try:
        response = client.models.generate_content(model=model_name, contents=prompt)
        text = re.sub(r"```json|```", "", response.text.strip()).strip()
        parsed = json.loads(text)
        all_terms = (
            [parsed.get("core_concept", "")]
            + parsed.get("retrieval_queries", [])
            + parsed.get("clinical_terms", [])
            + parsed.get("physiological_concepts", [])
            + parsed.get("related_topics", [])
        )
        expanded_flat = " ".join(t for t in all_terms if t).strip()
        return {**parsed, "expanded_flat": expanded_flat, "original_question": user_question}
    except Exception as exc:
        logger.warning("Query expansion failed: %s", exc)
        return {
            "core_concept": user_question,
            "retrieval_queries": [user_question],
            "clinical_terms": [],
            "physiological_concepts": [],
            "related_topics": [],
            "expanded_flat": user_question,
            "original_question": user_question,
        }

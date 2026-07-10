from __future__ import annotations

import json
import logging
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from app.services.knowledge_store import save_fact, save_skill
from app.services.llm_client import LlmServiceError, llm_client

logger = logging.getLogger(__name__)


class FactCandidate(BaseModel):
    fact_type: str = Field(default="document_default", max_length=100)
    content: str = Field(min_length=1, max_length=1000)
    structured: dict[str, Any] = Field(default_factory=dict)
    confidence: float = Field(ge=0, le=1)


class SkillCandidate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=1000)
    trigger_text: str = Field(min_length=1, max_length=1000)
    steps: list[str] = Field(default_factory=list, max_length=20)
    required_fields: list[str] = Field(default_factory=list, max_length=20)
    confidence: float = Field(ge=0, le=1)
    scope: str = "user"


class LearningCandidates(BaseModel):
    facts: list[FactCandidate] = Field(default_factory=list, max_length=5)
    skills: list[SkillCandidate] = Field(default_factory=list, max_length=3)


LEARNING_SCHEMA = {
    "type": "object",
    "properties": {
        "facts": {"type": "array", "items": {"type": "object"}},
        "skills": {"type": "array", "items": {"type": "object"}},
    },
    "required": ["facts", "skills"],
}


def _safe_json(content: str) -> dict[str, Any]:
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        start = content.find("{")
        end = content.rfind("}")
        if start >= 0 and end > start:
            return json.loads(content[start : end + 1])
        raise


async def extract_and_store_learning(
    *,
    user_id: str,
    chat_id: str,
    recent_messages: list[dict[str, Any]],
    session_state: dict[str, Any],
    business_profile_id: str | None = None,
    client_id: str | None = None,
) -> dict[str, int]:
    if not recent_messages:
        return {"facts_saved": 0, "skills_saved": 0}

    transcript = "\n".join(
        f"{message['role']}: {message['content']}"
        for message in recent_messages[-8:]
    )
    prompt = (
        "Extract stable document assistant learning from this chat. "
        "Save only durable preferences, defaults, client or business facts, and reusable workflows. "
        "Do not save greetings or one-time temporary document values. "
        "Do not save explicit requests to remember temporary numbers, colors, codes, or values; those stay in chat session state. "
        "Return JSON with facts and skills arrays only.\n\n"
        f"Session state JSON:\n{json.dumps(session_state, ensure_ascii=False)}\n\n"
        f"Recent chat:\n{transcript}\n"
    )

    try:
        content = await llm_client.complete_prompt(prompt, json_schema=LEARNING_SCHEMA, max_tokens=512)
        candidates = LearningCandidates.model_validate(_safe_json(content))
    except (LlmServiceError, ValidationError, ValueError, json.JSONDecodeError) as error:
        logger.warning("learning extraction skipped: %s", error)
        return {"facts_saved": 0, "skills_saved": 0}

    facts_saved = 0
    for fact in candidates.facts:
        save_fact(
            user_id=user_id,
            source_chat_id=chat_id,
            fact_type=fact.fact_type,
            content=fact.content,
            structured=fact.structured,
            confidence=fact.confidence,
            business_profile_id=business_profile_id,
            client_id=client_id,
        )
        facts_saved += 1

    skills_saved = 0
    for skill in candidates.skills:
        save_skill(
            user_id=user_id,
            source_chat_id=chat_id,
            title=skill.title,
            description=skill.description,
            trigger_text=skill.trigger_text,
            steps=skill.steps,
            required_fields=skill.required_fields,
            confidence=skill.confidence,
            scope=skill.scope,
            business_profile_id=business_profile_id,
            client_id=client_id,
        )
        skills_saved += 1

    return {"facts_saved": facts_saved, "skills_saved": skills_saved}

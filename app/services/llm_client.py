import asyncio
import logging
import time
from urllib.parse import urljoin

import httpx

from app.config import (
    LLM_BASE_URL,
    LLM_COMPLETION_ENDPOINT,
    LLM_MAX_TOKENS,
    LLM_TEMPERATURE,
    LLM_TIMEOUT_SECONDS,
)

logger = logging.getLogger(__name__)


class LlmServiceError(Exception):
    """Raised when the local LLM cannot return a usable response."""


class LlmClient:
    def __init__(self) -> None:
        endpoint = LLM_COMPLETION_ENDPOINT.lstrip("/")
        self.completion_url = urljoin(f"{LLM_BASE_URL}/", endpoint)
        self._request_lock = asyncio.Lock()

    async def complete(self, message: str) -> str:
        prompt = f"User: {message}\nAssistant:"
        return await self.complete_prompt(prompt)

    async def complete_prompt(
        self,
        prompt: str,
        json_schema: dict | None = None,
        max_tokens: int | None = None,
        stop: list[str] | None = None,
    ) -> str:
        payload = {
            "prompt": prompt,
            "n_predict": max_tokens if max_tokens is not None else LLM_MAX_TOKENS,
            "temperature": LLM_TEMPERATURE,
            "stop": stop if stop is not None else ["User:"],
        }
        if json_schema is not None:
            payload["json_schema"] = json_schema

        started_at = time.perf_counter()
        logger.info(
            "llm.request.started endpoint=%s prompt_length=%s has_json_schema=%s max_tokens=%s",
            self.completion_url,
            len(prompt),
            json_schema is not None,
            payload["n_predict"],
        )

        try:
            async with self._request_lock:
                async with httpx.AsyncClient(
                    timeout=LLM_TIMEOUT_SECONDS,
                    trust_env=False,
                ) as client:
                    response = await client.post(self.completion_url, json=payload)
                    response.raise_for_status()
        except httpx.HTTPError as error:
            duration_ms = (time.perf_counter() - started_at) * 1000
            logger.exception(
                "llm.request.failed endpoint=%s duration_ms=%.2f error_type=%s",
                self.completion_url,
                duration_ms,
                type(error).__name__,
            )
            raise LlmServiceError(f"Local LLM request failed: {error}") from error

        duration_ms = (time.perf_counter() - started_at) * 1000
        response_content = getattr(response, "content", b"")
        logger.info(
            "llm.request.completed endpoint=%s status_code=%s duration_ms=%.2f response_bytes=%s",
            self.completion_url,
            getattr(response, "status_code", "unknown"),
            duration_ms,
            len(response_content),
        )

        try:
            data = response.json()
        except ValueError as error:
            logger.exception("llm.response.invalid_json response_bytes=%s", len(response_content))
            raise LlmServiceError("Local LLM returned invalid JSON") from error

        if not isinstance(data, dict):
            logger.error("llm.response.invalid_type response_type=%s", type(data).__name__)
            raise LlmServiceError("Local LLM returned an invalid response")

        content = data.get("content")
        if not isinstance(content, str) or not content.strip():
            logger.error("llm.response.empty_content")
            raise LlmServiceError("Local LLM returned an empty answer")

        return content.strip()


llm_client = LlmClient()

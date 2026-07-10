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
from app.observability_events import include_llm_payload, log_event

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
        temperature: float | None = None,
    ) -> str:
        payload = {
            "prompt": prompt,
            "n_predict": max_tokens if max_tokens is not None else LLM_MAX_TOKENS,
            "temperature": temperature if temperature is not None else LLM_TEMPERATURE,
            "stop": stop if stop is not None else ["User:"],
        }
        if json_schema is not None:
            payload["json_schema"] = json_schema

        started_at = time.perf_counter()
        log_event(
            "llm.request.started",
            endpoint=self.completion_url,
            prompt_length=len(prompt),
            has_json_schema=json_schema is not None,
            max_tokens=payload["n_predict"],
            temperature=payload["temperature"],
            stop=payload["stop"],
            **include_llm_payload(prompt=prompt),
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
            log_event(
                "llm.request.failed",
                level=logging.ERROR,
                endpoint=self.completion_url,
                duration_ms=round(duration_ms, 2),
                error_type=type(error).__name__,
                error=str(error),
            )
            raise LlmServiceError(f"Local LLM request failed: {error}") from error

        duration_ms = (time.perf_counter() - started_at) * 1000
        response_content = getattr(response, "content", b"")
        log_event(
            "llm.request.completed",
            endpoint=self.completion_url,
            status_code=getattr(response, "status_code", "unknown"),
            duration_ms=round(duration_ms, 2),
            response_bytes=len(response_content),
        )

        try:
            data = response.json()
        except ValueError as error:
            log_event(
                "llm.response.invalid_json",
                level=logging.ERROR,
                response_bytes=len(response_content),
                error_type=type(error).__name__,
                error=str(error),
            )
            raise LlmServiceError("Local LLM returned invalid JSON") from error

        if not isinstance(data, dict):
            log_event(
                "llm.response.invalid_type",
                level=logging.ERROR,
                response_type=type(data).__name__,
                **include_llm_payload(raw_response=str(data)),
            )
            raise LlmServiceError("Local LLM returned an invalid response")

        content = data.get("content")
        if not isinstance(content, str) or not content.strip():
            log_event(
                "llm.response.empty_content",
                level=logging.ERROR,
                **include_llm_payload(raw_response=str(data)),
            )
            raise LlmServiceError("Local LLM returned an empty answer")

        log_event(
            "llm.response.parsed",
            content_length=len(content),
            **include_llm_payload(raw_response=content),
        )
        return content.strip()


llm_client = LlmClient()

import asyncio
from urllib.parse import urljoin

import httpx

from app.config import (
    LLM_BASE_URL,
    LLM_COMPLETION_ENDPOINT,
    LLM_MAX_TOKENS,
    LLM_TEMPERATURE,
    LLM_TIMEOUT_SECONDS,
)


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

        try:
            async with self._request_lock:
                async with httpx.AsyncClient(
                    timeout=LLM_TIMEOUT_SECONDS,
                    trust_env=False,
                ) as client:
                    response = await client.post(self.completion_url, json=payload)
                    response.raise_for_status()
        except httpx.HTTPError as error:
            raise LlmServiceError(f"Local LLM request failed: {error}") from error

        try:
            data = response.json()
        except ValueError as error:
            raise LlmServiceError("Local LLM returned invalid JSON") from error

        if not isinstance(data, dict):
            raise LlmServiceError("Local LLM returned an invalid response")

        content = data.get("content")
        if not isinstance(content, str) or not content.strip():
            raise LlmServiceError("Local LLM returned an empty answer")

        return content.strip()


llm_client = LlmClient()

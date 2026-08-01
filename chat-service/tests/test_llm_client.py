import json
import unittest
from unittest.mock import patch

import httpx

from app.services.llm_client import LlmClient, LlmServiceError
from app.services.llm_metrics import LlmRequestMetrics, reset_llm_request_metrics, set_llm_request_metrics


class FakeResponse:
    def __init__(
        self,
        data: object = None,
        *,
        status_error: httpx.HTTPError | None = None,
        invalid_json: bool = False,
    ) -> None:
        self.data = data
        self.status_error = status_error
        self.invalid_json = invalid_json

    def raise_for_status(self) -> None:
        if self.status_error:
            raise self.status_error

    def json(self) -> object:
        if self.invalid_json:
            raise json.JSONDecodeError("Invalid JSON", "", 0)
        return self.data


class FakeAsyncClient:
    response = FakeResponse({"content": "Generated answer"})
    last_url: str | None = None
    last_payload: dict | None = None

    def __init__(self, **_: object) -> None:
        pass

    async def __aenter__(self) -> "FakeAsyncClient":
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    async def post(self, url: str, json: dict) -> FakeResponse:
        type(self).last_url = url
        type(self).last_payload = json
        return type(self).response


class FakeStreamResponse:
    def raise_for_status(self) -> None:
        return None

    async def __aenter__(self) -> "FakeStreamResponse":
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    async def aiter_lines(self):
        yield 'data: {"content":"Hi","stop":false}'
        yield (
            'data: {"content":"","stop":true,'
            '"model":"/models/Qwen.gguf","tokens_evaluated":8,"tokens_predicted":2,'
            '"timings":{"predicted_ms":500}}'
        )


class FakeStreamingClient:
    def __init__(self, **_: object) -> None:
        pass

    async def __aenter__(self) -> "FakeStreamingClient":
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    def stream(self, *_: object, **__: object) -> FakeStreamResponse:
        return FakeStreamResponse()


class LlmClientTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        FakeAsyncClient.response = FakeResponse({"content": "  Generated answer  "})
        FakeAsyncClient.last_url = None
        FakeAsyncClient.last_payload = None

    async def test_sends_completion_request_and_returns_trimmed_content(self) -> None:
        with (
            patch("app.services.llm_client.LLM_BASE_URL", "http://llama.test:8080"),
            patch("app.services.llm_client.LLM_COMPLETION_ENDPOINT", "/completion"),
            patch("app.services.llm_client.LLM_MAX_TOKENS", 80),
            patch("app.services.llm_client.LLM_TEMPERATURE", 0.4),
            patch("app.services.llm_client.httpx.AsyncClient", FakeAsyncClient),
        ):
            answer = await LlmClient().complete("Create an invoice note.")

        self.assertEqual(answer, "Generated answer")
        self.assertEqual(FakeAsyncClient.last_url, "http://llama.test:8080/completion")
        self.assertEqual(
            FakeAsyncClient.last_payload,
            {
                "prompt": "User: Create an invoice note.\nAssistant:",
                "n_predict": 80,
                "temperature": 0.4,
                "stop": ["User:"],
            },
        )

    async def test_wraps_http_errors(self) -> None:
        FakeAsyncClient.response = FakeResponse(
            status_error=httpx.ConnectError("Connection refused")
        )

        with (
            patch("app.services.llm_client.httpx.AsyncClient", FakeAsyncClient),
            self.assertRaisesRegex(
                LlmServiceError,
                "Local LLM request failed: Connection refused",
            ),
        ):
            await LlmClient().complete("Create an invoice note.")

    async def test_includes_json_schema_when_provided(self) -> None:
        schema = {"type": "object"}

        with patch("app.services.llm_client.httpx.AsyncClient", FakeAsyncClient):
            await LlmClient().complete_prompt("Return JSON.", json_schema=schema)

        self.assertEqual(FakeAsyncClient.last_payload["json_schema"], schema)

    async def test_allows_completion_options(self) -> None:
        with patch("app.services.llm_client.httpx.AsyncClient", FakeAsyncClient):
            await LlmClient().complete_prompt(
                "Choose an action.",
                max_tokens=8,
                stop=["User:"],
            )

        self.assertEqual(FakeAsyncClient.last_payload["n_predict"], 8)
        self.assertEqual(FakeAsyncClient.last_payload["stop"], ["User:"])

    async def test_rejects_invalid_json(self) -> None:
        FakeAsyncClient.response = FakeResponse(invalid_json=True)

        with (
            patch("app.services.llm_client.httpx.AsyncClient", FakeAsyncClient),
            self.assertRaisesRegex(LlmServiceError, "returned invalid JSON"),
        ):
            await LlmClient().complete("Create an invoice note.")

    async def test_rejects_empty_content(self) -> None:
        FakeAsyncClient.response = FakeResponse({"content": "   "})

        with (
            patch("app.services.llm_client.httpx.AsyncClient", FakeAsyncClient),
            self.assertRaisesRegex(LlmServiceError, "returned an empty answer"),
        ):
            await LlmClient().complete("Create an invoice note.")

    async def test_records_final_stream_metrics(self) -> None:
        metrics = LlmRequestMetrics(request_id="request-1", trace_id="trace-1")
        token = set_llm_request_metrics(metrics)
        try:
            with patch("app.services.llm_client.httpx.AsyncClient", FakeStreamingClient):
                chunks = [chunk async for chunk in LlmClient().stream_prompt("Say hi")]
        finally:
            reset_llm_request_metrics(token)

        self.assertEqual(chunks, ["Hi"])
        self.assertEqual(metrics.llm_calls, 1)
        self.assertEqual(metrics.prompt_tokens, 8)
        self.assertEqual(metrics.completion_tokens, 2)
        self.assertEqual(metrics.model, "Qwen.gguf")


if __name__ == "__main__":
    unittest.main()

import unittest
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from app.routes.ai import test_ai
from app.schemas import AiTestRequest
from app.services.llm_client import LlmServiceError


class AiRouteTests(unittest.IsolatedAsyncioTestCase):
    async def test_returns_llm_answer(self) -> None:
        with patch(
            "app.routes.ai.llm_client.complete",
            AsyncMock(return_value="Generated answer"),
        ):
            response = await test_ai(AiTestRequest(message="Create an invoice note."))

        self.assertEqual(response.answer, "Generated answer")

    async def test_maps_llm_failure_to_bad_gateway(self) -> None:
        with patch(
            "app.routes.ai.llm_client.complete",
            AsyncMock(side_effect=LlmServiceError("Local LLM is offline")),
        ):
            with self.assertRaises(HTTPException) as context:
                await test_ai(AiTestRequest(message="Create an invoice note."))

        self.assertEqual(context.exception.status_code, 502)
        self.assertEqual(context.exception.detail, "Local LLM is offline")


if __name__ == "__main__":
    unittest.main()

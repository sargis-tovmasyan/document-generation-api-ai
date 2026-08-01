import unittest

from app.services.llm_metrics import LlmRequestMetrics


class LlmRequestMetricsTests(unittest.TestCase):
    def test_aggregates_llm_calls_into_response_diagnostics(self) -> None:
        metrics = LlmRequestMetrics(request_id="request-123", trace_id="trace-456")

        metrics.record_response(
            {
                "model": "/models/Qwen2.5-3B-Instruct-Q4_K_M.gguf",
                "tokens_evaluated": 100,
                "tokens_predicted": 10,
                "timings": {"predicted_ms": 1000},
            }
        )
        metrics.record_response(
            {
                "model": "/models/Qwen2.5-3B-Instruct-Q4_K_M.gguf",
                "tokens_evaluated": 200,
                "tokens_predicted": 20,
                "timings": {"predicted_ms": 2000},
            }
        )

        diagnostics = metrics.snapshot(duration_ms=4500)

        self.assertEqual(
            diagnostics,
            {
                "request_id": "request-123",
                "trace_id": "trace-456",
                "model": "Qwen2.5-3B-Instruct-Q4_K_M.gguf",
                "duration_ms": 4500,
                "llm_calls": 2,
                "prompt_tokens": 300,
                "completion_tokens": 30,
                "total_tokens": 330,
                "tokens_per_second": 10.0,
            },
        )
        self.assertNotIn("confidence", diagnostics)

    def test_keeps_unavailable_metrics_as_null(self) -> None:
        metrics = LlmRequestMetrics(
            request_id="request-123",
            trace_id=None,
            default_model="Configured-Qwen.gguf",
        )

        self.assertEqual(
            metrics.snapshot(duration_ms=25),
            {
                "request_id": "request-123",
                "trace_id": None,
                "model": "Configured-Qwen.gguf",
                "duration_ms": 25,
                "llm_calls": 0,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
                "tokens_per_second": None,
            },
        )

    def test_counts_cached_and_processed_prompt_tokens(self) -> None:
        metrics = LlmRequestMetrics(request_id="request-123", trace_id=None)

        metrics.record_response(
            {
                "tokens_evaluated": 1,
                "timings": {
                    "cache_n": 40,
                    "prompt_n": 10,
                    "predicted_n": 5,
                    "predicted_ms": 1000,
                },
            }
        )

        diagnostics = metrics.snapshot(duration_ms=1200)
        self.assertEqual(diagnostics["prompt_tokens"], 50)
        self.assertEqual(diagnostics["completion_tokens"], 5)
        self.assertEqual(diagnostics["total_tokens"], 55)


if __name__ == "__main__":
    unittest.main()

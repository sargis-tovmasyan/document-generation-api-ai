from __future__ import annotations

from contextvars import ContextVar, Token
from pathlib import PurePath
from typing import Any


class LlmRequestMetrics:
    def __init__(
        self,
        *,
        request_id: str | None,
        trace_id: str | None,
        default_model: str | None = None,
    ) -> None:
        self.request_id = request_id
        self.trace_id = trace_id
        self.model = PurePath(default_model).name if default_model else None
        self.llm_calls = 0
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.prediction_ms = 0.0
        self.finalized = False

    def record_response(self, data: dict[str, Any]) -> None:
        if self.finalized:
            return
        self.llm_calls += 1
        model = data.get("model")
        if isinstance(model, str) and model:
            self.model = PurePath(model).name

        timings = data.get("timings")
        if isinstance(timings, dict):
            if "prompt_n" in timings or "cache_n" in timings:
                self.prompt_tokens += _non_negative_int(timings.get("prompt_n"))
                self.prompt_tokens += _non_negative_int(timings.get("cache_n"))
            else:
                self.prompt_tokens += _non_negative_int(data.get("tokens_evaluated"))

            if "predicted_n" in timings:
                self.completion_tokens += _non_negative_int(timings.get("predicted_n"))
            else:
                self.completion_tokens += _non_negative_int(data.get("tokens_predicted"))

            predicted_ms = timings.get("predicted_ms")
            if isinstance(predicted_ms, (int, float)) and not isinstance(predicted_ms, bool) and predicted_ms > 0:
                self.prediction_ms += float(predicted_ms)
        else:
            self.prompt_tokens += _non_negative_int(data.get("tokens_evaluated"))
            self.completion_tokens += _non_negative_int(data.get("tokens_predicted"))

    def snapshot(self, *, duration_ms: float) -> dict[str, Any]:
        tokens_per_second = None
        if self.completion_tokens > 0 and self.prediction_ms > 0:
            tokens_per_second = round(self.completion_tokens * 1000 / self.prediction_ms, 2)

        self.finalized = True
        return {
            "request_id": self.request_id,
            "trace_id": self.trace_id,
            "model": self.model,
            "duration_ms": round(duration_ms, 2),
            "llm_calls": self.llm_calls,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.prompt_tokens + self.completion_tokens,
            "tokens_per_second": tokens_per_second,
        }


_metrics_context: ContextVar[LlmRequestMetrics | None] = ContextVar("llm_request_metrics", default=None)


def set_llm_request_metrics(metrics: LlmRequestMetrics) -> Token[LlmRequestMetrics | None]:
    return _metrics_context.set(metrics)


def reset_llm_request_metrics(token: Token[LlmRequestMetrics | None]) -> None:
    _metrics_context.reset(token)


def record_llm_response(data: dict[str, Any]) -> None:
    metrics = _metrics_context.get()
    if metrics is not None:
        metrics.record_response(data)


def _non_negative_int(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    return max(0, int(value))

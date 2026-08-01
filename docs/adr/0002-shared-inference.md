# ADR 0002: Shared Inference Deployment

Status: accepted.

Chat and Document use an HTTP-compatible llama.cpp deployment configured through the existing `LLM_*` and `LLAMA_*` variables. The model directory remains at workspace root. Sharing inference avoids duplicating VRAM; service clients remain replaceable and independently configurable.

# Automatic llama.cpp GPU Acceleration

## Goal

Use the NVIDIA RTX 3090 automatically when the backend starts on the Windows development workstation, while preserving the current CPU-only behavior on the Ubuntu VPS and any machine where Docker cannot use an NVIDIA GPU.

The normal entry point remains:

```sh
./start.sh
```

No manual Compose command or configuration change is required for the normal case.

## Scope

This change affects only the backend's llama.cpp container and startup orchestration. It does not change the Qwen model, API behavior, frontend configuration, observability services, or model download flow.

Supported accelerator selections are:

- `LLAMA_ACCELERATOR=auto` (default): use CUDA when a Docker NVIDIA GPU probe succeeds; otherwise use the existing CPU service.
- `LLAMA_ACCELERATOR=cuda`: require CUDA and stop with a clear error if Docker cannot access an NVIDIA GPU.
- `LLAMA_ACCELERATOR=cpu`: always use the existing CPU service.

The target environments are Docker Desktop with WSL2 GPU support on the Windows RTX 3090 workstation and Docker Engine without a GPU on the Ubuntu VPS. Other platforms safely follow the CPU fallback when the NVIDIA probe cannot succeed.

## Architecture

The existing `docker-compose.yml` and `docker/llama.cpp/Dockerfile` remain the complete CPU implementation. A second Compose file overlays only the `llama-server` service for CUDA mode. A separate CUDA Dockerfile builds llama.cpp with CUDA enabled and provides the required CUDA runtime libraries.

This structure keeps the VPS path independent of CUDA images, NVIDIA runtime configuration, and GPU-specific options. It also makes forced CPU mode useful for troubleshooting and regression checks.

### Components

1. `start.sh`
   - Validates `LLAMA_ACCELERATOR`.
   - Detects the available Compose command as it does today.
   - In `auto` and `cuda` modes, checks for the NVIDIA Docker runtime and performs a real container GPU probe.
   - Selects either the base Compose configuration or the base plus CUDA override.
   - Prints the selected accelerator before building and starting services.

2. `docker-compose.gpu.yml`
   - Overrides the llama.cpp build to use the CUDA Dockerfile.
   - Reserves one NVIDIA GPU for `llama-server`.
   - Enables full model-layer offload and Flash Attention through llama.cpp arguments.
   - Leaves all API, networking, health-check, volume, port, and observability settings inherited from the base Compose file.

3. `docker/llama.cpp/Dockerfile.cuda`
   - Uses pinned NVIDIA CUDA development and runtime image tags.
   - Builds the same llama.cpp ref as the CPU image with `GGML_CUDA=ON`.
   - Copies `llama-server` and its llama.cpp libraries into the CUDA runtime image.
   - Reuses the existing entrypoint.

4. `docker/llama.cpp/entrypoint.sh`
   - Continues to own common llama.cpp arguments.
   - Accepts the GPU-specific arguments supplied by the Compose override through the existing `LLAMA_EXTRA_ARGS` interface.

## Startup Flow

`start.sh` loads `.env`, validates the requested accelerator, verifies Docker and Compose, and ensures the model exists as it does today.

For `LLAMA_ACCELERATOR=cpu`, it selects only `docker-compose.yml`.

For `LLAMA_ACCELERATOR=auto` or `cuda`, it first checks whether Docker advertises an NVIDIA runtime. When it does, the script runs a short CUDA container probe that must successfully execute `nvidia-smi`. A successful probe selects:

```text
docker-compose.yml + docker-compose.gpu.yml
```

If the probe fails in `auto` mode, the script prints the reason and selects only the CPU base file. If it fails in `cuda` mode, startup stops before any running stack is replaced.

Once the configuration is selected, the existing down, build, up, status, and usage-output flow uses the selected Compose file list consistently.

## Safety and Failure Handling

- GPU detection is based on Docker container access, not merely the host `nvidia-smi` command. This avoids selecting CUDA when Docker Desktop or the NVIDIA container runtime is misconfigured.
- Auto mode falls back only when GPU capability detection fails. If detection succeeds but the CUDA image fails to build or the CUDA service fails to start, startup fails visibly instead of silently replacing it with CPU mode.
- Forced CUDA validates capability before `docker compose down`, so a missing runtime does not stop a currently running stack.
- Forced CPU never pulls or builds CUDA images and never requests a GPU.
- The CPU Dockerfile and base Compose service retain their current behavior and remain the VPS fallback.
- Existing environment values are preserved. The GPU override adds default GPU arguments only for CUDA mode; users can still replace `LLAMA_EXTRA_ARGS` explicitly when troubleshooting.

## Cross-Platform Behavior

- Windows workstation with working Docker Desktop WSL2 NVIDIA integration: `auto` selects CUDA.
- Ubuntu VPS without an NVIDIA runtime or GPU: `auto` reports CPU fallback and starts the unchanged CPU image.
- A system with a host GPU but no working Docker GPU integration: `auto` uses CPU; `cuda` reports the failed probe and exits.
- Shell path-conversion protection already present for `LLAMA_MODEL_PATH` remains unchanged.

## Verification

Implementation is complete only when all of the following pass:

1. Shell syntax validation for the startup and entrypoint scripts.
2. `docker compose config` validation for the CPU configuration and merged CUDA configuration.
3. `LLAMA_ACCELERATOR=cpu ./start.sh` starts a healthy CPU stack without a Docker GPU device request.
4. On the RTX 3090 workstation, default `./start.sh` selects CUDA.
5. Docker inspection shows an NVIDIA GPU device request for `llama-server`.
6. The llama.cpp container can run `nvidia-smi`, its logs identify the CUDA backend, and the logs show model layers offloaded to GPU memory.
7. The backend `/health` endpoint succeeds.
8. The frontend's proxied backend health request succeeds.
9. A fixed generation benchmark is compared with the observed CPU baseline of approximately 5.7 tokens per second.
10. After the CPU fallback check, the workstation is returned to healthy automatic CUDA mode.

## Non-Goals

- Selecting a different model or quantization.
- CPU thread tuning beyond preserving the existing fallback.
- Supporting AMD, Intel, or Apple GPU acceleration.
- Changing frontend container behavior.
- Hiding CUDA build or runtime failures after a successful GPU capability probe.

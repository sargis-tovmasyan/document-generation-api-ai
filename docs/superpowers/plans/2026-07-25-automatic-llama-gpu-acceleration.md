# Automatic llama.cpp GPU Acceleration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `./start.sh` use the RTX 3090 through Docker CUDA automatically while retaining the existing CPU-only stack on hosts without Docker NVIDIA GPU access.

**Architecture:** The base Compose file and existing llama.cpp Dockerfile remain CPU-only. A CUDA Dockerfile and Compose override replace only the `llama-server` build and GPU settings, while testable functions in `start.sh` select the correct Compose file set before the running stack is stopped.

**Tech Stack:** Bash, Docker Compose, multi-stage Docker builds, NVIDIA CUDA 12.6.3, llama.cpp CMake, Qwen2.5 GGUF.

## Global Constraints

- `LLAMA_ACCELERATOR=auto` is the default; accepted values are exactly `auto`, `cuda`, and `cpu`.
- Auto mode uses CUDA only when Docker advertises the NVIDIA runtime and a real CUDA container can run `nvidia-smi`.
- Forced CUDA exits before `docker compose down` when the GPU probe fails.
- Forced CPU does not pull, build, or request CUDA.
- A CUDA build or startup failure after a successful probe remains a visible failure and does not silently fall back.
- The existing Qwen model, API behavior, networking, ports, volumes, health checks, observability services, and CPU Dockerfile behavior remain unchanged.
- The Windows RTX 3090 workstation selects CUDA; the GPU-less Ubuntu VPS retains CPU mode.
- Existing Git Bash path-conversion protection for `LLAMA_MODEL_PATH` remains unchanged.

---

## File Structure

- Create `docker/llama.cpp/Dockerfile.cuda`: build and run a CUDA-enabled llama.cpp server.
- Create `docker-compose.gpu.yml`: override only the llama-server build, GPU reservation, and default GPU arguments.
- Create `tests/start-accelerator-selection.sh`: dependency-free Bash tests for accelerator validation and selection.
- Modify `start.sh`: expose small selection functions, choose Compose files before shutdown, and print the active accelerator.
- Modify `docs/llama-cpp-docker.md`: document automatic, forced CUDA, and forced CPU startup.

### Task 1: Accelerator Selection Logic

**Files:**
- Create: `tests/start-accelerator-selection.sh`
- Modify: `start.sh`

**Interfaces:**
- Produces: `validate_accelerator VALUE`, returning success only for `auto|cuda|cpu`.
- Produces: `docker_has_nvidia_runtime`, returning success when `docker info --format '{{json .Runtimes}}'` contains `nvidia`.
- Produces: `probe_nvidia_gpu`, returning success when the pinned CUDA probe container runs `nvidia-smi`.
- Produces: `select_accelerator REQUESTED`, printing exactly `cpu` or `cuda` to stdout and diagnostics to stderr.
- Consumes: `NVIDIA_CUDA_VERSION`, defaulting to `12.6.3`.

- [ ] **Step 1: Write the failing selection tests**

Create `tests/start-accelerator-selection.sh` with a source-only guard and function overrides:

```bash
#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
START_SH_SOURCE_ONLY=true
# shellcheck disable=SC1091
. "${ROOT_DIR}/start.sh"

fail() {
    echo "FAIL: $*" >&2
    exit 1
}

assert_equal() {
    local expected="$1"
    local actual="$2"
    local message="$3"
    [ "${actual}" = "${expected}" ] || fail "${message}: expected ${expected}, got ${actual}"
}

validate_accelerator auto || fail "auto should be valid"
validate_accelerator cuda || fail "cuda should be valid"
validate_accelerator cpu || fail "cpu should be valid"
if validate_accelerator invalid; then
    fail "invalid accelerator should fail"
fi

docker_has_nvidia_runtime() { return 0; }
probe_nvidia_gpu() { return 0; }
assert_equal cuda "$(select_accelerator auto)" "auto with working NVIDIA Docker"
assert_equal cuda "$(select_accelerator cuda)" "forced CUDA with working NVIDIA Docker"
assert_equal cpu "$(select_accelerator cpu)" "forced CPU"

docker_has_nvidia_runtime() { return 1; }
probe_nvidia_gpu() { return 1; }
assert_equal cpu "$(select_accelerator auto 2>/dev/null)" "auto without NVIDIA Docker"
if select_accelerator cuda >/dev/null 2>&1; then
    fail "forced CUDA should fail without NVIDIA Docker"
fi

echo "Accelerator selection tests passed."
```

- [ ] **Step 2: Run the test and verify it fails**

Run:

```bash
bash tests/start-accelerator-selection.sh
```

Expected: non-zero exit because `validate_accelerator` and the other selection functions do not exist.

- [ ] **Step 3: Add testable selection functions and a source-only guard**

In `start.sh`, define:

```bash
NVIDIA_CUDA_VERSION="${NVIDIA_CUDA_VERSION:-12.6.3}"

validate_accelerator() {
    case "$1" in
        auto|cuda|cpu) return 0 ;;
        *) return 1 ;;
    esac
}

docker_has_nvidia_runtime() {
    docker info --format '{{json .Runtimes}}' 2>/dev/null | grep -q '"nvidia"'
}

probe_nvidia_gpu() {
    docker run --rm --gpus all \
        "nvidia/cuda:${NVIDIA_CUDA_VERSION}-base-ubuntu24.04" \
        nvidia-smi >/dev/null 2>&1
}

select_accelerator() {
    local requested="$1"

    if [ "${requested}" = "cpu" ]; then
        printf '%s\n' cpu
        return 0
    fi

    if docker_has_nvidia_runtime && probe_nvidia_gpu; then
        printf '%s\n' cuda
        return 0
    fi

    if [ "${requested}" = "cuda" ]; then
        echo "CUDA was requested, but Docker cannot access an NVIDIA GPU." >&2
        return 1
    fi

    echo "Docker NVIDIA GPU access is unavailable; using CPU mode." >&2
    printf '%s\n' cpu
}
```

Move executable startup into `main()` and finish the script with:

```bash
if [ "${START_SH_SOURCE_ONLY:-false}" != "true" ]; then
    main "$@"
fi
```

Inside `main`, validate before model download or stack shutdown:

```bash
local requested_accelerator="${LLAMA_ACCELERATOR:-auto}"
if ! validate_accelerator "${requested_accelerator}"; then
    echo "Invalid LLAMA_ACCELERATOR: ${requested_accelerator}" >&2
    echo "Expected one of: auto, cuda, cpu" >&2
    return 1
fi

ACTIVE_ACCELERATOR="$(select_accelerator "${requested_accelerator}")" || return 1
export ACTIVE_ACCELERATOR
```

- [ ] **Step 4: Run selection and syntax tests**

Run:

```bash
bash -n start.sh
bash tests/start-accelerator-selection.sh
```

Expected: both commands exit zero and the test prints `Accelerator selection tests passed.`

- [ ] **Step 5: Commit the selection logic**

```bash
git add start.sh tests/start-accelerator-selection.sh
git commit -m "feat: select llama accelerator safely"
```

### Task 2: CUDA llama.cpp Image and Compose Override

**Files:**
- Create: `docker/llama.cpp/Dockerfile.cuda`
- Create: `docker-compose.gpu.yml`

**Interfaces:**
- Consumes: build argument `LLAMA_CPP_REF`, matching the CPU Dockerfile.
- Consumes: `NVIDIA_CUDA_VERSION`, default `12.6.3`, for both CUDA stages.
- Produces: a `llama-server` image containing CUDA-enabled llama.cpp binaries and the existing entrypoint.
- Produces: a Compose override that reserves one NVIDIA GPU and defaults `LLAMA_EXTRA_ARGS` to `--gpu-layers all --flash-attn on`.

- [ ] **Step 1: Add a failing Compose/configuration check**

Before creating the files, run:

```bash
docker compose -f docker-compose.yml -f docker-compose.gpu.yml config
```

Expected: non-zero exit because `docker-compose.gpu.yml` does not exist.

- [ ] **Step 2: Create the CUDA Dockerfile**

Create `docker/llama.cpp/Dockerfile.cuda`:

```dockerfile
ARG NVIDIA_CUDA_VERSION=12.6.3

FROM nvidia/cuda:${NVIDIA_CUDA_VERSION}-devel-ubuntu24.04 AS builder

ARG LLAMA_CPP_REF=master

RUN apt-get update \
    && apt-get install --no-install-recommends -y \
        build-essential \
        ca-certificates \
        cmake \
        git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /src

RUN git clone --depth 1 --branch "${LLAMA_CPP_REF}" https://github.com/ggml-org/llama.cpp.git . \
    && cmake -B build \
        -DGGML_CUDA=ON \
        -DGGML_NATIVE=OFF \
        -DGGML_OPENMP=OFF \
        -DLLAMA_CURL=OFF \
    && cmake --build build --config Release -j 1 --target llama-server

ARG NVIDIA_CUDA_VERSION=12.6.3
FROM nvidia/cuda:${NVIDIA_CUDA_VERSION}-runtime-ubuntu24.04

RUN apt-get update \
    && apt-get install --no-install-recommends -y \
        ca-certificates \
        curl \
        libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /src/build/bin/llama-server /usr/local/bin/llama-server
COPY --from=builder /src/build/bin/*.so* /usr/local/lib/
COPY docker/llama.cpp/entrypoint.sh /usr/local/bin/llama-server-entrypoint

RUN chmod +x /usr/local/bin/llama-server-entrypoint \
    && ldconfig

EXPOSE 8080

ENTRYPOINT ["/usr/local/bin/llama-server-entrypoint"]
```

- [ ] **Step 3: Create the GPU Compose override**

Create `docker-compose.gpu.yml`:

```yaml
services:
  llama-server:
    build:
      context: .
      dockerfile: docker/llama.cpp/Dockerfile.cuda
      args:
        LLAMA_CPP_REF: "${LLAMA_CPP_REF:-master}"
        NVIDIA_CUDA_VERSION: "${NVIDIA_CUDA_VERSION:-12.6.3}"
    environment:
      LLAMA_EXTRA_ARGS: "${LLAMA_EXTRA_ARGS:---gpu-layers all --flash-attn on}"
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: 1
              capabilities: [gpu]
```

- [ ] **Step 4: Validate both Compose configurations**

Run:

```bash
docker compose -f docker-compose.yml config --quiet
docker compose -f docker-compose.yml -f docker-compose.gpu.yml config --quiet
docker compose -f docker-compose.yml -f docker-compose.gpu.yml config |
    grep -q 'Dockerfile.cuda'
docker compose -f docker-compose.yml -f docker-compose.gpu.yml config |
    grep -q 'capabilities:'
```

Expected: every command exits zero.

- [ ] **Step 5: Commit the CUDA image and override**

```bash
git add docker/llama.cpp/Dockerfile.cuda docker-compose.gpu.yml
git commit -m "feat: add CUDA llama server image"
```

### Task 3: Apply the Selected Compose Configuration

**Files:**
- Modify: `start.sh`
- Modify: `tests/start-accelerator-selection.sh`

**Interfaces:**
- Consumes: `ACTIVE_ACCELERATOR` from Task 1.
- Produces: `configure_compose_files MODE`, setting `COMPOSE_FILES` to `(-f docker-compose.yml)` for CPU and `(-f docker-compose.yml -f docker-compose.gpu.yml)` for CUDA.
- Produces: `run_compose ARGS...`, invoking the detected Compose command with the selected file list.

- [ ] **Step 1: Extend the test with Compose file assertions**

Add to `tests/start-accelerator-selection.sh`:

```bash
configure_compose_files cpu
assert_equal "-f docker-compose.yml" "${COMPOSE_FILES[*]}" "CPU Compose files"

configure_compose_files cuda
assert_equal \
    "-f docker-compose.yml -f docker-compose.gpu.yml" \
    "${COMPOSE_FILES[*]}" \
    "CUDA Compose files"
```

- [ ] **Step 2: Run the test and verify the new assertions fail**

Run:

```bash
bash tests/start-accelerator-selection.sh
```

Expected: non-zero exit because `configure_compose_files` does not exist.

- [ ] **Step 3: Implement Compose file selection**

Add to `start.sh`:

```bash
configure_compose_files() {
    COMPOSE_FILES=(-f docker-compose.yml)
    if [ "$1" = "cuda" ]; then
        COMPOSE_FILES+=(-f docker-compose.gpu.yml)
    fi
}

run_compose() {
    "${COMPOSE_CMD[@]}" "${COMPOSE_FILES[@]}" "$@"
}
```

After `ACTIVE_ACCELERATOR` is selected, call:

```bash
configure_compose_files "${ACTIVE_ACCELERATOR}"
echo "Selected llama.cpp accelerator: ${ACTIVE_ACCELERATOR}"
```

Replace every lifecycle call with the wrapper:

```bash
run_compose down
run_compose build
run_compose up -d
run_compose ps
```

For a no-cache rebuild, use:

```bash
run_compose build --no-cache
```

Update help and final output to include:

```text
LLAMA_ACCELERATOR               auto (default), cuda, or cpu
NVIDIA_CUDA_VERSION             CUDA image version, default: 12.6.3
```

and:

```bash
echo "  LLAMA_ACCELERATOR=${LLAMA_ACCELERATOR:-auto}"
echo "  ACTIVE_ACCELERATOR=${ACTIVE_ACCELERATOR}"
```

Render the final log commands from the actual selected command:

```bash
echo "CLI logs: ${COMPOSE_CMD[*]} ${COMPOSE_FILES[*]} logs -f api"
echo "LLM logs: ${COMPOSE_CMD[*]} ${COMPOSE_FILES[*]} logs -f llama-server"
echo "Stop all:  ${COMPOSE_CMD[*]} ${COMPOSE_FILES[*]} down"
```

- [ ] **Step 4: Run unit, syntax, and Compose tests**

Run:

```bash
bash -n start.sh
bash tests/start-accelerator-selection.sh
docker compose -f docker-compose.yml config --quiet
docker compose -f docker-compose.yml -f docker-compose.gpu.yml config --quiet
```

Expected: every command exits zero.

- [ ] **Step 5: Commit startup integration**

```bash
git add start.sh tests/start-accelerator-selection.sh
git commit -m "feat: start selected llama compose stack"
```

### Task 4: Document Operator Controls

**Files:**
- Modify: `docs/llama-cpp-docker.md`

**Interfaces:**
- Documents: default automatic selection, forced CPU, forced CUDA, expected fallback, and CUDA failure behavior.

- [ ] **Step 1: Add exact startup examples**

Add a section containing:

````markdown
## Accelerator selection

`./start.sh` uses `LLAMA_ACCELERATOR=auto` by default. It runs a Docker
NVIDIA GPU probe before stopping the current stack. A successful probe uses
the CUDA llama.cpp image; an unavailable GPU or runtime keeps the existing
CPU image.

Force CPU mode:

```sh
LLAMA_ACCELERATOR=cpu ./start.sh
```

Require CUDA:

```sh
LLAMA_ACCELERATOR=cuda ./start.sh
```

Forced CUDA exits without stopping the current stack when Docker cannot
access an NVIDIA GPU. Auto mode does not silently fall back when CUDA was
detected successfully but the CUDA image later fails to build or start.
````

- [ ] **Step 2: Check documentation and repository whitespace**

Run:

```bash
git diff --check
grep -q 'LLAMA_ACCELERATOR=auto' docs/llama-cpp-docker.md
grep -q 'LLAMA_ACCELERATOR=cpu' docs/llama-cpp-docker.md
grep -q 'LLAMA_ACCELERATOR=cuda' docs/llama-cpp-docker.md
```

Expected: every command exits zero.

- [ ] **Step 3: Commit the operator documentation**

```bash
git add docs/llama-cpp-docker.md
git commit -m "docs: explain llama accelerator selection"
```

### Task 5: Runtime Verification and Restoration

**Files:**
- No source changes expected.

**Interfaces:**
- Verifies: CPU fallback, automatic CUDA selection, Docker GPU reservation, CUDA backend loading, model offload, health endpoints, frontend proxy, and generation throughput.

- [ ] **Step 1: Capture the current repository and container state**

Run:

```bash
git status --short
docker compose ps
```

Expected: source changes are committed and the current stack state is recorded.

- [ ] **Step 2: Start and verify forced CPU mode**

Run:

```bash
LLAMA_ACCELERATOR=cpu ./start.sh
docker inspect backend-llama-server-1 \
    --format '{{json .HostConfig.DeviceRequests}}'
curl --fail http://localhost:8000/health
```

Expected: startup prints `Selected llama.cpp accelerator: cpu`, device requests are `null` or empty, and health returns success.

- [ ] **Step 3: Start automatic CUDA mode**

Run:

```bash
./start.sh
```

Expected: the probe succeeds and startup prints `Selected llama.cpp accelerator: cuda`. The initial CUDA image build may take several minutes.

- [ ] **Step 4: Verify GPU access and layer offload**

Run:

```bash
docker inspect backend-llama-server-1 \
    --format '{{json .HostConfig.DeviceRequests}}'
docker compose -f docker-compose.yml -f docker-compose.gpu.yml \
    exec -T llama-server nvidia-smi
docker compose -f docker-compose.yml -f docker-compose.gpu.yml \
    logs llama-server
```

Expected: Docker reports an NVIDIA device request, `nvidia-smi` identifies the RTX 3090, and llama.cpp logs identify CUDA and show all model layers offloaded.

- [ ] **Step 5: Verify backend and frontend connectivity**

Run:

```bash
curl --fail http://localhost:8000/health
curl --fail http://localhost:5173/api/proxy/health \
    -H 'X-Api-Base: http://localhost:8000'
```

Expected: both responses report healthy status.

- [ ] **Step 6: Run a fixed generation benchmark**

Send the same deterministic prompt to llama.cpp with temperature zero and a fixed token limit, then read the timings:

```bash
curl --fail http://127.0.0.1:8080/completion \
    -H 'Content-Type: application/json' \
    -d '{"prompt":"Write the numbers from one to one hundred in words, one per line.","n_predict":256,"temperature":0}'
docker compose -f docker-compose.yml -f docker-compose.gpu.yml \
    logs --tail 100 llama-server
```

Expected: the response succeeds and the llama.cpp timing log reports generation throughput materially above the observed CPU baseline of approximately 5.7 tokens per second.

- [ ] **Step 7: Run the final verification suite**

Run:

```bash
bash -n start.sh
bash -n docker/llama.cpp/entrypoint.sh
bash tests/start-accelerator-selection.sh
docker compose -f docker-compose.yml config --quiet
docker compose -f docker-compose.yml -f docker-compose.gpu.yml config --quiet
curl --fail http://localhost:8000/health
curl --fail http://localhost:5173/api/proxy/health \
    -H 'X-Api-Base: http://localhost:8000'
git diff --check
git status --short
```

Expected: syntax, tests, both Compose configurations, both health paths, and whitespace checks pass; the final running backend uses automatic CUDA mode.

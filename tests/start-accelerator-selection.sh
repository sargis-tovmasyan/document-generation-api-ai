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

    if [ "${actual}" != "${expected}" ]; then
        fail "${message}: expected '${expected}', got '${actual}'"
    fi
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

configure_compose_files cpu
assert_equal "-f docker-compose.yml" "${COMPOSE_FILES[*]}" "CPU Compose files"

configure_compose_files cuda
assert_equal \
    "-f docker-compose.yml -f docker-compose.gpu.yml" \
    "${COMPOSE_FILES[*]}" \
    "CUDA Compose files"

grep -q -- '-DCMAKE_CUDA_ARCHITECTURES=86' \
    "${ROOT_DIR}/docker/llama.cpp/Dockerfile.cuda" ||
    fail "CUDA build should target only RTX 3090 compute capability 8.6"

grep -q -- '-DCMAKE_EXE_LINKER_FLAGS=-Wl,--allow-shlib-undefined' \
    "${ROOT_DIR}/docker/llama.cpp/Dockerfile.cuda" ||
    fail "CUDA build should allow the driver library injected at runtime"

compose_services="$(cd "${ROOT_DIR}" && docker compose -f docker-compose.yml config --services)"
for service in chat-service document-service; do
    if ! printf '%s\n' "${compose_services}" | grep -qx "${service}"; then
        fail "Compose should define independent ${service} container"
    fi
done

compose_config="$(cd "${ROOT_DIR}" && docker compose -f docker-compose.yml config)"
printf '%s\n' "${compose_config}" | grep -q 'target: /app/chat-data' ||
    fail "Chat Service should mount only chat data"
printf '%s\n' "${compose_config}" | grep -q 'target: /app/document-data' ||
    fail "Document Service should mount only document data"
printf '%s\n' "${compose_config}" | grep -q 'target: /app/generated' ||
    fail "Document Service should own generated files"

storage_root="$(mktemp -d)"
trap 'rm -rf "${storage_root}"' EXIT
prepare_service_storage "${storage_root}"
for directory in data/chat data/documents generated; do
    if [ ! -d "${storage_root}/${directory}" ]; then
        fail "start.sh should create ${directory}"
    fi
done

echo "Accelerator selection tests passed."

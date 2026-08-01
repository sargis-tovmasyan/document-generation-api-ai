#!/usr/bin/env sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
BACKEND_DIR=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
CONTRACT_DIR="$BACKEND_DIR/packages/contracts"
OUTPUT_DIR="$CONTRACT_DIR/src"
PROTO_ROOT="$CONTRACT_DIR/schemas/proto"

mkdir -p "$OUTPUT_DIR/backend_contracts/documents/v1"

cd "$BACKEND_DIR"
uv run --package doco-backend-contracts python -m grpc_tools.protoc \
  -I"$PROTO_ROOT" \
  --python_out="$OUTPUT_DIR" \
  --grpc_python_out="$OUTPUT_DIR" \
  "$PROTO_ROOT/backend_contracts/documents/v1/document_service.proto"

#!/usr/bin/env sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
BACKEND_DIR=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
OUTPUT_DIR="$BACKEND_DIR/contracts/python"

mkdir -p "$OUTPUT_DIR/documents/v1"

python -m grpc_tools.protoc \
  -I"$BACKEND_DIR/contracts" \
  --python_out="$OUTPUT_DIR" \
  --grpc_python_out="$OUTPUT_DIR" \
  "$BACKEND_DIR/contracts/documents/v1/document_service.proto"

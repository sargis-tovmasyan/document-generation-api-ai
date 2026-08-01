# ADR 0001: gRPC Document Boundary

Status: accepted.

Chat-to-Document application calls use the versioned `documents.v1` gRPC contract. This provides typed failures and independent deployment while preserving frontend HTTP compatibility routes in Chat. The wire package, service/RPC names, messages, and field numbers are compatibility constraints.

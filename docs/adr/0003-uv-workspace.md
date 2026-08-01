# ADR 0003: uv Workspace

Status: accepted.

Chat, Document, and Contracts are installable Python 3.12 `src/` packages in one uv workspace. A single frozen lockfile provides reproducible local, CI, and image builds. Hatchling builds each package; dependency ownership remains in each package's `pyproject.toml`.

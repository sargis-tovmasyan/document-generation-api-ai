# Local Startup

Run `./start.sh` from the backend root in Git Bash, macOS, or Linux. The script creates service-owned runtime directories, migrates any legacy monolith database, downloads Qwen if absent, detects Docker GPU access, builds images, runs migrations, and starts the stack. Use `./start.sh --no-cache` for a clean rebuild or `LLAMA_ACCELERATOR=cpu ./start.sh` to force CPU mode.

Verify with `docker compose -f compose.yaml -f compose.dev.yaml ps`, then request `/health` and `/ready` on ports 8000 and 8001.

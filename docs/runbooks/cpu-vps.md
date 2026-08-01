# CPU-only Ubuntu VPS

Install Docker Engine and Compose. Set `LLAMA_ACCELERATOR=cpu`; do not include `compose.gpu.yaml`. Set `LLAMA_THREADS` to the available physical CPU cores and adjust context/parallelism for available RAM. Run `./start.sh`. Confirm the resolved stack with `docker compose -f compose.yaml -f compose.dev.yaml config` and inspect llama logs if model loading is slow.

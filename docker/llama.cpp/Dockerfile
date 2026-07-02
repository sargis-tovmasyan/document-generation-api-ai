FROM debian:bookworm-slim AS builder

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
    && cmake -B build -DGGML_NATIVE=OFF -DGGML_OPENMP=OFF -DLLAMA_CURL=OFF \
    && cmake --build build --config Release -j 1 --target llama-server

FROM debian:bookworm-slim

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

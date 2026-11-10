# syntax=docker/dockerfile:1
FROM mambaorg/micromamba:2.3.3@sha256:800e7ade3ffe29c9a9ac2026163131495f8197c3852e572c5835beb4e8a33cd6 AS mamba
FROM node:22.21.1-bookworm-slim@sha256:25b3eb23a00590b7499f2a2ce939322727fcce1b15fdd69754fcd09536a3ae2c AS node
FROM python:3.12-slim-bookworm@sha256:6dd8b6dab6b3adc7812bab69eca5dbdc1a5cc690ad28306d234ffb4ef8ac9753 AS base

COPY --from=mamba /bin/micromamba /usr/local/bin/micromamba
COPY --from=node /usr/local/bin/node /opt/node/bin/node
COPY --from=node /usr/local/lib/node_modules/npm /opt/node/lib/node_modules/npm
ENV PATH=/opt/conda/bin:/opt/node/bin:/usr/local/bin:/usr/bin:/bin \
    PYTHONPATH=/opt/gtoc/src GTOC_BENCH_ROOT=/opt/gtoc \
    PYTHONDONTWRITEBYTECODE=1 DISABLE_UPDATES=1 DISABLE_AUTOUPDATER=1
RUN ln -s ../lib/node_modules/npm/bin/npm-cli.js /opt/node/bin/npm \
    && apt-get update && apt-get install -y --no-install-recommends \
       ca-certificates libstdc++6 libgomp1 bash file git ripgrep build-essential cmake curl pkg-config poppler-utils \
    && rm -rf /var/lib/apt/lists/*
COPY environments/science-linux-64.lock /tmp/science.lock
RUN micromamba create -y -p /opt/conda -f /tmp/science.lock \
    && micromamba list -p /opt/conda --explicit > /opt/science-explicit.txt \
    && micromamba clean --all --yes
ENV LD_LIBRARY_PATH=/opt/conda/lib:/usr/local/nvidia/lib:/usr/local/nvidia/lib64
COPY environments/science-tools.lock /opt/science-tools.lock
COPY environments/science-source.lock /opt/science-source.lock
RUN /opt/conda/bin/python -m pip install --no-cache-dir --require-hashes --no-deps --only-binary=:all: -r /opt/science-tools.lock \
    && PKG_CONFIG_PATH=/opt/conda/lib/pkgconfig /opt/conda/bin/python -m pip install --no-cache-dir --require-hashes --no-deps --no-build-isolation -r /opt/science-source.lock \
    && ln -s /opt/conda/bin/python-zig /usr/local/bin/zig \
    && zig version
# CPU and CUDA variants share all preceding scientific layers. CUDA is installed
# directly, without keeping a CPU-only Torch installation in a lower layer.
ARG TORCH_VARIANT=cpu
RUN /opt/conda/bin/pip install --no-cache-dir torch==2.8.0 filelock==3.32.3 fsspec==2026.7.0 Jinja2==3.1.6 MarkupSafe==3.0.3 --index-url https://download.pytorch.org/whl/${TORCH_VARIANT} --report /opt/pip-install.json \
    && /opt/conda/bin/pip freeze > /opt/science-pip.txt \
    && /opt/conda/bin/python -c "import numpy,scipy,torch,pykep; print(numpy.__version__,scipy.__version__,torch.__version__,pykep.__version__)"
# Keep a universal image by default. Official-client profiles omit other agent
# runtimes while retaining exactly the same scientific tools and problem data.
ARG AGENT=all
LABEL org.gtoc-bench.agent-profile=${AGENT}
COPY environments/package.json environments/package-lock.json /opt/agents/
RUN case "$AGENT" in all|codex|claude-code) ;; *) echo "Unknown agent image profile" >&2; exit 1 ;; esac \
    && npm ci --prefix /opt/agents --omit=dev --cache /tmp/npm-cache \
    && if [ "$AGENT" = codex ]; then rm -rf /opt/agents/node_modules/@anthropic-ai /opt/agents/node_modules/.bin/claude; fi \
    && if [ "$AGENT" = claude-code ]; then rm -rf /opt/agents/node_modules/@openai /opt/agents/node_modules/.bin/codex; fi \
    && if [ "$AGENT" != claude-code ]; then ln -s /opt/agents/node_modules/.bin/codex /opt/node/bin/codex; codex --version; fi \
    && if [ "$AGENT" != codex ]; then ln -s /opt/agents/node_modules/.bin/claude /opt/node/bin/claude; claude --version; fi \
    && rm -rf /tmp/npm-cache
COPY environments/python-agents.lock /opt/python-agents.lock
RUN if [ "$AGENT" = all ]; then /usr/local/bin/python -m venv /opt/agents-python \
    && /opt/agents-python/bin/pip install --no-cache-dir setuptools==80.9.0 wheel==0.45.1 \
    && /opt/agents-python/bin/pip install --no-cache-dir --require-hashes --no-build-isolation -r /opt/python-agents.lock \
    && /opt/agents-python/bin/pip freeze > /opt/python-agents-installed.txt; fi
ENV TIKTOKEN_CACHE_DIR=/opt/tiktoken-cache
COPY environments/antigravity.lock /opt/antigravity.lock
RUN if [ "$AGENT" = all ]; then /usr/local/bin/python -m venv /opt/antigravity-python \
    && /opt/antigravity-python/bin/pip install --no-cache-dir --require-hashes -r /opt/antigravity.lock \
    && /opt/antigravity-python/bin/pip freeze > /opt/antigravity-installed.txt; fi
COPY environments/install-antigravity-runtime.py /opt/install-antigravity-runtime.py
RUN if [ "$AGENT" = all ]; then /usr/local/bin/python /opt/install-antigravity-runtime.py \
    && /opt/agents-python/bin/python -c "import tiktoken; [tiktoken.get_encoding(name) for name in ('cl100k_base','o200k_base','p50k_base','r50k_base')]"; fi
COPY src/ /opt/gtoc/src/
RUN /opt/conda/bin/python -c "import json; from gtoc_bench.hardware import tools_inventory; print(json.dumps(tools_inventory(),indent=2))" > /opt/science-tools.json
COPY environments/swe-tools/ /opt/gtoc/environments/swe-tools/
COPY environments/agents.lock.json /opt/gtoc/environments/agents.lock.json
COPY environments/providers.lock.json /opt/gtoc/environments/providers.lock.json
COPY problems/gtoc12/statement.md problems/gtoc12/instructions.md problems/gtoc12/provenance.json problems/gtoc12/PROVENANCE.md problems/gtoc12/problem.yaml /opt/gtoc/problems/gtoc12/
COPY problems/gtoc13/ /opt/gtoc/problems/gtoc13/
COPY problems/gtocx/ /opt/gtoc/problems/gtocx/
COPY problems/gtoc11/ /opt/gtoc/problems/gtoc11/
COPY problems/gtoc12/data/GTOC12_Asteroids_Data.txt /opt/gtoc/problems/gtoc12/data/
COPY problems/gtoc12/validator/GTOC12_Verify problems/gtoc12/validator/ScoreData.txt /opt/gtoc/problems/gtoc12/validator/
COPY environments/judge-checks/ /opt/gtoc/environments/judge-checks/
COPY environments/finalize-image.py /opt/gtoc/environments/finalize-image.py
RUN mkdir -p /workspace /judge-tmp /problem \
    && /usr/sbin/groupadd -g 1000 agent && /usr/sbin/useradd -u 1000 -g 1000 -M -d /workspace -s /bin/bash agent \
    && /usr/sbin/groupadd -g 1001 judge && /usr/sbin/useradd -u 1001 -g 1001 -M -d /judge-tmp -s /usr/sbin/nologin judge \
    && chown 1000:1000 /workspace && chown 1001:1001 /judge-tmp \
    && chmod 700 /judge-tmp \
    && /opt/conda/bin/python /opt/gtoc/environments/finalize-image.py \
    && printf '#!/opt/conda/bin/python\nfrom gtoc_bench.cli import main\nraise SystemExit(main())\n' > /usr/local/bin/gtoc \
    && chmod 755 /usr/local/bin/gtoc
WORKDIR /workspace
ENTRYPOINT ["/opt/conda/bin/python", "-s", "-P", "-m", "gtoc_bench.bridge"]

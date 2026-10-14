FROM ghcr.io/astral-sh/uv:0.13.1-python3.13-trixie-slim

WORKDIR /app

COPY pyproject.toml uv.lock ./

RUN uv sync --locked --no-dev

COPY gfjproxy ./gfjproxy
COPY presets ./presets

ARG GFJPROXY_BRANCH
ARG GFJPROXY_VERSION

ENV GFJPROXY_BRANCH=${GFJPROXY_BRANCH} \
    GFJPROXY_VERSION=${GFJPROXY_VERSION} \
    PATH="/app/.venv/bin:$PATH" \
    PORT=5000 \
    PYTHONUNBUFFERED=1

EXPOSE 5000

HEALTHCHECK CMD python -c "import httpx2, os, sys; sys.exit(0 if httpx2.get('http://127.0.0.1:' + os.environ.get('PORT', '5000') + '/health').is_success else 1)"

SHELL ["/bin/sh", "-c"]

CMD exec gunicorn -b 0.0.0.0:${PORT} -k gevent -w 1 -t ${GFJPROXY_PROCESS_TIMEOUT:-300} "gfjproxy.app:create_app()"

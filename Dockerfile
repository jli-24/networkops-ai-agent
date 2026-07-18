FROM python:3.11-slim AS builder

WORKDIR /build

COPY pyproject.toml requirements.txt README.md LICENSE THIRD_PARTY_NOTICES.md ./
COPY src ./src

RUN python -m pip wheel --no-cache-dir --no-deps --wheel-dir /dist .


FROM python:3.11-slim AS runtime

ARG APP_UID=10001
ARG APP_GID=10001

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/home/networkops/.local/bin:${PATH}"

RUN groupadd --gid "${APP_GID}" networkops \
    && useradd --uid "${APP_UID}" --gid "${APP_GID}" --create-home networkops

WORKDIR /app

COPY --from=builder /dist/networkops_ai_agent-0.12.0-py3-none-any.whl /tmp/networkops_ai_agent.whl
RUN python -m pip install --no-cache-dir /tmp/networkops_ai_agent.whl \
    && rm /tmp/networkops_ai_agent.whl

COPY --chown=networkops:networkops deployment ./deployment

USER networkops

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)" || exit 1

CMD ["python", "-m", "uvicorn", "deployment.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]

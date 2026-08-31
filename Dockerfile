FROM ghcr.io/astral-sh/uv:0.8.22 AS uv

FROM python:3.12.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

COPY --from=uv /uv /uvx /bin/
WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY src ./src
COPY config ./config
COPY README.md ./
RUN uv sync --frozen --no-dev

RUN groupadd --gid 10001 jeju \
    && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin jeju
USER 10001:10001

EXPOSE 8443
HEALTHCHECK --interval=15s --timeout=3s --retries=3 \
  CMD ["/app/.venv/bin/python", "-c", "import ssl,urllib.request; urllib.request.urlopen('https://127.0.0.1:8443/health', context=ssl._create_unverified_context(), timeout=2).read()"]

ENTRYPOINT ["/app/.venv/bin/jeju-trip-mcp-http"]

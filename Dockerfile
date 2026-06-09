FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

WORKDIR /app

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    MPLCONFIGDIR=/tmp/matplotlib

COPY pyproject.toml uv.lock README.md main.py ./
COPY balance_bot ./balance_bot/

RUN uv sync --frozen --no-dev

ENV PATH="/app/.venv/bin:${PATH}"

CMD ["balance-bot", "-c", "/config/config.yaml"]

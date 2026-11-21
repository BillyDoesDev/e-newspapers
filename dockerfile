FROM ghcr.io/astral-sh/uv:python3.14-trixie-slim

WORKDIR /app

# Install dependencies first so this layer is cached unless the lockfile changes
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev

# Use the venv that uv created
ENV PATH="/app/.venv/bin:$PATH"

# Anything passed after the service name in `docker compose run` becomes args to the script
ENTRYPOINT ["uv", "run", "main.py"]

FROM python:3.12-slim

# Node.js is needed to run the Claude Code CLI (the coding assistant) in headless mode.
# Claude Code refuses --dangerously-skip-permissions when run as root, so this service
# runs as a dedicated non-root user.
RUN apt-get update \
    && apt-get install -y --no-install-recommends nodejs npm \
    && npm install -g @anthropic-ai/claude-code \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --shell /bin/bash appuser

COPY --from=ghcr.io/astral-sh/uv:0.8.22 /uv /uvx /bin/
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen
COPY app.py ./
RUN mkdir -p /data/incidents && chown -R appuser:appuser /data/incidents /app

USER appuser
ENV INCIDENTS_DIR=/data/incidents
EXPOSE 8001
CMD ["uv", "run", "--no-sync", "uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8001"]

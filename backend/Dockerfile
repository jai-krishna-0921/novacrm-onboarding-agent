# Build context: the repo root (needs backend/ and web/):  docker build -f backend/Dockerfile .
# Stage 1 builds the React dashboard; only its static output (web/dist) reaches the runtime image.
FROM node:24.13.0-slim AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web/ ./
RUN npm run build

FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.8.15 /uv /usr/local/bin/uv
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PYTHONUNBUFFERED=1 PATH="/app/.venv/bin:$PATH" WEB_DIST_DIR=/app/web_dist
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY backend/ ./
RUN uv sync --frozen --no-dev
COPY --from=web /web/dist /app/web_dist
# One worker on purpose: the Gmail poller and the scheduler tick run inside the app process.
# No uvicorn access log: Caddy logs requests with the push token redacted.
CMD ["sh", "-c", "alembic upgrade head && python -m app.db.seed && exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1 --proxy-headers --no-access-log --log-config logging.json"]

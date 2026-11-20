# Single-container AITube image (the "work" deployment; see WORK_DEPLOYMENT_PLAN.md).
#
# One process: uvicorn serves the API and the built frontend at /aitube/.
# Unlike the smaug compose setup (backend/Dockerfile + frontend/Dockerfile), it
# needs no nginx, no host content-dlp, no Hermes SSH keys and no cron. It reads
# from the Elasticsearch cluster in ELASTICSEARCH_URL and nothing else.
#
#   docker build -t aitube-work .
#   docker run --env-file .env.work -p 8104:8000 aitube-work
#   open http://localhost:8104/aitube/

# ---- frontend build ---------------------------------------------------------
FROM node:22-alpine AS frontend

ARG VITE_ELASTIC_APM_RUM_ENABLED="false"
ARG VITE_ELASTIC_APM_SERVER_URL=""
ARG VITE_ELASTIC_APM_ENVIRONMENT="work"
ENV VITE_ELASTIC_APM_RUM_ENABLED=$VITE_ELASTIC_APM_RUM_ENABLED \
    VITE_ELASTIC_APM_SERVER_URL=$VITE_ELASTIC_APM_SERVER_URL \
    VITE_ELASTIC_APM_ENVIRONMENT=$VITE_ELASTIC_APM_ENVIRONMENT

WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# ---- runtime ----------------------------------------------------------------
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.9.5 /uv /usr/local/bin/uv

ENV PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

# Dependencies first so code changes don't reinstall them.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY backend/ backend/
COPY --from=frontend /build/dist /app/frontend_dist

# Least privilege by default: this image is the passive deployment. Override
# DEPLOYMENT / FEATURES_* via the environment if needed.
ENV DEPLOYMENT=work \
    SERVE_FRONTEND_DIR=/app/frontend_dist \
    ELASTIC_APM_ENVIRONMENT=work \
    PATH="/app/.venv/bin:$PATH"

RUN useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin aitube
USER 10001

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
  CMD python -c "import urllib.request,sys; sys.exit(urllib.request.urlopen('http://127.0.0.1:8000/health/', timeout=4).status != 200)"

CMD ["uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8000", \
     "--proxy-headers", "--forwarded-allow-ips", "*"]

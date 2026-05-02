# syntax=docker/dockerfile:1.7

# ── Stage 1: Download testssl.sh ─────────────────────────────────────────────
FROM alpine:3.21 AS testssl-dl

ARG TESTSSL_VERSION=3.2

RUN apk add --no-cache curl ca-certificates

RUN curl -fsSL \
      "https://github.com/drwetter/testssl.sh/archive/refs/tags/v${TESTSSL_VERSION}.tar.gz" \
      | tar xz -C /opt \
    && mv /opt/testssl.sh-${TESTSSL_VERSION} /opt/testssl \
    && chmod +x /opt/testssl/testssl.sh \
    && echo "${TESTSSL_VERSION}" > /opt/testssl/VERSION


# ── Stage 2: Python dependencies ─────────────────────────────────────────────
FROM python:3.12-alpine AS python-deps

WORKDIR /build
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt


# ── Stage 3: Runtime ──────────────────────────────────────────────────────────
FROM alpine:3.21 AS runtime

# bind-tools = dig/nslookup, required by testssl.sh
RUN apk add --no-cache \
      bash \
      curl \
      bind-tools \
      openssl \
      ca-certificates \
      python3 \
    && update-ca-certificates

# Non-root user
RUN addgroup -S -g 1000 appuser \
    && adduser -S -D -u 1000 -G appuser -s /sbin/nologin -h /app appuser

# Copy testssl.sh (read-only)
COPY --from=testssl-dl --chown=root:root /opt/testssl /opt/testssl
RUN chmod -R a-w /opt/testssl

# Copy Python packages from build stage
COPY --from=python-deps /install /usr/local

# App directory
WORKDIR /app
COPY --chown=appuser:appuser backend/ /app/backend/
COPY --chown=appuser:appuser frontend/ /app/frontend/

USER appuser

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD curl -fs http://localhost:8000/api/health || exit 1

EXPOSE 8000

ENV TESTSSL_PATH=/opt/testssl/testssl.sh \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

CMD ["python3", "-m", "uvicorn", "backend.main:app", \
     "--host", "0.0.0.0", "--port", "8000", \
     "--workers", "1", "--no-access-log"]

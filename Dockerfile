# syntax=docker/dockerfile:1.7

# ── Stage 1: Download testssl.sh ─────────────────────────────────────────────
FROM alpine:3.21 AS testssl-dl

RUN apk add --no-cache curl ca-certificates

# Resolve latest release via redirect, then download the tarball
RUN TAG=$(curl -fsSLI -o /dev/null -w '%{url_effective}' \
            https://github.com/drwetter/testssl.sh/releases/latest \
          | grep -oE '[^/]+$') \
    && mkdir -p /opt/testssl \
    && curl -fsSL \
         "https://github.com/drwetter/testssl.sh/archive/refs/tags/${TAG}.tar.gz" \
       | tar xz -C /opt/testssl --strip-components=1 \
    && chmod +x /opt/testssl/testssl.sh \
    && echo "${TAG#v}" > /opt/testssl/VERSION


# ── Stage 2: Python dependencies ─────────────────────────────────────────────
FROM python:3.12-alpine AS python-deps

WORKDIR /build
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt


# ── Stage 3: Runtime ──────────────────────────────────────────────────────────
# Use the same Python image as the build stage so site-packages paths match
FROM python:3.12-alpine AS runtime

# bind-tools = dig/nslookup, required by testssl.sh
RUN apk add --no-cache \
      bash \
      curl \
      bind-tools \
      openssl \
      ca-certificates \
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

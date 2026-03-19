# BorgorTube – Python backend Dockerfile
# Multi-stage: slim final image with only runtime deps

# ── Build stage ──────────────────────────────────────────────────────────────
FROM python:3.12-slim AS builder

WORKDIR /app

# System deps needed to build some Python packages
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc g++ libffi-dev libssl-dev && \
    rm -rf /var/lib/apt/lists/*

# Copy and install Python deps into a prefix we can copy cleanly
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install \
    fastapi \
    "uvicorn[standard]" \
    python-multipart \
    yt_dlp \
    requests \
    requests-cache \
    bs4 \
    pyppeteer \
    aiosqlite \
    slowapi

# ── Runtime stage ─────────────────────────────────────────────────────────────
FROM python:3.12-slim

WORKDIR /app

# Runtime system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    mpv \
    ffmpeg \
    chromium \
    chromium-driver \
    && rm -rf /var/lib/apt/lists/*

# Copy installed Python packages from builder
COPY --from=builder /install /usr/local

# Copy application source
COPY backend/ ./backend/
COPY frontend/ ./frontend/

# Persistent data volume mount point
RUN mkdir -p /data
ENV BORGORTUBE_DB=/data/borgortube.db
ENV BORGORTUBE_DOWNLOADS=/data/downloads

# Expose API port
EXPOSE 8000

WORKDIR /app/backend

CMD ["python", "-m", "uvicorn", "main:app", \
     "--host", "0.0.0.0", "--port", "8000", \
     "--workers", "2"]

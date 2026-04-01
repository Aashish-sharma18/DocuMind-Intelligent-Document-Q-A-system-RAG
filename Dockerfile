# ── DocuMind — Dockerfile ─────────────────────────────────────────────────────
# Multi-stage build for a lean production image

# ── Stage 1: Builder ──────────────────────────────────────────────────────────
FROM python:3.11-slim AS builder

WORKDIR /app

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    gcc \
    g++ \
    git \
    && rm -rf /var/lib/apt/lists/*

# Copy and install Python dependencies into a dedicated prefix
COPY requirements.txt .
RUN pip install --upgrade pip && \
    pip install --prefix=/install --no-cache-dir -r requirements.txt

# Pre-download the embedding model so the container starts instantly
RUN python -c "from sentence_transformers import SentenceTransformer; \
               SentenceTransformer('sentence-transformers/all-MiniLM-L6-v2')"


# ── Stage 2: Runtime ──────────────────────────────────────────────────────────
FROM python:3.11-slim AS runtime

WORKDIR /app

# Copy installed packages from builder
COPY --from=builder /install /usr/local
# Copy pre-downloaded HuggingFace models cache
COPY --from=builder /root/.cache /root/.cache

# Copy application source
COPY app.py            .
COPY rag_pipeline.py   .
COPY templates/        ./templates/
COPY static/           ./static/

# Create writable directories for uploaded docs and vector DB
RUN mkdir -p /app/documents /app/chroma_db && \
    chmod 777 /app/documents /app/chroma_db

# Non-root user for security
RUN adduser --disabled-password --no-create-home appuser && \
    chown -R appuser:appuser /app
USER appuser

# Cloud Run injects PORT env var; default to 8080
ENV PORT=8080
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

EXPOSE 8080

# Use gunicorn for production serving
# --workers 1 because we keep an in-memory pipeline singleton
# --timeout 120 for LLM calls that may take a few seconds
CMD ["gunicorn", \
     "--bind", "0.0.0.0:8080", \
     "--workers", "1", \
     "--threads", "4", \
     "--timeout", "120", \
     "--access-logfile", "-", \
     "--error-logfile", "-", \
     "app:app"]
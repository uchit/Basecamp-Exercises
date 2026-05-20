# Pro hackathon agent — runnable image.
# Build:  docker build -t pro-hackathon .
# Run:    docker run --rm -e ANTHROPIC_API_KEY=$KEY pro-hackathon python pro_run.py sample
#
# The sentence-transformers MiniLM model is downloaded at build time so
# the container starts with retrieval ready instead of a 30s cold start.

FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Minimal system deps (sentence-transformers needs libgomp).
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# Install Python deps first (better layer caching).
COPY requirements.txt ./
RUN pip install -r requirements.txt

# Pre-download the embedding model so first run is instant.
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('all-MiniLM-L6-v2')"

# Copy app sources last so code edits don't bust the deps layer.
COPY pro/ ./pro/
COPY hackathon.py pro_run.py analyze.py ./

# Read-only mount target for KB + write target for runs/cache.
RUN mkdir -p /app/runs /app/cache /app/audit
VOLUME ["/app/runs", "/app/cache", "/app/audit"]

ENV PRO_RUNS_DIR=/app/runs \
    PRO_CACHE_DIR=/app/cache \
    PRO_AUDIT_DB=/app/audit/audit.db

# Default entrypoint runs the test suite. Override with `docker run pro python pro_run.py …`.
ENTRYPOINT ["python"]
CMD ["-m", "pytest", "pro/tests/", "-q"]

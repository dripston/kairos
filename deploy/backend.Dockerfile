# Kairos API (FastAPI) for any Docker host: Hugging Face Spaces (Docker), Render, Railway, Fly.
# Build from the repo root:   docker build -f deploy/backend.Dockerfile -t kairos-api .
# Runtime env:  SARVAM_API_KEY (secret, optional: models stay off without it), PORT (default 7860).
# The image ships a demo database seeded from the SIMULATED journey (40 synthetic borrowers,
# campaign CMP-JOURNEY). Every number it serves is SIMULATED.
FROM python:3.11-slim

# non-root user with uid 1000 (what Hugging Face Spaces expects)
RUN useradd -m -u 1000 user
USER user
ENV HOME=/home/user PATH=/home/user/.local/bin:$PATH PYTHONUNBUFFERED=1 PYTHONIOENCODING=utf-8
WORKDIR /home/user/app

# policy/ is the single source of truth; the backend finds it at ../policy
COPY --chown=user policy ./policy
COPY --chown=user backend ./backend
RUN pip install --no-cache-dir --user -e ./backend

WORKDIR /home/user/app/backend
ENV KAIROS_DB=/home/user/app/backend/data/kairos_demo.db
RUN mkdir -p data && python -c "from replay.journey import run; run(seed=42, quick=True, echo=False, db_path='data/kairos_demo.db', cohort_size=40)"

EXPOSE 7860
CMD ["sh", "-c", "uvicorn kairos_backend.api:app --host 0.0.0.0 --port ${PORT:-7860} --proxy-headers"]

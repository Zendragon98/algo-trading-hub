# Algo Trading Hub — backend (engine + FastAPI in one process).
# Build from repo root:  docker build -f backend/Dockerfile -t algo-trading-backend backend
# Or from backend/:   docker build -t algo-trading-backend .

FROM python:3.11-slim-bookworm

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app

# Runtime deps only; wheels ship for scipy/statsmodels on linux/amd64.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Persist runs, jobs, and calibration under /app/data (mount a volume in prod).
VOLUME ["/app/data"]

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=90s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4)"

CMD ["python", "main.py"]

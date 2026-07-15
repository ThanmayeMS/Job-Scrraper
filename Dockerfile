# ── Stage 1: build wheels ─────────────────────────────────────────────────────
FROM python:3.12-slim AS builder

ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --upgrade pip build && pip wheel --no-deps -w /wheels .

# ── Stage 2: runtime ──────────────────────────────────────────────────────────
FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1

# Slim runtime — no browser (keeps the image small enough for free hosts). The Amazon
# (requests) scraper plus all API/matching work need no browser. To run Selenium
# scrapers (google/citi/...) in-container, add:
#   RUN apt-get update && apt-get install -y --no-install-recommends chromium chromium-driver
#   ENV CHROME_BIN=/usr/bin/chromium

WORKDIR /app
COPY --from=builder /wheels /wheels
COPY pyproject.toml README.md ./
COPY src ./src
COPY scripts ./scripts
COPY migrations ./migrations
COPY alembic.ini ./
COPY frontend ./frontend

RUN pip install --no-cache-dir /wheels/*.whl && pip install --no-cache-dir .

EXPOSE 8000
# Auto-apply migrations, then serve on $PORT (managed hosts inject PORT; default 8000).
CMD ["sh", "scripts/render-start.sh"]

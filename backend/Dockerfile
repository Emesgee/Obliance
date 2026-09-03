# api container (ADR-0007 §2) — FastAPI/uvicorn (ADR-0023). No LibreOffice here:
# document tooling lives in the worker image so the API starts fast.
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv

RUN pip install --no-cache-dir uv==0.4.30 || pip install --no-cache-dir uv

WORKDIR /srv/backend

# Dependencies first (layer cache), then the code.
COPY pyproject.toml ./
COPY uv.lock* ./
RUN uv sync --no-dev --frozen 2>/dev/null || uv sync --no-dev

COPY . .

ENV PATH="/opt/venv/bin:$PATH"

# Non-root runtime user (ADR-0007 §5).
RUN useradd -r -u 10001 app && chown -R app:app /srv/backend
USER app

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]

# backend «Орбита ФСП» — модульный монолит FastAPI. Один артефакт для API, миграций (alembic),
# сида и CLI-инструментов (app.tools.*) — версии кода и миграций не расходятся.
#
# Стадии: deps (venv /opt/venv с зависимостями из pyproject.toml через uv; uv.lock не ведём —
# локально venv + pip) → dev (dev-зависимости + код; compose монтирует backend/ поверх /app
# для hot-reload) → runtime (slim, non-root, uvicorn без --reload).
# venv лежит вне /app намеренно: bind-mount исходников его не перекрывает.

# --- deps ---
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS deps
WORKDIR /app
ENV UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never UV_COMPILE_BYTECODE=1
RUN uv venv /opt/venv
ENV VIRTUAL_ENV=/opt/venv PATH="/opt/venv/bin:$PATH"
COPY pyproject.toml ./
# только runtime-зависимости проекта; слой кэшируется до изменения pyproject.toml
RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install -r pyproject.toml

# --- dev ---
FROM deps AS dev
RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install -r pyproject.toml --extra dev
COPY . .
ENV PYTHONUNBUFFERED=1
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]

# --- runtime ---
FROM python:3.12-slim-bookworm AS runtime
RUN groupadd -g 10001 app && useradd -u 10001 -g app -m app
WORKDIR /app
COPY --from=deps --chown=app:app /opt/venv /opt/venv
COPY --chown=app:app . .
ENV PATH="/opt/venv/bin:$PATH" PYTHONUNBUFFERED=1
USER app
EXPOSE 8000
# curl в образе нет — проба через stdlib; /healthz = liveness (без зависимостей)
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=5 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=3).status == 200 else 1)"
# один воркер намеренно: семафор LLM, rate-limit и кэш — in-process (один экземпляр backend,
# PLAN.md §7.5); async-воркер uvicorn держит целевые 30–50 VU
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]

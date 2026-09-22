FROM python:3.14-slim

# 1. Python runtime ----
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# 2. Рабочая директория ----
WORKDIR /app

# 3. Прокси для сборки (передаётся через build args) ----
ARG HTTP_PROXY
ARG HTTPS_PROXY
ARG NO_PROXY
ENV HTTP_PROXY=${HTTP_PROXY} \
    HTTPS_PROXY=${HTTPS_PROXY} \
    NO_PROXY=${NO_PROXY}
ENV MALLOC_ARENA_MAX=2
ENV MALLOC_TRIM_THRESHOLD_=100000
ENV MALLOC_MMAP_THRESHOLD_=100000

# 4. Зависимости ----
COPY --from=ghcr.io/astral-sh/uv:0.11.16 /uv /uvx /bin/

ENV UV_PYTHON=3.14 \
    UV_COMPILE_BYTECODE=0 \
    UV_LINK_MODE=symlink \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PYTHONPATH=/app

COPY pyproject.toml uv.lock* ./
RUN uv sync --no-install-project
ENV PATH="/opt/venv/bin:$PATH"

COPY . .

EXPOSE 8000


CMD ["uv", "run", "uvicorn", "src.app:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]
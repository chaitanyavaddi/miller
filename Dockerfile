# syntax=docker/dockerfile:1

FROM python:3.12-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy

# Dependencies first: this layer only rebuilds when uv.lock changes.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev

COPY . .

# collectstatic imports the real settings module, so every required key has to resolve to
# *something* here. None of these reach the runtime image; the host injects the real ones.
ENV DJANGO_SETTINGS_MODULE=config.settings.prod \
    SECRET_KEY=build-time-placeholder \
    DB_NAME=build DB_USER=build DB_PASSWORD=build DB_HOST=build
RUN uv run --no-sync python manage.py collectstatic --noinput

FROM python:3.12-slim AS runtime
RUN groupadd -r app && useradd -r -g app -d /app app
WORKDIR /app
ENV PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src \
    PATH="/app/.venv/bin:$PATH" \
    DJANGO_SETTINGS_MODULE=config.settings.prod

COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/staticfiles /app/staticfiles
COPY manage.py ./
COPY config ./config
COPY src ./src
COPY templates ./templates
COPY static ./static
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh

# MEDIA_ROOT, created empty so a mounted volume inherits app ownership.
RUN mkdir -p /app/media && chmod +x /usr/local/bin/docker-entrypoint.sh && chown -R app:app /app
USER app
EXPOSE 8000
ENTRYPOINT ["docker-entrypoint.sh"]

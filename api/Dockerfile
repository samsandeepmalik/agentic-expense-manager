FROM python:3.13-slim

# libmagic: required by neonize (WhatsApp); curl for healthcheck
RUN apt-get update \
    && apt-get install -y --no-install-recommends libmagic1 curl \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir poetry

WORKDIR /app
COPY pyproject.toml poetry.lock* ./
RUN poetry config virtualenvs.create false \
    && poetry install --no-root --no-interaction --no-ansi

COPY app ./app

ENV DATA_DIR=/data
VOLUME /data

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

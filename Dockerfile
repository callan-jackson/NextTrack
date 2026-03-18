# Stage 1: Builder - install dependencies and build wheels
FROM python:3.11-slim AS builder

RUN apt-get update && apt-get install -y \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /code

COPY requirements.txt /code/
RUN pip install --upgrade pip && \
    pip wheel --no-cache-dir --wheel-dir /code/wheels -r requirements.txt

# Stage 2: Runtime - lean production image
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y \
    libpq5 \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /code

COPY --from=builder /code/wheels /wheels
COPY --from=builder /code/requirements.txt .
RUN pip install --upgrade pip && \
    pip install --no-cache-dir /wheels/* && \
    rm -rf /wheels

RUN addgroup --system appgroup && \
    adduser --system --ingroup appgroup appuser

COPY . /code/

RUN python manage.py collectstatic --noinput || true

RUN chown -R appuser:appgroup /code

USER appuser

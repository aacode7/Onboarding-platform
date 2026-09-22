# syntax=docker/dockerfile:1.7
FROM python:3.12-slim AS build

WORKDIR /build
RUN apt-get update \
    && apt-get install -y --no-install-recommends git openssh-client ca-certificates \
    && rm -rf /var/lib/apt/lists/*
RUN mkdir -p -m 0700 /root/.ssh \
    && ssh-keyscan github.com >> /root/.ssh/known_hosts
COPY backend/requirements.txt ./requirements.txt
RUN --mount=type=ssh python -m venv /venv \
    && /venv/bin/pip install --no-cache-dir -r requirements.txt

FROM python:3.12-slim

WORKDIR /app
COPY --from=build /venv /venv
COPY backend ./backend

EXPOSE 8001
CMD ["/venv/bin/uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8001"]

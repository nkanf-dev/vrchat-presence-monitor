# syntax=docker/dockerfile:1.7@sha256:a57df69d0ea827fb7266491f2813635de6f17269be881f696fbfdf2d83dda33e

FROM node:26-alpine@sha256:aadf416b2cdce311a8811ba3f0608a61b77dbf997500e2eafe781b51f6a0b019 AS web-builder
WORKDIR /build/web
COPY web/package.json web/package-lock.json ./
ARG NPM_CONFIG_REGISTRY=https://registry.npmjs.org
RUN npm ci --ignore-scripts --no-audit --no-fund
COPY web/ ./
RUN npm run build

FROM python:3.13-slim@sha256:7e3a6aca9d74f93cca21a91d86a8dad8c34749afd5b4a98ee481c9c47b9f5ed4 AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PORT=8080 \
    DATA_DIR=/data
WORKDIR /app

RUN addgroup --system --gid 10001 app \
    && adduser --system --uid 10001 --ingroup app --home /nonexistent app
COPY requirements.txt ./
RUN pip install --no-cache-dir --no-compile --require-hashes -r requirements.txt
COPY --chown=app:app server ./server
COPY --chown=app:app \
    scripts/backup_format.py \
    scripts/backup_hosted.py \
    scripts/r2_backup.py \
    scripts/restore_hosted.py \
    ./scripts/
COPY --from=web-builder --chown=app:app /build/server/static ./server/static

RUN install -d -o app -g app -m 0700 /data
USER app
VOLUME ["/data"]
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/readyz', timeout=3).read()"]
CMD ["python", "-m", "server.app"]

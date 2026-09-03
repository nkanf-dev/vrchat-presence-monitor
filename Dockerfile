FROM node:22-alpine@sha256:c610fcdfb1d5b4740dd70c284ed3cb16bb857e0f7166196e36a5501df7a3aa32 AS web-builder
WORKDIR /build/web
COPY web/package.json web/package-lock.json ./
ARG NPM_CONFIG_REGISTRY=https://registry.npmjs.org
RUN npm ci --ignore-scripts --no-audit --no-fund
COPY web/ ./
RUN npm run build

FROM python:3.14-slim@sha256:cad9a2c871761c413caa6fdd6441c783451e740a48aaeba60ae62a8b53525ef6 AS runtime

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
COPY --chown=app:app vrchat_monitor ./vrchat_monitor
COPY --chown=app:app \
    scripts/backup_format.py \
    scripts/backup_hosted.py \
    scripts/proxy_relay.py \
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

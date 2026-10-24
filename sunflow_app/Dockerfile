# The Supervisor builds apps with this folder as context, so the code is cloned
# from GitHub. BUILD_VERSION (set by the Supervisor from config.yaml) invalidates
# the cached clone: bump the version in config.yaml to ship a new state of main.
FROM python:3.14-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

ARG BUILD_VERSION
ARG SUNFLOW_REPO=https://github.com/Zwer2k/sunflow
ARG SUNFLOW_REF=main
RUN echo "Sunflow ${BUILD_VERSION}" \
    && git clone --depth 1 --branch "${SUNFLOW_REF}" "${SUNFLOW_REPO}" /app \
    && pip install --no-cache-dir /app/backend \
    && rm -rf /app/.git

ENV SUNFLOW_PROFILES_DIR=/app/profiles \
    SUNFLOW_SCHEMA_DIR=/app/schema \
    SUNFLOW_FRONTEND_DIR=/app/frontend \
    SUNFLOW_DATA_DIR=/data \
    SUNFLOW_PORT=8099

CMD ["python", "-m", "sunflow"]

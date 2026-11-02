# Image of the stable App, built by .github/workflows/release.yml with the repository as
# context (docker build -f sunflow_app/Dockerfile .). Sunflow Edge builds on the device instead.
FROM python:3.14-slim

ARG BUILD_VERSION
ARG BUILD_ARCH

COPY backend /app/backend
COPY profiles /app/profiles
COPY schema /app/schema
COPY frontend /app/frontend
RUN pip install --no-cache-dir /app/backend

ENV SUNFLOW_PROFILES_DIR=/app/profiles \
    SUNFLOW_SCHEMA_DIR=/app/schema \
    SUNFLOW_FRONTEND_DIR=/app/frontend \
    SUNFLOW_DATA_DIR=/data \
    SUNFLOW_PORT=8099

LABEL io.hass.version="${BUILD_VERSION}" \
      io.hass.type="addon" \
      io.hass.arch="${BUILD_ARCH}"

CMD ["python", "-m", "sunflow"]

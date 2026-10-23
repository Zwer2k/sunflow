# Build from the repository root: docker build -f sunflow_app/Dockerfile .
FROM python:3.14-slim

WORKDIR /app
COPY backend/ /app/backend/
COPY profiles/ /app/profiles/
COPY schema/ /app/schema/
COPY frontend/ /app/frontend/
RUN pip install --no-cache-dir /app/backend

ENV SUNFLOW_PROFILES_DIR=/app/profiles \
    SUNFLOW_SCHEMA_DIR=/app/schema \
    SUNFLOW_FRONTEND_DIR=/app/frontend \
    SUNFLOW_DATA_DIR=/data \
    SUNFLOW_PORT=8099

CMD ["python", "-m", "sunflow"]

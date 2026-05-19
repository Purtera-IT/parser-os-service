# syntax=docker/dockerfile:1
FROM python:3.11-slim AS runtime

RUN useradd -m -u 10001 parseros
WORKDIR /build

COPY parser-os ./parser-os
COPY parser-os-service ./parser-os-service

ENV PIP_ROOT_USER_ACTION=ignore
RUN set -eux; \
    pip install --no-cache-dir --upgrade pip setuptools wheel; \
    pip install --no-cache-dir ./parser-os; \
    pip install --no-cache-dir \
      "azure-identity>=1.15" \
      "azure-storage-blob>=12.19" \
      "fastapi>=0.110" \
      "psycopg[binary,pool]>=3.1" \
      "pydantic>=2.5" \
      "uvicorn[standard]>=0.27"; \
    pip install --no-cache-dir --no-deps ./parser-os-service; \
    rm -rf /build

WORKDIR /app
USER parseros
EXPOSE 8000
ENV PYTHONUNBUFFERED=1
CMD ["uvicorn", "parser_os_service.server.app:app", "--host", "0.0.0.0", "--port", "8000"]

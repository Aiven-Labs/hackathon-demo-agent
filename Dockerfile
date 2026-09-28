FROM otel/opentelemetry-collector-contrib:0.161.0 AS collector
FROM python:3.12-slim-bookworm
WORKDIR /app
RUN useradd --create-home --uid 10001 demo
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY --from=collector /otelcol-contrib /usr/local/bin/otelcol-contrib
COPY agent/ ./agent/
COPY fixtures/ ./fixtures/
COPY clickhouse/ ./clickhouse/
COPY start.py evaluate.py ./
USER demo
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
EXPOSE 8080
ENTRYPOINT ["python", "start.py"]

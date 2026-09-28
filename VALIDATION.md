# Validation record

## Implemented

- Three deterministic failure scenarios and reference policies; development and held-out fixture sets.
- Application-originated OTel traces, logs and metrics; no Runtime log scraping.
- A single container with a loopback Collector, authenticated runner and verified TLS to ClickHouse.
- Resumable setup using Aiven's REST API, automatic database/table creation, and ingestion verification.

## Local checks

- 15 tests passed: failures/fixes across both task sets, span parentage and metadata, auth/input validation, redacted model errors, TLS requirements, plan selection, VCS pagination and deployment-state permissions.
- Development and held-out evaluations: 1/6 baseline cases passed, 6/6 reference cases passed on each set. The afternoon stale-source case legitimately passes because both source versions give the same answer at that time.
- Optional live-model mode is not yet exercised against a model provider.

## Cloud/container checks

Validated on 2026-09-28 against a dedicated Aiven ClickHouse service:

- Schema creation through verified HTTPS passed.
- Collector 0.161.0 configuration validation passed.
- All six faulty/reference scenario runs produced stored traces, correlated logs and metrics.
- Example stored signal tables included 46 spans and 29 log records after the six-run check; metric counters and histograms were also populated.
- Collector TLS must retain system roots when adding the Aiven project CA (`include_system_ca_certs_pool: true`), because the service HTTPS certificate can use a public CA.

Still to validate: Runtime container build/deployment, the complete setup flow from GitHub, and the GitHub Actions local Compose integration job. The preceding test used a local native Collector and Python application with real Aiven ClickHouse.

## Version choices

OpenTelemetry Collector Contrib is pinned to **0.161.0**. SQL is derived from that exact release, with explicit database/table names, seven-day TTLs and bloom-filter indexes. Schema creation by the exporter is disabled. Python packages are pinned in requirements files; transitive dependencies are resolved at build time. Aiven provisions the database through its managed API, avoiding upstream database-engine assumptions. The starter selects a single-shard plan to avoid additional distributed-table routing.

References:

- https://aiven.io/docs/products/runtime/deploy-apps
- https://aiven.io/docs/products/clickhouse/reference/limitations
- https://aiven.io/docs/products/clickhouse/reference/supported-interfaces-drivers
- https://github.com/open-telemetry/opentelemetry-collector-contrib/tree/v0.161.0/exporter/clickhouseexporter
- https://github.com/aiven/aiven-client/blob/main/aiven/client/client.py

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

Also passed on 2026-09-28:

- Runtime built GitHub commit `16c8a1b` and started the agent + Collector together on the 1 GiB plan.
- `setup.py` discovered the GitHub integration, created the Runtime service, injected connection settings, waited for the correct application version, and completed verification.
- All six faulty/reference runs from **Runtime** produced stored traces, correlated logs and metrics in **Aiven ClickHouse**.
- Redeployment of code commit `4ddef89` passed the same six live checks using `setup.py redeploy`.
- All five participant SQL examples executed successfully against stored Aiven telemetry.
- Both GitHub Actions jobs passed: unit/evaluation checks and full Docker Compose integration with a real local ClickHouse instance.

The ClickHouse service/database was created with the same API implementation during the preceding ingestion proof, then resumed by the full setup script. The flow was not tested with a brand-new Aiven account or GitHub connection.

The initial local Compose check exposed a startup connection-reset race. The readiness loop now tolerates both socket errors and incomplete HTTP connections while the application starts. The corrected [CI run](https://github.com/Aiven-Labs/hackathon-demo-agent/actions/runs/36408489720) passed.

Remaining limits: no live model-provider test, no new-account onboarding rehearsal, no concurrent/load test, and no long-running durability test. The checked path is the deterministic hackathon starter, not a production observability platform.

## Version choices

OpenTelemetry Collector Contrib is pinned to **0.161.0**. SQL is derived from that exact release, with explicit database/table names, seven-day TTLs and bloom-filter indexes. Schema creation by the exporter is disabled. Python packages are pinned in requirements files; transitive dependencies are resolved at build time. Aiven provisions the database through its managed API, avoiding upstream database-engine assumptions. The starter selects a single-shard plan to avoid additional distributed-table routing.

References:

- https://aiven.io/docs/products/runtime/deploy-apps
- https://aiven.io/docs/products/clickhouse/reference/limitations
- https://aiven.io/docs/products/clickhouse/reference/supported-interfaces-drivers
- https://github.com/open-telemetry/opentelemetry-collector-contrib/tree/v0.161.0/exporter/clickhouseexporter
- https://github.com/aiven/aiven-client/blob/main/aiven/client/client.py

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

## Runtime ClickHouse integration update — 2026-10-05

- Runtime now provisions an `application_service_credential` integration for ClickHouse. New applications include it in their creation request; existing applications reconcile it on setup/redeploy.
- Setup no longer reads or copies the ClickHouse username/password into application environment settings. Runtime supplies them through the integration.
- The integration exposes a native protocol port and service-default database. The starter deliberately keeps its verified HTTPS endpoint and explicitly created `blackbox` database separate; no Python client or Collector changes are needed.
- 21 local tests passed, including first-deployment wiring, resuming an existing deployment, ignoring disabled integration candidates, and excluding static credentials.
- Both [GitHub Actions starter checks](https://github.com/Aiven-Labs/hackathon-demo-agent/actions/runs/37289579204) passed: unit/evaluation checks and the full local container ingestion test.
- `setup.py redeploy` deployed commit `8d084e6` to the existing Runtime service. The integration reported enabled and active; explicitly configured username/password variables were absent.
- All six failure/reference runs produced traces, correlated logs and metrics in Aiven ClickHouse after redeployment. The saved integration mapping also matched setup's expected configuration for subsequent reuse.
- The previously stopped demo ClickHouse service was temporarily started for validation and returned to its stopped state afterward. Start it again before using the hosted demo's database-dependent features.

Live validation covered migration/redeployment of the existing demo. First-creation integration wiring is covered by automated tests; a fresh-account/GitHub onboarding rehearsal remains outstanding.

## Version choices

OpenTelemetry Collector Contrib is pinned to **0.161.0**. SQL is derived from that exact release, with explicit database/table names, seven-day TTLs and bloom-filter indexes. Schema creation by the exporter is disabled. Python packages are pinned in requirements files; transitive dependencies are resolved at build time. Aiven provisions the database through its managed API, avoiding upstream database-engine assumptions. The starter selects a single-shard plan to avoid additional distributed-table routing.

References:

- https://aiven.io/docs/products/runtime/deploy-apps
- https://aiven.io/docs/products/clickhouse/reference/limitations
- https://aiven.io/docs/products/clickhouse/reference/supported-interfaces-drivers
- https://github.com/open-telemetry/opentelemetry-collector-contrib/tree/v0.161.0/exporter/clickhouseexporter
- https://github.com/aiven/aiven-client/blob/main/aiven/client/client.py

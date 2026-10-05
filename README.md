# Agent Black Box — hackathon starter

This repo supplies the agent, reproducible scenarios and telemetry pipeline: A fictional Paris travel agent with three deliberate failures.

```text
Aiven Runtime — one container
  Python demo agent → localhost OpenTelemetry Collector
                                   │ HTTPS with verified TLS
                                   ▼
                        Aiven for ClickHouse
                        traces · logs · metrics
```

## Start here

You need **Python 3.10+**, **Git**, a GitHub account, and an **Aiven account/project with credits and permission to create services**. No model API key or local Docker installation are required to use this repo.

1. **Fork this repository** into your GitHub account, then clone your fork and enter its directory. You will deploy your own changes from this fork.
2. In [Aiven Console](https://console.aiven.io/), create/select your project. Under **Runtime → Deploy application**, connect your GitHub account and grant access to the fork. Stop before creating an application: the script creates both services. For a GitHub organisation, connecting its account may require an organisation owner.
3. Create an [Aiven authentication token](https://aiven.io/docs/platform/howto/create_authentication_token). Keep it private.
4. Run:

   ```sh
   python3 setup.py
   ```

   Enter your project name and token when prompted. The token input is hidden and is never stored or sent into Runtime. The script discovers available service plans, displays a cost estimate, and asks before creating them. Default region: `aws-eu-west-1`; choose another with `--cloud`.

5. Let the script finish. It creates a dedicated **single-shard ClickHouse service**, creates the database, connects ClickHouse through Runtime’s **service integration**, deploys the **agent + Collector** to Runtime, automatically creates the telemetry tables, then checks all three scenarios end to end.
   Runtime supplies the ClickHouse credentials through the integration. The script configures the HTTPS endpoint and `blackbox` database automatically; no connection details need to be copied.

6. Open the printed URL in Chrome, Safari or Firefox. Username: **`demo`**. Your generated password is in **`.deployment.json`** (or Runtime's `DEMO_PASSWORD` secret). This local file is private and Git-ignored; do not share it.

Provisioning/build time may vary. Do not infer readiness from `RUNNING` alone: the script waits for the correct application version and actual telemetry in ClickHouse.

If interrupted, run `python3 setup.py` again from the same clone to resume. Keep `.deployment.json`: it identifies resources owned by this setup. A name collision without matching local state stops safely; `--name team-two` chooses fresh names.

## Your first investigation

1. Choose **Retry spiral → Baseline** and click **Run agent**.
2. Copy the `trace_id`. Click **Check stored evidence** after a few seconds to see the raw spans and correlated logs retrieved from ClickHouse.
3. Open the ClickHouse query editor, select database `blackbox`, and run [these queries](clickhouse/queries.sql).
4. Run the same scenario with **Reference fix**. Compare the outcome, tool-call count and latency.
5. Build your own timeline, detector, source investigation, comparison view or improvement workflow. The supplied runner and raw JSON are intentionally not a complete observability product.

| Scenario | What the agent encounters | Where to investigate |
| --- | --- | --- |
| Retry spiral | A reservation service returns HTTP 429 | Repeated tool calls, status codes, retries and duration |
| Stale source | Different versions of a venue's opening hours | Retrieval span source ID/version/timestamp and retrieved record |
| Silent failure | A tool returns HTTP 200 but a failed booking result | Tool response versus the final success claim |

All venues, tool services and reservations are fictional. Each scenario is bounded; nothing makes a real booking. The reference policy is an example fix, not a solution to the observability challenge. See [agent/engine.py](agent/engine.py) to change the policies and agent logic.

## Make and deploy a change

Edit your fork, commit and push, then run:

```sh
python3 setup.py redeploy
```

Runtime builds the **pushed branch**, not your local working files. Setup refuses dirty or unpushed code so the recorded Git commit matches the deployed version. Pushing alone does not redeploy Runtime. To check ingestion again without rebuilding:

```sh
python3 setup.py verify
```

The generated Runtime service exposes only HTTP port 8080, protected by the demo password. The Collector listens on loopback port 4318; it is not a public ingestion endpoint. To instrument another agent, add it to this application or explicitly design authenticated external ingestion. Do not expose an unauthenticated Collector to the internet.

## Run evaluations locally

Use Python **3.12** for application development (setup itself only needs 3.10+):

```sh
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
python evaluate.py
python evaluate.py --split heldout
python -m pytest -q
```

The evaluator runs baseline/reference pairs and writes `reports/comparison.json`. It checks opening-hours advice, unsupported booking claims and the tool-call budget independently of the model. Held-out fixtures are separate from development tasks and not exposed by the web runner; keep them out of optimisation prompts. They are public in this repo, not a hidden competition benchmark. Add representative tasks before drawing production conclusions.

By default local evaluation does not export telemetry. `--export` sends it to `OTEL_EXPORTER_OTLP_ENDPOINT` (default `http://127.0.0.1:4318`). Cloud ingestion is verified separately by `setup.py verify`.

For an optional full local container preview, copy `.env.example` to `.env`, set two unique passwords, then run `docker compose up --build`. Open `http://localhost:8080`. Stop with `docker compose down`; adding `-v` deletes the local telemetry volume. A local preview does not satisfy the challenge's Aiven deployment requirement.

## Deterministic versus live models

**Deterministic mode is the default.** Scripted model responses drive the real Python execution loop, tools and instrumentation. It reliably reproduces failures without an API key. `simulated_*_tokens` are illustrative workload counts, never billed usage; `estimated_cost_usd` is `null`.

Optional live mode uses a Chat Completions-compatible HTTPS API. Set these in Runtime's environment settings and redeploy: `MODEL_MODE=live`, `MODEL_BASE_URL` (ending in `/v1`), `MODEL_NAME`, and secret `MODEL_API_KEY`. Optionally set `INPUT_USD_PER_MILLION` and `OUTPUT_USD_PER_MILLION` to your model's current rates. The default base URL is `https://api.openai.com/v1`. Only models supporting this request format, `max_tokens`, and JSON answers are compatible. Tools remain simulated; the planning/final-answer model calls are live. Live behaviour is not guaranteed to reproduce every deterministic failure. Model API charges are separate from Aiven credits.

Provider-reported usage is recorded when available. Cost remains unknown if usage or prices are missing; estimates exclude pricing features such as cached-token discounts. Full live-model/provider compatibility is not implied by deterministic tests. Subsequent `setup.py redeploy` restores the default deterministic configuration; keep custom model settings in your own deployment code if you want them persisted.

## What gets recorded

- `agent.run` root span, child model/tool spans, structured application logs and aggregate counters/histograms.
- Run/task IDs, Git commit, policy/configuration hash, fixture version, source record versions and evaluated outcome.
- Tool statuses, call counts, duration and real/simulated usage explicitly distinguished.

`agent.result` on the root span contains the full bounded run result. Logs carry the same trace/span IDs and `agent.run_id`. Metrics have low-cardinality scenario/profile labels, not per-run IDs. SQL examples explain cumulative counters and deduplicating retried spans. Retention is seven days.

This starter records only bundled fictional inputs. If you add customer data, add explicit capture controls/redaction and review the recorded fields before ingestion.

## Troubleshooting

| Symptom | Next step |
| --- | --- |
| Aiven 401 | Create a fresh token and rerun setup. |
| Aiven 403 | Check project write permissions, credits and service availability. |
| ClickHouse integration unavailable | Check that your project supports ClickHouse under Runtime’s Connected services, or contact an on-site mentor. |
| Repository not connected | Grant Runtime's GitHub connection access to your fork, in the same Aiven organisation as your project. Rerun. |
| Dirty/unpushed branch | Commit and push before deployment; `.deployment.json` and local secrets must remain ignored. |
| Name already exists | Use the original `.deployment.json` to resume, or choose another `--name`. Existing resources are never adopted blindly. |
| Build or startup fails | Open Runtime's build/run logs in Console. Check pinned image availability, ClickHouse settings and schema errors. These logs are for setup troubleshooting, not agent telemetry ingestion. |
| Run completes but evidence is missing | Wait a few seconds, then rerun `setup.py verify`. Check Collector errors and ClickHouse connectivity; SDK flush alone does not prove storage. |
| Wrong password | Read `.deployment.json` or the `DEMO_PASSWORD` Runtime secret. Login username is `demo`. |

## Limits and cleanup

This is a **single-replica hackathon foundation**, not a production service. It uses a dedicated service's credentials supplied by the Runtime integration for schema bootstrap and reads/writes, shared demo authentication, an in-memory Collector queue, and one active run at a time. Abrupt shutdowns can lose buffered telemetry; retries can duplicate it. Add least-privilege credentials, tenant isolation, durable buffering, retention/deletion policy, load testing and representative evaluations before production use. No filesystem state in Runtime is treated as durable. Generated code execution/sandboxing is outside this starter.

**Services continue to consume credits after you close your terminal.** In Aiven Console, stop or delete the two services recorded in `.deployment.json` (default `blackbox-app` and `blackbox-ch`) when finished. Deleting ClickHouse deletes the stored demo telemetry. The setup script never deletes services automatically.

Implementation and validation evidence: [VALIDATION.md](VALIDATION.md). License: MIT; upstream SQL attribution is in [NOTICE](NOTICE).

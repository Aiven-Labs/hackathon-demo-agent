import json

import pytest
from fastapi.testclient import TestClient
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.sdk.trace.export import SimpleSpanProcessor

from agent.engine import Agent, SCENARIOS, load_tasks
from agent.telemetry import Telemetry
from agent.server import app
from agent.storage import ClickHouse
from start import collector_config


@pytest.fixture
def telemetry():
    t = Telemetry(enabled=False)
    exporter = InMemorySpanExporter()
    t.traces.add_span_processor(SimpleSpanProcessor(exporter))
    yield t, exporter
    t.shutdown()


@pytest.mark.parametrize("split", ["development", "heldout"])
@pytest.mark.parametrize("scenario", SCENARIOS)
def test_reference_improves_real_checks(telemetry, split, scenario):
    t, exporter = telemetry
    agent = Agent(t, delay=0, mode="deterministic")
    task = load_tasks(split)[0]
    before, after = [agent.run(scenario, p, task) for p in ("baseline", "reference")]
    assert not before["success"]
    assert after["success"]
    assert after["estimated_cost_usd"] is None
    if scenario == "retry_spiral":
        assert before["reservation_calls"] == 5
        assert after["reservation_calls"] == 2
        assert not after["booking_confirmed"]
    elif scenario == "stale_source":
        assert before["venue_open"] and not after["venue_open"]
    else:
        assert before["booking_confirmed"] and not before["tool_confirmed"]
        assert not after["booking_confirmed"]
    spans = exporter.get_finished_spans()
    roots = [s for s in spans if s.name == "agent.run"]
    assert len(roots) == 2
    assert roots[0].attributes["agent.config_hash"] != roots[1].attributes["agent.config_hash"]
    assert json.loads(roots[0].attributes["agent.result"])["run_id"] == before["run_id"]
    for root in roots:
        children = [s for s in spans if s.parent and s.parent.span_id == root.context.span_id]
        assert len(children) >= 5
        assert all(c.context.trace_id == root.context.trace_id for c in children)


def test_open_hours_can_correctly_pass(telemetry):
    result = Agent(telemetry[0], delay=0).run("stale_source", "baseline", load_tasks()[1])
    assert result["success"]  # A stale record is not automatically a wrong answer on every task.


def test_live_model_failure_is_visible_without_secret(telemetry, monkeypatch):
    agent = Agent(telemetry[0], delay=0, mode="live")
    def fail(*args):
        raise RuntimeError("SECRET_PROVIDER_MESSAGE")
    monkeypatch.setattr(agent, "model", fail)
    result = agent.run("silent_failure")
    assert not result["success"]
    assert result["error"] == "RuntimeError"
    assert "SECRET_PROVIDER_MESSAGE" not in json.dumps(result)
    assert "SECRET_PROVIDER_MESSAGE" not in str(telemetry[1].get_finished_spans())


def test_http_auth_and_scenario_validation(monkeypatch):
    monkeypatch.setenv("DEMO_PASSWORD", "a-long-test-password-123")
    monkeypatch.setenv("TELEMETRY_DISABLED", "true")
    with TestClient(app) as client:
        assert client.get("/").status_code == 401
        assert client.get("/healthz").status_code == 200
        client.auth = ("demo", "a-long-test-password-123")
        assert client.get("/").status_code == 200
        assert client.post("/run", json={"scenario": "not-real"}).status_code == 422
        result = client.post("/run", json={"scenario": "silent_failure", "profile": "reference"})
        assert result.status_code == 200
        assert result.json()["success"]


def test_clickhouse_rejects_unsafe_connection():
    with pytest.raises(ValueError):
        ClickHouse({"CLICKHOUSE_URL": "http://example.com", "CLICKHOUSE_PASSWORD": "secret"})
    with pytest.raises(ValueError):
        ClickHouse({"CLICKHOUSE_URL": "https://user:secret@example.com", "CLICKHOUSE_PASSWORD": "secret"})
    with pytest.raises(ValueError):
        ClickHouse({"CLICKHOUSE_URL": "https://example.com", "CLICKHOUSE_PASSWORD": "secret", "CLICKHOUSE_DATABASE": "x; DROP DATABASE y"})


def test_collector_private_and_schema_external():
    config = collector_config()
    assert config["receivers"]["otlp"]["protocols"]["http"]["endpoint"].startswith("127.0.0.1:")
    assert config["exporters"]["clickhouse"]["create_schema"] is False
    assert set(config["service"]["pipelines"]) == {"logs", "traces", "metrics"}

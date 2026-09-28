import json
from pathlib import Path

import pytest

import setup


def test_plan_choice_avoids_sharded_and_too_small():
    catalog = {
        "clickhouse": {"service_plans": [
            {"service_plan": "multi", "shard_count": 2, "regions": {"eu": {"price_usd": "0"}}},
            {"service_plan": "single", "shard_count": 1, "regions": {"eu": {"price_usd": "1"}}}]},
        "application": {"service_plans": [
            {"service_plan": "small", "regions": {"eu": {"price_usd": "0", "node_memory_mb": 256}}},
            {"service_plan": "enough", "regions": {"eu": {"price_usd": "1", "node_memory_mb": 1024}}}]},
    }
    assert setup.choose_plan(catalog, "clickhouse", "eu")["service_plan"] == "single"
    assert setup.choose_plan(catalog, "application", "eu")["service_plan"] == "enough"
    with pytest.raises(RuntimeError):
        setup.choose_plan(catalog, "application", "missing")


def test_source_discovery_follows_pagination():
    class API:
        def call(self, method, path):
            if path == "/project/test":
                return {"project": {"organization_id": "org"}}
            if path.endswith("vcs-integrations"):
                return {"vcs_integrations": [{"vcs_integration_id": "vcs1"}]}
            if "cursor=" not in path:
                return {"repositories": [], "next": "page two"}
            assert "page%20two" in path
            return {"repositories": [{"source_url": "https://github.com/team/repo", "remote_repository_id": 123}]}
    source = setup.resolve_source(API(), "test", "git@github.com:team/repo.git", "main")
    assert source["remote_repository_id"] == "123"
    assert source["containerfile_path"] == "Dockerfile"


def test_deployment_secrets_file_permissions(tmp_path, monkeypatch):
    path = tmp_path / ".deployment.json"
    monkeypatch.setattr(setup, "STATE", path)
    setup.save({"demo_password": "secret"})
    assert path.stat().st_mode & 0o777 == 0o600


def test_schema_is_rendered_and_has_all_signals():
    schema = (Path(__file__).parents[1] / "clickhouse/schema.sql").read_text()
    assert "{{" not in schema and "%s" not in schema and "%q" not in schema
    for name in ("otel_traces", "otel_logs", "otel_metrics_sum", "otel_metrics_histogram"):
        assert name in schema
    assert "CREATE DATABASE" not in schema
    assert "DROP TABLE" not in schema

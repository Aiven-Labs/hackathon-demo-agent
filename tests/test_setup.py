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


def test_connection_uses_https_and_never_copies_credentials():
    service = {"components": [
        {"component": "clickhouse", "host": "native.example", "port": 9000},
        {"component": "clickhouse_https", "host": "https.example", "port": 8443, "route": "public"}],
        "users": [{"username": "avnadmin", "password": "must-not-be-copied"}]}
    assert setup.connection(service) == {"CLICKHOUSE_URL": "https://https.example:8443",
                                         "CLICKHOUSE_DATABASE": "blackbox"}
    keys = setup.clickhouse_integration("db")["user_config"]["exposed_values"]
    assert keys["port"]["environment_variable_key"] != "CLICKHOUSE_URL"
    assert keys["database"]["environment_variable_key"] != "CLICKHOUSE_DATABASE"


@pytest.mark.parametrize("enabled", [False, True])
def test_integration_resume_ignores_available_candidates_and_reuses_enabled(enabled):
    calls = []
    expected = setup.clickhouse_integration("db")
    class API:
        def call(self, method, path, data=None):
            calls.append((method, path, data))
            return {"service_integrations": [dict(expected, enabled=enabled, dest_service="app")]}
    setup.ensure_clickhouse_integration(API(), "test", "db", "app")
    writes = [c for c in calls if c[0] == "POST"]
    assert len(writes) == (0 if enabled else 1)
    if writes:
        assert writes[0] == ("POST", "/project/test/integration", dict(expected, dest_service="app"))


def test_integration_does_not_silently_reuse_wrong_mappings():
    class API:
        def call(self, method, path, data=None):
            assert method == "GET"
            return {"service_integrations": [{"enabled": True, "integration_type": "application_service_credential",
                "source_service": "db", "dest_service": "app", "user_config": {}}]}
    with pytest.raises(RuntimeError, match="different variable mappings"):
        setup.ensure_clickhouse_integration(API(), "test", "db", "app")


@pytest.mark.parametrize("existing", [False, True])
def test_deployment_wires_integration_without_static_credentials(monkeypatch, existing):
    from types import SimpleNamespace
    calls = []
    service = {"service_name": "blackbox-ch", "state": "RUNNING", "components": [
        {"component": "clickhouse_https", "host": "db.example", "port": 8443}]}
    class API:
        def call(self, method, path, data=None):
            calls.append((method, path, data))
            if path.endswith("integration_types"):
                return {"integration_types": [{"integration_type": "application_service_credential", "source_service_types": ["clickhouse"]}]}
            if path.endswith("service_types"):
                plan = {"service_plan": "test", "regions": {"eu": {"price_usd": "0.1", "node_memory_mb": 1024}}}
                return {"service_types": {s: {"service_plans": [plan]} for s in ("clickhouse", "application")}}
            if path.endswith("/clickhouse/db"):
                return {"databases": ["blackbox"]}
            if path.endswith("/kms/ca"):
                return {"certificate": "test-ca"}
            if path.endswith("/integration") and method == "GET":
                return {"service_integrations": []}
            if method == "GET" and path.endswith("/blackbox-app") and not existing and not any(c[0] == "POST" and c[2].get("service_type") == "application" for c in calls if c[2]):
                raise setup.APIError(404, path)
            if method == "GET" and path.endswith("/blackbox-ch") and not existing:
                raise setup.APIError(404, path)
            return {"service": {"components": [{"path": "https://app.example"}]}}
    monkeypatch.setattr(setup, "git", lambda *args: {"status": "", "rev-parse": "sha", "ls-remote": "sha refs/heads/main"}[args[0]])
    monkeypatch.setattr(setup, "resolve_source", lambda *args: {"repository_url": "https://github.com/team/repo"})
    monkeypatch.setattr(setup, "wait_service", lambda *args: service)
    monkeypatch.setattr(setup, "save", lambda state: None)
    monkeypatch.setattr(setup, "app_call", lambda *args: {"version": "sha"})
    monkeypatch.setattr(setup, "verify", lambda state: [])
    args = SimpleNamespace(project="test", name="blackbox", repository="https://github.com/team/repo", branch="main", cloud="eu", yes=True, action="deploy")
    state = {"created": ["blackbox-ch", "blackbox-app"]} if existing else {}
    setup.deploy(API(), args, state)
    app_write = next(data for method, path, data in calls if method in ("POST", "PUT") and data and "user_config" in data and "application" in data["user_config"])
    env = {v["key"] for v in app_write["user_config"]["application"]["environment_variables"]}
    assert "CLICKHOUSE_PASSWORD" not in env and "CLICKHOUSE_USER" not in env
    assert {"CLICKHOUSE_URL", "CLICKHOUSE_DATABASE", "DEMO_PASSWORD"} <= env
    if existing:
        methods = [(m, p) for m, p, _ in calls]
        assert methods.index(("PUT", "/project/test/service/blackbox-app")) < methods.index(("POST", "/project/test/integration"))
        assert methods.index(("POST", "/project/test/integration")) < methods.index(("POST", "/project/test/service/blackbox-app/application/redeploy"))
    else:
        assert app_write["service_integrations"] == [setup.clickhouse_integration("blackbox-ch")]

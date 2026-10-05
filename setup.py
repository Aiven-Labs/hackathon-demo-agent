#!/usr/bin/env python3
"""Deploy the whole starter with Python 3.10+ and Git; no pip install required.

python3 setup.py           interactive, resumable deployment
python3 setup.py verify   check deployed agent -> Collector -> ClickHouse
python3 setup.py redeploy build the current, pushed branch again
"""
import argparse
import base64
import getpass
import json
import os
import re
import secrets
import subprocess
import sys
import time
from pathlib import Path
from urllib import error, parse, request

ROOT = Path(__file__).resolve().parent
STATE = ROOT / ".deployment.json"


class APIError(RuntimeError):
    def __init__(self, code, path):
        self.code = code
        super().__init__(f"Aiven HTTP {code} for {path}. " + {
            401: "Token is missing or expired.", 403: "Check project permissions and credits.",
            404: "Resource was not found.", 409: "Resource already exists or is busy.",
            400: "Request rejected; check supported plans, source connection and service configuration."
        }.get(code, "Check the Aiven Console for service status."))


class Aiven:
    def __init__(self, token):
        self.token = token

    def call(self, method, path, data=None):
        req = request.Request("https://api.aiven.io/v1" + path, method=method,
                              data=json.dumps(data).encode() if data is not None else None,
                              headers={"Authorization": "aivenv1 " + self.token, "Content-Type": "application/json"})
        try:
            with request.urlopen(req, timeout=60) as response:
                raw = response.read()
                return json.loads(raw) if raw else {}
        except error.HTTPError as exc:
            raise APIError(exc.code, path) from None
        except error.URLError:
            raise RuntimeError("Cannot connect to Aiven; check network access") from None


def segment(value):
    return parse.quote(value, safe="")


def save(state):
    # Local deployment state includes the demo password, never the Aiven API token.
    fd = os.open(STATE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.chmod(STATE, 0o600)
    with os.fdopen(fd, "w") as stream:
        json.dump(state, stream, indent=2)


def git(*args):
    try:
        return subprocess.check_output(["git", *args], cwd=ROOT, text=True, stderr=subprocess.DEVNULL).strip()
    except (OSError, subprocess.CalledProcessError):
        raise RuntimeError("Git command failed; install Git and check the repository/remote access") from None


def canonical(url):
    return url.removesuffix(".git").rstrip("/").lower().replace("git@github.com:", "https://github.com/")


def resolve_source(api, project, repository, branch):
    organization = api.call("GET", f"/project/{segment(project)}")["project"]["organization_id"]
    base = f"/organization/{segment(organization)}/application/vcs-integrations"
    for integration in api.call("GET", base)["vcs_integrations"]:
        integration_id = integration["vcs_integration_id"]
        cursor = None
        for _ in range(100):
            path = base + "/" + segment(integration_id) + "/repositories"
            if cursor:
                path += "?cursor=" + segment(cursor)
            page = api.call("GET", path)
            for repo in page.get("repositories", []):
                if canonical(repo.get("source_url", "")) == canonical(repository):
                    return {"repository_url": repo["source_url"], "branch": branch,
                            "build_path": "./", "containerfile_path": "Dockerfile",
                            "vcs_integration_id": integration_id,
                            "remote_repository_id": str(repo["remote_repository_id"])}
            cursor = page.get("next")
            if not cursor:
                break
    raise RuntimeError("Repository is not connected to this Aiven organisation. In Console > Runtime > Deploy application, connect GitHub and grant access to your fork, then rerun this script. No services have been created.")


def choose_plan(catalog, service_type, cloud):
    plans = catalog[service_type]["service_plans"]
    eligible = [p for p in plans if cloud in p.get("regions", {}) and
                (p.get("shard_count", 1) == 1 if service_type == "clickhouse" else
                 p["regions"][cloud].get("node_memory_mb", 0) >= 1024)]
    if not eligible:
        raise RuntimeError(f"No suitable {service_type} plan in {cloud}; choose another --cloud")
    return min(eligible, key=lambda p: float(p["regions"][cloud]["price_usd"]))


def wait_service(api, path, timeout=1800):
    deadline = time.monotonic() + timeout
    previous = None
    while time.monotonic() < deadline:
        service = api.call("GET", path)["service"]
        status = service["state"]
        if status != previous:
            print(service["service_name"] + ": " + status, flush=True)
            previous = status
        if status == "RUNNING":
            return service
        if status in ("POWEROFF", "REBALANCING"):
            raise RuntimeError("Service is not ready. Check Console, then rerun setup.py to resume.")
        time.sleep(10)
    raise RuntimeError("Provisioning timed out. Resources are retained; check Console and rerun setup.py.")


def connection(service):
    components = [c for c in service.get("components", []) if c["component"] == "clickhouse_https" and c.get("route") in (None, "dynamic", "public")]
    if not components:
        raise RuntimeError("No reachable ClickHouse HTTPS endpoint; check service networking")
    c = components[0]
    return {"CLICKHOUSE_URL": f'https://{c["host"]}:{c["port"]}',
            "CLICKHOUSE_DATABASE": "blackbox"}


def clickhouse_integration(source):
    # The integration's port is native, not HTTPS. Keep the HTTPS endpoint and
    # our explicitly provisioned blackbox database separate from those defaults.
    keys = {"host": "CLICKHOUSE_HOST", "port": "CLICKHOUSE_NATIVE_PORT",
            "user": "CLICKHOUSE_USER", "password": "CLICKHOUSE_PASSWORD",
            "database": "CLICKHOUSE_SERVICE_DATABASE", "secure": "CLICKHOUSE_SECURE"}
    return {"integration_type": "application_service_credential", "source_service": source,
            "user_config": {"service_type": "clickhouse", "exposed_values": {
                field: {"environment_variable_key": key} for field, key in keys.items()}}}


def require_clickhouse_integration(api, project):
    types = api.call("GET", f"/project/{segment(project)}/integration_types")["integration_types"]
    if not any(t["integration_type"] == "application_service_credential" and
               "clickhouse" in t.get("source_service_types", []) for t in types):
        raise RuntimeError("ClickHouse service integration is not available in this project. Contact Aiven support before deploying.")


def ensure_clickhouse_integration(api, project, source, destination):
    expected = clickhouse_integration(source)
    base = f"/project/{segment(project)}"
    integrations = api.call("GET", base + f"/service/{segment(destination)}/integration")["service_integrations"]
    for integration in integrations:
        if (integration.get("enabled") and integration.get("integration_type") == expected["integration_type"]
                and integration.get("source_service") == source and integration.get("dest_service") == destination):
            if integration.get("user_config") != expected["user_config"]:
                raise RuntimeError("Existing ClickHouse integration uses different variable mappings. Check Connected services in Runtime before retrying.")
            return
    # Disabled entries in this API are available connections, not existing integrations.
    api.call("POST", base + "/integration", dict(expected, dest_service=destination))


def app_call(url, password, path, body=None):
    if not url.startswith("https://"):
        raise ValueError("Runtime endpoint must use HTTPS")
    req = request.Request(url.rstrip("/") + path, data=json.dumps(body).encode() if body is not None else None,
                          headers={"Authorization": "Basic " + base64.b64encode(("demo:" + password).encode()).decode(),
                                   "Content-Type": "application/json"})
    with request.urlopen(req, timeout=180) as response:
        return json.load(response)


def verify(state):
    print("Running all three faulty/reference pairs and checking stored evidence…", flush=True)
    checked = []
    for scenario in ("retry_spiral", "stale_source", "silent_failure"):
        for profile in ("baseline", "reference"):
            run = app_call(state["url"], state["demo_password"], "/run", {"scenario": scenario, "profile": profile})
            if run["mode"] == "deterministic" and run["success"] != (profile == "reference"):
                raise RuntimeError(f"Unexpected evaluation outcome: {scenario}/{profile}")
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline:
                evidence = app_call(state["url"], state["demo_password"], "/evidence/" + run["trace_id"])
                root = any(s["SpanName"] == "agent.run" and s["SpanAttributes"].get("agent.run_id") == run["run_id"] for s in evidence["traces"])
                logs = any(l["LogAttributes"].get("agent.run_id") == run["run_id"] and l["SpanId"] for l in evidence["logs"])
                metrics = any(m["MetricName"] == "agent.runs" and m["Attributes"].get("scenario") == scenario and m["Attributes"].get("profile") == profile for m in evidence["metrics"])
                if root and logs and metrics:
                    print(f"PASS {scenario}/{profile}: trace + correlated logs + metric in ClickHouse", flush=True)
                    checked.append({"scenario": scenario, "profile": profile, "run_id": run["run_id"], "trace_id": run["trace_id"]})
                    break
                time.sleep(2)
            else:
                raise RuntimeError("Telemetry did not reach ClickHouse within 60 seconds. Check Collector credentials/schema in Runtime settings.")
    return checked


def deploy(api, args, state):
    project = state.get("project") or args.project or input("Aiven project name: ").strip()
    prefix = state.get("prefix") or args.name
    if not re.fullmatch(r"[a-z][a-z0-9-]{0,30}", prefix):
        raise ValueError("--name must be 1–31 lowercase letters, digits or hyphens, starting with a letter")
    repository = args.repository or state.get("repository") or git("remote", "get-url", "origin")
    branch = args.branch or git("branch", "--show-current")
    if not branch:
        raise RuntimeError("Check out a branch before deployment")
    if git("status", "--porcelain"):
        raise RuntimeError("Commit your changes first; Runtime builds the pushed repository, not local files")
    sha = git("rev-parse", "HEAD")
    remote = git("ls-remote", repository, "refs/heads/" + branch)
    if not remote or remote.split()[0] != sha:
        raise RuntimeError("Push this branch first so Runtime builds the same code you are reviewing")
    source = resolve_source(api, project, repository, branch)
    require_clickhouse_integration(api, project)
    path = "/project/" + segment(project) + "/service"
    catalog = api.call("GET", "/project/" + segment(project) + "/service_types")["service_types"]
    cloud = state.get("cloud") or args.cloud
    cp = choose_plan(catalog, "clickhouse", cloud)
    ap = choose_plan(catalog, "application", cloud)
    ch_name, app_name = prefix + "-ch", prefix + "-app"
    for name in (ch_name, app_name):
        if name not in state.get("created", []):
            try:
                api.call("GET", path + "/" + segment(name))
            except APIError as exc:
                if exc.code != 404:
                    raise
            else:
                raise RuntimeError(f"{name} already exists and is not owned by this local setup state. Choose a different --name.")
    estimate = sum(float(p["regions"][cloud]["price_usd"]) for p in (cp, ap))
    print(f"Project: {project}\nRegion: {cloud}\nClickHouse: {ch_name} / {cp['service_plan']}\nRuntime: {app_name} / {ap['service_plan']}")
    print(f"Catalog base estimate: US${estimate:.4f}/hour; storage, traffic and account pricing may add charges. Services keep billing until stopped/deleted.")
    if not args.yes and input("Create/resume these services? [y/N] ").strip().lower() != "y":
        return
    state.update(project=project, prefix=prefix, cloud=cloud, repository=repository, branch=branch,
                 commit=sha, demo_password=state.get("demo_password") or secrets.token_urlsafe(24))
    state.setdefault("created", [])
    save(state)
    ch_path = path + "/" + ch_name
    if ch_name not in state["created"]:
        api.call("POST", path, {"service_name": ch_name, "service_type": "clickhouse", "plan": cp["service_plan"], "cloud": cloud,
                                "project_vpc_id": None, "user_config": {}})
        state["created"].append(ch_name)
        save(state)
    ch = wait_service(api, ch_path)
    databases = api.call("GET", ch_path + "/clickhouse/db").get("databases", [])
    if not any((d.get("name") or d.get("database")) == "blackbox" if isinstance(d, dict) else d == "blackbox" for d in databases):
        api.call("POST", ch_path + "/clickhouse/db", {"database": "blackbox"})
    env = connection(ch)
    certificate = api.call("GET", "/project/" + segment(project) + "/kms/ca")["certificate"]
    env["CLICKHOUSE_CA_BASE64"] = base64.b64encode(certificate.encode()).decode()
    env.update(DEMO_PASSWORD=state["demo_password"], AGENT_VERSION=sha, MODEL_MODE="deterministic")
    # The cloud application bootstraps tables: no local DB access, SDK, Docker or CA setup needed.
    variables = [{"key": key, "value": value, "kind": "secret" if key in ("DEMO_PASSWORD", "CLICKHOUSE_CA_BASE64") else "variable"} for key, value in env.items()]
    app_path = path + "/" + app_name
    config = {"application": {"source": source, "ports": [{"name": "http", "port": 8080, "protocol": "HTTP"}], "environment_variables": variables}}
    if app_name not in state["created"]:
        api.call("POST", path, {"service_name": app_name, "service_type": "application", "plan": ap["service_plan"], "cloud": cloud,
                                "project_vpc_id": None, "user_config": config,
                                "service_integrations": [clickhouse_integration(ch_name)]})
        state["created"].append(app_name)
        save(state)
    else:
        # Also reconcile interrupted deployments and starters created before integrations.
        # Remove manually copied credentials before the integration owns these keys.
        api.call("PUT", app_path, {"user_config": config})
        ensure_clickhouse_integration(api, project, ch_name, app_name)
        api.call("POST", app_path + "/application/redeploy", {})
    wait_service(api, app_path)
    deadline = time.monotonic() + 1200
    print("Waiting for the container build and authenticated HTTP endpoint…", flush=True)
    while time.monotonic() < deadline:
        app = api.call("GET", app_path)["service"]
        urls = [c["path"] for c in app.get("components", []) if c.get("path", "").startswith("https://")]
        if urls:
            state["url"] = urls[0].rstrip("/")
            save(state)
            try:
                health = app_call(state["url"], state["demo_password"], "/healthz")
                if health.get("version") == sha:
                    break
            except (error.URLError, TimeoutError):
                pass
        time.sleep(10)
    else:
        raise RuntimeError("Application did not become ready; check Runtime build status. Rerun setup.py redeploy after fixing the cause.")
    print("Demo:", state["url"], "\nUsername: demo\nPassword saved in .deployment.json (also available in Runtime secret settings).")
    checked = verify(state)
    print("All ingestion checks passed. Build your investigation feature using README.md and clickhouse/queries.sql.")
    return checked


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("action", nargs="?", choices=("deploy", "verify", "redeploy"), default="deploy")
    parser.add_argument("--project")
    parser.add_argument("--name", default="blackbox")
    parser.add_argument("--cloud", default="aws-eu-west-1")
    parser.add_argument("--repository")
    parser.add_argument("--branch")
    parser.add_argument("--yes", action="store_true", help="Accept the displayed service creation costs")
    args = parser.parse_args()
    state = json.loads(STATE.read_text()) if STATE.exists() else {}
    if args.action == "verify":
        if not state.get("url"):
            raise RuntimeError("Run setup.py first")
        verify(state)
        return
    token = os.getenv("AIVEN_TOKEN") or getpass.getpass("Aiven API token (hidden, never saved): ")
    if not token.strip():
        raise ValueError("An Aiven API token is required")
    deploy(Aiven(token.strip()), args, state)


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, ValueError, KeyError) as exc:
        print("Setup stopped:", str(exc), file=sys.stderr)
        print("Existing resources have NOT been deleted. Fix the issue and rerun. See README.md > Troubleshooting.", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print("\nInterrupted. Resources remain; rerun to resume or remove them in Aiven Console.")
        sys.exit(130)

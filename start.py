"""One Runtime container: bootstrap tables, then supervise agent + local Collector."""
import base64
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.request import urlopen

from agent.storage import ClickHouse


def collector_config(ca_file=None):
    exporter = {
        "endpoint": "${env:CLICKHOUSE_URL}", "username": "${env:CLICKHOUSE_USER}",
        "password": "${env:CLICKHOUSE_PASSWORD}", "database": "${env:CLICKHOUSE_DATABASE}",
        "create_schema": False, "async_insert": False, "timeout": "15s",
        "sending_queue": {"enabled": True, "queue_size": 1000, "num_consumers": 1,
                          "batch": {"flush_timeout": "1s", "min_size": 100}},
        "retry_on_failure": {"enabled": True, "initial_interval": "1s", "max_interval": "5s", "max_elapsed_time": "60s"},
    }
    if ca_file:
        # Aiven HTTPS can use a public CA; preserve system roots as well as project CA.
        exporter["tls"] = {"ca_file": ca_file, "include_system_ca_certs_pool": True, "insecure_skip_verify": False}
    return {
        "receivers": {"otlp": {"protocols": {"http": {"endpoint": "127.0.0.1:4318"}}}},
        "processors": {"memory_limiter": {"check_interval": "1s", "limit_mib": 192, "spike_limit_mib": 48}},
        "exporters": {"clickhouse": exporter},
        "extensions": {"health_check": {"endpoint": "127.0.0.1:13133"}},
        "service": {"extensions": ["health_check"], "pipelines": {
            signal_name: {"receivers": ["otlp"], "processors": ["memory_limiter"], "exporters": ["clickhouse"]}
            for signal_name in ("traces", "logs", "metrics")}},
    }


def main():
    if len(os.getenv("DEMO_PASSWORD", "")) < 16:
        raise ValueError("DEMO_PASSWORD must have at least 16 characters")
    os.environ.setdefault("CLICKHOUSE_DATABASE", "blackbox")
    os.environ.setdefault("CLICKHOUSE_USER", "avnadmin")
    ClickHouse().initialize()
    children = []
    def shutdown(*_):
        raise SystemExit(0)
    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    with tempfile.TemporaryDirectory(prefix="blackbox-") as directory:
        ca_file = None
        if os.getenv("CLICKHOUSE_CA_BASE64"):
            ca_file = str(Path(directory) / "ca.pem")
            Path(ca_file).write_bytes(base64.b64decode(os.environ["CLICKHOUSE_CA_BASE64"], validate=True))
        config = Path(directory) / "collector.json"
        config.write_text(json.dumps(collector_config(ca_file)))
        try:
            collector = subprocess.Popen([os.getenv("COLLECTOR_BINARY", "/usr/local/bin/otelcol-contrib"), "--config", str(config)])
            children.append(collector)
            for _ in range(60):
                if collector.poll() is not None:
                    raise RuntimeError("Collector failed to start")
                try:
                    with urlopen("http://127.0.0.1:13133", timeout=1) as response:
                        if response.status == 200:
                            break
                except OSError:
                    time.sleep(0.5)
            else:
                raise RuntimeError("Collector readiness timed out")
            children.append(subprocess.Popen([sys.executable, "-m", "uvicorn", "agent.server:app", "--host", "0.0.0.0", "--port", "8080", "--no-access-log"]))
            print("Demo ready on port 8080; Collector is private on localhost", flush=True)
            while all(p.poll() is None for p in children):
                time.sleep(0.5)
            raise RuntimeError("A required process stopped")
        finally:
            # Stop the agent first so SDK shutdown can flush into the still-running Collector.
            for child in reversed(children):
                if child.poll() is None:
                    child.terminate()
                    try:
                        child.wait(timeout=20)
                    except subprocess.TimeoutExpired:
                        child.kill()
                        child.wait()


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("Startup failed:", str(exc) if isinstance(exc, (ValueError, RuntimeError)) else type(exc).__name__, file=sys.stderr)
        sys.exit(1)

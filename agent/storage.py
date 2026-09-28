"""Minimal ClickHouse HTTPS client. SQL is fixed or validated, secrets stay in headers."""
import base64
import json
import os
import re
import ssl
from pathlib import Path
from urllib import error, parse, request


class ClickHouse:
    def __init__(self, env=None):
        env = os.environ if env is None else env
        self.url = env["CLICKHOUSE_URL"].rstrip("/")
        parsed = parse.urlsplit(self.url)
        local = env.get("LOCAL_DEVELOPMENT") == "true"
        if parsed.scheme != "https" and not (local and parsed.scheme == "http" and parsed.hostname in ("localhost", "127.0.0.1", "clickhouse")):
            raise ValueError("ClickHouse requires HTTPS (except explicit local development)")
        if parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ("", "/"):
            raise ValueError("CLICKHOUSE_URL must be a plain origin without credentials")
        self.database = env.get("CLICKHOUSE_DATABASE", "blackbox")
        if not re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_]{0,62}", self.database):
            raise ValueError("Invalid ClickHouse database name")
        self.username = env.get("CLICKHOUSE_USER", "avnadmin")
        self.password = env["CLICKHOUSE_PASSWORD"]
        self.context = ssl.create_default_context()
        if env.get("CLICKHOUSE_CA_BASE64"):
            self.context.load_verify_locations(cadata=base64.b64decode(env["CLICKHOUSE_CA_BASE64"], validate=True).decode())

    def query(self, sql, *, database=True):
        suffix = "?" + parse.urlencode({"database": self.database}) if database else ""
        req = request.Request(self.url + "/" + suffix, data=sql.encode(), headers={
            "X-ClickHouse-User": self.username, "X-ClickHouse-Key": self.password,
            "Content-Type": "text/plain; charset=utf-8"})
        try:
            with request.urlopen(req, context=self.context, timeout=20) as response:
                return response.read().decode()
        except error.HTTPError as exc:
            # Query error text can contain literals; never return credentials or raw SQL to callers.
            raise RuntimeError(f"ClickHouse returned HTTP {exc.code}; check schema, credentials and permissions") from None
        except (error.URLError, TimeoutError):
            raise RuntimeError("ClickHouse connection failed; check URL, TLS and network access") from None

    def initialize(self):
        # The setup wizard creates the database through Aiven's API first.
        schema = Path(__file__).resolve().parents[1] / "clickhouse/schema.sql"
        for statement in schema.read_text().replace('"blackbox"', '"' + self.database + '"').split(";"):
            if statement.strip():
                self.query(statement)

    def evidence(self, trace_id):
        if not re.fullmatch(r"[0-9a-f]{32}", trace_id):
            raise ValueError("Invalid trace ID")
        result = {}
        for name, sql in {
            "traces": f"SELECT SpanName, SpanId, ParentSpanId, Duration, StatusCode, SpanAttributes FROM otel_traces WHERE TraceId = '{trace_id}' ORDER BY Timestamp",
            "logs": f"SELECT Timestamp, SpanId, Body, LogAttributes FROM otel_logs WHERE TraceId = '{trace_id}' ORDER BY Timestamp",
            "metrics": "SELECT MetricName, Attributes, Value FROM otel_metrics_sum WHERE ServiceName = 'hackathon-demo-agent' AND MetricName = 'agent.runs' AND TimeUnix > now() - INTERVAL 5 MINUTE ORDER BY TimeUnix DESC LIMIT 30",
        }.items():
            result[name] = json.loads(self.query(sql + " FORMAT JSON"))["data"]
        result["metrics_note"] = "Recent aggregate counters, not per-trace metrics. Use root span attributes for run-level results."
        return result

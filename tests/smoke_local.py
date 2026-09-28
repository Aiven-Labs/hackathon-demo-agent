"""Container integration test, including real Collector writes to real ClickHouse."""
import base64
import json
import http.client
import sys
import time
from urllib import request, error


def report_failure(kind, value, traceback):
    message = str(value).replace("%", "%25").replace("\n", "%0A").replace("\r", "%0D")
    print("::error::Container verification: " + kind.__name__ + ": " + message, flush=True)
    sys.__excepthook__(kind, value, traceback)


sys.excepthook = report_failure


def call(path, body=None):
    req = request.Request("http://127.0.0.1:8080" + path, data=json.dumps(body).encode() if body else None,
                          headers={"Authorization": "Basic " + base64.b64encode(b"demo:ci-demo-password-long-enough").decode(), "Content-Type": "application/json"})
    with request.urlopen(req, timeout=30) as r:
        return json.load(r)


for attempt in range(120):
    try:
        call("/healthz")
        break
    except (OSError, http.client.HTTPException):
        time.sleep(2)
else:
    raise RuntimeError("Container did not become ready")

for scenario in ("retry_spiral", "stale_source", "silent_failure"):
    for profile in ("baseline", "reference"):
        run = call("/run", {"scenario": scenario, "profile": profile})
        assert run["success"] == (profile == "reference"), run
        for _ in range(30):
            data = call("/evidence/" + run["trace_id"])
            if data["traces"] and data["logs"] and any(m["MetricName"] == "agent.runs" and m["Attributes"].get("scenario") == scenario and m["Attributes"].get("profile") == profile for m in data["metrics"]):
                break
            time.sleep(2)
        else:
            raise AssertionError("Missing telemetry for " + scenario + "/" + profile)
        print("PASS", scenario, profile, "traces/logs/metrics")

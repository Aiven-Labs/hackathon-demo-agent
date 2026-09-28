"""Edit Policy or the marked decisions to implement your own improvement.

Tools and fixtures are always simulated. Optional live mode calls a real model
for the planning/final-answer steps; it never makes a real booking.
"""
import hashlib
import json
import os
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib import request, error

from opentelemetry.trace import Status, StatusCode

DATA = Path(__file__).resolve().parents[1] / "fixtures"
SCENARIOS = ("retry_spiral", "stale_source", "silent_failure")


@dataclass(frozen=True)
class Policy:
    max_attempts: int = 5
    newest_source: bool = False
    require_confirmation: bool = False


POLICIES = {"baseline": Policy(), "reference": Policy(2, True, True)}


def load_tasks(split="development"):
    if split not in ("development", "heldout"):
        raise ValueError("Unknown task split")
    return json.loads((DATA / (split + ".json")).read_text())


def evaluate(task, result, scenario):
    """Independent outcome checks, using fixture truth rather than HTTP status."""
    return {
        "venue_open": task["truth_open_from"] <= task["arrival_hour"] < task["truth_open_until"],
        "correct_opening_advice": result["venue_open"] == (
            task["truth_open_from"] <= task["arrival_hour"] < task["truth_open_until"]),
        "no_unsupported_booking_claim": not result["booking_confirmed"] or result["tool_confirmed"],
        "within_tool_budget": result["reservation_calls"] <= 2,
    }


class Agent:
    def __init__(self, telemetry, delay=0.08, mode=None):
        self.t = telemetry
        self.delay = delay
        self.mode = mode or os.getenv("MODEL_MODE", "deterministic")
        if self.mode not in ("deterministic", "live"):
            raise ValueError("MODEL_MODE must be deterministic or live")

    def model(self, phase, payload, attrs, usage):
        with self.t.tracer.start_as_current_span("model." + phase, attributes=attrs) as span:
            span.set_attribute("gen_ai.operation.name", "chat")
            span.set_attribute("demo.model.simulated", self.mode == "deterministic")
            if self.mode == "deterministic":
                # These are fixture workload units, not billed/provider token counts.
                usage["simulated_input_tokens"] += 100 + len(json.dumps(payload)) // 4
                usage["simulated_output_tokens"] += 30
                span.set_attribute("gen_ai.request.model", "scripted-fixture-v1")
                time.sleep(self.delay)
                return payload
            base = os.getenv("MODEL_BASE_URL", "https://api.openai.com/v1").rstrip("/")
            if not base.startswith("https://"):
                raise ValueError("Live MODEL_BASE_URL must use HTTPS")
            model = os.environ["MODEL_NAME"]
            span.set_attribute("gen_ai.request.model", model)
            body = {"model": model, "messages": [
                {"role": "system", "content": "You are a fictional Paris itinerary assistant. Return only a JSON object. For planning return an empty object. For final answers return venue_open (boolean), booking_confirmed (boolean), and explanation (string). Use only supplied evidence. No real bookings exist."},
                {"role": "user", "content": json.dumps({"phase": phase, "evidence": payload})}],
                "max_tokens": 300}
            req = request.Request(base + "/chat/completions", data=json.dumps(body).encode(),
                                  headers={"Authorization": "Bearer " + os.environ["MODEL_API_KEY"],
                                           "Content-Type": "application/json"})
            try:
                with request.urlopen(req, timeout=20) as response:
                    data = json.load(response)
            except (error.URLError, TimeoutError):
                raise RuntimeError("Model request failed; check model configuration and quota") from None
            counts = data.get("usage", {})
            if "prompt_tokens" not in counts or "completion_tokens" not in counts:
                usage["complete"] = False
            else:
                usage["input_tokens"] += counts["prompt_tokens"]
                usage["output_tokens"] += counts["completion_tokens"]
                span.set_attribute("gen_ai.usage.input_tokens", counts["prompt_tokens"])
                span.set_attribute("gen_ai.usage.output_tokens", counts["completion_tokens"])
            text = data["choices"][0]["message"]["content"]
            try:
                return json.loads(text)
            except (ValueError, TypeError):
                raise RuntimeError("Model returned invalid JSON; this run is unsuccessful") from None

    def run(self, scenario, profile="baseline", task=None):
        if scenario not in SCENARIOS or profile not in POLICIES:
            raise ValueError("Unknown scenario or profile")
        task = task or load_tasks()[0]
        policy = POLICIES[profile]
        run_id = str(uuid.uuid4())
        config = asdict(policy)
        attrs = {"agent.run_id": run_id, "agent.scenario": scenario, "agent.profile": profile,
                 "agent.task_id": task["id"], "agent.config_hash": hashlib.sha256(
                     json.dumps(config, sort_keys=True).encode()).hexdigest()[:16],
                 "agent.config": json.dumps(config, sort_keys=True),
                 "agent.code_version": os.getenv("AGENT_VERSION", "worktree"),
                 "data.version": "fictional-paris-v1", "demo.mode": self.mode}
        usage = {"input_tokens": 0, "output_tokens": 0, "simulated_input_tokens": 0,
                 "simulated_output_tokens": 0, "complete": True}
        started = time.monotonic()
        with self.t.tracer.start_as_current_span("agent.run", attributes=attrs) as root:
            result = {"run_id": run_id, "trace_id": format(root.get_span_context().trace_id, "032x"),
                      "scenario": scenario, "profile": profile, "task_id": task["id"],
                      "mode": self.mode, "config": config, "config_hash": attrs["agent.config_hash"],
                      "code_version": attrs["agent.code_version"], "reservation_calls": 0,
                      "tool_calls": 0, "booking_confirmed": False, "tool_confirmed": False,
                      "venue_open": False}
            self.t.event("Agent execution started", attrs)
            try:
                records = json.loads((DATA / "venues.json").read_text())
                with self.t.tracer.start_as_current_span("tool.search_venues", attributes=attrs) as span:
                    matches = [r for r in records if r["id"] == task["venue_id"]]
                    if scenario == "stale_source" and not policy.newest_source:
                        venue = min(matches, key=lambda r: r["version"])
                    else:
                        venue = max(matches, key=lambda r: r["version"])
                    for key in ("id", "version", "updated_at", "open_from", "open_until"):
                        span.set_attribute("source." + key, venue[key])
                    self.t.event("Venue record retrieved", {**attrs, "source.record": json.dumps(venue)})
                    self.count_tool("search_venues", scenario, profile, result)
                    time.sleep(self.delay)
                with self.t.tracer.start_as_current_span("tool.plan_route", attributes=attrs) as span:
                    span.set_attribute("route.walk_minutes", task["walk_minutes"])
                    self.count_tool("plan_route", scenario, profile, result)
                    time.sleep(self.delay)
                response = {}
                for attempt in range(1, policy.max_attempts + 1):
                    self.model("plan", {"venue": venue, "attempt": attempt}, attrs, usage)
                    with self.t.tracer.start_as_current_span("tool.check_reservation", attributes={
                            **attrs, "retry.attempt": attempt}) as span:
                        self.count_tool("check_reservation", scenario, profile, result)
                        result["reservation_calls"] += 1
                        if scenario == "retry_spiral":
                            response = {"status": 429, "confirmed": False, "retry_after_seconds": 30}
                        elif scenario == "silent_failure":
                            response = {"status": 200, "confirmed": False, "error": "NO_AVAILABILITY"}
                        else:
                            response = {"status": 200, "confirmed": True, "reference": "FICTIONAL-ONLY"}
                        span.set_attribute("http.response.status_code", response["status"])
                        span.set_attribute("tool.confirmed", response["confirmed"])
                        if response["status"] == 429:
                            span.set_status(Status(StatusCode.ERROR, "Rate limited"))
                        self.t.event("Reservation service response", {
                            **attrs, "retry.attempt": attempt, "tool.response": json.dumps(response)},
                            warning=not response["confirmed"])
                        time.sleep(self.delay)
                    if response["status"] != 429:
                        break
                result["tool_confirmed"] = response["confirmed"]
                # Deliberately flawed harness decisions. Change these or Policy.
                claimed = response["confirmed"] if policy.require_confirmation else response["status"] == 200
                evidence = {"venue_open": venue["open_from"] <= task["arrival_hour"] < venue["open_until"],
                            "booking_confirmed": claimed,
                            "explanation": "Fictional itinerary based on retrieved venue and booking status."}
                final = self.model("answer", evidence, attrs, usage)
                if not isinstance(final, dict) or any(type(final.get(k)) is not bool for k in ("venue_open", "booking_confirmed")):
                    raise RuntimeError("Model answer did not follow the required boolean schema")
                result.update({k: final[k] for k in ("venue_open", "booking_confirmed")})
                result["answer"] = str(final.get("explanation", ""))[:1000]
                checks = evaluate(task, result, scenario)
                # venue_open is contextual truth, not a success condition: a correct closure warning passes.
                result["checks"] = {k: v for k, v in checks.items() if k != "venue_open"}
                result["success"] = all(result["checks"].values())
            except Exception as exc:
                usage["complete"] = False
                root.set_status(Status(StatusCode.ERROR, type(exc).__name__))
                self.t.event("Agent execution failed", {**attrs, "error.type": type(exc).__name__}, warning=True)
                result.update(success=False, checks={}, error=type(exc).__name__)
            result["duration_seconds"] = round(time.monotonic() - started, 4)
            result["usage"] = usage
            result["estimated_cost_usd"] = None
            if self.mode == "live" and usage["complete"]:
                prices = [os.getenv("INPUT_USD_PER_MILLION"), os.getenv("OUTPUT_USD_PER_MILLION")]
                if all(prices):
                    result["estimated_cost_usd"] = (usage["input_tokens"] * float(prices[0]) + usage["output_tokens"] * float(prices[1])) / 1_000_000
            root.set_attribute("evaluation.success", result["success"])
            root.set_attribute("agent.result", json.dumps(result, sort_keys=True))
            self.t.event("Agent execution evaluated", {**attrs, "evaluation.success": result["success"],
                                                       "agent.result": json.dumps(result, sort_keys=True)})
            dimensions = {"scenario": scenario, "profile": profile, "mode": self.mode,
                          "outcome": "success" if result["success"] else "failure"}
            self.t.runs.add(1, dimensions)
            self.t.duration.record(result["duration_seconds"], dimensions)
        return result

    def count_tool(self, name, scenario, profile, result):
        result["tool_calls"] += 1
        self.t.calls.add(1, {"tool": name, "scenario": scenario, "profile": profile, "mode": self.mode})

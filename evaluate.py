"""Run paired scenarios locally and export the same telemetry when configured."""
import argparse
import json
from pathlib import Path

from agent.engine import Agent, SCENARIOS, load_tasks
from agent.telemetry import Telemetry


def summarize(results):
    return {"runs": len(results), "success_rate": sum(r["success"] for r in results) / len(results),
            "tool_calls": sum(r["tool_calls"] for r in results),
            "total_duration_seconds": round(sum(r["duration_seconds"] for r in results), 4),
            "simulated_tokens": sum(r["usage"]["simulated_input_tokens"] + r["usage"]["simulated_output_tokens"] for r in results),
            "estimated_cost_usd": (sum(r["estimated_cost_usd"] for r in results)
                                   if all(r["estimated_cost_usd"] is not None for r in results) else None)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--split", choices=("development", "heldout"), default="development")
    parser.add_argument("--scenario", choices=SCENARIOS)
    parser.add_argument("--export", action="store_true", help="Send telemetry to the configured OTLP endpoint")
    parser.add_argument("--output", default="reports/comparison.json")
    args = parser.parse_args()
    telemetry = Telemetry(enabled=args.export)
    try:
        agent = Agent(telemetry)
        runs = [agent.run(scenario, profile, task) for task in load_tasks(args.split)
                for scenario in ([args.scenario] if args.scenario else SCENARIOS)
                for profile in ("baseline", "reference")]
        report = {"split": args.split, "mode": agent.mode,
                  "summary": {p: summarize([r for r in runs if r["profile"] == p]) for p in ("baseline", "reference")},
                  "runs": runs,
                  "note": "Deterministic token counts are simulated workload units, not actual model cost. Tiny fixture sets do not establish production accuracy."}
        target = Path(args.output)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps(report["summary"], indent=2))
        print("Full report:", target)
    finally:
        telemetry.shutdown()


if __name__ == "__main__":
    main()

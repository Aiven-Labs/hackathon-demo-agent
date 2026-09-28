-- Run in the ClickHouse query editor with database blackbox selected.
-- 1. Recent runs and their evaluated outcomes.
SELECT Timestamp, TraceId, SpanAttributes['agent.scenario'] AS scenario,
       SpanAttributes['agent.profile'] AS profile,
       SpanAttributes['evaluation.success'] AS success,
       SpanAttributes['agent.result'] AS result
FROM otel_traces WHERE SpanName = 'agent.run'
ORDER BY Timestamp DESC LIMIT 20;

-- 2. Replace TRACE_ID with a trace from the runner to reconstruct its timeline.
SELECT Timestamp, SpanName, SpanId, ParentSpanId,
       Duration / 1000000 AS duration_ms, StatusCode, SpanAttributes
FROM otel_traces WHERE TraceId = 'TRACE_ID' ORDER BY Timestamp;

-- 3. Logs correlated with the same trace and span IDs.
SELECT Timestamp, SpanId, SeverityText, Body, LogAttributes
FROM otel_logs WHERE TraceId = 'TRACE_ID' ORDER BY Timestamp;

-- 4. Most recent cumulative count for each configuration/outcome.
-- Do not SUM cumulative snapshots: that double-counts runs.
SELECT Attributes, argMax(Value, TimeUnix) AS latest_count
FROM otel_metrics_sum WHERE MetricName = 'agent.runs'
GROUP BY Attributes;

-- 5. Before/after results. Deduplicate retried span deliveries by TraceId first.
SELECT scenario, profile, count() AS runs, avg(success) AS success_rate,
       avg(duration_seconds) AS mean_latency_seconds, avg(tool_calls) AS mean_tool_calls
FROM (
  SELECT TraceId,
         any(SpanAttributes['agent.scenario']) AS scenario,
         any(SpanAttributes['agent.profile']) AS profile,
         any(JSONExtractBool(SpanAttributes['agent.result'], 'success')) AS success,
         any(JSONExtractFloat(SpanAttributes['agent.result'], 'duration_seconds')) AS duration_seconds,
         any(JSONExtractUInt(SpanAttributes['agent.result'], 'tool_calls')) AS tool_calls
  FROM otel_traces WHERE SpanName = 'agent.run' GROUP BY TraceId
) GROUP BY scenario, profile ORDER BY scenario, profile;

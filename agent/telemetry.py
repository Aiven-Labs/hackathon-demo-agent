"""Export application telemetry directly; never scrape Runtime/stdout logs."""
import logging
import os

from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor


class Telemetry:
    def __init__(self, enabled=True):
        resource = Resource.create({
            "service.name": "hackathon-demo-agent",
            "service.version": os.getenv("AGENT_VERSION", "worktree"),
            "deployment.environment.name": os.getenv("ENVIRONMENT", "hackathon"),
            "demo.mode": os.getenv("MODEL_MODE", "deterministic"),
        })
        self.traces = TracerProvider(resource=resource)
        self.logs = LoggerProvider(resource=resource)
        readers = []
        if enabled:
            endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://127.0.0.1:4318").rstrip("/")
            self.traces.add_span_processor(BatchSpanProcessor(
                OTLPSpanExporter(endpoint=endpoint + "/v1/traces", timeout=5)))
            self.logs.add_log_record_processor(BatchLogRecordProcessor(
                OTLPLogExporter(endpoint=endpoint + "/v1/logs", timeout=5)))
            readers.append(PeriodicExportingMetricReader(
                OTLPMetricExporter(endpoint=endpoint + "/v1/metrics", timeout=5),
                export_interval_millis=5000))
        self.metrics = MeterProvider(resource=resource, metric_readers=readers)
        self.tracer = self.traces.get_tracer("blackbox.agent", "1.0")
        self.logger = logging.getLogger("blackbox.events." + str(id(self)))
        self.logger.setLevel(logging.INFO)
        self.logger.propagate = False
        self.logger.addHandler(LoggingHandler(logger_provider=self.logs))
        meter = self.metrics.get_meter("blackbox.agent", "1.0")
        self.runs = meter.create_counter("agent.runs", unit="{run}")
        self.calls = meter.create_counter("agent.tool.calls", unit="{call}")
        self.duration = meter.create_histogram("agent.run.duration", unit="s")

    def event(self, message, attributes, warning=False):
        self.logger.log(logging.WARNING if warning else logging.INFO, message, extra=attributes)

    def flush(self):
        return all([self.traces.force_flush(timeout_millis=5000),
                    self.logs.force_flush(timeout_millis=5000),
                    self.metrics.force_flush(timeout_millis=5000)])

    def shutdown(self):
        self.traces.shutdown()
        self.logs.shutdown()
        self.metrics.shutdown()

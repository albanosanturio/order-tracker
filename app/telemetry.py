"""OpenTelemetry setup: traces, metrics, and logs exported to the console.

Run `docker compose logs app` to see all three signals interleaved with the
regular application output.
"""

import logging

from opentelemetry import metrics, trace
from opentelemetry._logs import set_logger_provider
from opentelemetry.instrumentation.logging import LoggingInstrumentor
from opentelemetry.sdk._logs import LoggerProvider
from opentelemetry.sdk._logs.export import ConsoleLogRecordExporter, SimpleLogRecordProcessor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import ConsoleMetricExporter, PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import ConsoleSpanExporter, SimpleSpanProcessor

SERVICE_NAME = "order-tracker"


def setup_telemetry() -> None:
    resource = Resource.create({"service.name": SERVICE_NAME})

    tracer_provider = TracerProvider(resource=resource)
    tracer_provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))
    trace.set_tracer_provider(tracer_provider)

    meter_provider = MeterProvider(
        resource=resource,
        metric_readers=[
            PeriodicExportingMetricReader(ConsoleMetricExporter(), export_interval_millis=5000)
        ],
    )
    metrics.set_meter_provider(meter_provider)

    logger_provider = LoggerProvider(resource=resource)
    logger_provider.add_log_record_processor(SimpleLogRecordProcessor(ConsoleLogRecordExporter()))
    set_logger_provider(logger_provider)

    logging.getLogger().setLevel(logging.INFO)
    LoggingInstrumentor().instrument(set_logging_format=True)


tracer = trace.get_tracer(SERVICE_NAME)
meter = metrics.get_meter(SERVICE_NAME)
logger = logging.getLogger(SERVICE_NAME)

order_lookup_counter = meter.create_counter(
    "order_lookups_total",
    description="Number of order lookups by outcome",
    unit="1",
)

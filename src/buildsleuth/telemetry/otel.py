"""Tracer setup and the span helpers BuildSleuth instruments with.

Tracing is best effort. A missing, misconfigured or unreachable collector must
never break triage, so every failure path here degrades to a no-op tracer.
Attribute names all come from `attrs` so a semantic-convention rename stays a
one-file diff.
"""

import logging
import os
from collections.abc import Iterator, Mapping
from contextlib import contextmanager

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import NoOpTracer, Span, SpanKind, Status, StatusCode, Tracer
from opentelemetry.util.types import AttributeValue

from buildsleuth import __version__
from buildsleuth.llm.types import Usage
from buildsleuth.telemetry import attrs

_LOG = logging.getLogger(__name__)

DEFAULT_SERVICE_NAME = "buildsleuth"
TRACER_NAME = "buildsleuth"
OTLP_ENDPOINT_ENV = "OTEL_EXPORTER_OTLP_ENDPOINT"
OTLP_TRACES_PATH = "/v1/traces"
EXPORT_TIMEOUT_SECONDS = 5

# Request attributes whose key matches one of these never reach a span. Provider
# credentials are the one thing a trace backend must never see, and traces are
# routinely shared more widely than logs.
SENSITIVE_KEY_MARKERS = (
    "api_key",
    "apikey",
    "access_token",
    "auth_token",
    "authorization",
    "credential",
    "password",
    "secret",
)

_provider: TracerProvider | None = None
_global_provider_registered = False
_disabled = False


def setup_tracing(
    service_name: str = DEFAULT_SERVICE_NAME,
    endpoint: str | None = None,
    enabled: bool = True,
) -> None:
    """Configure the tracer provider once per process.

    Spans are exported over OTLP HTTP when `endpoint` or the
    OTEL_EXPORTER_OTLP_ENDPOINT environment variable names a collector, and are
    dropped otherwise. Calling this twice is a no-op, and it never raises.
    """
    global _provider, _global_provider_registered, _disabled

    if not enabled:
        _disabled = True
        return

    _disabled = False
    if _provider is not None:
        return

    try:
        resource = Resource.create(
            {attrs.SERVICE_NAME: service_name, attrs.SERVICE_VERSION: __version__}
        )
        provider = TracerProvider(resource=resource)
        target = endpoint if endpoint is not None else os.environ.get(OTLP_ENDPOINT_ENV)
        if target:
            exporter = OTLPSpanExporter(
                endpoint=_traces_endpoint(target), timeout=EXPORT_TIMEOUT_SECONDS
            )
            provider.add_span_processor(BatchSpanProcessor(exporter))
        # The OTel global can only be set once per process. Later calls would be
        # ignored with a warning, so guard them and keep our own reference.
        if not _global_provider_registered:
            trace.set_tracer_provider(provider)
            _global_provider_registered = True
        _provider = provider
    except Exception:
        _LOG.warning("tracing setup failed, continuing untraced", exc_info=True)
        _provider = None


def shutdown_tracing() -> None:
    """Flush pending spans and return the module to its unconfigured state."""
    global _provider, _disabled

    provider, _provider = _provider, None
    _disabled = False
    if provider is None:
        return
    try:
        provider.shutdown()
    except Exception:
        _LOG.warning("tracing shutdown failed", exc_info=True)


def get_tracer() -> Tracer:
    """Return the BuildSleuth tracer, or a no-op tracer when tracing is not set up."""
    if _disabled:
        return NoOpTracer()
    if _provider is not None:
        return _provider.get_tracer(TRACER_NAME, __version__)
    return trace.get_tracer(TRACER_NAME, __version__)


class LlmSpan:
    """A GenAI span that knows how to record a model response or a failure."""

    def __init__(self, span: Span) -> None:
        self._span = span

    @property
    def span(self) -> Span:
        return self._span

    def set_attribute(self, key: str, value: AttributeValue) -> None:
        """Set one attribute, dropping it if the key looks like a credential."""
        _set_safe_attributes(self._span, {key: value})

    def record_response(
        self,
        result_model: str,
        usage: Usage | None = None,
        finish_reason: str | None = None,
    ) -> None:
        """Record the response model, token usage and finish reason on the span."""
        if result_model:
            self._span.set_attribute(attrs.GEN_AI_RESPONSE_MODEL, result_model)
        if usage is not None:
            self._span.set_attribute(attrs.GEN_AI_USAGE_INPUT_TOKENS, usage.input_tokens)
            self._span.set_attribute(attrs.GEN_AI_USAGE_OUTPUT_TOKENS, usage.output_tokens)
        if finish_reason:
            self._span.set_attribute(attrs.GEN_AI_RESPONSE_FINISH_REASONS, (finish_reason,))

    def record_error(self, error: BaseException) -> None:
        """Mark the span failed, recording the exception type but never its message."""
        _mark_error(self._span, error)


@contextmanager
def llm_span(
    operation: str,
    model: str,
    provider: str,
    request_attrs: Mapping[str, AttributeValue] | None = None,
) -> Iterator[LlmSpan]:
    """Open a GenAI client span named per convention, for example "chat gpt-4o-mini".

    Request attributes are set on entry. Use the yielded object to record the
    response. Exceptions mark the span ERROR and propagate unchanged.
    """
    name = attrs.LLM_SPAN_NAME_TEMPLATE.format(operation=operation, model=model)
    with _started_span(name, SpanKind.CLIENT) as span:
        span.set_attribute(attrs.GEN_AI_OPERATION_NAME, operation)
        span.set_attribute(attrs.GEN_AI_PROVIDER_NAME, provider)
        span.set_attribute(attrs.GEN_AI_REQUEST_MODEL, model)
        _set_safe_attributes(span, request_attrs)
        yield LlmSpan(span)


@contextmanager
def stage_span(name: str, **span_attrs: AttributeValue) -> Iterator[Span]:
    """Open an internal span for a deterministic pipeline stage such as ingest.

    Nesting these under one another keeps a whole triage in a single trace.
    """
    span_name = attrs.STAGE_SPAN_NAME_TEMPLATE.format(stage=name)
    with _started_span(span_name, SpanKind.INTERNAL) as span:
        span.set_attribute(attrs.BUILDSLEUTH_STAGE, name)
        _set_safe_attributes(span, span_attrs)
        yield span


@contextmanager
def _started_span(name: str, kind: SpanKind) -> Iterator[Span]:
    """Start a current span whose error handling is ours rather than the SDK's."""
    tracer = get_tracer()
    with tracer.start_as_current_span(
        name, kind=kind, record_exception=False, set_status_on_exception=False
    ) as span:
        try:
            yield span
        except BaseException as error:
            _mark_error(span, error)
            raise


def _mark_error(span: Span, error: BaseException) -> None:
    """Record an error using only its type.

    The SDK default records `exception.message` and a stack trace. Provider
    errors quote the failing request, which carries the Authorization header, so
    only the exception type is safe to attach.
    """
    error_type = type(error).__qualname__
    span.set_attribute(attrs.ERROR_TYPE, error_type)
    span.set_status(Status(StatusCode.ERROR, error_type))
    span.add_event(attrs.EXCEPTION_EVENT_NAME, {attrs.EXCEPTION_TYPE: error_type})


def _set_safe_attributes(span: Span, values: Mapping[str, AttributeValue] | None) -> None:
    if not values:
        return
    for key, value in values.items():
        if _is_sensitive(key):
            _LOG.debug("dropping attribute %s from span, key looks like a credential", key)
            continue
        span.set_attribute(key, value)


def _is_sensitive(key: str) -> bool:
    lowered = key.lower()
    return any(marker in lowered for marker in SENSITIVE_KEY_MARKERS)


def _traces_endpoint(base: str) -> str:
    """Build the OTLP HTTP traces URL, tolerating a base URL with or without the path."""
    trimmed = base.rstrip("/")
    if trimmed.endswith(OTLP_TRACES_PATH):
        return trimmed
    return f"{trimmed}{OTLP_TRACES_PATH}"

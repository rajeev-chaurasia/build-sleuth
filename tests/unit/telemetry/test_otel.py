"""Tracing behaviour, verified through an in-process span exporter."""

from collections.abc import Iterator, Mapping

import pytest
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import SpanKind, StatusCode
from opentelemetry.util.types import AttributeValue

from buildsleuth.llm.types import CompletionRequest, CompletionResult, Message, Role, Usage
from buildsleuth.telemetry import attrs, otel

TEST_SERVICE_NAME = "buildsleuth-test"
MODEL = "gpt-4o-mini"
RESPONSE_MODEL = "gpt-4o-mini-2024-07-18"
FINISH_REASON = "stop"
INPUT_TOKENS = 1200
OUTPUT_TOKENS = 340
FAKE_API_KEY = "sk-secret-123"
# Port 9 is the discard service and is closed on developer machines and CI.
UNREACHABLE_ENDPOINT = "http://127.0.0.1:9"
# Enough for one refused connection, short enough not to slow the suite down.
SHORT_EXPORT_TIMEOUT_SECONDS = 1
CASE_ID = "octocat/hello-world#4211"


@pytest.fixture(autouse=True)
def _reset_tracing(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Give every test a clean, collector-free tracing state."""
    monkeypatch.delenv(otel.OTLP_ENDPOINT_ENV, raising=False)
    otel.shutdown_tracing()
    yield
    otel.shutdown_tracing()


@pytest.fixture
def memory_exporter() -> InMemorySpanExporter:
    """Set tracing up with an in-process exporter and no network destination."""
    otel.setup_tracing(service_name=TEST_SERVICE_NAME)
    # The provider reference is private because nothing in production needs it,
    # but the tests have to attach their own exporter to it.
    provider = otel._provider
    assert provider is not None
    exporter = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    return exporter


def _attributes(span: ReadableSpan) -> Mapping[str, AttributeValue]:
    assert span.attributes is not None
    return span.attributes


def _request() -> CompletionRequest:
    return CompletionRequest(
        model=MODEL,
        messages=[Message(role=Role.USER, content="why did the build fail")],
        temperature=0.2,
        max_tokens=512,
    )


def _request_attrs(request: CompletionRequest) -> dict[str, AttributeValue]:
    return {
        attrs.GEN_AI_REQUEST_TEMPERATURE: request.temperature,
        attrs.GEN_AI_REQUEST_MAX_TOKENS: request.max_tokens,
    }


def test_llm_span_uses_conventional_name_and_request_attributes(
    memory_exporter: InMemorySpanExporter,
) -> None:
    request = _request()

    with otel.llm_span(
        operation=attrs.OPERATION_CHAT,
        model=request.model,
        provider=attrs.PROVIDER_OPENAI,
        request_attrs=_request_attrs(request),
    ):
        pass

    (span,) = memory_exporter.get_finished_spans()
    assert span.name == f"{attrs.OPERATION_CHAT} {MODEL}"
    assert span.kind is SpanKind.CLIENT

    recorded = _attributes(span)
    assert recorded[attrs.GEN_AI_OPERATION_NAME] == attrs.OPERATION_CHAT
    assert recorded[attrs.GEN_AI_PROVIDER_NAME] == attrs.PROVIDER_OPENAI
    assert recorded[attrs.GEN_AI_REQUEST_MODEL] == MODEL
    assert recorded[attrs.GEN_AI_REQUEST_TEMPERATURE] == pytest.approx(0.2)
    assert isinstance(recorded[attrs.GEN_AI_REQUEST_TEMPERATURE], float)
    assert recorded[attrs.GEN_AI_REQUEST_MAX_TOKENS] == 512
    assert isinstance(recorded[attrs.GEN_AI_REQUEST_MAX_TOKENS], int)


def test_record_response_writes_usage_from_completion_result(
    memory_exporter: InMemorySpanExporter,
) -> None:
    result = CompletionResult(
        content="flaky npm install",
        usage=Usage(input_tokens=INPUT_TOKENS, output_tokens=OUTPUT_TOKENS),
        model=RESPONSE_MODEL,
    )

    with otel.llm_span(
        operation=attrs.OPERATION_CHAT, model=MODEL, provider=attrs.PROVIDER_OPENAI
    ) as span:
        span.record_response(result.model, result.usage, FINISH_REASON)

    (recorded_span,) = memory_exporter.get_finished_spans()
    recorded = _attributes(recorded_span)
    assert recorded[attrs.GEN_AI_RESPONSE_MODEL] == RESPONSE_MODEL
    assert recorded[attrs.GEN_AI_USAGE_INPUT_TOKENS] == INPUT_TOKENS
    assert recorded[attrs.GEN_AI_USAGE_OUTPUT_TOKENS] == OUTPUT_TOKENS
    assert isinstance(recorded[attrs.GEN_AI_USAGE_INPUT_TOKENS], int)
    assert recorded[attrs.GEN_AI_RESPONSE_FINISH_REASONS] == (FINISH_REASON,)
    assert recorded_span.status.status_code is StatusCode.UNSET


def test_failure_marks_span_error_reraises_and_hides_the_api_key(
    memory_exporter: InMemorySpanExporter,
) -> None:
    with (
        pytest.raises(RuntimeError),
        otel.llm_span(
            operation=attrs.OPERATION_CHAT,
            model=MODEL,
            provider=attrs.PROVIDER_OPENAI,
            request_attrs={"api_key": FAKE_API_KEY},
        ),
    ):
        raise RuntimeError(f"401 unauthorized, Authorization: Bearer {FAKE_API_KEY}")

    (span,) = memory_exporter.get_finished_spans()
    assert span.status.status_code is StatusCode.ERROR
    assert _attributes(span)[attrs.ERROR_TYPE] == "RuntimeError"
    assert "api_key" not in _attributes(span)
    assert FAKE_API_KEY not in span.to_json()


def test_setup_tracing_is_idempotent(memory_exporter: InMemorySpanExporter) -> None:
    otel.setup_tracing(service_name=TEST_SERVICE_NAME)

    with otel.stage_span(attrs.STAGE_INGEST):
        pass

    # A second provider would have dropped the exporter attached to the first.
    assert len(memory_exporter.get_finished_spans()) == 1


def test_unreachable_collector_never_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(otel, "EXPORT_TIMEOUT_SECONDS", SHORT_EXPORT_TIMEOUT_SECONDS)
    otel.setup_tracing(endpoint=UNREACHABLE_ENDPOINT)

    with otel.stage_span(attrs.STAGE_VERIFY):
        pass

    otel.shutdown_tracing()


def test_stage_spans_nest_into_one_trace(memory_exporter: InMemorySpanExporter) -> None:
    with otel.stage_span(attrs.STAGE_TRIAGE, **{attrs.BUILDSLEUTH_CASE_ID: CASE_ID}):
        with otel.stage_span(attrs.STAGE_CONDENSE):
            pass
        with otel.llm_span(
            operation=attrs.OPERATION_CHAT, model=MODEL, provider=attrs.PROVIDER_OPENAI
        ):
            pass

    condense, chat, triage = memory_exporter.get_finished_spans()
    assert triage.name == f"stage {attrs.STAGE_TRIAGE}"
    assert _attributes(triage)[attrs.BUILDSLEUTH_CASE_ID] == CASE_ID
    assert _attributes(condense)[attrs.BUILDSLEUTH_STAGE] == attrs.STAGE_CONDENSE

    parent_context = triage.get_span_context()
    assert parent_context is not None
    for child in (condense, chat):
        child_context = child.get_span_context()
        assert child.parent is not None
        assert child_context is not None
        assert child.parent.span_id == parent_context.span_id
        assert child_context.trace_id == parent_context.trace_id


def test_disabled_tracing_records_nothing(memory_exporter: InMemorySpanExporter) -> None:
    otel.shutdown_tracing()
    otel.setup_tracing(enabled=False)

    with otel.stage_span(attrs.STAGE_INGEST) as stage:
        assert not stage.is_recording()
    with otel.llm_span(
        operation=attrs.OPERATION_CHAT, model=MODEL, provider=attrs.PROVIDER_OPENAI
    ) as llm:
        llm.record_response(RESPONSE_MODEL, Usage(), FINISH_REASON)
        assert not llm.span.is_recording()

    assert memory_exporter.get_finished_spans() == ()

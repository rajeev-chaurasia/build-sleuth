"""Canary for GenAI semantic-convention churn.

The conventions are Development status, so our hand-written constants are pinned
against the names shipped by opentelemetry-semantic-conventions. When an upgrade
renames an attribute, this test fails and `attrs.py` is the only file to edit.
"""

from opentelemetry.semconv._incubating.attributes import gen_ai_attributes as semconv

from buildsleuth.telemetry import attrs

BUILDSLEUTH_PREFIX = "buildsleuth."


def test_gen_ai_attribute_names_match_the_published_registry() -> None:
    assert attrs.GEN_AI_OPERATION_NAME == semconv.GEN_AI_OPERATION_NAME
    assert attrs.GEN_AI_PROVIDER_NAME == semconv.GEN_AI_PROVIDER_NAME
    assert attrs.GEN_AI_REQUEST_MODEL == semconv.GEN_AI_REQUEST_MODEL
    assert attrs.GEN_AI_REQUEST_TEMPERATURE == semconv.GEN_AI_REQUEST_TEMPERATURE
    assert attrs.GEN_AI_REQUEST_MAX_TOKENS == semconv.GEN_AI_REQUEST_MAX_TOKENS
    assert attrs.GEN_AI_RESPONSE_MODEL == semconv.GEN_AI_RESPONSE_MODEL
    assert attrs.GEN_AI_RESPONSE_FINISH_REASONS == semconv.GEN_AI_RESPONSE_FINISH_REASONS
    assert attrs.GEN_AI_USAGE_INPUT_TOKENS == semconv.GEN_AI_USAGE_INPUT_TOKENS
    assert attrs.GEN_AI_USAGE_OUTPUT_TOKENS == semconv.GEN_AI_USAGE_OUTPUT_TOKENS


def test_operation_values_are_well_known() -> None:
    assert semconv.GenAiOperationNameValues.CHAT.value == attrs.OPERATION_CHAT
    assert semconv.GenAiOperationNameValues.INVOKE_AGENT.value == attrs.OPERATION_INVOKE_AGENT
    assert semconv.GenAiOperationNameValues.EXECUTE_TOOL.value == attrs.OPERATION_EXECUTE_TOOL


def test_span_name_templates_follow_the_convention() -> None:
    assert attrs.LLM_SPAN_NAME_TEMPLATE.format(operation="chat", model="gpt-4o-mini") == (
        "chat gpt-4o-mini"
    )
    assert attrs.AGENT_SPAN_NAME_TEMPLATE.format(operation="invoke_agent", agent="sleuth") == (
        "invoke_agent sleuth"
    )


def test_local_attributes_stay_out_of_the_gen_ai_namespace() -> None:
    local = (
        attrs.BUILDSLEUTH_CASE_ID,
        attrs.BUILDSLEUTH_STAGE,
        attrs.BUILDSLEUTH_FAILURE_CLASS_PREDICTED,
        attrs.BUILDSLEUTH_PROMPT_HASH,
        attrs.BUILDSLEUTH_SCHEMA_REPAIR_ATTEMPTED,
        attrs.BUILDSLEUTH_CONDENSE_STRATEGY,
    )
    assert all(name.startswith(BUILDSLEUTH_PREFIX) for name in local)

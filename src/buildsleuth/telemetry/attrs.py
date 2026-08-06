"""Every span name and attribute key BuildSleuth emits.

The GenAI semantic conventions are still Development status and have already
churned once in a way that breaks dashboards: `gen_ai.system` was deprecated in
favour of `gen_ai.provider.name` around semconv 1.37, when the conventions moved
out of open-telemetry/semantic-conventions into
open-telemetry/semantic-conventions-genai. Keeping every name in this one module
makes the next rename a one-file diff instead of a grep across the pipeline.
"""

# OpenTelemetry resource semantic conventions (Stable).
SERVICE_NAME = "service.name"
SERVICE_VERSION = "service.version"

# OpenTelemetry error semantic conventions (Stable).
ERROR_TYPE = "error.type"
EXCEPTION_EVENT_NAME = "exception"
EXCEPTION_TYPE = "exception.type"

# GenAI semantic conventions, span attributes (Development).
# Source: open-telemetry/semantic-conventions-genai docs/gen-ai/gen-ai-spans.md
GEN_AI_OPERATION_NAME = "gen_ai.operation.name"
GEN_AI_PROVIDER_NAME = "gen_ai.provider.name"
GEN_AI_AGENT_NAME = "gen_ai.agent.name"
GEN_AI_CONVERSATION_ID = "gen_ai.conversation.id"
GEN_AI_TOOL_NAME = "gen_ai.tool.name"

# GenAI semantic conventions, request attributes (Development).
GEN_AI_REQUEST_MODEL = "gen_ai.request.model"
GEN_AI_REQUEST_TEMPERATURE = "gen_ai.request.temperature"
GEN_AI_REQUEST_MAX_TOKENS = "gen_ai.request.max_tokens"

# GenAI semantic conventions, response attributes (Development).
GEN_AI_RESPONSE_MODEL = "gen_ai.response.model"
GEN_AI_RESPONSE_FINISH_REASONS = "gen_ai.response.finish_reasons"

# GenAI semantic conventions, token usage attributes (Development).
GEN_AI_USAGE_INPUT_TOKENS = "gen_ai.usage.input_tokens"
GEN_AI_USAGE_OUTPUT_TOKENS = "gen_ai.usage.output_tokens"

# Well-known `gen_ai.operation.name` values (Development).
OPERATION_CHAT = "chat"
OPERATION_INVOKE_AGENT = "invoke_agent"
OPERATION_EXECUTE_TOOL = "execute_tool"
OPERATION_TEXT_COMPLETION = "text_completion"
OPERATION_EMBEDDINGS = "embeddings"

# Well-known `gen_ai.provider.name` values (Development). BuildSleuth talks to
# every provider over an OpenAI-compatible API, so the wire format is not a
# reliable discriminator and the provider must be passed in explicitly.
PROVIDER_OPENAI = "openai"
PROVIDER_ANTHROPIC = "anthropic"
PROVIDER_AZURE_OPENAI = "azure.ai.openai"
PROVIDER_GCP_GEMINI = "gcp.gemini"
PROVIDER_MISTRAL_AI = "mistral_ai"

# Span name conventions. Inference spans are `{gen_ai.operation.name}
# {gen_ai.request.model}`, for example "chat gpt-4o-mini". Agent spans fall back
# to the bare operation name when the agent name is not available.
LLM_SPAN_NAME_TEMPLATE = "{operation} {model}"
AGENT_SPAN_NAME_TEMPLATE = "{operation} {agent}"
STAGE_SPAN_NAME_TEMPLATE = "stage {stage}"

# BuildSleuth-specific attributes. Not part of any convention, so they carry the
# `buildsleuth.` prefix to stay out of the way of future GenAI attribute names.
BUILDSLEUTH_CASE_ID = "buildsleuth.case.id"
BUILDSLEUTH_STAGE = "buildsleuth.stage"
BUILDSLEUTH_FAILURE_CLASS_PREDICTED = "buildsleuth.failure_class.predicted"
BUILDSLEUTH_PROMPT_HASH = "buildsleuth.prompt.hash"
BUILDSLEUTH_SCHEMA_REPAIR_ATTEMPTED = "buildsleuth.schema.repair_attempted"
BUILDSLEUTH_CONDENSE_STRATEGY = "buildsleuth.condense.strategy"

# Deterministic pipeline stage names used with `stage_span`.
STAGE_INGEST = "ingest"
STAGE_CONDENSE = "condense"
STAGE_TRIAGE = "triage"
STAGE_VERIFY = "verify"

"""Adapt the LLM triage stage to the eval runner's Classifier protocol.

Keeping this in evals rather than the pipeline means the runner scores the
regex baseline and a model through exactly the same code path, which is what
makes the comparison between them fair.
"""

from buildsleuth.llm.types import ModelClient, Usage
from buildsleuth.models.triage import TriageVerdict
from buildsleuth.pipeline.triage import TriageContext, classify
from buildsleuth.prompts.loader import Prompt, load_prompt

TRIAGE_PROMPT_NAME = "triage"


class LlmClassifier:
    """Classifies a failure with one model call, tracking usage as it goes."""

    def __init__(self, client: ModelClient, model: str, prompt: Prompt | None = None) -> None:
        self._client = client
        self._model = model
        self._prompt = prompt if prompt is not None else load_prompt(TRIAGE_PROMPT_NAME)
        self.name = model
        self.usage = Usage()
        self.repairs = 0
        self.latency_ms_total = 0.0

    @property
    def prompt_hash(self) -> str:
        return self._prompt.content_hash

    def classify(self, log_text: str, diff_text: str | None = None) -> TriageVerdict:
        result = classify(
            client=self._client,
            model=self._model,
            context=TriageContext.from_log(log_text, diff_text=diff_text),
            prompt=self._prompt,
        )
        self.usage = self.usage + result.usage
        self.latency_ms_total += result.latency_ms
        self.repairs += int(result.repaired)
        return result.value

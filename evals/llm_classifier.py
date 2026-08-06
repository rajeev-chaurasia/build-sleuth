"""Adapt the LLM triage stage to the eval runner's Classifier protocol.

Keeping this in evals rather than the pipeline means the runner scores the
regex baseline and a model through exactly the same code path, which is what
makes the comparison between them fair.
"""

from buildsleuth.llm.types import ModelClient, Usage
from buildsleuth.models.triage import TriageVerdict
from buildsleuth.pipeline.localize import LOCALIZE_PROMPT_NAME, localize, ranked_paths
from buildsleuth.pipeline.triage import TriageContext, classify
from buildsleuth.prompts.loader import Prompt, combined_hash, load_prompt
from buildsleuth.tools.evidence import Evidence

TRIAGE_PROMPT_NAME = "triage"


class LlmClassifier:
    """Classifies a failure with one model call, tracking usage as it goes."""

    def __init__(
        self,
        client: ModelClient,
        model: str,
        prompt: Prompt | None = None,
        localize_prompt: Prompt | None = None,
    ) -> None:
        self._client = client
        self._model = model
        self._prompt = prompt if prompt is not None else load_prompt(TRIAGE_PROMPT_NAME)
        self._localize_prompt = (
            localize_prompt if localize_prompt is not None else load_prompt(LOCALIZE_PROMPT_NAME)
        )
        self.name = model
        self.usage = Usage()
        self.repairs = 0
        self.latency_ms_total = 0.0

    @property
    def prompt_hash(self) -> str:
        """Covers both prompts, so editing either produces a new scorecard identity."""
        return combined_hash([self._prompt, self._localize_prompt])

    def classify(self, log_text: str, diff_text: str | None = None) -> TriageVerdict:
        result = classify(
            client=self._client,
            model=self._model,
            context=TriageContext.from_log(log_text, diff_text=diff_text),
            prompt=self._prompt,
        )
        self._record(result.usage, result.latency_ms, result.repaired)
        return result.value

    def localize(
        self, verdict: TriageVerdict, log_text: str, diff_text: str | None = None
    ) -> list[str]:
        """Rank likely culprit files. Empty when the class implies there are none."""
        result = localize(
            client=self._client,
            model=self._model,
            verdict=verdict,
            evidence=Evidence(log_text=log_text, diff_text=diff_text),
            prompt=self._localize_prompt,
        )
        if result is None:
            return []
        self._record(result.usage, result.latency_ms, result.repaired)
        return ranked_paths(result.value)

    def _record(self, usage: Usage, latency_ms: float, repaired: bool) -> None:
        self.usage = self.usage + usage
        self.latency_ms_total += latency_ms
        self.repairs += int(repaired)

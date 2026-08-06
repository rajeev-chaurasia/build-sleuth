"""Deterministic log condensation: cleanup, error pattern scan, budget routing.

No LLM calls live here; everything is unit-testable pure logic.
"""

from buildsleuth.condense.clean import clean_log
from buildsleuth.condense.router import condense

__all__ = ["clean_log", "condense"]

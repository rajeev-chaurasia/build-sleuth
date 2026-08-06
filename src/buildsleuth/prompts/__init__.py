"""Versioned prompt files.

Prompts live on disk rather than inline in Python so a prompt change is a
reviewable diff, and so its content hash can be recorded in every scorecard
and trace. A metric is only meaningful next to the prompt that produced it.
"""

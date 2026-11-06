"""Streaming guardrail that redacts Swiss AHV numbers and email addresses."""

from .guard import StreamingGuard

__all__ = ["StreamingGuard"]

"""PII scrubbing before anything enters the permanent chain (E10).

Presidio is the production choice (decision backlog); this is a dependency-free
stand-in with the same call shape, so swapping it in is one function body.
"""
from __future__ import annotations
import re
from typing import Any

_PATTERNS = [
    (re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b"), "<EMAIL>"),
    (re.compile(r"\b(?:\d[ -]*?){13,19}\b"), "<PAN>"),
    (re.compile(r"\b\d{3}\b(?=\s*(?:cvv|cvc))", re.I), "<CVV>"),
    (re.compile(r"\+\d[\d\s().-]{7,}\d"), "<PHONE>"),
]


def scrub_text(text: str) -> str:
    for pattern, placeholder in _PATTERNS:
        text = pattern.sub(placeholder, text)
    return text


def scrub_payload(value: Any) -> Any:
    if isinstance(value, str):
        return scrub_text(value)
    if isinstance(value, dict):
        return {k: scrub_payload(v) for k, v in value.items()}
    if isinstance(value, list):
        return [scrub_payload(v) for v in value]
    return value

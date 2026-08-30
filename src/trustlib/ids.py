"""Prefixed ULIDs.

Prefixes make an id self-describing in a log line or an audit event, and make
a mismatched id (an intent jti where a mandate jti was expected) obvious at a
glance instead of at the database constraint.
"""

from __future__ import annotations

from ulid import ULID

MANDATE = "mdt"
INTENT = "int"
ESCALATION = "esc"
OFFER = "ofr"
PURCHASE = "pur"
YUNO_TOKEN = "ynt"
YUNO_PAYMENT = "ynp"
YUNO_DISPUTE = "ynd"
EVENT = "evt"
ORDER = "ord"


def new_id(prefix: str) -> str:
    """Return a fresh sortable id, e.g. `mdt_01J8Z...`."""
    return f"{prefix}_{ULID()}"


def has_prefix(value: str, prefix: str) -> bool:
    return value.startswith(f"{prefix}_")

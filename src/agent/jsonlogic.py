"""A deliberately small JsonLogic evaluator (S5).

Pure, total, no I/O, no custom functions, no user-supplied callables.  Variables
resolve only against the context the caller passes -- `offer.*` and `now`.  A
rule engine that can call out is a rule engine that can be surprised, and the
whole claim of decision #1 is that the same input gives the same answer.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any


class RuleError(ValueError):
    pass


def _num(value: Any) -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError) as exc:
        raise RuleError(f"not comparable as a number: {value!r}") from exc


def _var(path: str, context: dict, default: Any = None) -> Any:
    current: Any = context
    if path == "":
        return context
    for part in str(path).split("."):
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return default
    return current


def _cmp(op: str, args: list) -> bool:
    left, right = _num(args[0]), _num(args[1])
    if op == "<":
        return left < right
    if op == "<=":
        return left <= right
    if op == ">":
        return left > right
    return left >= right


def evaluate(rule: Any, context: dict) -> Any:
    if not isinstance(rule, dict):
        return rule
    if len(rule) != 1:
        raise RuleError(f"a rule node takes exactly one operator, got {list(rule)}")
    op, raw = next(iter(rule.items()))
    args = raw if isinstance(raw, list) else [raw]

    if op == "var":
        resolved = [evaluate(a, context) for a in args]
        return _var(resolved[0], context, resolved[1] if len(resolved) > 1 else None)
    if op == "missing":
        return [a for a in args if _var(a, context) is None]
    if op in ("<", "<=", ">", ">="):
        values = [evaluate(a, context) for a in args]
        if len(values) == 3:  # between form: [lo, x, hi]
            return _cmp(op, values[:2]) and _cmp(op, values[1:])
        if len(values) != 2:
            raise RuleError(f"{op} takes 2 or 3 arguments")
        return _cmp(op, values)
    if op in ("==", "==="):
        a, b = (evaluate(x, context) for x in args[:2])
        return str(a) == str(b) if op == "==" else a == b
    if op in ("!=", "!=="):
        a, b = (evaluate(x, context) for x in args[:2])
        return str(a) != str(b) if op == "!=" else a != b
    if op == "and":
        result: Any = True
        for a in args:
            result = evaluate(a, context)
            if not result:
                return result
        return result
    if op == "or":
        result = False
        for a in args:
            result = evaluate(a, context)
            if result:
                return result
        return result
    if op == "!":
        return not evaluate(args[0], context)
    if op == "in":
        needle, haystack = (evaluate(x, context) for x in args[:2])
        return needle in (haystack or [])
    if op == "if":
        values = list(args)
        while len(values) >= 2:
            if evaluate(values[0], context):
                return evaluate(values[1], context)
            values = values[2:]
        return evaluate(values[0], context) if values else None
    raise RuleError(f"operator not allowed in a mandate: {op!r}")


def describe(rule: Any) -> str:
    """Plain-English rendering, for the escalation diff and the auditor view."""
    if not isinstance(rule, dict):
        return str(rule)
    op, raw = next(iter(rule.items()))
    args = raw if isinstance(raw, list) else [raw]
    if op == "var":
        return str(args[0])
    words = {
        "<": "below",
        "<=": "at most",
        ">": "above",
        ">=": "at least",
        "==": "is",
        "!=": "is not",
        "in": "one of",
    }
    if op in words and len(args) == 2:
        return f"{describe(args[0])} {words[op]} {describe(args[1])}"
    if op in ("and", "or"):
        return f" {op} ".join(describe(a) for a in args)
    return f"{op}({', '.join(describe(a) for a in args)})"

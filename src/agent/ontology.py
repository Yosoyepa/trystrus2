"""Domain knowledge: what the agent knows, as opposed to what it may spend.

The database is the source of truth (K2) -- a YAML file is import/export only,
because a file cannot answer "which brain produced this proposal?" three days
later.  Everything in here is UNTRUSTED text as far as the model is concerned:
an ontology is an injection surface exactly like a merchant description (K5),
so it is delimited the same way.
"""
from __future__ import annotations
import json
from pathlib import Path
from typing import Any

import yaml

from .config import ONTOLOGY_DIR


def load_file(path: str | Path) -> dict[str, Any]:
    p = Path(path)
    if not p.is_absolute() and not p.exists():
        p = ONTOLOGY_DIR / p
    text = p.read_text(encoding="utf-8")
    data = yaml.safe_load(text) if p.suffix in (".yaml", ".yml") else json.loads(text)
    if not isinstance(data, dict):
        raise ValueError(f"{p} must contain a mapping at the top level")
    return data


def render(ontology: dict[str, Any]) -> str:
    """Flatten to the delimited block the model sees. Never interpolated raw."""
    lines: list[str] = []

    def walk(node: Any, indent: int = 0) -> None:
        pad = "  " * indent
        if isinstance(node, dict):
            for key, value in node.items():
                if isinstance(value, (dict, list)):
                    lines.append(f"{pad}{key}:")
                    walk(value, indent + 1)
                else:
                    lines.append(f"{pad}{key}: {value}")
        elif isinstance(node, list):
            for item in node:
                if isinstance(item, (dict, list)):
                    walk(item, indent)
                else:
                    lines.append(f"{pad}- {item}")
        else:
            lines.append(f"{pad}{node}")

    walk(ontology)
    return "\n".join(lines)

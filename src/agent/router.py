"""Agent dispatcher: which active agent (and its mandate/MCP surface) serves
this request?

Routing is a propose-side concern (0016): the model may read the category and
the scope match is deterministic table lookups — and the gate enforces the
mandate regardless of who routed, so a wrong pick can never buy outside its
scope. Ties break toward the mandate the buyer signed most recently.
"""

from __future__ import annotations

import json
from typing import Any

from . import llm

# Category → buyer words. Spanish-first: the demo audience talks to the agent
# in Spanish. Kept as data so adding a merchant never means editing logic.
CATEGORY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "flights": (
        "vuelo",
        "vuelos",
        "volar",
        "flight",
        "flights",
        "aerolinea",
        "aerolínea",
        "boleto",
        "tiquete",
        "avión",
        "avion",
    ),
    "hotels": ("hotel", "hoteles", "noche", "noches", "alojamiento", "hospedaje"),
    "food": (
        "rappi",
        "pizza",
        "hamburguesa",
        "comida",
        "domicilio",
        "restaurante",
        "almuerzo",
        "cena",
        "desayuno",
        "food",
        "antojo",
    ),
    "groceries": (
        "mercado",
        "supermercado",
        "papas",
        "pringles",
        "producto",
        "productos",
        "groceries",
        "market",
        "compras del mercado",
        "fruta",
        "cereal",
        "botella",
        "agua",
        "gaseosa",
        "jugo",
        "leche",
        "pan",
    ),
    "retail": (
        "tienda",
        "comprar",
        "compra",
        "retail",
        "snack",
        "galleta",
        "bebida",
        "cigarro",
        "papeleria",
        "papelería",
    ),
}

_MERCHANT_WORD = "-mcp"


def _claims(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if raw:
        return json.loads(raw)
    return {}


def _tokens(text: str) -> set[str]:
    return set(text.lower().split())


def inferred_category(text: str) -> str | None:
    """Category from buyer words. Deterministic; beats the model's catalog guess."""
    tokens = _tokens(text)
    best: str | None = None
    best_hits = 0
    for category, words in CATEGORY_KEYWORDS.items():
        hits = len(set(words) & tokens)
        if hits > best_hits:
            best, best_hits = category, hits
    return best


def score_candidates(
    candidates: list[dict[str, Any]], criteria: dict[str, Any], text: str
) -> list[dict[str, Any]]:
    """Deterministic scoring. Pure: trivially testable, trivially explainable."""
    tokens = _tokens(text)
    category = (criteria or {}).get("category")
    scored: list[dict[str, Any]] = []
    for candidate in candidates:
        scope = candidate["scope"]
        scope_categories = list(scope.get("categories", []))
        scope_merchants = [
            str(merchant).removesuffix(_MERCHANT_WORD) for merchant in scope.get("merchants", [])
        ]
        score = 0
        reasons: list[str] = []
        if category and category in scope_categories:
            score += 3
            reasons.append(f"categoría '{category}' en el scope del mandato")
        for scope_category in scope_categories:
            hits = sorted(set(CATEGORY_KEYWORDS.get(scope_category, ())) & tokens)
            if hits:
                score += 2
                reasons.append(f"palabras {hits} del rubro '{scope_category}' en el pedido")
        merchant_hits = [
            merchant for merchant in scope_merchants if merchant and merchant in tokens
        ]
        if merchant_hits:
            score += 1
            reasons.append(f"merchant '{merchant_hits[0]}' nombrado en el pedido")
        scored.append({**candidate, "score": score, "reasons": reasons})
    # Score first; ties break toward the NEWEST mandate — the freshest
    # agreement is the one the person signed last.
    scored.sort(key=lambda item: (-item["score"], str(item.get("created_at", ""))))
    return scored


def select_agent(conn, text: str) -> dict[str, Any] | None:
    """Pick the best active (agent, mandate) pair for `text`, or None."""
    rows = conn.execute(
        "SELECT a.id AS agent_id, a.name AS agent_name, m.jti AS mandate_jti,"
        " m.claims AS claims, m.created_at AS created_at"
        " FROM agents a JOIN mandates m ON m.agent_id = a.id"
        " WHERE a.status = 'active' AND m.status = 'active'"
    ).fetchall()
    candidates: list[dict[str, Any]] = []
    for row in rows:
        claims = _claims(row["claims"])
        candidates.append(
            {
                "agent_id": row["agent_id"],
                "agent_name": row["agent_name"],
                "mandate_jti": row["mandate_jti"],
                "scope": (claims.get("scope") or {}),
                "currency": claims.get("currency"),
                "created_at": row["created_at"],
            }
        )
    if not candidates:
        return None
    keyword_category = inferred_category(text)
    if keyword_category:
        # Buyer words beat the model and avoid a variable network wait for
        # obvious requests such as "botella de agua" or "vuelo a Córdoba".
        criteria = {"category": keyword_category}
    else:
        criteria = dict(llm.parse_request(text))
    category = criteria.get("category")
    ranked = score_candidates(candidates, criteria, text)
    best = ranked[0]
    if best["score"] <= 0:
        return None  # refuse to guess rather than send groceries to a flights mandate
    return {
        "agent_id": best["agent_id"],
        "agent_name": best["agent_name"],
        "mandate_jti": best["mandate_jti"],
        "currency": best["currency"],
        "category": category,
        "score": best["score"],
        "reason": "; ".join(best["reasons"]) or "único agente activo disponible",
    }

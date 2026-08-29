"""Configuration. Reads .env from the repo root; no dependencies."""
from __future__ import annotations
import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]      # src/agent -> src -> repo
PKG_ROOT = Path(__file__).resolve().parent
VAR_DIR = Path(os.environ.get("TRYTRUST_VAR", REPO_ROOT / "var"))
ONTOLOGY_DIR = PKG_ROOT / "ontologies"


def _load_dotenv() -> None:
    for candidate in (REPO_ROOT / ".env", PKG_ROOT / ".env"):
        if not candidate.exists():
            continue
        for raw in candidate.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip("'\""))


_load_dotenv()
VAR_DIR.mkdir(parents=True, exist_ok=True)

PRODUCT_NAME = "TryTrust"
PRODUCT_DOMAIN = "trytrust.lat"
ISSUER = f"https://api.{PRODUCT_DOMAIN}"

DB_PATH = Path(os.environ.get("TRYTRUST_DB", VAR_DIR / "trytrust.db"))

LLM_API_KEY = os.environ.get("LLM_API_KEY", "")
LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "https://api.openai.com/v1")
# Cheapest/dumbest tier on purpose: the model only proposes, it never decides.
LLM_MODEL = os.environ.get("LLM_MODEL", "gpt-4.1-nano")
LLM_MAX_TOKENS = int(os.environ.get("LLM_MAX_TOKENS", "300"))
LLM_TIMEOUT_S = float(os.environ.get("LLM_TIMEOUT_S", "20"))

ESCALATION_TIMEOUT_S = int(os.environ.get("TRYTRUST_ESCALATION_TIMEOUT_S", "120"))
INTENT_TTL_S = 120  # C6: exp - iat <= 120s

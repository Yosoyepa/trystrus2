# CONTRATOS DE DATOS E INTERFACES — Aval v1.0 (M0)

> **Fuente única de verdad para los 4 workstreams.** Congelado en M0 (Día 0). Cambios: PR aditivo `v1.x` (actualizando mock + trustlib en el mismo commit); cambios rompibles solo antes de M2. Compañero de [api.yaml](api.yaml) (transporte REST) — este archivo define lo que viaja DENTRO.

---

## 1. Contrato cripto — Mandato SD-JWT (`MandateClaims`)

Emisor: servicio `api` (Dev A). Clave: Ed25519 PEM en Secret Manager, publicada en `/.well-known/jwks.json` con `kid = vN`. Formato: **SD-JWT (RFC 9901) + Key Binding** (el KB lo emite el AGENTE al presentar, con `nonce` del merchant).

```json
{
  "iss": "https://api.trustchannel.example",
  "iat": 1770000000, "nbf": 1770000000, "exp": 1772608000,
  "jti": "mdt_01J8Z...",
  "type": "purchase_mandate_v1",
  "sub": "usr_marta",
  "agent": "agt_flights",
  "cnf": { "jwk": { "kty": "OKP", "crv": "Ed25519", "x": "…pubkey del agente base64url…" } },
  "payment_method_ref": "ppt_9XZ...",
  "currency": "USD",
  "scope": { "categories": ["flights"], "merchants": ["vuelaya"] },
  "conditions": { "<": [ { "var": "offer.price" }, 150 ] },
  "limits": {
    "max_per_txn": 150,
    "total_budget": 400,
    "max_txn": { "count": 3, "period": "month" }
  },
  "validity": { "not_before": "2026-09-01T00:00:00Z", "expires_at": "2026-09-30T23:59:59Z" },
  "_sd_alg": "sha-256",
  "_sd": ["…digests de claims selectivos: shipping_address, email…"]
}
```

Reglas:
- `conditions` = **JsonLogic puro** evaluado sobre el contexto `{"offer": Offer, "now": iso}`. Variables permitidas: `offer.*`, `now`. Sin funciones custom.
- `payment_method_ref` es OPACO: quien lo recibe no interpreta su contenido, solo lo pasa al `PaymentRail`.
- Claims selectivos (SD): `shipping_address`, `email` — el agente los revela solo si el merchant los exige en el checkout.
- Rotación: `kid` incremental; JWKS publica actual + anterior (gracia 24 h).

## 2. Contrato cripto — Payment Intent canónico (`PurchaseIntent`)

Firmado por el **agente** (Dev C) con la clave `cnf.jwk` del mandato. **Serialización: JSON Canonicalization Scheme (RFC 8785 / JCS)** — sin ella, la firma no es verificable entre servicios. JWS **detached** EdDSA (payload viaja aparte del objeto firma, patrón estándar JOSE).

```json
{
  "typ": "purchase_intent_v1",
  "mandate_jti": "mdt_01J8Z...",
  "agent": "agt_flights",
  "merchant_id": "vuelaya",
  "offer_id": "ofr_COR_130",
  "amount": "130.00",
  "currency": "USD",
  "nonce": "8f3a…(≥128 bits aleatorios)",
  "jti": "int_01J8Z…",
  "iat": 1770000600,
  "exp": 1770000660
}
```

Reglas:
- `amount` = **string decimal fijo a 2 decimales** (evita float drift entre servicios).
- `amount` **debe ser igual al precio de la offer referenciada** — el verify endpoint lo comprueba contra el catálogo. El agente no elige el monto: elimina toda manipulación de precio en la fuente (subdivisión C2↔C3, ver PLAN-PARALELO §3.1).
- `exp − iat ≤ 120 s`. `jti` + `nonce` únicos globalmente (constraint UNIQUE en BD del merchant/verify).
- Verificación en orden (Dev B, usada también por Dev D en checkout): firma del intent contra `cnf.jwk` → KB-JWT del presentación SD-JWT (nonce/aud del verifier) → frescura temporal → unicidad.

## 3. Interfaces Python (viven en `packages/trustlib`)

```python
# ── trustlib/interfaces.py — implementado por el dueño indicado, consumido por el resto ──

class Decision(BaseModel):
    decision: Literal["APPROVED", "REJECTED", "ESCALATED"]
    reason_code: ReasonCode | None = None
    reservation_id: str | None = None      # solo APPROVED; TTL 120 s
    diff: dict | None = None               # solo ESCALATED: {"limit":"max_per_txn","value":150,"attempted":300}

# [Dev B] PolicyGate — determinista, sin I/O, testeado con T1/T10
class PolicyGate(Protocol):
    def evaluate(self, mandate: MandateClaims, intent: PurchaseIntent,
                 spend: SpendView, now: datetime) -> Decision: ...

class SpendView(BaseModel):                 # proyección del ledger para evaluar limits
    spent_total: Decimal
    reserved_total: Decimal
    txn_count_period: int
    mandate_status: MandateStatus           # active | suspended | revoked | …

# [Dev D] PaymentRail — adaptador PayPal; A usa delete_token, merchant usa el resto
class PaymentRail(Protocol):
    def create_setup_token(self, mandate_id: str) -> SetupToken:        # → approve_url
    def exchange_payment_token(self, setup_token_id: str) -> str:       # → payment_token_id
    def delete_payment_token(self, token_id: str) -> None:              # kill-switch del rail
    def capture(self, *, token_id: str, amount: Decimal, currency: str,
                idempotency_key: str, intent_ref: str) -> Receipt:      # order con vault_id + capture
    def open_dispute(self, capture_id: str, reason: str = "UNAUTHORISED") -> DisputeRef
    def verify_webhook(self, headers: dict, body: bytes) -> WebhookEvent | None

# [Dev A] MandateRegistry — emisión/verificación SD-JWT
class MandateRegistry(Protocol):
    def issue(self, claims: MandateClaimsInput) -> IssuedMandate:       # sd_jwt + jti
    def verify(self, sd_jwt: str, *, nonce: str, aud: str) -> MandateClaims:  # + KB check
    def jwks(self) -> JWKSet

# [Dev B] Ledger — append-only hash-chained
class Ledger(Protocol):
    def append(self, type: EventType, mandate_id: str, payload: dict) -> AuditEvent
    def verify_chain(self) -> ChainResult          # recomputa hashes + valida roots KMS
    def sign_root(self) -> RootCheckpoint          # KMS asymmetricSign + witness GCS
```

**`trustlib` además provee (compartido, propiedad común):** modelos Pydantic de TODO lo anterior, `ReasonCode` (enum), `canonical_json()` (JCS), helpers detached-JWS Ed25519, y **`fake.mandate() / fake.intent() / fake.offer() / fake.spend()`** con semillas — los generadores que hacen posible que cada dev testeé sin los demás.

## 4. Catálogo de eventos (outbox → SSE / bot / webhook merchant)

Envelope único: `{event_id, type, aggregate_id, payload, created_at}` (+ `seq` interno).

| type | Emite | Payload mínimo | Consumen |
|---|---|---|---|
| `mandate.created` / `mandate.activated` | A | jti, limits | B (ledger), D (webhook) |
| `mandate.revoked` | A | jti, by, at | B (ledger), D (merchant anula checkout), bot |
| `mandate.suspended` / `mandate.exhausted` / `mandate.expired` | A | jti | B, D |
| `payment_instrument.linked` | A | mandate_jti, token_ref (opaco) | B |
| `offer.seen` | C | offer_id, price, mandate_jti, conditions_result | B |
| `purchase.requested` | B | purchase_id, intent_jti | B (ledger) |
| `purchase.verified` | B | purchase_id, reservation_id | D |
| `purchase.escalated` | B | purchase_id, escalation_id, diff | A (bot), SSE |
| `purchase.captured` | B | purchase_id, receipt | A (registro Marta), SSE |
| `purchase.rejected` | B | purchase_id, reason_code | C (agente replanifica), SSE |
| `escalation.resolved` | A | escalation_id, decision, receipt_sig | B (resume saga), SSE |
| `escalation.expired` | A | escalation_id (timeout fail-closed) | B (compensa), C |
| `dispute.opened` / `dispute.resolved` | D | capture_id, dispute_id, outcome | B (evidence bundle), SSE |
| `root.checkpoint` | B | root_hash, root_sig, seq_range | GCS witness |

## 5. Semántica de "escalation resume" (contrato A↔B↔C — el más delicado)

1. Gate devuelve `ESCALATED` → B crea `purchase` en `awaiting_escalation` + evento `purchase.escalated` → NO hay reserva de presupuesto todavía.
2. A (bot/UI) resuelve dentro de `timeout_at` (120 s):
   - `APPROVE` → A emite `escalation.resolved` con `receipt_sig` → **B re-ejecuta el gate** (estado puede haber cambiado — nunca confía en el approval como bypass) → si APPROVED ahora sí reserva y cobra. El approval autoriza a reintentar, **no** a saltarse el gate.
   - `REJECT` o timeout → `escalation.expired` → B compensa la compra (`rejected`/`compensated`) → C replanifica.
3. `sticky: true` → A emite **mini-mandato derivado** (nuevo SD-JET con `parent_jti`, límites acotados) → el agente debe usar el nuevo mandato para reintentos.
4. Agente (C): el nodo `await_human` antes del nodo pay es **idempotente** (T7) — el run persiste en `agent_runs` (checkpointing del grafo propio, ADR-006 del plan maestro) y el nodo re-ejecuta al resumir; ningún side effect monetario ocurre antes del resume.

## 6. Contrato de base de datos (DDL semilla — una migración por dueño)

```sql
-- [A] mandates + escalations
CREATE TABLE mandates (
  id TEXT PRIMARY KEY, jti TEXT UNIQUE NOT NULL,
  user_id TEXT NOT NULL, agent_id TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'draft',        -- draft|active|suspended|revoked|expired|exhausted
  claims JSONB NOT NULL,                       -- MandateClaims (canónico firmado)
  sd_jwt TEXT,
  reserved_amount NUMERIC(12,2) NOT NULL DEFAULT 0,  -- escrito SOLO por verify [B]
  spent_total NUMERIC(12,2) NOT NULL DEFAULT 0,
  txn_count_period INT NOT NULL DEFAULT 0,
  parent_jti TEXT REFERENCES mandates(jti),    -- mini-mandatos sticky
  version INT NOT NULL DEFAULT 1,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE escalations (
  id TEXT PRIMARY KEY, purchase_id TEXT NOT NULL, mandate_id TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',      -- pending|resolved|expired
  diff JSONB, timeout_at TIMESTAMPTZ NOT NULL,
  decision TEXT, approver TEXT, channel TEXT, receipt_sig TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- [B] decisión + evidencia
CREATE TABLE purchase_intents (
  jti TEXT PRIMARY KEY, mandate_jti TEXT NOT NULL, agent_id TEXT NOT NULL,
  intent_canonical JSONB NOT NULL, status TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE purchases (
  id TEXT PRIMARY KEY, mandate_id TEXT NOT NULL, intent_jti TEXT NOT NULL,
  status TEXT NOT NULL,                        -- pending_verification|awaiting_escalation|charging|captured|rejected|compensated
  reason_code TEXT, reservation_id TEXT, receipt JSONB,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE idempotency_keys (
  key TEXT PRIMARY KEY, scope TEXT NOT NULL, response JSONB,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE audit_events (
  seq BIGSERIAL PRIMARY KEY, mandate_id TEXT NOT NULL, type TEXT NOT NULL,
  payload JSONB NOT NULL, prev_hash CHAR(64) NOT NULL, hash CHAR(64) NOT NULL,
  root_sig TEXT, created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE outbox (
  seq BIGSERIAL PRIMARY KEY, event_id TEXT UNIQUE NOT NULL, type TEXT NOT NULL,
  aggregate_id TEXT NOT NULL, payload JSONB NOT NULL,
  relayed_at TIMESTAMPTZ, created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- [C] runs del agente — checkpointing del grafo propio (ADR-006): cada transición de nodo persiste
CREATE TABLE agent_runs (
  run_id UUID PRIMARY KEY,
  mandate_jti TEXT NOT NULL,
  node TEXT NOT NULL,                     -- perceive|search|propose|gate|await_human|pay|receipt|done|denied
  state JSONB NOT NULL,                   -- estado canónico acumulado del run
  status TEXT NOT NULL DEFAULT 'running', -- running|awaiting_human|done|denied|failed
  escalation_id TEXT,                     -- set cuando status=awaiting_human (resume vía escalation.resolved)
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- [C] comercio & rail (sub-misión C2)
CREATE TABLE payment_instruments (
  token_ref TEXT PRIMARY KEY, mandate_jti TEXT NOT NULL,
  rail TEXT NOT NULL DEFAULT 'paypal', status TEXT NOT NULL DEFAULT 'active',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE offers (
  id TEXT PRIMARY KEY, merchant_id TEXT NOT NULL, category TEXT NOT NULL,
  title TEXT NOT NULL, amount NUMERIC(12,2) NOT NULL, currency TEXT NOT NULL,
  description TEXT, active BOOLEAN NOT NULL DEFAULT true
);
```

Convención de escritura cruzada: `verify` [B] es el ÚNICO escritor de `mandates.reserved_amount/spent_total/txn_count_period` [tabla de A]; lo hace vía el UPDATE condicional atómico (`WHERE status='active' AND …`). A nunca escribe esas columnas.

## 7. Semántica de `ReasonCode` (para mensajes de UI idénticos entre vistas)

| Código | Significado exacto | ¿Quién lo produce? |
|---|---|---|
| `AMOUNT_EXCEEDS_PER_TXN` / `BUDGET_EXCEEDED` / `LIMIT_EXHAUSTED` | Límites numéricos/conteo | gate |
| `CATEGORY_FORBIDDEN` / `MERCHANT_NOT_ALLOWED` | Scope del mandato | gate |
| `MANDATE_EXPIRED` / `MANDATE_NOT_YET_VALID` / `MANDATE_REVOKED` / `MANDATE_SUSPENDED` / `MANDATE_EXHAUSTED` | Estado del mandato | verify |
| `CONDITION_FAILED` | JsonLogic del mandato evaluó falso (ej. precio > umbral) | gate |
| `INVALID_SIGNATURE` / `INVALID_PROOF_OF_POSSESSION` | Cripto: intent o KB inválidos → posible impersonación | verify |
| `DUPLICATE_JTI` / `NONCE_REUSED` | Replay | verify |
| `ESCALATION_TIMEOUT_DENIED` | Timeout 120 s fail-closed | A |
| `RAIL_ERROR` / `RAIL_TOKEN_DELETED` | PayPal falla / token borrado (revocación a nivel rail) | PaymentRail |

## 8. Estrategia de mocks (la que habilita el paralelismo)

| Mock (en `contracts/mocks/`) | Simula | Comportamiento obligatorio |
|---|---|---|
| `mock-api` | `POST /mandates/:id/verify`, `POST /purchases`, `GET /.well-known/jwks.json` | **Decide según el fixture**: dado `fake.mandate(limits=…)` + `fake.intent(amount=…)`, aplica la MISMA tabla de decisiones que el gate real (aprobado/rejected/escalated + reason_code). Prohibido "aprueba todo". |
| `mock-merchant` | `GET /catalog/offers`, `POST /checkout/charge` | Sirve `fake.offer()`s (incluyendo `description` con inyecciones para C); charge rechaza si `VerifyResponse.decision != APPROVED` (respeta el 402 del contrato). |
| `mock-jwks` | JWKS del issuer | Sirve la clave pública de prueba de trustlib (la privada está en fixtures para firmar mandatos de prueba). |

Los mocks corren en `docker-compose` junto a Postgres desde el Día 0 — **cada workstream desarrolla 100% contra mocks hasta su milestone de integración (M1–M3)**, y los contract-tests (mismos tests, parametrizados mock vs real) garantizan que el real se comporta como el mock.

## 9. Checklist de freeze M0 (firmado por los 4)

- [ ] Cada dev respondió: *"¿con esto y solo esto puedo construir mi módulo?"* — si la respuesta es no, falta contrato.
- [ ] `api.yaml` validado (redocly/spectral) y revisado cruzado.
- [ ] `trustlib` v0.1: modelos + `canonical_json` + helpers firma/verificación + `fake.*` + mocks corriendo.
- [ ] Fixtures canónicos: mandato VuelaYa (`<$150`, 3/mes, USD 400), intent $130 (APPROVED), intent $300 (ESCALATED/REJECTED), intent categoría wrong (REJECTED), intent firmado con clave equivocada (INVALID_PROOF_OF_POSSESSION).

## 10. Contrato MCP — tools del merchant consumidas por el agente (C2 implementa · C3 consume)

El agente descubre y compra vía MCP (ADR-013 del plan maestro). **Tres tools, sin más:**

| Tool | Args | Return | Efectos |
|---|---|---|---|
| `search_offers` | `{origin?: str, destination?: str, date?: date}` | `Offer[]` | ninguno (read-only) |
| `get_offer` | `{offer_id: str}` | `Offer` | ninguno (read-only) |
| `request_purchase` | `{offer_id: str, mandate_jti: str}` | `{status: "submitted", purchase_id: str}` | Internamente llama `POST /purchases` del kernel — **nunca cobra directo** |

Reglas de la frontera:
1. **Outputs = datos, no instrucciones.** Todo texto del merchant (`title`, `description`, reviews) viaja delimitado y el agente lo trata como dato (spotlighting). Las descripciones con payload adversarial viven en `contracts/fixtures/offers_adversarial.json` — propiedad comunidad: C3 aporta los strings del ataque, C2 los monta en el catálogo.
2. **`request_purchase` no acepta `amount`.** El monto sale de la offer referenciada; el invariante `intent.amount == offer.amount` se verifica en el kernel (§2). El agente no puede proponer un precio distinto del catálogo.
3. **No existe tool de pago.** Ninguna tool toca `PaymentRail`; la única ruta al dinero sigue siendo gate → verify → checkout.
4. El watcher (C3) NO usa MCP: sondea el REST `GET /catalog/offers` (api.yaml) — MCP es para el agente interactivo, el polling es para el job.
- [ ] DDL aplicado en Cloud SQL staging + docker-compose local.

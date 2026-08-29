# Coder brief — Implementación P0 antifraude en `src/`

**Para:** coder (agente o humano) · **Rama:** `dev3/fraud-transaction-research` (ya actualizada con main; `src/` presente) · **Emitido:** 2026-08-29 · **Verificación final:** Dev 3 (revisión de rango de commits + suite).
**Este archivo ES el prompt. Cópialo tal cual.** El texto íntegro vive aquí para el registro del equipo.

---

## PROMPT — empieza aquí

Eres el coder de implementación del equipo Dev 3 de **Aval** (hackathon Yuno × Nauta 2026): la capa de confianza para compras ejecutadas por agentes de IA. Un humano firma un **mandato** SD-JWT con límites de gasto y scope; un agente propone compras con **intents firmados**; un **gate determinista** (sin LLM) decide APPROVED/ESCALATED/REJECTED; el cobro va por un **rail** (PayPal sandbox; mock fiel de Yuno); todo queda en un **trail hash-encadenado**.

Tu trabajo: implementar la **Fase 1 completa del plan antifraude (F1.1–F1.8) + F2.1 (mock de Yuno) + F2.3 (switch de rail)** en la carpeta `src/` del repo. La especificación ya está decidida y congelada — tu valor es implementarla limpia, testeada y por fases cerradas, no rediseñarla.

### 0. Orden de lectura OBLIGATORIO antes de escribir una línea

1. `AGENTS.md` (raíz) — reglas del repo y del kernel.
2. `aval/docs/plans/2026-08-29-fraud-implementation.md` — **TU ESPECIFICACIÓN MAESTRA** (task cards F1.1–F2.3, matriz, DoD, checklist §10).
3. `aval/docs/decisions/0020-yunorail-adapter.md`, `0021-escalation-ttl-by-level.md`, `0022-p0-ownership-split.md` — el porqué de cada detalle.
4. `aval/contracts/schemas.md` (**v1.1**) — interfaces Python (§3), eventos (§4), DDL exacta (§6), reason codes (§7), mocks (§8).
5. `aval/contracts/api.yaml` — transporte REST: `Escalation.level`, TTLs, `GET /purchases/{id}/evidence-pack`.
6. `aval/docs/research/2026-08-29-fraud-transaction-research.md` — solo consulta (§6 amenazas, §8 matriz, §9 arquitectura).

**Jerarquía de verdad si algo parece contradecirse:** contratos v1.1 > plan > research > tu criterio. Si encuentras un hueco real, NO improvises cambios de contrato: impleméntalo del lado más conservador (fail-closed) y repórtalo en tu informe final.

### 1. Invariantes inviolables (violar cualquiera = implementación rechazada)

1. **Ningún modelo/ML en el camino de enforcement.** El gate y todas las reglas nuevas son funciones puras deterministas. Los baselines EWMA NO se implementan ahora (son Fase 3) — solo las tablas ya definidas en el DDL si aplica.
2. **Regla de oro:** solo señales verdictivas (firma inválida, límite duro, replay, amount≠offer, violación de cooldown) producen REJECT. Las corroborativas solo ESCALATE. Ninguna señal conductual rechaza sola.
3. **Fail-closed en todo timeout o duda** (webhook sin verificar, UV pendiente, rail caído → nunca "aprobar por defecto").
4. **El agente jamás ve la PAN.** Ninguna capa nueva toca datos de tarjeta.
5. **`aval/` es documentación y contratos: NO escribas código allí** (excepción única: `aval/docs/devlogs/dev3.md` y el guion `aval/docs/plans/attacks-demo.md`). `aval/contracts/` y `DECISIONS.md` están CONGELADOS: **cero ediciones**.
6. **Todo el código va en `src/`** (servicios). El CI (`scripts/docs-guard.sh`) ya exige devlog para cambios en `src/`: cada commit con código DEBE incluir su entrada en `aval/docs/devlogs/dev3.md`. Verifica con `bash scripts/docs-guard.sh origin/main` antes de cada commit.
7. **No hagas push.** Trabajas local; Dev 3 revisa al final.
8. Sin frameworks de agente (langgraph/openai-agents/crewai prohibidos — decisión #16). Stack: Python 3.13 + uv + FastAPI + httpx + pydantic + pytest. Nada más sin justificarlo en el informe.

### 2. Estado actual del repo (tu punto de partida)

- Rama `dev3/fraud-transaction-research`, ya mergueada con `origin/main` (que aportó `src/`).
- `src/api/`: SOLO 4 archivos vacíos — `router.py`, `services.py`, `schemas.py`, `respository.py` (**typo: renómbralo a `repository.py` en tu commit C0**).
- `src/agent/text.txt`: placeholder (32 bytes) — elimínalo en el commit de fixtures (C8).
- Raíz: `pyproject.toml` (uv, deps vacías), `uv.lock`, `Dockerfile`, `main.py`, `.python-version`.
- `aval/merchant/` y `aval/web/`: solo `.gitkeep`. **No los toques.**
- Herramienta: `uv sync`, `uv add`, `uv run pytest`. `main.py` debe quedar como entrypoint delgado que importa la app de `src/api`.

### 3. Arquitectura exigida (clean architecture, adaptada a lo que ya existe)

```
src/api/
  main.py            # factory de la app FastAPI (la monta el main.py de raíz)
  config.py          # settings por env (pydantic-settings): AVAL_RAIL, PayPal creds, IDEM_SECRET, TTLs
  router.py          # SOLO HTTP: request/response, status codes, validación. Cero lógica de negocio.
  schemas.py         # DTOs pydantic (request/response) — alineados con api.yaml v1.1
  services.py        # casos de uso: orquestan puertos, no saben de HTTP ni SQL
  repository.py      # persistencia: SQL puro, sin lógica de dominio
  ports.py           # Protocolos (puertos): PaymentRail, UVVerifier, Clock, IdempotencyStore...
  domain/policy.py   # reglas PURAS (R-BURST, R-STEPUP, R-PRICE lado gate): sin I/O, property-testeables
  rail/paypal.py     # adapter PayPal (implementa PaymentRail)
  rail/yuno_mock.py  # adapter contra el mock de Yuno (mismo Protocol)
  rail/factory.py    # AVAL_RAIL=paypal|yuno_mock → instancia
  webhooks/          # verificación, allow-list, pull-before-mutate, archivo
  evidence/          # builder del EvidencePack
  db/schema.sql      # DDL semilla = aval/contracts/schemas.md §6 v1.1 (copiada, no referenciada)
  tests/             # pytest: unit (fakes en memoria) + contract (respx para httpx)
  tests/fixtures/    # offers_adversarial.json y demás
src/agent/           # NO lo toques salvo eliminar el placeholder en C8
```

Reglas de dependencia (se auditan en review):
- `domain/` y `ports.py` no importan nada de infraestructura (no httpx, no sql, no fastapi).
- `services.py` depende de PUERTOS, nunca de adapters concretos (los adapters se inyectan).
- `router.py` no llama repositorios ni adapters: solo services.
- `repository.py` devuelve modelos de `schemas.py`/dominio, nunca filas crudas.
- Archivos < ~300 líneas; cuando uno crezca, parte por responsabilidad (no por capricho).
- Typing completo; `Decimal`/`str` para dinero (JAMÁS float); todo timestamp UTC-aware; IDs como str.
- Errores de negocio = `ReasonCode` del contrato (§7 v1.1), nunca strings mágicos.
- Añade ruff (format+lint) en C0 y déjalo verde en cada commit.
- Logs estructurados con `trace_id` (propaga: intent.jti → purchase → order/capture → webhook transmission_id).

### 4. Plan de commits — cada uno es una fase CERRADA y FUNCIONAL

Formato de mensaje: `feat(api): <task-id> <qué> (regla, test)` · convención conventional commits. **Cada commit debe:** compilar, dejar la suite en verde, incluir su entrada de devlog, y pasar `bash scripts/docs-guard.sh origin/main`. Nada de "WIP", nada de código muerto, nada de features a medias entre commits.

**C0 — `chore(api): bootstrap service skeleton (uv, fastapi, ruff, pytest)`**
Deps: fastapi, uvicorn, httpx, pydantic, pydantic-settings, pytest, respx, ruff. Renombra `respository.py`→`repository.py`. `config.py` con todas las envs del brief (tabla abajo). App factory + `/healthz`. `db/schema.sql` con la DDL de `aval/contracts/schemas.md` §6 v1.1 (las tablas nuevas incluidas: `idempotency_keys.derived_from/expires_at`, `risk_subjects`, `velocity_counters`, `baseline_metrics`, `baseline_hists`, `risk_lists`, `webhook_archive`, `payment_instruments.fraudnet_session`). Test humo: app arranca, `/healthz` 200.

**C1 — F1.1 R-IDEM · `feat(api): f1.1 derived idempotency key + retry policy (R-IDEM, T19)`**
- `idem_key(jti) = HMAC-SHA256(key=settings.IDEM_SECRET, msg=jti).hexdigest()` — helper puro en `domain/`.
- `IdempotencyStore` (puerto) + implementación Postgres (`idempotency_keys`: `key`, `scope` p.ej. `capture:{purchase_id}`, `response`, `derived_from=jti`, `expires_at=now()+45d`) + fake en memoria para tests.
- Toda llamada create/capture/refund al rail lleva `PayPal-Request-Id`/`X-Idempotency-Key` = esa clave. Retries ante timeout/5xx/409-`PREVIOUS_REQUEST_IN_PROGRESS`/`REQUEST_IN_PROCESS`: backoff fijo 0.5s/1s/2s, máx 3, MISMA clave. Misma clave + body canónico distinto → 409 local ANTES de salir.
- **T19:** (a) retry tras timeout no duplica cobro (respx responde 500 luego 201; se envía 1 sola orden con misma clave); (b) clave reutilizada con payload mutado → 409 local; (c) `expires_at` = 45 días.

**C2 — F1.2 R-PRICE · `feat(api): f1.2 end-to-end amount integrity (R-PRICE, T20)`**
- (i) En el camino de decisión: `intent.amount == offer.amount` (comparación de strings de 2 decimales contra `offers`) — pre-check verdictivo en `domain/policy.py` (puro).
- (ii) La orden al rail se crea con el amount byte-igual al del intent firmado.
- (iii) Post-captura: `get_status(order_ref)` relee el monto capturado; si ≠ aprobado → refund completo (con su propia idempotency key), evento `fraud.alert`, pausa del mandato, reason `PRICE_MISMATCH_AUTO_REFUND`.
- **T20:** mutar el precio de la offer entre propuesta y captura (fixture TOCTOU) termina en refund + `fraud.alert`, sin intervención humana.

**C3 — F1.3 R-WEBHOOK · `feat(api): f1.3 trustworthy webhooks (R-WEBHOOK, T21)`**
- Endpoint receptor (`/webhooks/paypal`, y `/webhooks/yuno` en C7): verifica firma vía `POST /v1/notifications/verify-webhook-signature` (respx en tests); ANTES de tocar `cert_url` valida host contra allow-list `*.paypal.com` (anti-SSRF, falla cerrado).
- **Pull-before-mutate:** ningún webhook muta estado sin re-leer el recurso por API (`get_status`).
- Archivo crudo append-only en `webhook_archive` (headers JSONB, raw body, `signature_valid`, `resource_pulled`).
- Procesamiento idempotente: clave `(source, transmission_id)` para PayPal; `(payment.id, type_event, retry)` para Yuno.
- Inválido → evento `webhook.rejected`, SIN mutar estado, respondiendo 200 (no filtres información al atacante).
- **T21:** firma inválida / host ajeno / replay → descartados sin efecto y archivados con `signature_valid=false`.

**C4 — F1.4 R-BURST · `feat(api): f1.4 anti-burst + cooldown (R-BURST, T22)`**
- `velocity_counters` upsert por `(mandate_id, counter='intents', window='1m')`.
- >3 intents/60 s → la decisión pasa a ESCALATED (aunque los límites den APPROVED) + cooldown hasta `now()+10 min` + evento `agent.paused_cooldown`.
- Intent NUEVO durante cooldown → REJECT con `VELOCITY_BURST` (verdictivo: es política determinista, no señal conductual).
- >5 escalamientos/hora por mandato → auto-pausa del mandato (`suspended`).
- Tope de autorizaciones abiertas simultáneas (>k=3) → ESCALATED.
- **T22:** ráfaga de 4 → ESCALATED+cooldown; segunda ráfaga en cooldown → REJECT.

**C5 — F1.5 R-STEPUP · `feat(api): f1.5 step-up levels with TTL (R-STEPUP, T23)`**
- Reglas puras en `domain/policy.py`: `amount ≥ 0.7 × max_per_txn` → `STEPUP_AMOUNT_THRESHOLD`; `(spent+reserved)/total_budget ≥ 0.8` → `STEPUP_BUDGET_USAGE`.
- `Escalation.level`: `L3` (TTL 120 s, aprobación bot con diff) | `L3+` (TTL 300 s, semántica RFC 9470 `max_age`). Ambos fail-closed: sin respuesta al deadline → REJECT + `escalation.expired`.
- Puerto `UVVerifier` para L3+: verifica una aserción WebAuthn sobre `SHA256(canonical(diff))`. Implementación por ahora = **stub que SIEMPRE FALLA (fail-closed)** hasta que llegue la infra de passkeys de Dev 3/4 — documentado en el código y en tu informe. No inventes una verificación débil.
- **T23:** umbral dispara L3+; timeout de UV rechaza (fail-closed); L3 mantiene 120 s.

**C6 — F1.6 R-EVIDENCE · `feat(api): f1.6 dispute evidence pack (R-EVIDENCE, T24)`**
- `GET /purchases/{id}/evidence-pack` (tag audit en api.yaml) → `EvidencePack`: `mandate_sd_jwt`, `ceremony_log`, `intent_jwt`, `gate_decision` (decisión + reason codes + features), `trail`, `receipts`, `webhooks` (los archivados), `generated_at`.
- Builder en `evidence/` leyendo de los repositorios; response JSON 200; 404 si no existe.
- **T24:** para una compra capturada, el bundle contiene las piezas verificables y es estable (re-generación determinista).

**C7 — F1.7 + F2.1 + F2.3 · se pueden partir en 2 commits si crece:**
- **F1.7 `feat(api): f1.7 rail risk metadata (FraudNet session)`**: `payment_instruments.fraudnet_session` se persiste al vincular instrumento; toda orden MIT envía `PayPal-Client-Metadata-Id: <sesión>` cuando existe. Test: header presente y estable (T17 ext).
- **F2.1 `feat(mocks): f2.1 faithful Yuno mock (decision 0020)`**: servicio en `src/mocks/yuno/` (propio `main.py`, docker-compose-ready). Contrato fiel a lo verificado en la investigación: headers `public-api-key`/`private-secret-key`; `X-Idempotency-Key` con los 4 comportamientos (misma respuesta / `400 REQUEST_IN_PROCESS` / `400 IDEMPOTENCY_DUPLICATED` / normal); `POST /customers`, `POST /customers/{cid}/payment-methods` → 201 `{vaulted_token, status:"ENROLLED", fingerprint}`, `POST .../payment-methods/{vaulted_token}/unenroll` → `{status:"UNENROLLED"}`; `POST /payments` con `payment_method.vaulted_token` + `detail.card.capture:true` + `stored_credentials` → estados `CREATED → PENDING/{IN_PROCESS, PENDING_FRAUD_REVIEW, AUTHORIZED} → SUCCEEDED|DECLINED/FRAUD_DECLINED|ERROR/TIMEOUT`; `GET /payments/{id}`; refund; webhook v2 `{type_event:"payment.purchase", version:2, retry, data:{payment}}` con `x-hmac-signature = base64(HMAC-SHA256(secret, raw_body))` y reintentos comprimibles por env; `mock_mode` inyectable vía `metadata` (`approve|decline|fraud_decline|async|timeout`).
- **F2.3 `feat(api): f2.3 rail switch AVAL_RAIL (decision 0020)`**: `rail/factory.py` → `paypal|yuno_mock`; `rail/yuno_mock.py` implementa el Protocol completo (`get_status`, `respond_dispute`; `open_dispute` levanta `DeprecationWarning` — decisión 0020). Paridad: los contract tests de rail corren parametrizados sobre ambos (PayPal vía respx).

**C8 — F1.8 · `feat(fixtures): f1.8 adversarial offers + attack smoke (T25)`**
- `src/api/tests/fixtures/offers_adversarial.json` (≥6 payloads: inyección directa/indirecta, urgencia fabricada, merchant lookalike dentro de scope, intento de manipulación de precio, hint de replay, trucos unicode/RTL).
- `test_t25_attack_script.py`: los 6 escenarios del plan §10 como smoke integral (T1 injection → T3 replay → T6 webhook falso → T9 burst → T5 TOCTOU → T7 disputa con evidence-pack), cada uno terminando en su defensa correcta.
- Guion humano legible en `aval/docs/plans/attacks-demo.md` (única excepción permitida de escritura en `aval/docs/` además del devlog).
- Elimina `src/agent/text.txt` (placeholder).

### 5. Variables de entorno (defínelas en C0, documéntalas en `config.py`)

`AVAL_RAIL=paypal|yuno_mock` · `PAYPAL_BASE` (sandbox) · `PAYPAL_CLIENT_ID/SECRET` · `WEBHOOK_ID` · `IDEM_SECRET` (HMAC de jti; en prod vive en Secret Manager) · `STEPUP_TTL_L3_S=120` · `STEPUP_TTL_L3PLUS_S=300` · `BURST_INTENTS_60S=3` · `BURST_COOLDOWN_S=600` · `ESCALATIONS_H=5` · `OPEN_AUTHZ_MAX=3` · `YUNO_MOCK_BASE` · `YUNO_WEBHOOK_SECRET` · `DATABASE_URL`. Nada de secretos en el código ni en commits.

### 6. Estrategia de tests

- **Unit (sin red, sin DB):** `domain/policy.py` y helpers puros — incluyen property tests donde el plan lo pide (umbrales, cooldown, derivación de clave).
- **Contract (respx):** adapters PayPal y YunoMockRail contra respuestas fijadas; idempotencia con los 4 casos Yuno; webhook verification SUCCESS/failure.
- **Integración (marcadas `@pytest.mark.db`, corren con Postgres de docker-compose o se saltan):** repositorios reales contra `db/schema.sql`.
- Fakes en memoria de los puertos para los tests de services — nunca mocks de funciones internas.
- La suite completa debe pasar con `uv run pytest` sin credenciales reales de nada.

### 7. Definition of Done (autoverifícalo antes de declarar terminado)

- [ ] Los 9 commits existen, en orden, cada uno verde y con devlog.
- [ ] `bash scripts/docs-guard.sh origin/main` en verde en HEAD.
- [ ] `uv run pytest` 100% verde; `uv run ruff check .` limpio.
- [ ] Checklist §10 del plan (los 9 criterios del demo) cubierto por tests.
- [ ] `aval/contracts/` y `DECISIONS.md` sin UNA línea cambiada (`git diff origin/main...HEAD -- aval/contracts/ aval/DECISIONS.md` vacío).
- [ ] Sin push; working tree limpio.

### 8. Informe final (formato obligatorio de tu último mensaje)

1. Tabla de commits (hash, task-id, regla, tests añadidos).
2. Resultado de la suite (números) y del guard.
3. Desviaciones respecto al brief (y por qué — la regla #1 de la sección 1 no es desvidable).
4. Huecos/contradicciones encontradas en la especificación (para Dev 3).
5. Qué queda stub (UVVerifier) y qué habría que conectar después.

## FIN DEL PROMPT

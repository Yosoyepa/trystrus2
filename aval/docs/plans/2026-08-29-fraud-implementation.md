# Plan de implementación — Defensa antifraude Aval

**Base:** [`../research/2026-08-29-fraud-transaction-research.md`](../research/2026-08-29-fraud-transaction-research.md) (dictamen del comité, D-01..D-10).
**Rama:** `dev3/fraud-transaction-research` · **Fecha:** 2026-08-29 · **Estado:** **Fase 0 ratificada** (0020 en variante solo-mock — F2.2 descartada · 0021 120/300 s · 0022 tal cual). Contratos en v1.1. Ejecución liberada.
**Cómo leer:** task cards por fase con dueño (carril Dev 1–4 de PLAN-PARALELO v3), milestone objetivo (M1–M4), touchpoints, DoD, tests y estimación. Los IDs de test nuevos continúan la numeración existente (hoy hasta T18).

---

## 0. Alcance y principios

- Se implementa el veredicto del comité: **defensa en 5 capas** (verdictica → señales → humano step-up → rail → post-facto) que **complementa** 3DS/autenticación/validaciones del procesador, nunca las reemplaza.
- **Regla de oro (no negociable en implementación):** solo señales verdictivas producen REJECT; las corroborativas producen ESCALATED/step-up. Ninguna señal conductual rechaza sola.
- Invariantes congelados que este plan respeta: sin LLM/ML en el camino de enforcement (baselines solo como features deterministas auditadas), el agente jamás ve la PAN, fail-closed en todo timeout, contratos v1.0 intactos hasta ratificar la Fase 0 (protocolo decisión #17).
- Alcance del demo: **Fase 1 completa + Fase 2 (mock)**. Fase 3 post-evento, Fase 4 backlog.

---

## 1. Fase 0 — Ratificaciones y contratos (bloqueante · ~2 h de equipo)

Tres decisiones propuestas por la investigación que tocan contrato congelado. Borradores listos en Apéndice A; al ratificar se commitean como `docs/decisions/0020..0022-*.md` + índice en `DECISIONS.md` + el delta de contrato en el **mismo commit** (regla v1.x aditiva: contrato + mock + trustlib juntos).

| ID | Decisión | Resumen | Touchpoints |
|---|---|---|---|
| **F0.1** | 0020 — `YunoRail` + extensión del Protocol | Adapter Yuno tras `PaymentRail` + mock fiel; extender Protocol con `get_status()` y `respond_dispute()`; `open_dispute` queda deprecado (PayPal-only); switch `AVAL_RAIL=yuno\|yuno_mock\|paypal` | `contracts/schemas.md` §3 |
| **F0.2** | 0021 — TTL de escalamiento por nivel | L3 (bot estándar) sigue en 120 s fail-closed; L3+ (UV por deep-link) pasa a 300 s con semántica RFC 9470 `max_age`. Fail-closed intacto en ambos | `contracts/schemas.md` §5, api.yaml escalations |
| **F0.3** | 0022 — Split de ownership de las P0 | R-PRICE/R-BURST/R-STEPUP → Dev 2 (el gate es suyo); R-IDEM/R-WEBHOOK/R-EVIDENCE/metadata rail → Dev 3; fixtures de ataque → Dev 1; diff + deep-link UV → Dev 4 | PLAN-PARALELO §7 |

**Delta exacto de contrato (Apéndice B tiene el detalle):** schemas §3 (Protocol +2 métodos), §4 (+6 eventos `risk.*`/`fraud.*`/`webhook.*`), §6 (DDL `webhook_archive` [3], `risk_subject`/`velocity_counter`/`baseline_*`/`risk_list` [2], nota TTL 45 d en `idempotency_keys`); api.yaml (+`GET /purchases/{id}/evidence-pack`).

**DoD Fase 0:** registros 0020–0022 en `docs/decisions/`, `DECISIONS.md` con 22 entradas, `docs-guard` en verde, trustlib con los modelos nuevos compilando.

> **RATIFICADA 2026-08-29.** 0020 aceptada en variante *solo mock* (no hay credenciales sandbox de Yuno y el gate de 48 h no es alcanzable: el demo corre `AVAL_RAIL=paypal|yuno_mock` y F2.2 pasa a Fase 4); 0021 aceptada con 120/300 s; 0022 aceptada tal cual. El delta de contrato del Apéndice B ya está aplicado (contratos v1.1, mismo commit que los registros — protocolo #17).

---

## 2. Fase 1 — Núcleo P0 (el demo resiste T1–T10) · ~35 h totales

### F1.1 — R-IDEM: idempotencia derivada de `jti` · **Dev 3** · M1 · 4 h
- **Qué:** toda llamada create/capture/refund al rail usa `PayPal-Request-Id` = HMAC-SHA256(kms_key, `intent.jti`); los retries reutilizan la clave; 409 `PREVIOUS_REQUEST_IN_PROGRESS` → backoff fijo exponencial; `idempotency_keys` persiste 45 días (config `IDEM_TTL_DAYS`).
- **Touchpoints:** `kernel/rail` (adaptador PayPal), migración [2] (`idempotency_keys.ttl`), `packages/trustlib` (helper `idem_key(jti)`).
- **DoD:** reintentar una captura con timeout de red jamás genera doble cobro en sandbox; la misma clave con body distinto se rechaza localmente antes de salir.
- **Tests:** T17 (rail) + **T19** nuevo — retry tras timeout usa misma clave y no duplica; clave reutilizada con payload mutado → 4xx local.

### F1.2 — R-PRICE: integridad de monto punta a punta · **Dev 2** (gate) + **Dev 3** (post-captura) · M2 · 6 h
- **Qué:** (i) el gate verifica `intent.amount == offer.price` contra catálogo al decidir (ya es invariante del diseño; se hace explícito y testeado); (ii) la order se crea con el amount byte-igual al del intent firmado; (iii) post-captura, `GET order` relee el monto: mismatch ⇒ refund inmediato + evento `fraud.alert` + pausa del mandato.
- **Touchpoints:** `kernel/verify` (Dev 2), `kernel/rail` + job de verificación post-captura (Dev 3), catálogo fuente (fixtures Dev 1).
- **DoD:** mutar el precio de la oferta entre propuesta y captura termina en refund + `FRAUD_ALERT` en el trail, sin intervención humana.
- **Tests:** **T20** nuevo — TOCTOU de precio termina en auto-refund; T1 (gate) ampliado con amount alterado.

### F1.3 — R-WEBHOOK: webhooks confiables · **Dev 3** · M2 · 6 h
- **Qué:** verificación vía `POST /v1/notifications/verify-webhook-signature`; allow-list del host de `cert_url` (anti-SSRF); **pull del recurso antes de mutar estado**; archivo crudo append-only (`webhook_archive`: headers + raw body + `signature_valid` + `resource_pulled`); procesamiento idempotente (clave `payment.id`+`type_event`+`retry` para Yuno).
- **Touchpoints:** `kernel/routers/webhooks`, migración [3] (`webhook_archive`).
- **DoD:** un POST forjado como `PAYMENT.CAPTURE.COMPLETED` no muta estado, queda archivado con `signature_valid=false` y emite `webhook.rejected`.
- **Tests:** T14 (webhooks) + **T21** nuevo — firma inválida/host ajeno/replay se descartan sin efecto.

### F1.4 — R-BURST: anti-burst y anti-fatiga · **Dev 2** · M2 · 4 h
- **Qué:** contadores `velocity_counter` en el camino del gate: >3 intents del mismo mandato en 60 s ⇒ ESCALATED + cooldown 10 min (evento `agent.paused_cooldown`); >N escalamientos/hora por mandato ⇒ auto-pausa del mandato; tope de autorizaciones abiertas simultáneas.
- **Touchpoints:** `kernel/policy` (pre-checks deterministas antes de límites), migración [2] (`velocity_counter`).
- **DoD:** loop de retries del agente no desgasta al aprobador humano; el bombardeo de escalamientos auto-pausa.
- **Tests:** **T22** nuevo — burst → ESCALATED+cooldown; segunda ráfaga en cooldown → REJECT silencioso.

### F1.5 — R-STEPUP: step-up por umbral fijo · **Dev 2** (razón) + **Dev 4** (UX) · M3 · 6 h
- **Qué:** reglas deterministas: `amount ≥ 0.7 × max_per_txn` ∨ uso de budget ≥ 80 % ∨ (merchant nuevo ∧ monto > mediana histórica) ⇒ escalado con **UV** (deep-link a web con WebAuthn `userVerification:"required"`, firma del hash del diff — patrón SPC); L3 estándar sigue mostrando diff (regla, valor vs umbral, restante) en bot out-of-band.
- **Touchpoints:** `kernel/policy` (reason codes `stepup_amount_threshold`, `stepup_budget_usage`), `web/` (Dev 4: pantalla de firma del diff), contrato TTL (F0.2).
- **DoD:** compra de monto alto no se aprueba con un tap en el bot; requiere ceremonia UV dentro del TTL o fail-closed.
- **Tests:** **T23** nuevo — umbral dispara L3+; timeout de UV rechaza (fail-closed).

### F1.6 — R-EVIDENCE: paquete de evidencia de disputa · **Dev 3** · M3 · 4 h
- **Qué:** `GET /purchases/{id}/evidence-pack` arma el bundle: mandato SD-JWT + log de ceremonia passkey + PurchaseIntent JWS + decisión del gate con reason codes + trail hash-encadenado + receipts + webhooks archivados; export JSON/PDF; conexión al `provide-evidence` de Disputes API (y a dispute-evidence de Yuno en F2).
- **Touchpoints:** `kernel/routers/audit`, `packages/trustlib` (serializadores).
- **DoD:** para una compra disputada simulada, el bundle se genera en < 1 min y contiene las 6 piezas verificables.
- **Tests:** T18 (disputa, conjunto con Dev 2) + **T24** nuevo — bundle completo y verificable (`/audit/verify` pasa sobre él).

### F1.7 — Metadata de riesgo del rail · **Dev 3** · M2 · 2 h
- **Qué:** capturar sesión FraudNet (o device-id directo) en la pantalla donde el humano aprueba el setup token UNA vez; persistirla en el mandato (`payment_instruments`); enviarla como `PayPal-Client-Metadata-Id` en cada order MIT posterior.
- **DoD:** las orders sandbox llevan el correlator; en trail queda el linkage sesión→mandato→orders.
- **Tests:** T17 ampliado (header presente y estable).

### F1.8 — Fixtures y guion de ataque para el demo · **Dev 1** (strings) + **Dev 2** (catálogo) + **Dev 4** (guion) · M4 · 3 h
- **Qué:** `contracts/fixtures/offers_adversarial.json` (payloads T1: instrucción encubierta, urgencia fabricada, merchant lookalike dentro de scope); escenarios demo en el orden del threat model: T1 injection → T3 replay → T6 webhook falso → T9 burst → T5 TOCTOU de precio → T7 disputa con evidence-pack.
- **DoD:** guion de 6 ataques ejecutable end-to-end en sandbox, cada uno terminando en la defensa correcta (REJECT/ESCALATED/auto-refund/bundle).
- **Tests:** sirve de smoke-test integral pre-demo (T25 nuevo, opcional como CI job manual).

---

## 3. Fase 2 — YunoMockRail (paralela a F1, no bloquea el demo)

### F2.1 — Mock fiel de Yuno · **Dev 3** · 4 h · M2/M3
Servicio en `contracts/mocks/` + docker-compose: auth (`public-api-key`/`private-secret-key`), `X-Idempotency-Key` con los 4 comportamientos reales (misma respuesta / `REQUEST_IN_PROCESS` / `IDEMPOTENCY_DUPLICATED` / normal), estados reducidos fieles (`CREATED → PENDING/{IN_PROCESS, PENDING_FRAUD_REVIEW, AUTHORIZED} → SUCCEEDED|DECLINED/FRAUD_DECLINED|ERROR/TIMEOUT`), webhook v2 con HMAC real y reintentos comprimibles, `mock_mode` inyectable (`approve|decline|fraud_decline|async|timeout`), disputa entrante `payment.chargeback` + endpoint de evidencia. **DoD:** contract-tests parametrizados mock↔PayPal pasan los mismos casos (patrón del repo).

### F2.2 — ~~`YunoRail` real~~ · **DESCARTADA** (decisión 0020: sin credenciales sandbox; el gate de 48 h no es alcanzable)
Movida a Fase 4. El diseño queda en la investigación §9.2 (enrollment vía Testing Gateway, `stored_credentials` CIT/MIT, `unenroll` como kill switch, Receipt no-terminal con `get_status`): cuando existan credenciales, el adapter real es un cambio de configuración, no de diseño. El mock ES la historia Yuno del demo.

### F2.3 — Switch de rail + paridad · **Dev 3** · 4 h
`AVAL_RAIL=paypal|yuno_mock` por env (0020; el valor `yuno` real queda reservado a Fase 4); suite completa (T2/T8/T14/T17/T19/T21) parametrizada por rail.

---

## 4. Fase 3 — Endurecimiento P1 (post-hackathon, semanas 1–4)

| Orden | Tarea | Dueño | Nota |
|---|---|---|---|
| 1 | R-VELOCITY: matrices (mandato, merchant, hora) y (mandato, bucket de monto) | Dev 2 | Sobre `velocity_counter` de F1.4 |
| 2 | R-BASELINE: EWMA/histogramas como **features auditadas** (features + versión de fórmula + evento de recalibración en el trail; D-03) | Dev 2 | Baseline del agente versionado por build desde el día 1 |
| 3 | R-AUTH-DIFERIDA: AUTHORIZE→(ventana revocación)→CAPTURE; void automático en timeout con auth abierta | Dev 3 | Cambia el flujo del rail; requiere ADR de secuencia |
| 4 | R-REFUND-GATE: refunds solo vía kernel con verificación de mandato | Dev 3 | Manejo `REFUND_AMOUNT_EXCEEDED` |
| 5 | R-RECON: job diario Transaction Search/reporting ↔ hash chain; divergencia ⇒ pausa + `recon.divergence` | Dev 3 | Detector barato de "transferencia manipulada" |
| 6 | R-VAULT-LIMIT: máx. 3 vault-bindings/24 h por humano | Dev 3 | Patrón anti-card-testing (caso Weee!) |
| 7 | R-FIRST-PARTY: contador de disputas del payer ≥ 2/90 días ⇒ step-up | Dev 2 | Cierra T7 |
| 8 | Risk lists locales (block/allow) + admin mínimo | Dev 2 | Réplica de Yuno risk conditions |
| 9 | Recalibración programada (λ=0.15; aprobación humana entra con peso completo; absuelto en apelación NO entra) | Dev 2 | Anti-envenenamiento |
| 10 | Ladder completo L0–L4 + consentimiento granular antifraude en la UI del mandato (Ley 1581) | Dev 4 | Con asesoría del doc §11 |

## 5. Fase 4 — P2 backlog (escalamiento)

R-KEY-ROTATION (rotación de `cnf.jwk` con re-aprobación passkey) · R-CONTEXT-BINDING (fingerprint de runtime firmado en el KB-JWT) · R-COOLING (cooling-off primer escalamiento/beneficiario nuevo, precedente RBI) · Web Bot Auth/RFC 9421 agente→comercio · scoring conductual como servicio fuera del camino de enforcement · tracking de Visa Intelligent Commerce / Mastercard Agent Pay para riel nativo.

---

## 6. Matriz resumen

| Task | Regla | Dueño | Milestone | Est. | Tests |
|---|---|---|---|---|---|
| F1.1 | R-IDEM | 3 | M1 | 4 h | T17, T19 |
| F1.2 | R-PRICE | 2+3 | M2 | 6 h | T1, T20 |
| F1.3 | R-WEBHOOK | 3 | M2 | 6 h | T14, T21 |
| F1.4 | R-BURST | 2 | M2 | 4 h | T22 |
| F1.5 | R-STEPUP | 2+4 | M3 | 6 h | T23 |
| F1.6 | R-EVIDENCE | 3 | M3 | 4 h | T18, T24 |
| F1.7 | metadata rail | 3 | M2 | 2 h | T17 ext |
| F1.8 | fixtures ataque | 1+2+4 | M4 | 3 h | T25 (smoke) |
| F2.1 | mock Yuno | 3 | M2/M3 | 4 h | paridad |
| ~~F2.2~~ | ~~YunoRail real~~ | 3 | **descartada (0020)** | — | — |
| F2.3 | switch rail | 3 | M3 | 4 h | suite ×rail |

**Totales F1:** ~35 h (Dev 2 ≈ 12 h · Dev 3 ≈ 16 h · Dev 1+4 ≈ 7 h). La Fase 2 no bloquea: si Yuno no llega, PayPal + mock sostienen el demo completo.

## 7. Migraciones por carril (post-F0)

- **[2] Dev 2 añade:** `risk_subject`, `velocity_counter`, `baseline_metric`, `baseline_hist`, `risk_list`.
- **[3] Dev 3 añade:** `webhook_archive`; amplía `idempotency_keys` (TTL 45 d, `derived_from_jti`); columna `fraudnet_session` en `payment_instruments`.

## 8. Eventos nuevos (outbox, catálogo v1.1 aditivo)

| type | Emite | Consumen |
|---|---|---|
| `risk.stepup_required` | 2 | 4 (UI UV), SSE |
| `agent.paused_cooldown` | 2 | 1 (agente replanifica), SSE |
| `fraud.alert` | 2 | SSE, dashboard |
| `payment.refunded.auto` | 3 | 2 (ledger), SSE |
| `webhook.rejected` | 3 | 2 (ledger) |
| `recon.divergence` | 3 | 2 (pausa mandato), alertas |

## 9. Riesgos de implementación

| Riesgo | Mitigación |
|---|---|
| F1.2/F1.8 dependen del catálogo (mitigación #19: fixtures → Dev 1) | F1.8 se agenda con Dev 1 en la ventana de ~2 h ya acordada |
| UV por deep-link rompe el presupuesto de 120 s | F0.2 TTL por nivel; medir latencia real en M3; fallback: L3 con diff si UV no llega |
| Configuración no-código de Yuno (risk conditions/routing) | Fuera del demo (0020): el demo usa `yuno_mock`; las capturas del dashboard quedan como material de roadmap |
| Disputas sandbox no ponderan evidencia (`adjudicate` sandbox-only) | El demo muestra el bundle generado, no el outcome de PayPal |
| Fatiga del aprobador en ensayos | F1.4 cooldown desde el primer día; guion de demo con ráfaga controlada |

## 10. Criterios de aceptación del demo (checklist final)

- [ ] Replay de un intent capturado → rechazo silencioso + `fraud.alert` en trail (T3).
- [ ] Oferta con payload adversarial → el agente la trata como dato; fuera de scope → REJECT (T1).
- [ ] Webhook falsificado → descartado sin mutación, archivado `signature_valid=false` (T6).
- [ ] Ráfaga de 4 intents/60 s → ESCALATED + cooldown 10 min (T9).
- [ ] Precio mutado propuesta↔captura → auto-refund + `fraud.alert` (T5).
- [ ] Monto ≥ 0.7 × `max_per_txn` → diff en bot + UV por deep-link; timeout ⇒ fail-closed (T4).
- [ ] Evidence-pack de compra disputada generado en < 1 min y verificable (T7).
- [ ] `AVAL_RAIL=yuno_mock` ejecuta el flujo completo (historia sponsor).
- [ ] `/audit/verify` pasa sobre el trail completo de la sesión de demo.

---

## Apéndice A — Borradores de decisión (listos para ratificar)

**0020-yunorail-adapter.md** · Workstream: 3 (propone) / all. **Contexto:** investigación dev3 D-06/D-07; Yuno es sponsor y aporta risk conditions, fraud_screening, 3DS con `liability_shift`, stored credentials CIT/MIT. **Decisión:** añadir `YunoRail` + mock fiel detrás de `PaymentRail`; extender el Protocol con `get_status(external_ref) -> Receipt` y `respond_dispute(evidence) -> DisputeRef`; deprecar `open_dispute` (PayPal-only); switch `AVAL_RAIL`. PayPal permanece rail por defecto del demo hasta validar sandbox Yuno. **Consecuencias:** contratos v1.1 aditivos (mock + trustlib en el mismo commit); Receipt debe modelar estados no-terminales; disputas pasan a ser entrantes en ambos rails.

**0021-escalation-ttl-by-level.md** · Workstream: 2+4. **Contexto:** D-02/D-04 — 120 s es insuficiente para UV inter-canal; RBI/UK precedents. **Decisión:** L3 estándar 120 s fail-closed (sin cambio); L3+ (UV) TTL 300 s con semántica RFC 9470 `max_age`; sin respuesta ⇒ REJECT. **Consecuencias:** api.yaml escalations documenta ambos TTL; la UI debe renderizar cuenta regresiva por nivel.

**0022-p0-ownership-split.md** · Workstream: all. **Contexto:** Fase 1 del plan. **Decisión:** tabla de la §6 (R-PRICE/BURST/STEPUP → 2; IDEM/WEBHOOK/EVIDENCE/metadata → 3; fixtures → 1; UX step-up → 4); migraciones según §7. **Consecuencias:** PLAN-PARALELO §7 incorpora T19–T25 con estos dueños.

## Apéndice B — Delta exacto de contrato (a ejecutar en F0)

1. `contracts/schemas.md` §3 — bloque nuevo en `PaymentRail`:
   ```python
   def get_status(self, external_ref: str) -> Receipt          # PENDING-aware; Yuno GET /payments/{id}, PayPal GET order
   def respond_dispute(self, dispute_id: str, evidence: EvidencePack) -> DisputeRef  # provide-evidence / dispute-evidence
   # deprecado: open_dispute() — solo PayPal, solo sandbox/simulación
   ```
2. `contracts/schemas.md` §4 — filas de la §8 de este plan.
3. `contracts/schemas.md` §6 — DDL de la §7 de este plan (comentarios `[2]`/`[3]`).
4. `contracts/api.yaml` — `GET /purchases/{id}/evidence-pack` (tag audit, `[Dev 3]`); TTL por nivel en el schema de escalations.
5. `contracts/fixtures/offers_adversarial.json` + `attacks_demo.md` (guion F1.8).

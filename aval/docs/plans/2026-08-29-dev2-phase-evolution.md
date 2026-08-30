# Evolución de fases Dev 2 — cierre del núcleo, integración y evidencia (2026-08-29)

Deriva del análisis de brechas
([`../research/2026-08-29-dev2-gate-gap-analysis.md`](../research/2026-08-29-dev2-gate-gap-analysis.md))
y de los dictámenes de 8 auditorías paralelas. Reemplaza en alcance a los
briefs anteriores cuando estos terminen: el coder 1 (C1–C5) sigue en vuelo —
**Fase D2-C es la continuidad de su brief**; las fases D2-I en adelante son
nuevas. Ejecución sugerida: D2-C y D2-I pueden partirse entre dos coders
(no comparten archivo salvo el merge inicial); D2-S y D2-D dependen de D2-I.

Reglas permanentes de carril (0022): nada de HTTP/rail/webhooks entrantes
(Dev 3), fixtures (Dev 1), step-up UX (Dev 4). Contratos congelados: cualquier
delta (Q-04, Q-05, VELOCITY_BURST/COOLDOWN_ACTIVE) va como PR aditivo v1.x con
registro de decisión, jamás editado en silencio.

---

## Fase D2-C — Cierre del núcleo de decisión (continúa el brief C1–C5)

**Objetivo:** que el camino del dinero sea inequívocamente fail-closed y sin
replay, con commits C1–C5 cerrados.

| # | Tarea | Ref | Est. |
|---|---|---|---|
| C-1 | Estabilizar WIP: `IndentationError`, ruff (F841/E501), suite verde, particionar en commits C1–C5 con devlog + guard por commit | W-01/03/04 | 2 h |
| C-2 | Eliminar el bypass `uv_verified` del camino productivo: solo aserción criptográfica; test de regresión | RT-1 | 2 h |
| C-3 | Cablear `IdempotencyStore` en `verify` (reserve→gate→…→`save_response`); `IdempotencyConflict` → REJECTED `DUPLICATE_JTI` + `fraud.alert` (según Q-01); replay de ESCALATED no crea segunda escalación; serializar `level/ttl/requires_uv` en replay | RT-2, G-1, G-8, B-02 | 6 h |
| C-4 | Fail-closed en fallbacks: borrar el re-run sin offer; oferta ausente ⇒ ESCALATED (nunca downgrade); capturar (ValueError, TypeError, RecursionError) → REJECT determinista; caps de profundidad/nodos en `_jsonlogic` | RT-3, G-6, G-7 | 5 h |
| C-5 | Offer obligatoria para APPROVED + igualdad de moneda offer/intent/mandato | G-2, G-3 | 4 h |
| C-6 | Frescura del intent (`exp`/`iat`/120 s) y puerto de nonce (`NONCE_REUSED`) | G-4, B-01 | 5 h |
| C-7 | Guardia de arranque: puertos críticos obligatorios (o flag explícito de modo degradado); conectar `first_escalation`/`fresh_agent_key` | G-5, 0021 | 4 h |
| C-8 | Re-chequeo de ratios de step-up dentro del WHERE de la reserva; dedupe idempotente de reserva en PG; doble conteo burst en re-gate | RT-6, G-10 | 5 h |
| C-9 | Test de doble compra concurrente (T5 en memoria); test de regresión del delta de scope en policy.py; `purchase.requested` + status conforme a Q-04 | W-05/06/12 | 3 h |
| C-10 | C5 del brief: tests `@pytest.mark.db` de los 3 stores PG (psycopg a pyproject de gate-core, base temporal), perfil hypothesis `ci` | W-02, W-11 | 4 h |

**Gate de salida (todos o no se cierra la fase):** suite 100% verde con y sin
`DATABASE_URL`; ruff 0 en trackeados; docs-guard OK; ninguna sonda de las
auditorías reproduce RT-1/RT-2/RT-3/G-1/G-2 (tests que lo demuestran); replay
idéntico devuelve respuesta original y distinto-body rechaza con alerta; diff
sin archivos prohibidos; working tree limpio en commits atómicos C1–C5.

## Fase D2-I — Integración de carriles y wiring (merge + costuras)

**Objetivo:** una sola línea donde decisión y evidencia convivan y el flujo
gate→outbox→ledger→relay funcione de punta a punta.

| # | Tarea | Ref | Est. |
|---|---|---|---|
| I-1 | Merge `dev2/audit-evidence` → línea común; resolver devlog keep-both | — | 1 h |
| I-2 | Unificar `OutboxEvent` (adoptar el de events con validación) y `Clock` (uno compartido); eliminar la mina `as_dict()` | Outbox | 3 h |
| I-3 | Extraer `src/api/canonical.py` (canonical_json/normalize_utc/ensure_aware_utc) y migrar audit+events+fingerprints de domain — **una sola canonía** | RT-9 | 4 h |
| I-4 | `PostgresOutboxWriter` que acepta `transaction=` + `PostgresTransactionManager` (misma conexión que la reserva) | P-02 | 4 h |
| I-5 | Adaptadores PG faltantes: MandateReader, OfferCatalog, PurchaseStore (requiere `purchases.escalation_id` — Q-04), EscalationStore | P-01, W-10 | 8 h |
| I-6 | Sink `LedgerMirrorSink` (outbox→`LedgerService.append`) según Q-07 | Seam-13 | 3 h |
| I-7 | `decision/composition.py` (fábricas build_decision_service/build_event_relay) + `events/runner.py` (loop con backoff) — Dev 3 solo invoca | Seam | 5 h |
| I-8 | Settings propio de Dev 2 (`decision/settings.py`) sin tocar config.py; fail-fast de `IDEM_SECRET`/signer según Q-09 | P-08 | 3 h |
| I-9 | Test E2E de unión: verify → outbox → `audit_events` misma tx → `validate_chain` ok → relay entrega al sink firmado | TX-08 | 2 h |

**Gate de salida:** el test E2E de I-9 pasa en CI local; `uv run pytest` con
`DATABASE_URL` (docker) sin skips db; una sola implementación de
canonical_json en el repo; `build_decision_service(dsn=...)` construye con
Postgres y sin él (fakes); ningún import de decision→audit/events directo.

## Fase D2-S — Saga completa: captura, reconciliación y limpieza

**Objetivo:** que ninguna compra quede colgada y el fail-closed sea activo, no
perezoso.

| # | Tarea | Ref | Est. |
|---|---|---|---|
| S-1 | `capture/settle` en la reserva (reserved→spent) vía `CapturePort`; productor `purchase.captured`; acuerdo Q-06 con Dev 3 | B-04 | 4 h |
| S-2 | `ReconciliationService.reconcile_capture` (pull vía `RailStatusReader`): terminal→cierra clave+receipt; no-terminal→retiene; desconocido→**nunca libera**, emite `recon.divergence` y escala | I-03 del análisis | 5 h |
| S-3 | Sweeper de escalaciones (job 30 s): expira por TTL, compensa, emite `escalation.expired`; contador de expiradas → suspensión L4 ("3 sin respuesta") | P-04, RT-7 | 4 h |
| S-4 | Auto-suspensión durable (`mandates.status='suspended'` según Q-10) + reactivación diferida | RT-7 | 2 h |
| S-5 | `release` PG con `reservation_id` obligatorio (exactly-once) alineando fake y adaptador | W-08, P-03 | 2 h |
| S-6 | Retención: purga de idempotencia (45 d), buckets velocity >2 h, outbox relayed >7 d; `attempts`/`next_retry_at` + backoff + alerta de backlog en el relay | P-05, P-07 | 4 h |
| S-7 | `sign_root`: publicar witness **antes** de anotar; `get_range` paginado; advisory lock de génesis | P-06 | 3 h |
| S-8 | Evento `root.checkpoint` al outbox al cerrar checkpoint (para el trail público) | B-05 | 2 h |

**Gate de salida:** escenario "aprobador desaparece" se recupera solo (sweeper
compensa + evento); "rail timeout" retiene reserva y nunca re-captura;
`pending_capture` no persiste más de TTL sin evento; doble `release` es
inofensiva (test); relay con sink caído reintenta con backoff y expone backlog.

## Fase D2-D — Evidencia y disputas (el diferenciador ante jurados)

**Objetivo:** R-EVIDENCE real y el primer consumidor de riesgo sobre disputas.

| # | Tarea | Ref | Est. |
|---|---|---|---|
| D-1 | `src/api/evidence/`: `EvidenceService.assemble(purchase_id)` → mandato + intent + decisión + receipt + slice del ledger + `ChainResult` + root checkpoint; falla con `integrity: failed`, jamás oculta | H-03 | 6 h |
| D-2 | Persistir features de la decisión (gate_decision con reason codes + features) para el pack | B-08 | 3 h |
| D-3 | `fraud.alert` completo (kinds price-mismatch + payload canónico) y alinear `agent.paused_cooldown` (`mandate_jti`+`cause`) | B-06 | 2 h |
| D-4 | `DisputeService` (open_case/decide/on_resolved) sobre `risk_subjects`; DEFEND si el trail respalda, ACCEPT si no; **regla de oro intacta** (corroborativas solo escalan) | I-06 | 6 h |
| D-5 | R-FIRST-PARTY: contador de disputas 90 d → step-up recomendado + reason code (PR aditivo si falta) | B-11 | 4 h |
| D-6 | Anclar escalaciones al ledger (eventos con hash del diff; validar `timeout_at` contra el chain) | RT-4 | 4 h |
| D-7 | Envelope de aprobador: approver_id + `receipt_sig` en `escalation.resolved` | RT-5 | 4 h |
| D-8 | Vectores dorados del hash (fixture con semilla fija) — barato y protege el T9 en vivo | TX-10 | 2 h |

**Gate de salida:** `assemble()` sobre una compra con el demo T9 ejecutado
devuelve pack con `integrity: ok` y verificación byte-a-byte contra witness;
mutar un diff de escalación rompe la verificación (test D-6); ≥2 disputas/90 d
provoca recomendación de step-up (test determinista).

## Fase D2-B — Post-demo: baselines y endurecimiento P1/P2

EWMA auditado sobre `baseline_metrics`/`baseline_hists` (features
deterministas, cold start <10 obs escala, jamás enforcement — D-03) · 8 h.
Matrices de velocity (mandato×merchant×hora) · 6 h. `risk_lists`
(block/allow) en el gate · 4 h. Separar `schema.sql` en migraciones por dueño
+ runner idempotente · 3 h. `COOLDOWN_ACTIVE`/higiene de clasificación (PR
v1.x) y api.yaml ReasonCode + 5 códigos (B-12) · 4 h. L1/L2 de la escalera ·
post-plan. Total ≈ 25 h, sin gate de demo.

## Briefs cruzados que hay que emitir YA (desbloquean H-01..H-08)

| Brief | Carril | Desbloquea | Est. |
|---|---|---|---|
| R-WEBHOOK completo (verif. firma + allow-list + pull + archivo) | Dev 3 | T6, M6 | 6 h |
| R-IDEM lado rail (header, backoff 409) | Dev 3 | H-04 | 4 h |
| R-PRICE post-captura + auto-refund + pausa | Dev 3 | T5/M4, H-01 | 6 h |
| Adaptador WebAuthn de UV (sustituye el stub fail-closed) | Dev 3+4 | T4/L3+, H-07 | 8 h |
| Wiring HTTP + composition root (consume nuestras fábricas) | Dev 3 | H-08, T4/T13 | 6 h |
| Mock Yuno + switch `AVAL_RAIL` | Dev 3 | 0020/F2 | 8 h |
| Fixtures de ataque + guion (T1/T3/T5/T6) | Dev 1 | F1.8, T25 | 3 h |

## Dependencias y orden

```
D2-C (núcleo) ──► D2-I (merge+costuras) ──► D2-S (saga) ──► D2-D (evidencia)
        ▲                    │                                   │
        └── briefs cruzados Dev 3 (en paralelo) ──────────────────┘
D2-B: post-demo
```

Totales Dev 2: D2-C ≈ 39 h · D2-I ≈ 33 h · D2-S ≈ 26 h · D2-D ≈ 31 h ·
D2-B ≈ 25 h. Ruta crítica demo (recorte): C-1..C-5, C-9, I-1..I-4, I-7, I-9,
S-3, S-7, D-1, D-8 ≈ **40 h**.

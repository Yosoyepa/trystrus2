# Análisis extenso de brechas Dev 2 — gates, cobertura y costuras (2026-08-29)

**Método:** 8 auditorías paralelas de solo lectura sobre `dev2/gate-core` @ `36cedb6`
(+ WIP sin commitear del coder 1 en `src/api/decision/`) y `dev2/audit-evidence`
@ `54d54f2` (leído con `git show`): (1) completitud y fail-closed del
PolicyGate, (2) WIP del coder 1 vs brief C1–C5, (3) delta contratos v1.1 ↔
código, (4) cobertura de amenazas T1–T10 / M1–M13 / L0–L4 + red-team del
código, (5) matriz de pruebas, (6) costuras de integración/wiring,
(7) R-IDEM end-to-end y disputas, (8) persistencia y operabilidad.

**Advertencia de instantánea:** el coder 1 estuvo editando en vivo durante la
auditoría (`service.py` 821→934 líneas; se detectó un `IndentationError`
transitorio). Los hallazgos reflejan el estado ~19:35 y deben re-verificarse al
aterrizar su run. La suite en gate-core pasaba de 45 → 56 tests durante la
auditoría (añadió `hypothesis`).

**Síntesis ejecutiva:** el núcleo determinista está bien construido y bien
testeado — regla de oro completa y disjunta, límites exactos en `Decimal`,
R-BURST/R-STEPUP/R-PRICE(side gate) enforced, TTLs 120/300 correctos, re-gate
al resolver escalación, reserva atómica con UPDATE condicional, ledger con
tail-lock + witness fail-closed. Pero **el sistema hoy defiende un perímetro
que nadie ataca todavía**: no existe verificación de firma/replay en el camino
del gate, el `IdempotencyStore` está huérfano (nadie lo llama), y las P0 que
atraviesan el rail/webhook/evidencia están **huérfanas de brief**. Además hay
dos agujeros propios del núcleo que deben cerrarse antes de exponer cualquier
HTTP: el bypass de UV (RT-1) y la degradación silenciosa de R-PRICE (RT-3/G-2).

---

## 1. Registro de hallazgos (por severidad)

IDs conservan el prefijo de la auditoría de origen: G=gate, W=WIP coder 1,
RT=red-team, B=backlog contratos, H=P0 huérfanas, I=R-IDEM, P=persistencia/ops,
TX=pruebas.

### Críticos (cerrar antes de cablear HTTP)

| ID | Hallazgo | Evidencia |
|---|---|---|
| RT-1 | **Bypass de UV en L3+**: `resolve_escalation(..., uv_verified=True)` aprueba sin aserción WebAuthn; si Dev 3 llena el flag desde el cliente, la UV es decorativa | `domain/policy.py:848-849`, `decision/service.py:731` |
| RT-2 | **El verify path no conoce el replay**: `DecisionService` no recibe ni llama `IdempotencyStore`; `DUPLICATE_JTI`/`NONCE_REUSED` son códigos muertos; no se valida `exp`/`iat`/`nonce` del intent; única traba = colisión de PK `purchase-{jti}`, que se esquiva si el caller pasa `purchase_id`; y un replay en estado ESCALATED crea una **segunda escalación pending** (PK uuid4) sobre la misma compra | `decision/service.py:223-224,698-700`; grep 0 hits de `DUPLICATE_JTI` emitido |
| RT-3 | **Degradación silenciosa**: si `gate.evaluate` lanza `TypeError` a mitad de camino, el servicio re-ejecuta **sin offer** → R-PRICE, binding de offer y merchant-match se saltan; igual patrón en `_spend`/`_reserve` (6 sitios `except TypeError` con re-llamada duck-typed) | `decision/service.py:193-221,365-366` |
| G-2 | **El gate aprueba sin offer de catálogo** (R-PRICE se salta; scope se evalúa sobre merchant/categoría auto-declarados por el intent). Contrato §2 exige `amount == offer` contra catálogo | `domain/policy.py:889-938`, `service.py:837` |

### Altos

| ID | Hallazgo | Evidencia |
|---|---|---|
| G-1 | Mismo `jti` con cuerpo distinto → APPROVED silencioso cuando `idempotency_store is None` (cae al dedupe por compra, sin comparar fingerprint ni emitir `fraud.alert`) | `service.py:822-824` |
| G-3 | **La moneda del offer nunca se compara**: offer EUR 50.00 aprueba con mandato/intent USD | `policy.py:921-938` (sonda G) |
| RT-4 | **Las escalaciones viven fuera del hash chain**: un mutador de BD puede extender `timeout_at` o alterar el `diff` que el humano firmó; `/audit/verify` sigue en verde | `escalation_flow.py`, `audit/service.py` solo cubre `audit_events` |
| RT-6 | **TOCTOU gate↔reserva**: el step-up se evalúa sobre el `SpendView` previo; la reserva re-valida presupuesto/count pero **no los ratios** 0.70/0.80 → esquivable con verifies concurrentes | `repository_postgres.py:445-465` |
| H-01..H-08 | **P0 huérfanas de brief** (asignadas a otros carriles, nadie ejecutándolas): R-PRICE post-captura+auto-refund, R-WEBHOOK completo, R-EVIDENCE pack, R-IDEM lado rail, F1.7 FraudNet, F1.8 fixtures de ataque, adaptador WebAuthn de UV (sin él todo L3+ es inaprobable), wiring HTTP + composition root | 0022 + plan; sin brief activo |
| P-01 | **Estado del gate volátil**: sin adaptadores Postgres de MandateReader/OfferCatalog/PurchaseStore/EscalationStore ni composition root, un reinicio o N instancias de Cloud Run anulan R-BURST, cooldown, reservas y R-IDEM (fail-open operativo en 4 controles P0) | `decision/repository_postgres.py` solo cubre velocity/idempotencia/reserva |

### Medios

| ID | Hallazgo | Evidencia |
|---|---|---|
| G-4 | Sin frescura del intent (`exp`, `iat`, `exp−iat ≤ 120 s` sin chequear) | `models.py:286-287` |
| G-5 | Puertos opcionales fallan ABIERTO: `velocity_store=None` apaga R-BURST en silencio; ídem idempotencia/purchase/offer; sin aserción de arranque | `service.py:199-213,304-307` |
| RT-5 | Aprobaciones sin identidad ni telemetría (sin approver, `receipt_sig` ausente en `escalation.resolved`, sin anti-fatiga por aprobador) | `service.py:724-818` |
| RT-7 | **Auto-suspensión blanda**: `auto_suspend` solo emite `fraud.alert`; `mandates.status` queda `active` y el mandato revive al rodar la ventana 1h. La transición L4 "3 escalaciones sin respuesta" no existe | `service.py:426-432` |
| RT-9 | **Tres JSON canónicos divergentes** (`domain/idempotency` permite floats; `audit/hashing` los prohíbe; `service._canonical_payload` usa `default=str`): el evidence-pack futuro fallaría en verificaciones cruzadas | 3 archivos, ver §4 |
| W-02..W-12 | WIP: C5 (tests db) ausente; 0/5 commits (blob único ~2.800 líneas); `purchase.requested` nunca emitido; status `pending_capture` fuera del enum del contrato; guard SQL añade `spent_total` (más estricto que el brief — desviación a ratificar); `release` PG ignora `reservation_id` (fake≠real); sin test de doble compra concurrente | brief vs `decision/*` |
| B-01..B-05 | Contrato-sin-código P0: verificación de intent en verify path (firma→KB→frescura→unicidad), cablear IdempotencyStore en `verify`, productor `purchase.requested`, transición `captured`+`purchase.captured` vía CapturePort, evento `root.checkpoint` al outbox | schemas.md §2/§4 |
| Outbox | `OutboxEvent` duplicado con divergencia (decision `as_dict()` 5 campos vs events `to_dict()` +seq/relayed_at validado); `OutboxWriter(tx)` vs `OutboxStore(poll)` sobre la misma tabla; `Clock` triplicado | `decision/ports.py:60-77` vs `events/ports.py` |
| P-05 | Relay: ventana SKIP LOCKED se cierra al commit del fetch (locks liberados antes de entregar); sin backoff/`attempts`/DLQ (mensaje envenenado); nadie arranca el relay (sin runner ni Cloud Run Job) | `events/relay.py:69-105` |
| P-06 | `sign_root` anota `root_sig` **antes** de publicar al witness: un fallo de GCS tras anotar deja un rango que nunca se republica; `get_all()` O(n); carrera de génesis (tabla vacía no lockea) | `audit/service.py:52-125` |
| P-04 | **Sin sweeper de escalaciones expiradas**: el fail-closed de 120/300 s solo ocurre si alguien lee la escalación; si el aprobador desaparece, nadie compensa ni emite `escalation.expired` | lazy check en `service.py:744` |
| TX | Sin test de carrera contra la reserva atómica (T5 real); sin test E2E gate→outbox→ledger; sin vectores dorados; `psycopg` no está en el pyproject de gate-core (solo en audit-evidence); marcador `gcp` no registrado | `src/api/tests/*` |

### Bajos / info

G-8 (replay de ESCALATED pierde `level/requires_uv`), G-9 (`save_response` fallido
convierte APPROVED→REJECTED con presupuesto retenido), G-10 (reserva PG sin dedupe
por `reservation_key`), G-11 (`decision_from_signals([])`→APPROVED; timeout y
rechazo humano comparten `ESCALATION_TIMEOUT_DENIED`), RT-8 (rotación de
mini-mandatos resetea el burst — residual por diseño per-mandato), RT-10
(re-gate denegado se clasifica como expiración, contamina evidencia), B-12
(api.yaml `ReasonCode` quedó en 18 — le faltan los 5 de v1.1), P-11 (vocabulario
extendido de `velocity_counters` sin documentar), W-05/06/07 arriba,
delta policy.py del coder: **correcto y seguro** (normaliza scope inválido →
fail-closed MERCHANT_NOT_ALLOWED; falta test de regresión).

---

## 2. Matriz de completitud del gate (resumen)

Enforced y bien testeado: `max_per_txn`, `total_budget` (con
spent+reserved+amount, re-chequeo atómico en reserva), `max_txn.count`, scope
(fail-closed, listas vacías deniegan), estado/validez del mandato, condiciones
JsonLogic-mini (operador desconocido → REJECT; floats literales prohibidos),
cooldown/burst completo (4º intent escala, in-cooldown rechaza, flood
auto-suspende, cap authz), step-up 0.70/0.80 con fronteras inclusivas, TTLs
120/300, UV fail-closed con stub, re-gate al resolver.

Parcial: `max_txn.period` (valida vocabulario, no correlaciona ventana),
R-PRICE (requiere offer — ver G-2; moneda no comparada — G-3), replay (ver
RT-2), L3+ (cerrojo cerrado: stub UV).

Ausente del gate/servicio: verificación criptográfica del intent
(JWS/SD-JWT/KB — frontera de Dev 3, pero el caso de uso Dev 2 B-01 debe
exigir un "intent verificado"), frescura del intent (G-4), nonce (G-5),
`first_escalation`/`fresh_agent_key` como disparadores L3+ de 0021 (parámetros
existen, el gate jamás los pasa — código muerto).

Regla de oro: los 23 reason codes están clasificados, completos y disjuntos.
Una tensión documentable: `VELOCITY_BURST` es corroborativo pero el gate
RECHAZA con él al violar cooldown (defendible — violar cooldown es estado
determinista—; recomendamos separar `COOLDOWN_ACTIVE` verdictivo en v1.x o
documentar la excepción en contracts §7).

## 3. Cobertura de amenazas y escalera (resumen)

- **T9 (DoS económico)** IMPLEMENTADO (reserva atómica + burst + cap authz).
- **T1, T4, T5, T3, T12** PARCIALES (falta: trigger de oferta nueva en step-up,
  telemetría/anti-fatiga del aprobador, mitad post-captura de R-PRICE,
  enforcement de replay, revocación entre decisión y captura).
- **T2, T6, T7, T8, T10** SIN CUBRIR en su frente principal (firma/KB, webhook
  entrante, evidence pack, vault-bindings, ceremonias UV) — todas dependen de
  briefs no emitidos (H-xx) más el par crítico RT-1/RT-2.
- Matriz M1–M13: sin trazabilidad hoy M3 (replay→REJECT+alerta), M4
  (capture≠aprobado→auto-refund), M6 (webhook sin firma), M13 (declines del
  rail) — M3 y M4 son graves porque sus reason codes ya están congelados en
  v1.1 (falsa sensación de cobertura).
- Escalera L0–L4: L0/L3 implementados; **L1 y L2 no existen** (P1, coherente
  con D-03); L3+ completo pero con el cerrojo UV; L4 solo rechazo pasivo — sin
  suspensión durable ni revocación (RT-7).

## 4. Costuras de integración (resumen)

Cadena feliz: HTTP(⚪ Dev 3 no ha empezado) → **R-IDEM(🔴 huérfano)** →
gate(✅) → stores memoria(✅)/Postgres(🟡 3 faltantes) → reserva(✅) →
**misma-tx(🔴 sin TransactionManager real)** →
**outbox misma-tx(🔴 writer transaccional inexistente)** → relay(🟡 código ok,
sin runner) → webhook firmado(🟡 ok, sin composición) → sinks SSE/bot(⚪ sin
dueño) → captura(⚪ CapturePort jamás invocado) →
**ledger(🔴 nadie alimenta el chain)** → witness(✅ en sí, sin scheduler).

Dirección de dependencias: correcta (domain puro; decision no importa
audit/events). Única arista cruzada events→audit (`canonical_json`,
`RootSigner`) — aceptable, pero exige **unificar la canonía** (RT-9):
proponemos `src/api/canonical.py` hoja compartida.

## 5. Decisiones que el equipo debe tomar (Q-01..Q-10)

1. **Q-01** Replay con cuerpo distinto: ¿409 `IdempotencyConflict` (actual) o
   REJECTED `DUPLICATE_JTI` + `fraud.alert` (semántica M3)? Afecta B-01.
2. **Q-02** ¿Se permite `purchase_id` supplycido por el caller, o server-side
   `purchase-{jti}` siempre? (condición de validez de la única traba de replay).
3. **Q-03** Guard SQL de reserva: ¿ratificamos la fórmula del código (incluye
   `spent_total`, más estricta y consistente con el dominio) como corrección
   del brief (W-07)?
4. **Q-04** `pending_capture` vs `charging`: ¿ampliamos el enum del contrato
   (v1.x aditivo) o adoptamos `charging`? ¿Y la columna
   `purchases.escalation_id` (registro de decisión + migración propia)?
5. **Q-05** Ownership `escalation.resolved/expired`: el contrato dice emisor 3,
   el WIP emite 2 — ¿corregimos contrato o cableado futuro?
6. **Q-06** `spent_total`: verify [2] es el único escritor según §6, pero el
   valor solo existe tras capturar (Dev 3) — ¿el capture llama un puerto Dev 2
   (`settle`) o registramos excepción?
7. **Q-07** Feed del ledger: sink que refleja outbox→ledger (recomendado:
   decision puro, atomicidad garantizada por el outbox) vs `ledger.append`
   dentro del servicio.
8. **Q-08** Idempotencia en runtime: ¿la reserva R-IDEM la hace el adaptador
   HTTP de Dev 3 antes de `verify()` (recomendado) o se integra a
   `DecisionService`? (Define B-02.)
9. **Q-09** `AVAL_LOCAL_SIGNER_PEM` ausente genera clave efímera en silencio y
   `IDEM_SECRET` defaultea a `"local-development-only"` — ¿fail-fast en prod
   con flag explícito de modo dev?
10. **Q-10** Suspensión durable: ¿`mandates.status='suspended'` lo escribe Dev 2
    directo (el contrato ya lo contempla como auto-pausa) o vía puerto de
    Dev 3?

## 6. Ruta crítica para el demo (≈40 h Dev 2)

Cerrar WIP + RT-1/RT-2/RT-3/G-2/G-3 (≈16 h) → integrar carriles con
OutboxEvent único + writer transaccional + composición (≈12 h esenciales) →
sweeper de escalaciones + runner de relay + reconcile mínimo (≈8 h) →
evidence-pack ensamblado del ledger (≈6 h, requiere merge). El resto
(disputas completas, EWMA, risk_lists, vectores dorados, L1/L2) es
post-demo salvo los vectores dorados (2 h, baratos y protegen el T9 en vivo).

Fuente completa de tareas y fases: [`../plans/2026-08-29-dev2-phase-evolution.md`](../plans/2026-08-29-dev2-phase-evolution.md).

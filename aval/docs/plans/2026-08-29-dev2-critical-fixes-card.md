# Tarjeta de fixes críticos — continuación del run decision-core (2026-08-29)

**Contexto:** derivada del análisis de brechas
([`../research/2026-08-29-dev2-gate-gap-analysis.md`](../research/2026-08-29-dev2-gate-gap-analysis.md))
y del plan
([`2026-08-29-dev2-phase-evolution.md`](2026-08-29-dev2-phase-evolution.md),
fase D2-C). **Se ejecuta DESPUÉS de cerrar C1–C5 del brief original**, sobre
el árbol limpio, como commits separados (uno por tarjeta, con devlog). Estas
tarjetas NO formaban parte del brief original — los hallazgos son de la
auditoría posterior.

Regla inamovible: nada de esto cambia la regla de oro (corroborativo jamás
rechaza). Todas las adiciones son fail-closed.

---

## Tarjeta 1 (RT-1, 2 h) — Eliminar el bypass de UV en L3+

- **Dónde:** `src/api/domain/policy.py` (`resolve_escalation`, firma con
  `uv_verified`), `src/api/decision/service.py` (`resolve_escalation`).
- **Problema:** aprobar un L3+ con `uv_verified=True` no requiere aserción
  criptográfica; si el endpoint de Dev 3 llena el flag desde el cliente, la
  UV es decorativa.
- **Fix:** quitar el parámetro `uv_verified` del camino productivo. La única
  vía de aprobación L3+ es `verifier.verify(assertion, challenge, max_age)`
  con `challenge = canonical_diff_digest(diff)`. Los tests pueden seguir
  inyectando un verifier falso (el stub `FailClosedUVVerifier` ya es el
  default fail-closed).
- **Test de regresión:** `resolve_escalation` de un L3+ con `uv_verified`
  eliminado — verificar que sin aserción REJECT y que el parámetro ya no
  existe en la firma (test con `TypeError` esperado o firma inspeccionada).

## Tarjeta 2 (RT-2/G-1/G-8, 6 h) — Replay: cablear idempotencia y jti

- **Dónde:** `src/api/decision/service.py` (`verify`, `_read_purchase_by_intent`,
  `_result_for_existing_purchase`), constructor de `DecisionService`.
- **Problema:** el `IdempotencyStore` existe y está probado pero nadie lo
  llama; `DUPLICATE_JTI`/`NONCE_REUSED` nunca se emiten; no se valida
  `exp`/`iat` del intent; un replay de una compra ESCALATED crea una segunda
  escalación (PK uuid4); el dedupe actual no compara fingerprint del cuerpo.
- **Fix (orden dentro de `verify`):**
  1. Derivar clave HMAC(jti) y `reserve_for(jti, scope="verify", body)`.
  2. Si el registro existe con misma huella → devolver la `response`
     almacenada **verbatim** (no reconstruida desde `PurchaseRecord`).
  3. Si huella distinta → REJECTED `DUPLICATE_JTI` + evento `fraud.alert`
     (kind `replay`), sin tocar gate ni reserva.
  4. Al cerrar el flujo (cualquier terminal), `save_response`.
  5. Eliminar el atajo `_read_purchase_by_intent` como defensa primaria
     (puede quedar como optimización tras el check de huella).
  6. Validar frescura del intent antes del gate: `exp` futuro, `iat` no
     futuro-más-allá-deriva, `exp - iat <= 120 s` (configurable), si el intent
     trae exp/iat; auditar rechazo con `DUPLICATE_JTI` NO aplica aquí — usar
     `MANDATE_NOT_YET_VALID`-style: añadir reason `INTENT_EXPIRED` es cambio de
     contrato → usar `CONDITION_FAILED` y documentar, o PR aditivo v1.x.
  7. Serializar `level`/`ttl_seconds`/`requires_uv` en la respuesta guardada
     para que el replay de ESCALATED no aparezca como L3 plano.
- **Nota:** la segunda-escalación-por-replay muere sola con el paso 1 (el
  replay nunca llega a `create_escalation`).
- **Tests:** retry idéntico → misma respuesta, sin segundo evento; mismo jti +
  cuerpo distinto → `DUPLICATE_JTI` + `fraud.alert` emitido; replay de
  ESCALATED → una sola escalación; intent expirado → REJECT determinista.

## Tarjeta 3 (RT-3/G-2/G-6, 5 h) — Fail-closed: sin downgrades ni re-runs

- **Dónde:** `src/api/decision/service.py` (`_evaluate`, `_spend`, `_reserve`:
  6 sitios `except TypeError` con re-llamada), `src/api/domain/policy.py`
  (`evaluate` sin offer, `evaluate_conditions`).
- **Problema:** si `gate.evaluate` lanza `TypeError` a mitad de camino se
  re-ejecuta **sin offer** (R-PRICE, binding de offer y merchant-match se
  saltan); `PolicyGate.evaluate` aprueba sin offer de catálogo evaluando scope
  sobre campos auto-declarados; `RecursionError`/`ensure_utc` escapan del gate.
- **Fix:**
  1. Borrar todos los `except TypeError` + re-llamada. Cualquier excepción del
     gate se convierte UNA vez en REJECTED determinista (`CONDITION_FAILED`)
     con el error logueado en el diff del evento — jamás re-intentada con
     menos insumos.
  2. En `PolicyGate.evaluate`: sin offer de catálogo ⇒ **ESCALATED**
     (`STEPUP_AMOUNT_THRESHOLD`-style o `CONDITION_FAILED`) — nunca APPROVED.
     (Dejar modo sin-offer solo para tests explícitos vía flag privado.)
  3. Comparar `offer.currency == intent.currency == mandate.currency`
     (case-insensitive); mismatch ⇒ REJECTED `CONDITION_FAILED`.
  4. Envolver `evaluate_conditions` para capturar `RecursionError` y cap de
     profundidad (p. ej. 32) / nodos (p. ej. 256) ⇒ `False` (fail-closed).
- **Tests:** offer ausente ⇒ no-APPROVED; moneda EUR vs USD ⇒ REJECT; condición
  con 10k nodos ⇒ REJECT rápido (sin RecursionError); TypeError inyectado en
  el gate ⇒ REJECT único (verificar que no hay segundo evaluate).

## Tarjeta 4 (G-5/C-7, 3 h) — Sin modos silenciosos

- **Dónde:** `DecisionService.__init__` / `verify`.
- **Problema:** `velocity_store=None` apaga R-BURST en silencio (contadores a
  cero); ídem idempotencia/purchase/offer.
- **Fix:** parámetros de puertos críticos obligatorios (sin default `None`) +
  aserción en arranque; si se necesita modo degradado para tests, flag
  explícito `allow_degraded=True` que emita warning y quede en el evento.
- **Test:** construir el servicio sin velocity_store lanza `TypeError`.

## Tarjeta 7 (RT-1-hermana, 2 h) — `approved_stepup` solo interno

- **Dónde:** `src/api/domain/policy.py` (`PolicyGate.evaluate`, nuevo parámetro
  `approved_stepup: bool = False` observado en el WIP del 2026-08-29).
- **Problema:** el flag tiene la misma forma de bypass que RT-1: si el path de
  `verify` (o el wiring futuro de Dev 3) puede pasarlo en `True`, el step-up
  completo (L3+/UV en montos ≥ 0.7×max o budget ≥ 80%) es esquivable con un
  argumento.
- **Fix (razonable si se acota):** `approved_stepup` es legítimo SOLO para el
  re-gate interno de `resolve_escalation`. Exigir: (1) `verify` jamás lo pasa
  (test que lo afirme, p. ej. inspección de la llamada o firma privada
  `_approved_stepup`); (2) `resolve_escalation` lo deriva del registro de
  escalación aprobado (status approved + binding al mismo intent/jti + TTL
  vigente), no de un argumento externo; (3) cuando el flag esté activo, la
  decisión resultante queda anotada en el diff del evento con
  `stepup_satisfied_by: escalation_id` para que el trail explique por qué no
  se exigió UV.
- **Test de regresión:** `evaluate(..., approved_stepup=True)` desde el path
  de verify es imposible (firma/llamada); un re-gate con escalación aprobada
  aprueba y el evento porta `stepup_satisfied_by`; un re-gate sin escalación
  aprobada vuelve a exigir step-up.

## Tarjeta 5 (RT-6/C-8, 3 h) — Ratios de step-up dentro de la reserva

- **Dónde:** `src/api/decision/repository_postgres.py` (`reserve`), fake análogo.
- **Fix:** extender el guard del UPDATE condicional con el ratio: Rechazar si
  `(spent_total + reserved_amount + amt) / total_budget >= 0.80` o
  `amt >= 0.70 * max_per_txn` cuando la escalación correspondiente no está
  aprobada. Parámetros de ratio desde `StepUpConfig` (una sola fuente).
- **Test:** dos verifies concurrentes + captura que empuje el gasto sobre 80%
  ⇒ la segunda reserva exige L3+ (o rechaza) — probar con fake threaded.

## Tarjeta 6 (C-9, 1 h) — Deudas menores del brief

- `purchase.requested` emitido al entrar a `verify` (misma tx).
- Status `pending_capture` → alinear con Q-04 (pendiente ratificación del
  equipo; mientras tanto dejar TODO y registro en devlog, no commitear el
  vocabulario nuevo como definitivo).
- Test de regresión del delta de `policy.py` (scope inválido ⇒
  MERCHANT_NOT_ALLOWED, no excepción).
- Test de doble compra concurrente (T5 en memoria con hilos).

---

**DoD de la continuación:** commits atómicos C1–C5 primero (brief original),
después un commit por tarjeta (`fix(gate): rt1 uv bypass`, etc.), suite 100%
verde con/sin `DATABASE_URL`, ruff 0, docs-guard OK, working tree limpio.
**Prohibido:** editar `src/api/canonical.py`, `src/api/evidence/`,
`src/api/tests/test_canonical_golden.py`, `src/api/tests/test_evidence_pack.py`
(ya ejecutados y commiteados por la lane de verificación).

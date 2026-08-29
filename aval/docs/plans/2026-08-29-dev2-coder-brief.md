# Coder brief — Dev 2 lane: núcleo de decisión (verify path, stores, T1)

**Para:** coder (agente o humano) · **Rama:** `dev2/gate-core` (HEAD `f4d9a69`) · **Emitido:** 2026-08-29 · **Verificación final:** Dev 2 (rango de commits + suite + disciplina de carril).
**Este archivo ES el prompt. Cópialo tal cual.**

---

## PROMPT — empieza aquí

Eres el coder de implementación del equipo **Dev 2 de Aval** (hackathon Yuno × Nauta 2026) — el carril de **fraude, contratos e idempotencia**: el núcleo de decisión del kernel. Nuestra misión: *nada fuera de mandato pasa jamás; los contratos firmados verifican de verdad; toda operación es exactly-once con evidencia criptográficamente verificable.*

Tu trabajo: completar las cuatro piezas pendientes de nuestro carril sobre la rama `dev2/gate-core`:
1. **T1 property-based** extendido a las reglas nuevas (R-BURST, R-STEPUP, R-PRICE, regla de oro).
2. **Wiring Postgres de `velocity_counters`** (el lado store de R-BURST).
3. **Integración del gate en el camino de verify** con reserva atómica y saga de compensación.
4. **Lado store de T19**: `IdempotencyStore` sobre `idempotency_keys`.

El dominio puro YA EXISTE y está verificado (`src/api/domain/`, 22 tests en verde). No lo rediseñes: úsalo.

### 0. Orden de lectura OBLIGATORIO antes de escribir una línea

1. `AGENTS.md` (raíz) — reglas del repo.
2. `aval/docs/devlogs/dev2.md` — nuestro devlog, incluida la entrada de extracción (qué hay y de dónde vino).
3. `aval/docs/decisions/0022-p0-ownership-split.md` y `0021-escalation-ttl-by-level.md` — tu alcance y los TTLs. Contexto adicional: decisiones #1 (sin modelo en enforcement), #4 (revocación/reserva sincrónica), #5 (JSON Logic), #10 (outbox) en `aval/DECISIONS.md`.
4. `aval/contracts/schemas.md` **v1.1** — §3 (`PolicyGate`, `SpendView`), §4 (eventos: quién emite qué), §5 (semántica de escalation resume — **el contrato más delicado**), §6 (DDL exacta y la convención de escritura cruzada), §7 (reason codes).
5. `aval/docs/plans/2026-08-29-fraud-implementation.md` — task cards F1.2/F1.4/F1.5 (las nuestras).
6. Código existente: `src/api/domain/policy.py`, `domain/models.py`, `domain/idempotency.py`, `src/api/repository.py` (boundary en memoria de C0), `src/api/config.py`, `src/api/db/schema.sql`, `src/api/tests/test_domain_gate.py`.

**Jerarquía de verdad:** contratos v1.1 > plan > devlog > tu criterio. Hueco real → impleméntalo fail-closed y repórtalo; jamás edites `aval/contracts/` ni `DECISIONS.md`.

### 1. Invariantes inviolables (violar cualquiera = implementación rechazada)

1. **Sin modelos/ML en el camino de enforcement** — todo determinista y reproducible.
2. **Regla de oro** (ya codificada en `domain/policy.py`, no la debilites): solo señales verdictivas producen REJECT; corroborativas solo ESCALATE.
3. **Fail-closed** en todo timeout, duda o error de infraestructura.
4. **El approval NUNCA es un bypass:** al resolver un escalamiento se RE-EJECUTA el gate completo (estado puede haber cambiado) — contratos §5.2.
5. **Reserva atómica:** `verify` [2] es el ÚNICO escritor de `mandates.reserved_amount/spent_total/txn_count_period`, vía UPDATE condicional (`WHERE status='active' AND ...`); cero filas actualizadas = rechazo. Sin SELECT-then-UPDATE.
6. **Exactly-once:** eventos de outbox y cambios de negocio en la MISMA transacción (decisión #10).
7. **Disciplina de carril — qué puedes tocar:** `src/api/domain/` (solo aditivo), `src/api/decision/` (nuevo, tuyo), `src/api/db/`, `src/api/tests/`, `pyproject.toml` (solo dev-deps justificados), `aval/docs/devlogs/dev2.md`. **Qué NO:** `router.py`, `main.py`, `schemas.py`, `config.py` (superficie Dev 3), `src/agent/`, `src/mocks/`, `aval/contracts/`, `DECISIONS.md`. La capa HTTP la cablea Dev 3 contra tus servicios.
8. **NO apliques el stash** que existe en el repo (`git stash list` lo muestra): es el archivo del trabajo de superficie de Dev 3; traerlo arrastraría el carril equivocado.
9. **No hagas push.** Dev 2 revisa al final.
10. Cada commit con código incluye su entrada en `aval/docs/devlogs/dev2.md` y pasa `bash scripts/docs-guard.sh origin/main`.

### 2. Estado actual (tu punto de partida)

- Rama `dev2/gate-core`, HEAD `f4d9a69`, working tree limpio, 22 tests en verde (`uv run pytest src/api/tests -q`).
- `src/api/domain/`: `policy.py` (PolicyGate puro, R-PRICE/BURST/STEPUP, TTLs 120/300, gold rule, UV stub fail-closed, `resolve_escalation` con re-gate), `models.py` (modelos + configs con defaults: burst 3/60 s/cooldown 10 min/authz 3/esc-h 5; stepup 0.70/0.80), `idempotency.py` (HMAC(jti), `make_record`, `validate_reuse`, TTL 45 días).
- `src/api/db/schema.sql`: DDL v1.1 COMPLETA ya aplicable — usa esas tablas, no inventes columnas. Si crees que falta una (p. ej. `escalations.level`), guárdala en el `diff` JSONB y repórtalo como propuesta; cambiar la DDL exige registro de decisión.
- `src/api/repository.py`: boundary en memoria de C0 (reléelo antes de decidir si extenderlo o sustituirlo con puertos propios en `decision/`).
- Stack: Python 3.13 + uv; deps: fastapi, httpx, pydantic, pydantic-settings, pytest, respx, ruff. Puedes añadir **hypothesis** (dev) para T1 — es la única adición permitida y está justificada por el propio test.

### 3. Arquitectura exigida (clean architecture, nuestro carril)

```
src/api/decision/          # NUEVO — propiedad Dev 2 (kernel decision core)
  ports.py                 # Protocolos: MandateReader, OfferCatalog, VelocityStore,
                          #   IdempotencyStore (si no reutilizas el de domain), OutboxWriter,
                          #   EscalationStore, PurchaseStore, Clock
  service.py               # DecisionService.verify(intent, ...) — el caso de uso
  reservation.py           # la reserva atómica (SQL guardado) + release de compensación
  escalation_flow.py       # creación con level/TTL, resolución con re-gate, expiración lazy
  repository_postgres.py   # implementaciones Postgres de los puertos (asyncpg o psycopg)
  repository_memory.py     # fakes en memoria para tests unitarios
src/api/tests/
  test_t1_properties.py    # C4
  test_verify_path.py      # C3 (fakes)
  test_stores.py           # C1+C2 (unit con fakes + marcados db)
  test_domain_gate.py      # existente — no romper
```

Reglas de dependencia: `decision/service.py` importa `domain/` y puertos, nunca SQL ni drivers directamente; `repository_postgres.py` es el único archivo que habla SQL; los fakes viven junto a los puertos para que Dev 3 y los tests los reutilicen. Dinero siempre `Decimal`/`str` (nunca float); timestamps UTC-aware; reason codes del enum del contrato; archivos < ~300 líneas; ruff verde; typing completo; logs estructurados con `trace_id` = `intent.jti`.

### 4. Plan de commits — cada uno una fase CERRADA y FUNCIONAL

Conventional commits. Cada commit: compila, suite en verde, devlog actualizado, guard en verde. Nada de WIP ni código muerto.

**C1 — `feat(decision): velocity store + burst wiring (R-BURST store side)`**
- Puerto `VelocityStore` con la operación que necesita R-BURST: contar intents del mandato en la ventana de 60 s, leer/cooldown activo, escalamientos de la última hora, autorizaciones abiertas.
- Implementación Postgres sobre `velocity_counters(mandate_id, counter, window, bucket_start, val)`: upsert atómico `INSERT ... ON CONFLICT ... DO UPDATE SET val = velocity_counters.val + 1` (nunca leer-escribir); buckets por minuto truncado; contadores `intents|escalations|amount_sum|open_authz`.
- Fake en memoria con semántica idéntica.
- Almacenar el cooldown como registro con `expires` (o contador dedicado) — consistente con la DDL, sin columnas nuevas.
- Tests unitarios con fake (transiciones de `evaluate_burst` alimentadas por el store) + tests `@pytest.mark.db` (skip sin `DATABASE_URL`).

**C2 — `feat(decision): idempotency store (R-IDEM store side, T19)`**
- `IdempotencyStore` Postgres sobre `idempotency_keys(key, scope, response, derived_from, expires_at, created_at)`: `reserve(record)` atómico (INSERT; conflicto de PK → leer existente y validar con `validate_reuse` de `domain/idempotency.py`: mismo fingerprint OK, distinto → `IdempotencyConflict` → 409 conceptual), `save_response(key, response)`, `get(key)`, y purge de expirados (lazy o job).
- La clave SIEMPRE es `derive_idempotency_key(jti, settings.IDEM_SECRET)` — el caller nunca la inventa.
- T19 lado store: (a) retry con mismo body devuelve la respuesta original sin re-ejecutar; (b) misma clave con body distinto → conflicto; (c) registro expirado permite reserva nueva; (d) TTL de 45 días persistido.

**C3 — `feat(decision): verify path with atomic reservation + escalation flow`**
- `DecisionService.verify(intent, ...)` orquestando: cargar mandato (puerto) → estado/validez → `PolicyGate.evaluate(mandate, intent, spend, now, offer)` con la offer del catálogo (puerto) → ramificar:
  - **REJECTED** → persistir compra `rejected` + evento `purchase.rejected` (outbox, misma tx).
  - **APPROVED** → reserva atómica: `UPDATE mandates SET reserved_amount = reserved_amount + $amt, txn_count_period = txn_count_period + 1 WHERE id = $id AND status = 'active' AND reserved_amount + $amt <= $total_budget` (los límites de count/period también como condición del WHERE o verificación post-update con rollback) → cero filas = `BUDGET_EXCEEDED`/estado → compra `pending_capture` + `reservation_id` + evento `purchase.verified`.
  - **ESCALATED** → SIN reserva (contrato §5.1) → crear `escalations` con `timeout_at = now + TTL(level)` (L3 120 s / L3+ 300 s, decisión 0021; el `level` va dentro del `diff` JSONB) + evento `purchase.escalated` (+ `risk.stepup_required` si aplica).
- `resolve_escalation(approval)`: valida el envelope con `domain/policy.resolve_escalation` (UV fail-closed para L3+ mientras el stub exista), **re-ejecuta el gate completo** y solo entonces reserva y continúa; REJECT/timeout → `escalation.expired` + compensación (release de reserva si la hubiera) + compra `compensated`.
- Expiración lazy: toda lectura de una escalación primero comprueba `timeout_at` → si venció, marca `expired` + compensa (fail-closed).
- Eventos que emite nuestro carril (contratos §4): `purchase.requested/verified/escalated/rejected`, `risk.stepup_required`, `agent.paused_cooldown`, `fraud.alert` — envelope `{event_id, type, aggregate_id, payload, created_at}` vía `OutboxWriter` en la MISMA transacción que el cambio.
- Tests con fakes: camino feliz, cada reason code del contrato (T4), TOCTOU de precio → REJECT (T20), burst → ESCALATED y rechazo en cooldown (T22), stepup L3+ con TTL correcto y timeout fail-closed (T23), aprobación que NO bypasea el gate (estado cambiado entre escalada y resolución → rechaza), doble compra concurrente → una sola reserva (con fake que simula la guardia; la carrera real va en los tests db).

**C4 — `test(decision): T1 property-based over the new rules`**
- `test_t1_properties.py` con Hypothesis y strategies para mandatos/intents/spend/offers (imports de `domain/models`).
- Propiedades mínimas: (1) **ningún intent fuera de mandato jamás aprueba** (amount>max, budget, count, scope, categoría, validez, condición JsonLogic falsa, price mismatch — la T1 original extendida); (2) regla de oro: ninguna señal corroborativa produce REJECT bajo ningún input generado; (3) fronteras de stepup (exactamente 0.7× aprueba con escalada L3+, un céntimo menos no escala); (4) transiciones de burst (3 previos escalan, durante cooldown siempre REJECT, expirado el cooldown se despega); (5) `derive_idempotency_key` inyectiva en jtis distintos y estable; (6) TTLs: L3 siempre 120, L3+ siempre 300.
- Registra el perfil `--hypothesis-profile=ci` (p. ej. 200 ejemplos) para que el CI sea determinista en tiempo.

**C5 — `test(decision): db-marked integration for stores + verify path`**
- Con `DATABASE_URL`: aplica `db/schema.sql` en una base temporal (o testcontainers si ya disponible — si no, skip limpio), corre los flujos de C1–C3 reales: upsert de velocity concurrente, reserva atómica bajo carrera real (dos tareas, una gana), outbox escrito en la misma tx (rollback del cambio → rollback del evento).
- Sin `DATABASE_URL`: los tests se marcan skip y la suite sigue en verde.

### 5. Variables de entorno

`DATABASE_URL` (solo integración), `IDEM_SECRET` (ya existe en `config.py`; úsala). Si necesitas algo nuevo (p. ej. `BURST_*`), decláralo en `decision/` con defaults de `domain/models` — **no edites `config.py`** (superficie Dev 3): pásalo por constructor desde el futuro wiring.

### 6. Definition of Done (autoverifícalo antes de declarar terminado)

- [ ] 5 commits C1–C5, en orden, cada uno verde con devlog.
- [ ] `uv run pytest src/api/tests -q` 100% verde (sin `DATABASE_URL`) y también con ella si está disponible.
- [ ] `uv run ruff check .` limpio.
- [ ] `bash scripts/docs-guard.sh origin/main` en verde en HEAD.
- [ ] `git diff origin/main...HEAD --stat` NO muestra: `src/api/router.py`, `src/api/main.py`, `src/api/schemas.py`, `src/api/config.py`, `src/agent/`, `src/mocks/`, `aval/contracts/`, `aval/DECISIONS.md`.
- [ ] El stash sigue intacto (no aplicado) y el working tree limpio.
- [ ] Sin push.

### 7. Informe final (formato obligatorio de tu último mensaje)

1. Tabla de commits (hash, fase, qué, tests añadidos).
2. Resultado de la suite (con y sin `DATABASE_URL`) y del guard.
3. Desviaciones del brief y por qué (los invariantes de §1 no son desviables).
4. Huecos/propuestas detectados (p. ej. columnas de DDL que faltarían, puertos que Dev 3 necesitará cablear).
5. Qué quedó como stub/pending y para quién (UV verifier → passkeys Dev 3; wiring HTTP → Dev 3).

## FIN DEL PROMPT

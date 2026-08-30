# Coder brief — Dev 2 fase paralela: evidencia y distribución (ledger + outbox relay)

**Para:** coder (agente o humano) · **Rama:** `dev2/audit-evidence` (ya creada para ti) · **Emitido:** 2026-08-29 · **Verificación final:** Dev 2.
**Ejecución paralela:** otro coder corre SIMULTÁNEAMENTE en `dev2/gate-core` sobre `src/api/domain/` y `src/api/decision/` (verify path, stores, T1). Ustedes NO comparten ni un archivo de código — la integración se hace por merge al final.

---

## PROMPT — empieza aquí

Eres el coder de la **fase paralela de evidencia y distribución del equipo Dev 2 de Aval** (hackathon Yuno × Nauta 2026). Nuestro carril (decisión 0019): fraude, contratos, idempotencia — y explícitamente el **ledger hash-encadenado con roots firmados por KMS** y el **outbox relay**. Esta fase construye la columna vertebral de evidencia del producto: el trail que los jueces ven romperse en vivo cuando alguien toca la base de datos, y el mecanismo que distribuye cada evento sin perder ni duplicar.

Tu trabajo, sobre la rama `dev2/audit-evidence` (haz `git checkout dev2/audit-evidence` — ya existe):

1. **Ledger append-only hash-encadenado** sobre `audit_events` (decisión #7).
2. **Roots firmados** con KMS `EC_SIGN_ED25519` + **witness externo** en bucket GCS versionado (decisiones #7/#15/#11).
3. **Verificación de cadena** — el caso de uso detrás de `/audit/verify` (T9, test nuestro).
4. **Outbox relay** con `FOR UPDATE SKIP LOCKED` (decisión #10) y sinks como puertos.

### 0. Orden de lectura OBLIGATORIO antes de escribir una línea

1. `AGENTS.md` (raíz).
2. `aval/DECISIONS.md` — #7 (forma del audit log: POR QUÉ hash chain + witness externo), #10 (outbox, no broker), #11 (Cloud Run/GCS), #15 (dónde viven las llaves: KMS para evidencia).
3. `aval/contracts/schemas.md` **v1.1** — §3 (Protocol `Ledger`: `append`/`verify_chain`/`sign_root`), §4 (envelope de eventos `{event_id, type, aggregate_id, payload, created_at}`), §6 (DDL de `audit_events` y `outbox` — es tu fuente de columnas).
4. `aval/docs/devlogs/dev2.md` — contexto del carril.
5. Código existente: `src/api/db/schema.sql` (tablas ya definidas — NO inventes columnas), `src/api/domain/` (solo para estilo; NO lo toques).

**Jerarquía de verdad:** contratos v1.1 > decisiones > tu criterio. Hueco real → fail-closed + reportarlo. Jamás edites `aval/contracts/` ni `DECISIONS.md`.

### 1. Invariantes inviolables (violar cualquiera = implementación rechazada)

1. **Append-only estricto:** jamás un UPDATE/DELETE de `prev_hash`, `hash`, `payload`, `type`, `mandate_id`, `created_at` en `audit_events`. La ÚNICA escritura posterior permitida es anotar `root_sig` en un checkpoint (guardada: `WHERE root_sig IS NULL` y sin tocar campos de cadena).
2. **Sin SELECT-then-INSERT desprotegido:** dos appends concurrentes no pueden bifurcar la cadena — serializa la cola (`SELECT ... ORDER BY seq DESC LIMIT 1 FOR UPDATE` dentro de la tx de insert, o advisory lock).
3. **Determinismo total:** el hash se computa sobre serialización canónica — `hash = sha256(canonical_json({mandate_id, type, payload, prev_hash, created_at}))` con `canonical_json` de claves ordenadas/compacta (mismo estilo que `domain/idempotency.py`), `created_at` UTC-aware **suministrado por la app** (nunca el DEFAULT de la BD, o el hash no es reproducible). `seq` queda fuera del hash: es orden de la BD, no contenido.
4. **El witness es la prueba:** un root que solo vive en nuestra BD no prueba nada contra nosotros (decisión #7) — `verify_chain` DEBE cruzar contra el objeto de GCS y fallar si difiere o no existe.
5. **Fail-closed** en toda duda de verificación (firma inválida, root ausente, payload mutado ⇒ verificación falla, nunca "pass por defecto").
6. **Relay at-least-once con sinks idempotentes:** el relay puede reentregar tras un crash; el contrato del sink es deduplicar por `event_id`. Nunca pierdas un evento silenciosamente: sink que falla ⇒ `relayed_at` sigue NULL y se reintenta con backoff.
7. **Disciplina de carril — qué puedes tocar:** `src/api/audit/` (nuevo), `src/api/events/` (nuevo), `src/api/tests/test_audit_*.py` y `test_events_*.py` (nuevos), `pyproject.toml` (deps justificadas + markers), `aval/docs/devlogs/dev2.md`. **Qué NO:** `src/api/domain/`, `src/api/decision/`, `src/api/tests/test_domain_gate.py` (territorio del coder paralelo), `src/api/router.py`, `src/api/main.py`, `src/api/schemas.py`, `src/api/config.py`, `src/api/repository.py`, `src/agent/`, `src/mocks/`, `aval/contracts/`, `DECISIONS.md`, `src/api/db/schema.sql` (las tablas ya están; si crees que falta una columna, repórtalo — cambiar DDL exige registro de decisión).
8. **No hagas push.** Dev 2 revisa al final.
9. Cada commit con código incluye su entrada en `aval/docs/devlogs/dev2.md` y pasa `bash scripts/docs-guard.sh origin/main`.

### 2. Estado actual (tu punto de partida)

- Rama `dev2/audit-evidence`, working tree limpio, stash vacío. Suite actual: `uv run pytest src/api/tests -q` → 22 passed (test_smoke + test_domain_gate — no los rompas).
- `src/api/db/schema.sql` ya tiene: `audit_events(seq BIGSERIAL PK, mandate_id, type, payload JSONB, prev_hash CHAR(64), hash CHAR(64), root_sig, created_at)` y `outbox(seq BIGSERIAL PK, event_id UNIQUE, type, aggregate_id, payload JSONB, relayed_at, created_at)`.
- Stack: Python 3.13 + uv; deps existentes: fastapi, httpx, pydantic, pydantic-settings, pytest, respx, ruff. Adiciones permitidas y justificadas: `google-cloud-kms` y `google-cloud-storage` (importación LAZY dentro de los adapters — la suite unitaria corre sin credenciales ni SDKs), `cryptography` (Ed25519 local para dev).
- Env por constructor/módulo (NO edites `config.py`): `DATABASE_URL`, `AVAL_KMS_KEY_RESOURCE` (p. ej. `projects/p/locations/southamerica-east1/keyRings/aval/cryptoKeys/audit-roots`), `AVAL_WITNESS_BUCKET`, `AVAL_LOCAL_SIGNER_PEM` (solo dev). Los tests sin GCP usan fakes.

### 3. Arquitectura exigida (clean architecture)

```
src/api/audit/            # NUEVO — propiedad Dev 2 (evidencia)
  hashing.py              # canonical_json + event_hash — PURO
  chain.py                # validación de secuencia — PURO (sin I/O)
  ports.py                # LedgerRepository, RootSigner, Witness, Clock
  models.py               # AuditEvent, ChainResult, RootCheckpoint
  repository_postgres.py  # append con tail-lock, rangos, anotación root_sig
  repository_memory.py    # fake con hook de tamper para tests
  signer_kms.py           # EC_SIGN_ED25519 (lazy google-cloud-kms)
  signer_local.py         # Ed25519 PEM para dev/sandbox
  witness_gcs.py          # bucket versionado (lazy google-cloud-storage)
  witness_memory.py       # fake
  service.py              # Ledger use case: append / verify_chain / sign_root
src/api/events/           # NUEVO — propiedad Dev 2 (distribución)
  ports.py                # Sink (EventBus, BotNotifier, WebhookPoster), Clock
  relay.py                # poller FOR UPDATE SKIP LOCKED + backoff
  sinks_memory.py         # fakes
  webhook_signed.py       # WebhookPoster real: httpx + firma vía RootSigner (#15)
src/api/tests/
  test_audit_hashing.py   # P1
  test_audit_repository.py# P2 (unit + @db)
  test_audit_signers.py   # P3 (unit + @gcp)
  test_audit_verify.py    # P4 — T9
  test_events_relay.py    # P5 (unit + @db)
```

Dependencias: `service.py` y `relay.py` importan puertos y `audit/hashing.py` (puro), nunca drivers; solo `repository_postgres.py`, `signer_*.py` y `witness_*.py` hablan con el mundo exterior. Dinero `Decimal`; timestamps UTC-aware; archivos < ~300 líneas; ruff verde; typing completo; logs con `trace_id`.

### 4. Plan de commits — cada uno una fase CERRADA y FUNCIONAL

**P1 — `feat(audit): pure chain algebra (hashing + validation)`**
`canonical_json` (claves ordenadas, compacta, sin floats — reusa el patrón de `domain/idempotency.py`, NO lo importes), `event_hash(...)`, validación de secuencia (cada `prev_hash` == hash anterior; recomputación de cada hash; detección de huecos de seq en el rango leído). Modelos `AuditEvent`/`ChainResult`/`RootCheckpoint`. Todo puro. Tests: hash estable, sensible a cada campo, cadena válida pasa, payload mutado/hash tocado/reordenación fallan.

**P2 — `feat(audit): postgres ledger repository (append-only, tail lock)`**
- `append(event)`: tx → `SELECT ... ORDER BY seq DESC LIMIT 1 FOR UPDATE` → computa `prev_hash`/`hash` → INSERT. Cero filas previas ⇒ `prev_hash` = 64 ceros (génesis, documéntalo).
- `annotate_root(seq_range, root_sig)`: UPDATE guardado (`WHERE root_sig IS NULL`) que jamás toca campos de cadena.
- Lecturas por rango y por mandate. Fake en memoria con `tamper(seq, field, value)` para tests de detección.
- Tests unit (fake) + `@pytest.mark.db` (skip sin `DATABASE_URL`): N appends secuenciales encadenan; **appends concurrentes no bifurcan** (dos tareas, la cadena queda lineal).

**P3 — `feat(audit): root signers + external witness`**
- Puerto `RootSigner.sign(data: bytes) -> bytes` + `verify(data, sig) -> bool`.
- `signer_kms.py`: `EC_SIGN_ED25519` vía `asymmetricSign`/`asymmetricVerify` (import lazy). `signer_local.py`: Ed25519 con PEM (dev/sandbox). Elección por env con **fail-closed si falta configuración en producción**.
- Puerto `Witness.put(checkpoint) / get(seq_range)`; `witness_gcs.py` (bucket versionado, objeto `roots/{seq_start}-{seq_end}.json`, content-type JSON, sin sobrescribir — el versionado es la inmutabilidad); `witness_memory.py`.
- Tests unit con fakes (firma verifica, mutación falla) + `@pytest.mark.gcp` (skip sin `AVAL_KMS_KEY_RESOURCE`/`AVAL_WITNESS_BUCKET`).

**P4 — `feat(audit): chain verification use case (T9)`**
- `Ledger.verify_chain(mandate_id=None | seq_range)`: relee eventos, revalida con `chain.py`, valida cada `root_sig` contra el signer, cruza cada checkpoint con el witness (presencia + igualdad byte a byte) → `ChainResult{ok, first_bad_seq, reason}`.
- `Ledger.sign_root(seq_range)`: computa root (hash del último hash del rango + cardinalidad, documéntalo), firma, anota, publica en el witness — todo fail-closed.
- **T9 (test nuestro, el del demo):** cadena de 25 eventos con 2 checkpoints → pass; mutar un payload (fake) → fail con `first_bad_seq` exacto; tocar un hash → fail; root firmado con clave equivocada → fail; witness divergente → fail; witness ausente → fail. Este test ES el guion del judge ("flip one byte, verification breaks on stage").

**P5 — `feat(events): outbox relay with SKIP LOCKED`**
- `Relay.drain(limit)`: tx → `SELECT ... WHERE relayed_at IS NULL ORDER BY seq LIMIT n FOR UPDATE SKIP LOCKED` → dispatch a sinks (uno por tipo según mapa tipo→sinks, con fallback "todos") → `relayed_at = now` SOLO para eventos entregados a todos sus sinks.
- Backoff por sink con reintento en el siguiente ciclo; error de sink no bloquea los demás eventos (aislamiento por evento).
- `webhook_signed.py`: POST httpx con header de firma `X-Aval-Signature: sha256=<sig>` sobre el body canónico usando el PUERTO `RootSigner` (decisión #15 — webhooks firmados con la llave de evidencia). Timeout corto, fail-closed.
- Tests: entrega en orden, reentrega tras fallo transitorio no duplica para un sink que deduplica por `event_id`, **dos relays concurrentes no entregan dos veces el mismo evento** (@db), evento sin sinks configurados queda marcado y auditado (report, no silencio).

### 5. Definition of Done (autoverifícalo antes de declarar terminado)

- [ ] 5 commits P1–P5, en orden, cada uno verde con devlog.
- [ ] `uv run pytest src/api/tests -q` 100% verde sin `DATABASE_URL`/GCP (markers db/gcp en skip limpio) y no rompes los 22 existentes.
- [ ] `uv run ruff check .` limpio.
- [ ] `bash scripts/docs-guard.sh origin/main` en verde en HEAD.
- [ ] `git diff --name-only dev2/gate-core...HEAD` SOLO muestra: `src/api/audit/`, `src/api/events/`, tus `src/api/tests/test_audit_*` / `test_events_*`, `pyproject.toml`, `uv.lock`, `aval/docs/devlogs/dev2.md`.
- [ ] Working tree limpio, `git stash list` vacío, sin push.

### 6. Informe final (formato obligatorio de tu último mensaje)

1. Tabla de commits (hash, fase, qué, tests añadidos).
2. Resultado de la suite (sin y con `DATABASE_URL`/GCP) y del guard.
3. Desviaciones del brief y por qué (los invariantes de §1 no son desviables).
4. Propuestas detectadas (columnas de DDL que faltarían — p. ej. contador de reintentos del outbox —, puertos que Dev 3/4 necesitarán cablear).
5. Qué quedó como stub/pending y para quién (SSE real y bot → Dev 4; HTTP wiring de `/audit/verify` → Dev 3).

## FIN DEL PROMPT

# Integración del puente Rappi y salida a prod

**Fecha:** 2026-08-30 · **Autor:** Dev 2 · **Estado:** fase 1 construida y
testeada (30 tests nuevos; suite completa 307 passed / 68 skipped-db). Este
plan ejecuta la decisión
[`0030`](decisions/0030-rappi-bridge-as-merchant-rail.md) sobre la evidencia
del run real (`research/2026-08-30-dev2-rappi-live-run-findings.md`).

## 1. Qué existe ya (commiteado)

| Pieza | Path | Rol |
|---|---|---|
| Guard bridge completo | `src/rappi_bridge/` (config, rappi, guard, state, token, service, app) | El único componente que toca Rappi; corre en la máquina de credenciales; DRY_RUN default, cap hardcodeado, dirección bindeada al mandato, drift centavo-a-centavo, single-flight SQLite que jamás re-cliquea un `uncertain` |
| Capture token (mint) | `src/api/decision/capture_token.py` | El kernel firma `capture-token+jwt` (TTL 120 s) bindeando `purchase_id + reservation_id + amount + cart_hash + dry_run` |
| Capture token (verify) | `src/rappi_bridge/token.py` | El puente valida firma/typ/TTL/todos los bindings contra el JWKS del kernel — sin token válido no se arma el checkout |
| Puerto `MerchantBridge` | `src/api/decision/ports.py` | Contrato del edge de ejecución junto a `CapturePort` (aditivo) |
| Contrato | `aval/contracts/rappi-bridge.yaml` | Superficie HTTP, máquina de estados, eventos outbox aditivos, invariantes |
| 30 tests | `src/rappi_bridge/tests/` | Roundtrip de token y 8 rechazos; cap/drift/dirección/carrito; single-flight y carrera ARMED→CLICKED; flujo dry-run, live, replay, mínimo de tienda reintentable, `uncertain` no re-cliqueable, kill switch |

## 2. Arquitectura de integración

```
Agente (propone) → kernel (PolicyGate → reserva → step-up humano → CAPTURE TOKEN)
                                                        │
Cloud Run / laptop (kernel :8001) ──── capture_token ──► ▼
                                        aval-rappi-bridge (:8010, 127.0.0.1)
                                        guardas: cap · address · drift · single-flight
                                                        │ httpx (headers web)
                                                        ▼
                                        services.grability.rappi.com (sesión vaulted)
```

El flujo completo de una compra real queda:

1. `POST /v1/rappi/quote` (puente) → Quote canónica (`total`, `cart_hash`,
   `return_key`) — **nunca precios de búsqueda** (drift medido: 9.400 → 10.300).
2. Agente firma intent con `amount = quote.total` → `POST /mandates/{id}/verify`
   → PolicyGate → reserva atómica → o `APPROVED` o `ESCALATED(L3+)`.
3. Si escapa: passkey WebAuthn del dueño resuelve la escalación → re-gate →
   reserva. **La aprobación humana es la llave.**
4. Kernel mintea el capture_token y llama `POST /v1/rappi/place_order`
   (header `Idempotency-Key = HMAC(idem_secret, jti)`).
5. Puente: single-flight → sesión → `cart_hash` actual == aprobado → token
   válido → cap/dirección/drift → `armed` → click → receipt → kernel cierra
   `pending_capture → captured` → evidence pack (screenshots locales hasheados).

## 3. Topología de prod

**La sesión de Rappi no sube a la nube** (invariante 1 + custodia). El puente
vive en la máquina de credenciales, siempre con conexión saliente:

| Topología | Cuándo | Cómo |
|---|---|---|
| **A — todo local** (demo/hackathon) | Default | kernel + bridge + Postgres en compose local; el bridge une las dos mitades en localhost |
| **B — kernel Cloud Run + bridge local** (prod del demo) | Cuando `trytrust.lat` esté arriba | Túnel saliente efímero (`cloudflared tunnel --url http://127.0.0.1:8010`) + `AVAL_BRIDGE_LOCAL_TOKEN` (bearer compartido por Secret Manager) + `AVAL_BRIDGE_KERNEL_JWKS_URL=https://api.trytrust.lat/.well-known/jwks.json`. El túnel solo abre durante la demo |
| C — pull-worker | Si B se considera superficie demais | El bridge polla `GET /bridge/jobs` del kernel; sin URL pública (asíncrono; segundos extra en el peor momento) |

Lo demás del stack ya está en `iac/cloudrun` (LB + Armor, Cloud Run jobs,
Secret Manager, CI/CD): **nada del puente cambia el despliegue del kernel**.

## 4. Variables de entorno (máquina de credenciales)

| Var | Default | Nota |
|---|---|---|
| `AVAL_BRIDGE_SESSION_FILE` | `secrets/rappi-config.json` | Token `ft.` del login del dueño; chmod 600, gitignored, rotar ≤72 h |
| `AVAL_BRIDGE_DRY_RUN` | `true` | El flip a real es del operador, jamás negociable por prompt |
| `AVAL_BRIDGE_ENABLED` | `true` | Kill switch: todo responde `BRIDGE_DISABLED` (423) |
| `AVAL_BRIDGE_MAX_ORDER_COP` | `50000.00` | Cap de la máquina, independiente del mandato |
| `AVAL_BRIDGE_KERNEL_JWKS_URL` | `http://127.0.0.1:8001/.well-known/jwks.json` | En topología B: la URL pública del kernel |
| `AVAL_BRIDGE_LOCAL_TOKEN` | — | Bearer opcional kernel→bridge (obligatorio en B) |
| `AVAL_BRIDGE_STATE_DB_PATH` | `var/rappi-bridge/state.sqlite3` | Checkpoint single-flight |

## 5. Lo que falta para cerrar la integración (brief Dev 3 / próximo run)

1. **Mint en el kernel:** al pasar a `pending_capture` (tras APPROVED o
   `resolve_escalation`), firmar el capture_token con la llave issuer ya
   existente y exponer `GET /.well-known/jwks.json` (misma llave que firma
   receipts — verificar qué emitter usa `escalation-receipt+jwt`).
2. **Endpoint de captura:** `POST /purchases/{id}/capture` (router Dev 3)
   → guarded-update del purchase → llamada `MerchantBridge.place_order` →
   `captured` + evento `purchase.captured`; drift → liberar reserva + re-quote;
   `uncertain` → `ChargeSettlementError` semántica.
3. **Eventos outbox** según `rappi-bridge.yaml` (`purchase.placed`,
   `bridge.price_drift`, `purchase.dry_run_captured`).
4. **Evidence pack:** añadir `bridge_artifacts[{step, png_sha256}]` al pack
   (puerto `ArtifactStore`); los PNG quedan locales, solo hashes viajan.

Estimación: 1–1.5 días de pareo Dev 2/Dev 3. Todo es aditivo; `api.yaml`
congelado no se toca.

## 6. Runbook de la máquina de credenciales

```bash
# 0. sesión (una vez cada ≤72 h; OTP llega al teléfono del dueño)
uv run python -m src.rappi_bridge.app          # arranca :8010 en 127.0.0.1
curl -s localhost:8010/healthz                 # {"ok":true,"dry_run":true,...}
curl -s localhost:8010/v1/rappi/session/preflight

# 1. carrito (por ahora vía CLI auditado; luego acción del agente)
# 2. cotizar — la ÚNICA fuente de precio permitida
curl -s -X POST localhost:8010/v1/rappi/quote
# 3. kernel: verify → (escalación/passkey) → pending_capture → capture_token
# 4. captura (el kernel llama; el operador puede forzar DRY_RUN ensayando)
curl -s -X POST localhost:8010/v1/rappi/place_order \
  -H "Idempotency-Key: <hmac-jti>" -d '{purchase_id, amount, cart_hash, capture_token, expected_address_id}'
# 5. verificación
curl -s localhost:8010/v1/rappi/orders/<idem_key>
```

Ensayos: F1 DRY_RUN ×6 (incluye 1 replay y 1 drift forzado) → F2 1–2 pedidos
reales con cap en cuenta secundaria → F3 demo. Plan de ensayos completo en
`research/2026-08-30-dev2-rappi-bridge-analysis.md` §7.

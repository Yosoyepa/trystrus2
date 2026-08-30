# Caso de uso: puente Rappi — compra real end-to-end sin API pública

**Fecha:** 2026-08-30 · **Autor:** Dev 2 · **Entradas:** 4 reportes de expertos
(mecánica de integración MCP/navegador, seguridad e identidad, arquitectura del
núcleo, operación de demo). Estado: análisis completo; construir queda pendiente
de las decisiones Q-R1..Q-R8 (§10).

## 0. Veredicto ejecutivo

**Viable como demo de hackathon, con una condición arquitectónica y una
condición de seguridad.** La condición arquitectónica: Rappi **no es un rail
estilo Yuno** — la tarjeta ya vive vaulteada en la sesión de Rappi, así que el
click final del puente **ES la captura**. El patrón correcto es
`ChargeService`/`settle` (kernel-only, decisión firmada), no
`AsyncPaymentRail` (firma congelada, decisión #24). La condición de seguridad:
el `storage_state` de la sesión es control total de la cuenta (tarjeta
incluida) y hoy no tiene custodia definida — es material crítico.

Recomendación: **puente de dominio propio** (`aval-rappi-bridge`, Playwright
headful + perfil persistente, 6 acciones cerradas) en la máquina de
credenciales, `place_order` gate-bound por **capture_token** (JWT ES256 del
kernel, TTL 120 s, binding `purchase_id+amount+cart_hash`), **DRY_RUN por
defecto**, screenshots hasheados al evidence pack. Fallback calentado:
`@playwright/mcp` oficial. Descartadas para demo: app Android y replay de API
privada. La web de Rappi carga **sin muro anti-bot visible** (verificado), el
checkout web funciona sin app, y el portal de desarrolladores es solo B2B
merchants — no hay API de consumidor.

## 1. El caso de uso y por qué es distinto de Yuno

El happy path del demo es Rappi, que no expone API buyer-side
(`aval/docs/PROTOCOLS.md` ya lo documenta; el dev portal es seller-side B2B).
La propuesta: la máquina física en Colombia ya tiene sesión real de Rappi con
tarjeta y dirección guardadas; el agente Aval se conecta a esa sesión (MCP u
otra vía) para explorar categorías, buscar bajo la identidad del dueño y
ejecutar pedidos reales.

Consecuencia estructural que fija todo el diseño: en Yuno el cobro era una
llamada a un procesador; aquí **el merchant es el procesador**. El puente no
"cobra vía rail": navega la sesión hasta la pantalla final y hace click, y ese
click es la captura. Por tanto:

- El invariante 1 ("el agente jamás ve el PAN") se cumple **por construcción
  de Rappi**: el DOM solo muestra `•••• last4`; el puente hace click, nunca lee
  tarjeta. Riesgo residual real: si el checkout pide **CVC**, automatizarlo
  exigiría almacenar el CVV → **fail-closed: abortar**, jamás autofill (§6).
- La saga existente termina en `pending_capture`: este diseño cierra la mitad
  faltante (capture → `purchase.captured`).

## 2. Evidencia empírica recogida (2026-08-30)

| Verificación | Resultado |
|---|---|
| `GET https://www.rappi.com.co` (externo) | 200 OK, SPA híbrida SSR+hidratación; **sin marcadores DataDome/PerimeterX/Akamai/Cloudflare** ni captcha |
| Web sin app | Checkout web completo funcional (buscar → carrito → checkout); existe además `web.rappi.com.co` ("Rappi Lite", DOM más simple, mismo OTP) |
| Login | OTP por SMS/WhatsApp al teléfono del dueño; sin email/contraseña en el flujo visible |
| Scrapers públicos | Existen scrapers Selenium de Rappi en GitHub → a bajo volumen la presión anti-bot del storefront es de facto baja |
| API oficial | dev-portal.rappi.com: solo Orders/Menus/Stores B2B con aprobación "Rappi ally". **No existe API de consumidor** |
| ToS Colombia (legal.rappi.com.co) | §7(ii) no transferir credenciales a terceros; §7(xv) cláusula paraguas contra conductas que afecten la plataforma; §7(xx) custodia del dispositivo; **§10 bloqueo y cancelación silenciosa de pedidos en check antifraude**; §23 **productos por peso cobran distinto al total mostrado** |
| Cancelaciones | Auto-cancelación sin costo solo mientras la tienda no ha confirmado (y sin repartidor asignado); vía "Ayuda" en el tracking |
| Repo | Ya existen: `src/merchant/mcp_server.py` (patrón MCP con `<merchant-data>` y lista `refused`), `src/merchant/charge.py` (`ChargeService` kernel-only), `src/agent/net.py` (egress allowlist), `src/agent/scrub.py` (PAN/CVV/teléfono; **sin** direcciones ni imágenes), kernel check `intent.amount == precio` y reserva CAS. **No existe** ningún `DRY_RUN` ni código Rappi (verificado con grep) |

## 3. Vías evaluadas y decisión

| Vía | Descripción | Veredicto |
|---|---|---|
| 1. MCP navegador genérico (`@playwright/mcp`) | `npx @playwright/mcp` (stdio o `--port 8931`), `--user-data-dir` de perfil persistente o `--isolated --storage-state=...`; headed por defecto | **FALLBACK.** Máxima flexibilidad, narrativa MCP directa, pero expone ~25 tools incluidas acciones destructivas (cambiar dirección/tarjeta, `browser_run_code_unsafe` ≈ RCE de sesión): "solo compra lo que el mandato dice" pasaría de código a convención de prompt |
| 2. Puente de dominio propio | Playwright embebido + perfil persistente + **6 acciones cerradas** (`search`, `get_item`, `add_to_cart`, `quote`, `place_order`, `order_status`) | **PRIMARIA.** Única vía donde los invariantes se cumplen **por construcción**: sin verbo para dirección/tarjeta/cupón, el total lo decide el kernel, screenshots nativos por paso |
| 3. App Android (Appium/adb) | Emulador o teléfono real, driver UiAutomator2 | **Descartada.** 1–2 días de build para replicar lo que la web da; es la superficie más vigilada por anti-fraude (detección emulador/root de RappiPay) |
| 4. Replay API privada (mitmproxy) | Interceptar endpoints internos `ms-*` y reusar el bearer del localStorage | **Descartada.** Fragilidad + riesgo real de ban + narrativa tóxica ante jueces. Único uso tolerable: observación pasiva de solo-lectura en desarrollo, jamás para colocar pedidos |

Notas de la vía 1 como fallback: reuso de sesión sin tocar la contraseña vía
`--user-data-dir` (lock exclusivo: un navegador a la vez) o
`--storage-state`; se mantiene calentada por si los selectores del puente
fallan a última hora. `browser-use` se prefiere **no** usar: trae LLM propio
dentro del loop de ejecución (roza la decisión 0016).

## 4. Arquitectura recomendada: "merchant con cobro embebido", no un rail

### 4.1 Secuencia de enforcement

```
AGENTE (propone)         KERNEL (decide, determinista)        BRIDGE RAPPI (actúa la sesión)
────────────────         ─────────────────────────────        ──────────────────────────────
1. search/cart (MCP) ──► (lectura no pasa por el kernel)
2. carrito armado ────────────────────────────────────────► 2. parsea carrito → Quote canónico
                                3. Quote{quote_id, items[],    cart_hash = sha256_hex(cart)
                                   total COP, cart_hash,          + screenshots
                                   expires_at ≤ 300 s}
3. intent firmado      4. verify(): idempotencia HMAC(jti) →
   (amount = total del    mandate_reader → PolicyGate:
   quote, offer=quote_id)    price_check exacto; scope
                             merchants=["rappi"]; step-up
                             0.70/0.80 → APPROVED+reserva
                             atómica | ESCALATED(L3/L3+)
5. (si ESCALATED) run parks en await_human → passkey WebAuthn del dueño →
   /escalations/{id}/resolve → re-gate completo (approved_stepup=True) →
   recién entonces reserva → pending_capture. "El silencio nunca aprueba".
6. POST /purchases/{id}/capture ◄── kernel mintea capture_token
   (JWT ES256 issuer, typ=capture-token+jwt, TTL 120 s,        7. place_order:
   claims purchase_id+amount+cart_hash)                          session_ok → cart_ok(cart_hash)
                                                                 → approval_verified (JWKS)
                                                                 → armed (screenshot)
                                                                 → [DRY_RUN → no-op, §4.5]
                                                                 → CLICK "Pedir" → order_id
                                8. receipt{order_id, total, cart_hash} → settle de la
                                   reserva → purchase.captured → outbox → SSE/webhooks
9. evidence pack: quote screenshots + intent + decisión + receipt + ledger slice
   + witness root + bridge_artifacts[{step, png_sha256}]
```

No negociables: (a) el step-up ocurre **antes** del cobro y antes de armar la
pantalla final; (b) el click es el único acto de dinero y lo dispara
únicamente `POST /purchases/{id}/capture` con capture_token verificable por el
puente contra el JWKS del kernel (espejo de `src/yuno_sim/ap2_verifier.py` y
`src/merchant/kernel_client.py`); (c) el monto que entra al gate es el del
**quote del puente**, nunca uno propuesto por el modelo.

### 4.2 Quote-vs-Charge: el drift se rechaza, jamás se "aprueba igual"

Tres puntos de verificación del monto:

1. **Gate (ya existe, costo cero):** un `RappiQuoteCatalog` implementa
   `OfferCatalog.get(quote_id)` devolviendo `Offer(amount=quote.total)`;
   `price_check` compara a centavo exacto (verdictive → `CONDITION_FAILED`).
   Tolerancia por defecto: **0 centavos**. Productos por peso (ToS §23) fuera
   del happy path: solo restaurantes de precio fijo.
2. **Checkout (el drift real):** `place_order` re-parsea la pantalla de resumen
   justo antes del click; si `sha256_hex(carrito_visible) != cart_hash` del
   quote → `409 BRIDGE_PRICE_DRIFT` con quote nuevo, **sin mirar siquiera el
   capture_token**. El kernel libera la reserva y el agente debe construir
   intent nuevo (jti nuevo → idempotencia nueva → gate de cero; si el total
   cruza umbral, **re-escala**). Nunca se reusa la aprobación humana de un
   carrito para cobrar otro (el capture_token bindea `cart_hash`).
3. **Receipt:** `total_captured` + `cart_hash` de la confirmación vs intent;
   discrepancia (no debería ocurrir) → `purchase.disputed_precheck` +
   `fraud.alert`.

### 4.3 Idempotencia contra un merchant sin API

Tres capas (un retry jamás duplica un pedido real):

- **Kernel (ya existe, no se toca):** `HMAC-SHA256(idem_secret, jti)`, TTL 45
  días; el `Idempotency-Key` viaja como header opaco; el secreto nunca sale del
  kernel.
- **Puente (checkpoint por paso):** `bridge_orders` (SQLite local,
  `idem_key PK, cart_hash, state, order_id, receipt_json`) con máquina de
  estados `received → session_ok → cart_ok → approval_verified → armed →
  clicked → confirmed | failed(stage) | uncertain`. `INSERT … ON CONFLICT DO
  NOTHING` = single-flight: un solo ejecutor puede llegar a clickear. Retry en
  `confirmed` → replay del receipt; retry `failed` **pre-click** → re-ejecuta
  desde ese paso (sin dinero movido).
- **Ventana de crash (`clicked` sin `order_id` = `uncertain`):** regla
  **nunca re-clickear**. `reconcile.py` abre "Mis pedidos", adopta el
  `order_id` por (tienda, total exacto, ventana, cart_hash) → `confirmed`; si
  no resuelve → `502 BRIDGE_UNCERTAIN`, que el kernel trata como
  `ChargeSettlementError`: el retry con el mismo `purchase_id` cae en
  `order_status` y lo residual va a reconciliación humana con evento
  `bridge.reconciled` en el outbox.

### 4.4 Step-up humano: la passkey libera el click

Mandato COP con `max_per_txn` y ratio 0.70 → un pedido de ~COP 38.500 escala a
L3 (120 s). El run duerme en `await_human`; el dueño aprueba con passkey WebAuthn
con UV; `resolve_escalation` re-ejecuta el gate completo y solo entonces
reserva → `pending_capture`. El kernel mintea el capture_token y el puente lo
verifica contra el JWKS **antes** de armar la pantalla final: la aprobación
humana es literalmente la llave que destraba el click. Demo de replay incluido:
reintentar el token tras 120 s → `BRIDGE_APPROVAL_EXPIRED`.

### 4.5 DRY_RUN por defecto

`AVAL_RAPPI_DRY_RUN=true` (default) en el puente, espejado kernel-side; el
valor se sella **dentro** del capture_token para que kernel y puente no puedan
discrepar. Ejecuta todo el flujo real —sesión, búsqueda bajo identidad del
dueño, carrito, quote, verificación del token— y sustituye el click por un
no-op con receipt `{dry_run: true, simulated: true, order_id: null,
stopped_at}` (misma honestidad que `simulated: true` del yuno_sim, decisión
#24). Status aditivo `dry_run_confirmed`; reserva liberada; evidence pack
normal con los screenshots **incluida la pantalla donde se detuvo** — el demo
muestra exactamente dónde terminaría el dinero. El flip a real es una variable
de entorno del operador, no negociable por prompt.

### 4.6 Topología

| | A: todo local (recomendada para demo) | B: kernel Cloud Run + puente local con túnel | C: pull-worker sin túnel |
|---|---|---|---|
| Esquema | laptop: kernel :8001 + bridge :8010 + Postgres docker; witness GCS por salida (funciona) | kernel en `southamerica-east1`; puente expone HTTPS saliente (cloudflared/tailnet) efímero + bearer | puente polla jobs (`GET /bridge/jobs` → ejecuta → `POST result`) |
| Pros | latencia ~0; passkeys en localhost hoy; cero superficie pública | auditoría ya en la nube (iac/) | sin URL pública |
| Contras | la demo vive en una LAN | túnel = URL pública hacia la máquina de la tarjeta | `place_order` asíncrono; segundos perdidos en el peor momento |

Para el hackathon: **A**. Documentar B (túnel saliente con bearer + egress
allowlist, o tailnet) como camino de producción y C como fallback si seguridad
de red lo exige.

## 5. Guardarrails bloqueantes antes del primer pedido real

1. ☐ `storage_state`/perfil **fuera del repo** (`chmod 600`, `.gitignore`
   `rappi-state*.json`, cifrado en reposo, nunca a Cloud Run/CI); rotación:
   re-login la mañana del demo, logout remoto al terminar; vida útil ≤72 h.
2. ☐ Kill switch (`AVAL_RAPPI_ENABLED=0`) + **DRY_RUN por defecto** con flip
   manual del operador.
3. ☐ Cap hardcodeado en el puente (`AVAL_RAPPI_MAX_COP`, p. ej. 50.000): el
   puente se niega a llegar a la pantalla de confirmación por encima del cap
   **aunque traiga aprobación válida** — defiende contra mandato mal
   configurado, no solo contra agente comprometido.
4. ☐ API de acciones cerrada: sin clicks por coordenadas/selector del LLM;
   **sin verbos** para cambiar dirección, guardar tarjeta, editar perfil o
   canjear cupón (replicar la lista `refused` de `merchants_mcp.py`).
5. ☐ Pre-flight de sesión (home autenticada sin redirect a login) + abort
   fail-closed ante pantalla de login/OTP o **campo CVC** (jamás autofill).
6. ☐ Marcador persistente "checkout intentado" **antes** del click final +
   bloqueo de re-entrada (anti doble pedido, §4.3).
7. ☐ Página → LLM solo JSON estructurado, recortado y cercado
   `<merchant-data>`; jamás HTML crudo, cookies, tokens, dirección o teléfono
   en un prompt; screenshots nunca salen de la máquina.
8. ☐ Regla kernel-total: click final solo si total de página == total
   preparado por el kernel (drift → §4.2).
9. ☐ Allowlist de dominios **por propósito** (hosts Rappi solo desde el
   transporte del puente, no en `TT_ALLOWED_HOSTS` global) + presupuesto de
   acciones por pedido (reusar `TT_MAX_STEPS_PER_RUN`).
10. ☐ Test anti-PAN: escanear DOM y screenshots del flujo completo por
    secuencias de 13–19 dígitos (estilo E10) — y decidir tratamiento PII de
    los screenshots (Q-R5).

## 6. Seguridad y postura ToS

- **Custodia:** el `storage_state` es takeover total de la cuenta (tarjeta
  guardada, direcciones, créditos). Ensayos en **cuenta secundaria propia con
  tarjeta de saldo controlado**; demo final en la primaria, 1–2 pedidos.
- **Prompt injection:** la UI (nombres, reseñas, cupones) es adversaria para
  automatización naive. Con API cerrada, un inyectado solo puede elegir entre
  verbos que ya existen; el gate rechaza propuestas envenenadas con razón
  verídica y queda en el ledger. El parseo del carrito y la comparación de
  montos son **deterministas, sin LLM** (0016): el LLM solo ranknea resultados
  (propone) y nunca toca el monto ni el click.
- **PII:** `src/agent/scrub.py` cubre PAN/CVV/teléfono pero no direcciones ni
  imágenes; el evidence pack es 100% JSON hoy. Los screenshots (contienen
  dirección + nombre) quedan como artefacto local gitignored o entran al pack
  con blur previo — decisión Q-R5.
- **ToS (postura honesta, no normalizada):** no hay cláusula anti-robots
  expresa, pero §7/§10 aplican; el riesgo operativo dominante es la
  **cancelación silenciosa del pedido por el check antifraude** en pleno demo
  (plan B: rail simulador como demo primaria, Rappi como bono; ensayo completo
  el día anterior). Producción = Rappi Partners, no self-serve. En slides:
  "prototipo de automatización personal del propio dueño", nunca "integramos
  Rappi".
- **Ritmo legítimo (no evasión):** headful, misma máquina/IP de siempre,
  ritmo humano, cero paralelismo, sin multi-cuenta ni rotación de
  fingerprint — eso último queda fuera por decisión explícita.

## 7. Operación del demo (resumen; runbook completo en el reporte de ops)

- **Guion <6 min:** mandato pre-creado (techo 60k/txn, 200k total, scope
  rappi) → búsqueda "pizza" con navegador Rappi en pantalla → intent con
  total del quote → escalación L3 visible con countdown → **passkey del dueño
  en vivo** → capture_token → confirmación real con order_id → tracking del
  domicilio (el momento "Instagram") → evidence pack con digest recomputado
  en vivo + `POST /audit/tamper` o KillSwitch como remate.
- **Fall in-live:** cualquier anomalía pre-cobro → "DRY_RUN"; post-cobro →
  "PIVOT" a evidencia (el pedido real llega y se come). OTP: el dueño lo lee
  de su teléfono (40–60 s) o reel de respaldo. Restante: shortlist de 3
  restaurantes verificados a T-30 min; drift de precio aborta por diseño y se
  muestra como **feature**.
- **Ensayos:** F0 read-only (≥10 búsquedas, selectores estables, 0
  escrituras) → F1 carrito DRY_RUN ×6 (incluye 1 drift y 1 replay de
  idempotencia) → F2 1–2 pedidos reales con cap (uno se cancela en ventana
  gratis para validar reembolso) → F3 demo cronometrada + reel de respaldo.
  Presupuesto realista: **COP 75.000–95.000** (~USD 19–24) contra un techo de
  mandato de 200.000.
- **Roles:** operador (jamás aprueba escalaciones), presentador (no toca
  teclado), aprobador step-up (dueño de la cuenta con su passkey), jefe de
  sala (cronómetro, pivots, artifacts S1–S8). Red: hotspot propio dual-SIM
  primario, wifi del venue de respaldo, failover ensayado <30 s.
- **Métricas ante jueces:** mandato firmado ≠ prompt (byte mutado →
  `INVALID_SIGNATURE`); techo atómico concurrente (`BUDGET_EXCEEDED` por
  código); step-up que un agente no puede producir (fail-closed a 120/300 s);
  evidencia re-verificable por un juez con curl; exactly-once (replay de
  `place_order` → mismo `order_id`, cero doble cobro).

## 8. Plan de construcción

Archivos nuevos (ninguno escrito aún):

```
src/rappi_bridge/            # despliegue nuevo (Dev 3), puerto 8010
  config.py session.py actions.py parse.py state.py idempotency.py
  reconcile.py approval.py dry_run.py jobs.py main.py
src/api/services/rappi_client.py      # RappiBridgeClient (espejo rail_client) — Dev 3
src/api/routers/rappi_bridge.py       # POST /purchases/{id}/capture (saga-final) — Dev 3
src/api/decision/ports.py             # + MerchantBridge (Protocol, aditivo) — Dev 2
src/api/decision/service.py           # + _mint_capture_token + captured/dry_run/
                                      #   price_drift/uncertain + eventos — Dev 2
src/api/evidence/artifacts.py         # bridge_artifacts en el pack — Dev 2
src/agent/ports/rappi_mcp.py          # vocabulario Rappi + request_purchase — Dev 1
tests/test_rappi_drift.py tests/test_bridge_idempotency.py
contracts/rappi-bridge.yaml           # OpenAPI nuevo (no toca api.yaml congelado)
docs/decisions/0029-rappi-bridge-as-merchant-rail.md
```

Contratos: `api.yaml` congelado **sin ediciones**; lo nuevo vive en
`rappi-bridge.yaml` como apéndice v1.2 (Quote, capture_token, DDL
`bridge_orders`, eventos `rappi.quote.created`, `purchase.placed`,
`bridge.price_drift`, `bridge.reconciled`, `purchase.dry_run_captured`).
`DECISIONS.md` #29 propuesta: *"El puente Rappi es un merchant-rail, no un
rail"* — chose: bridge en la máquina de sesión con capture_token e
`Idempotency-Key` opaco; el click es la captura y exige aprobación kernel
firmada. Rejected: `place_order` como tool MCP libre (segunda vía al dinero,
S2); subir la sesión a la nube; interceptar PAN. Does not solve: cambios de
DOM de Rappi (parseo quirúrgico, selectores versionados), la ventana
`uncertain` (reconciliación humana), y compras del dueño por fuera del agente
(el techo cubre lo que el agente hace, no toda la tarjeta).

## 9. Riesgos consolidados (top 6)

| # | Riesgo | Severidad | Mitigación |
|---|---|---|---|
| 1 | Filtración del `storage_state` (takeover con tarjeta) | **Crítica** | §5.1: custodia, cifrado, rotación, cuenta secundaria para ensayos |
| 2 | Rappi cancela el pedido en pleno demo (check antifraude, ToS §10) | Alta | Plan B: rail simulador como primaria; ensayo completo el día anterior; Rappi como bono |
| 3 | Prompt injection induce compra distinta | Alta | API cerrada sin verbos peligrosos; kernel decide el total; presupuesto de acciones |
| 4 | CVC pedido en checkout | Alta | Fail-closed abort; pre-verificar tarjeta que no pida CVC en F0/F1 |
| 5 | DOM de Rappi cambia y rompe selectores | Media | Selectores por rol+texto; smoke pre-demo; fallback `@playwright/mcp` calentado |
| 6 | Doble pedido en retry/crash post-click | Media | Checkpoint + single-flight + regla "nunca re-clickear en uncertain" + reconciliación |

## 10. Preguntas abiertas para el equipo (Q-R)

- **Q-R1** ¿Cuenta secundaria con tarjeta de saldo controlado para los
  ensayos F1–F2? (recomendado: sí)
- **Q-R2** ¿Topología demo A (todo local) con B documentado como producción?
  (recomendado: sí)
- **Q-R3** ¿Happy path limitado a restaurantes de precio fijo (tolerancia 0)?
  (recomendado: sí; banda de tolerancia solo como decisión documentada si
  acaso)
- **Q-R4** ¿Se intenta contacto Rappi partners/PR antes del demo? (costo vs
  legitimidad)
- **Q-R5** ¿Screenshots entran al evidence pack (tipo nuevo + blur PII) o
  quedan como artefacto local gitignored?
- **Q-R6** ¿Evento de ciclo de vida "orden cancelada por el merchant" para
  conciliar reserva→settle si el antifraude mata el pedido?
- **Q-R7** ¿Quién construye qué: puente a Dev 3 (HTTP/merchant), puertos y
  capture_token a Dev 2, vocabulario MCP a Dev 1? (recomendado: ese reparto)
- **Q-R8** Verificaciones empíricas de 10 min antes de aprobar el build:
  ¿checkout web pide CVC con tarjeta guardada? ¿vida útil de la sesión web?
  ¿`web.rappi.com.co` (Lite) permite checkout completo con tarjeta guardada
  (DOM más simple = menos fragilidad)?

## 11. Próximos pasos inmediatos

1. Ejecutar las verificaciones manuales de Q-R8 (10 min, navegador de la
   máquina de credenciales, sin automatizar nada).
2. F0: puente read-only (búsqueda + screenshots) — desbloquea todo lo demás.
3. F1 en DRY_RUN ×6 con pruebas de drift e idempotencia.
4. En paralelo: registro de decisión #29 y `contracts/rappi-bridge.yaml`
   para que Dev 3 pueda empezar el HTTP sin esperar al puente vivo.

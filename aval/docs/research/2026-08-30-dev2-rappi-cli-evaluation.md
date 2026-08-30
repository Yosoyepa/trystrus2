# Evaluación: `@crafter/rappi-cli` como vía de ejecución del puente Rappi

**Fecha:** 2026-08-30 · **Autor:** Dev 2 · **Refina** la §3 de
[`2026-08-30-dev2-rappi-bridge-analysis.md`](2026-08-30-dev2-rappi-bridge-analysis.md).
Fuente auditada: clon pinneado en `vendor/crafter-station-rappi-cli`
(commit `5c4e5b0`, 2026-07-24) — **verificado idéntico al tarball npm 0.0.6**
(shasum `2ec83556…`). Nunca instalado desde el registry.

## 1. Veredicto ejecutivo

**No exponer el CLI/MCP directamente al agente; adoptarlo como sustrato del
puente.** El paquete es un cliente pequeño (~20 archivos TS legibles, MIT,
sin ofuscación) de la **API web interna de Rappi**
(`services.grability.rappi.com`, los mismos endpoints que llama la web de
Rappi, con los headers del microfrontend web) que cubre el flujo completo:
login por captura de token → search → cart → checkout_preview → place_order →
track. La auditoría de seguridad es **limpia** (§3). Esto **cambia el orden de
las vías**: la "vía 4" (API privada) que descartamos por costo de ingeniería
invertida ya está hecha, field-testeada y es auditable — el puente recomendado
sigue siendo el mismo (kernel-gated, DRY_RUN, single-flight), pero su capa de
ejecución pasa de Playwright/DOM a HTTP determinista, con el browser quedando
solo para el login (OTP al teléfono del dueño) y los visuales del demo.

## 2. Qué es y su estado

- CLI Bun/TypeScript + servidor REST (Hono, `:3100`) + **servidor MCP con 14
  tools** (incluye `place_order`, `checkout_preview`, `add_to_cart`,
  `track_orders`). Enfoque Colombia (COP, rappi.com.co, coords Bogotá).
- npm: solo v0.0.6 (2026-07-24), **11 descargas/semana**, 4 maintainers
  (colectivo "crafter-station"; autor declarado Cristian Correa). GitHub: 34
  estrellas, último push 2026-07-24. Proyecto pequeño y poco adoptado — no es
  supply-chain masivo, pero tampoco librería madura: **pinneamos el commit y
  cualquier actualización se re-audita con diff**.
- Construido para usarse desde Claude Code (CLAUDE.md); el server MCP incluso
  instruye "Always confirm with the user before calling place_order" — guarda
  solo a nivel de prompt, sin enforcement. Para Aval eso es insuficiente por
  diseño (S2: un solo camino al dinero).

## 3. Auditoría de seguridad — limpia

| Chequeo | Resultado |
|---|---|
| Endpoints de red | **Solo** `services.grability.rappi.com` + `images.rappi.com`; único host externo extra: `registry.npmjs.org` (chequeo de versión). Cero telemetría |
| Ejecución dinámica | Sin `eval`, sin `child_process` (el único `Bun.spawn` es el dispatcher de subcomandos), sin blobs base64, sin postinstall |
| Dependencias | hono, zod, chalk, cli-table3, ora, MCP SDK, playwright (opcional, solo login). Todas mainstream |
| Token | Se guarda plano en `.rappi-config.json` **con entrada en `.gitignore`** del propio repo (el autor lo tuvo en cuenta). Para nosotros aplica igual la custodia crítica del análisis anterior (chmod 600, fuera del repo, rotación) |
| Tarball vs repo | Idénticos (diff excluso `.git`/lockfiles); lo auditado es lo que distribuye npm |

## 4. Cómo funciona por dentro (lo que nos ahorra semanas)

- **Login (`rappi login`):** abre Chromium headful en `rappi.com.co/login`,
  el dueño hace su login OTP normal, y el script captura pasivamente la
  respuesta de `/ms/application-user/auth` llevándose el header
  `Authorization: Bearer ft.gAAAAA…` (token Fernet de sesión web) y el
  `deviceid`. Alternativa manual: pegar el token desde DevTools. El browser
  **solo** se usa aquí.
- **Endpoints del flujo** (todos bajo `services.grability.rappi.com`):
  búsqueda `/api/pns-global-search-api/v1/unified-search`, catálogo
  `/api/restaurant-bus/stores/catalog-paged/home`, carrito
  `/api/ms/shopping-cart/v2/{storeType}/store` (add) y `…/v1/all/get`
  (lectura), recálculo `…/v1/{storeType}/recalculate`, checkout detail
  `…/v1/{storeType}/checkout/detail`, **place order**
  `POST /api/ms/shopping-cart-proxy/{storeType}/checkout` con body
  `{return_key}`, órdenes `/api/user-order-home/orders`, direcciones
  `/api/ms/users-address/addresses`, propina `…/v1/{storeType}/tip`, método
  de pago `…/v1/{storeType}/payment-method`.
- **Pago (CORREGIDO por el fork 6a20e1e):** sin método en el carrito Rappi
  cobra en **EFECTIVO** — no "el método de la cuenta" como decía esta
  sección. El fork documentó el resolver (`/payment-method/resolver/v6`) y
  el payload del PUT; el bridge ahora resuelve y aplica el método antes del
  click y rechaza efectivo (0030). **Sin CVV en el flujo** (confirmado).
  natural).
- **Detalle a favor nuestro:** el `return_key` es un binding **servidor-emito**
  entre el carrito cotizado y la confirmación — structuralmente el primo del
  capture_token que diseñamos. El drift check se vuelve trivia y 100%
  determinista: comparar el total del `checkout/detail` contra el monto
  aprobado por el kernel **antes** del POST; si difiere, no se llama place
  order.
- **Lo que NO trae:** idempotencia (el POST de checkout no lleva key — un
  retry puede duplicar un pedido real), caps de gasto, DRY_RUN, evidencia
  firmada. Todo eso sigue siendo nuestro.

## 5. Integración recomendada en Aval

La arquitectura del análisis anterior **no cambia**: kernel → PolicyGate →
reserva → (step-up humano) → capture_token → `place_order` gate-bound →
receipt → evidence pack → outbox. Lo que cambia es la capa de ejecución del
puente y el plan de construcción:

- **El MCP del CLI jamás se expone al agente** (tool `place_order` cruda =
  segunda vía al dinero, viola S2 y la decisión #24). Es nuestra referencia
  y fallback, no la superficie.
- **Opción A (recomendada para el hackathon):** correr el CLI **sin
  modificar** como edge executor — su servidor REST en `localhost:3100` — y
  construir nuestro `aval-rappi-bridge` (Python, en la máquina de
  credenciales) como la única capa con guardarrails: DRY_RUN por defecto
  (nunca llama `/api/place-order` sin aprobación), cap COP hardcodeado,
  single-flight por `idem_key` con checkpoint por paso, verificación del
  capture_token del kernel, drift check contra `checkout/detail`, y evidencia
  JSON + screenshots opcionales. El REST del CLI escucha solo en localhost y
  nuestro bridge es su único cliente (no tiene auth).
- **Opción B (limpieza posterior):** portar los ~10 archivos de servicios a
  Python (`httpx` + pydantic) dentro de `src/rappi_bridge/` para tener un
  solo runtime testeable con pytest y eliminar Bun del camino crítico. El
  repo TS queda como referencia oracular y fallback vivo.
- El plan de archivos, la decisión #29 propuesta ("el puente Rappi es un
  merchant-rail, no un rail") y el contrato `rappi-bridge.yaml` del análisis
  anterior siguen válidos; `actions.py`/`parse.py` (Playwright/DOM) se
  sustituyen por llamadas HTTP al CLI (A) o por `client.py` (B).

**Lo que gana el demo:** latencia por acción ~10× menor (pedido end-to-end en
<30–45 s vs 60–120 s del browser), cero fragilidad de selectores (no hay DOM
que rompa), evidencia JSON nativa (precios y totals exactos para el drift
check, no OCR de screenshots), y el visual del demo se resuelve con el
tracking real en el app/teléfono del dueño + consolas de Aval.

**Lo que se mantiene igual:** riesgo ToS (misma clase de exposición —
automatización contra la plataforma; el cliente imita la web, no evade nada),
custodia crítica del token (`ft.` = sesión completa), cuenta secundaria para
ensayos, plan B (rail simulador como demo primaria), y el riesgo de rotación
de API no documentada: el header `app-version` lleva un hash del build web
(`e1de6be4…`) que Rappi puede rotar — mitigación: smoke test F0 antes de cada
sesión y el puente Playwright/DOM del análisis anterior queda como plan C si
la API cambia sin aviso.

## 6. Próximos pasos

1. **F0′ (30 min, máquina de credenciales, cuenta secundaria):** `bun install`
   desde el clone pinneado (nunca `bun add -g` desde npm), `rappi login`,
   `whoami`, `search`, `add-to-cart`, `checkout_preview` — **sin** place
   order. Valida: token vivo, endpoints vigentes, forma real del
   `checkout/detail` (totals, `return_key`), y que no pida CVC.
2. Construir el guard bridge (Opción A) y sus tests de drift/idempotencia
   contra un CLI mockeado.
3. F1 en DRY_RUN ×6 y F2 con cap — mismo plan de ensayos del análisis
   anterior.
4. Registrar la decisión #29 incluyendo la elección de vía (HTTP interno del
   web-backend vía CLI auditado, con Playwright/DOM como plan C).

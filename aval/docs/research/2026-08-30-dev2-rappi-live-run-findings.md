# Hallazgos del primer run real del CLI de Rappi (sesión agéntica, 2026-08-30)

**Fecha:** 2026-08-30 · **Autor:** Dev 2 · **Entrada:** transcripción de una
sesión real de Claude Code operando `@crafter/rappi-cli` en la máquina del
dueño de la cuenta (login → búsqueda → carrito → checkout → **pedido real
pagado**). Refina
[`2026-08-30-dev2-rappi-cli-evaluation.md`](2026-08-30-dev2-rappi-cli-evaluation.md)
y los guardarrails de
[`2026-08-30-dev2-rappi-bridge-analysis.md`](2026-08-30-dev2-rappi-bridge-analysis.md).
PII de la cuenta redactada a propósito.

## 0. Qué se demostró (evidencia)

Flujo **end-to-end real, en producción, con dinero real**: login con captura
de token usando el Chrome del dueño (`channel: "chrome"`, sin descargar
Chromium de Playwright) → `whoami`/`addresses` → `search` → `add-to-cart` →
preview de checkout → confirmación conversacional ("sí") → `place-order` →
**orden `2496728264` creada y pagada** (Turbo Parque Bavaria, total $18.300
COP, ETA 8 min, delivery a la dirección activa). Tracking con máquina de
estados real: `created → in_store → on_the_way → delivered`.

Respuestas empíricas que quedan cerradas:

- **Q-R8/CVC:** el checkout con tarjeta guardada **no pidió CVC** — el POST
  de place-order pasó sin ninguna interacción adicional. (De esta cuenta;
  a re-verificar en la secundaria.)
- **Endpoints vigentes** al 2026-08-30 con el header `app-version` del CLI.
- **El patrón human-in-the-loop conversacional funciona** en la práctica:
  preview con desglose completo → "no pido nada hasta que me confirmes" →
  confirmación → ejecución.

## 1. Modos de falla observados (el diseño ya los contemplaba; ahora están medidos)

| # | Falla observada | Detalle del run | Consecuencia de diseño |
|---|---|---|---|
| 1 | **Drift de precio/tienda** | Búsqueda cotizó $6.900 + $2.500 envío = $9.400 en Turbo `900164011`; el carrito resolvió a Turbo Parque Bavaria `900139848` (bodega más cercana a la dirección **activa de la cuenta**): producto $7.200, envío $1.500 y **tarifa de servicio $1.600 que no existe en la búsqueda** → total real $10.300 | La Quote del puente viene **solo** de `checkout/detail` (post-resolución de tienda y dirección), jamás de search. Ya era el diseño; ahora hay números |
| 2 | **Split-brain de direcciones** | La búsqueda usa las coords del config local ("Casa"); el carrito/checkout usan la dirección activa del servidor (otra, recién creada). El agente cotizó para una zona y entregaría en otra | **Guardarrail nuevo — address binding:** el puente compara la dirección de entrega del `checkout/detail` contra la autorizada en el mandato y aborta si difiere |
| 3 | **Resolución de tienda server-side** | store_id de búsqueda ≠ store_id del carrito (el backend elige bodega) | Cubierto por `cart_hash` calculado sobre el `checkout/detail`, no sobre la búsqueda |
| 4 | **Mínimo de compra por tienda** | 1er place-order rechazado server-side **sin mover dinero**: "Te faltan $2.300" (mínimo $9.500 en productos en Turbo); carrito quedó intacto | Camino de replan estructurado, no error: `MERCHANT_MIN_AMOUNT` → replan con cotización nueva (el grafo ya soporta replan por `AMOUNT_MISMATCH`) |
| 5 | **`remove-from-cart` roto** (DELETE → 404) | Workaround del run: el PUT de add-to-cart **reemplaza** el contenido de la tienda | El puente usa semántica PUT-replace y **precondición de carrito limpio** al inicio del run (carrito residual → fail-closed o clear explícito). Guardarrail nuevo: cart precondition |
| 6 | Tarifa de servicio invisible en search | Solo aparece en checkout | Techos y ratios de step-up del mandato se calculan sobre el **total** (producto + envío + tarifas), nunca sobre el precio de búsqueda |

Bugs del CLI anotados (no nos afectan si no usamos su capa UI): `whoami`
crashea enmascarando nombres con espacios dobles; el dispatcher
`Bun.spawn(["bun", …])` depende del PATH (irrelevante vía REST); `rappi
login` exige el Chromium de Playwright salvo parche `channel: "chrome"`
(el run real demostró que con el Chrome del dueño funciona).

## 2. El human-in-the-loop observado, mapeado a Aval

La sesión usó el patrón de dos fases correcto pero **sin enforcement**.
Eso es exactamente el hueco que llena Aval — la teoría de la sesión se
conserva; la confirmación se vuelve criptográfica:

| Paso de la sesión (Claude + CLI) | Equivalente Aval | Qué se fortalece |
|---|---|---|
| Preview con desglose (tienda, dirección, total, ETA) | **Quote canónica** (`checkout/detail` → `cart_hash`, totals, `return_key`) | Binding criptográfico del carrito aprobado |
| "¿Confirmo el pedido?" en el chat | **Escalación L3+ con passkey WebAuthn** del dueño (`await_human`, countdown 120/300 s) | El "sí" deja de ser texto: es firma verificable, no-repudiable, y el silencio deniega (fail-closed) |
| "sí" → `place-order` inmediato | `capture_token` (JWT ES256, TTL 120 s, claims `purchase_id+amount+cart_hash`) y re-fetch del `checkout/detail` justo antes del POST | Si el precio cambió tras el "sí", se aborta — el run real mostró que eso pasa de verdad |
| Un "sí" = un pedido (sin protección) | **Single-flight** por `idem_key` con checkpoint por paso | Un "sí" repetido o un retry no puede duplicar un pedido real |
| Sin techo: el agente gasta lo que el usuario no frene | Mandato SD-JWT + reserva atómica + cap hardcodeado en el puente | El techo es código, no buen comportamiento |
| Sin registro verificable | Evidence pack + ledger hash-chain (decisión, aprobación, receipt, chain, digest) | Terceros re-verifican la compra con curl |

Lo que se **conserva** de la teoría de la sesión (y es buena UX a imitar):
la transparencia total antes de confirmar (tienda resuelta, dirección de
entrega, desglose de tarifas, ETA), la regla "no pido nada hasta que me
confirmes", y el anuncio honesto de los drifts encontrados ("el precio subió,
la tienda cambió") en lugar de ocultarlos.

## 3. Implicaciones para el plan de construcción

1. **F0′ (smoke test) queda esencialmente hecho** por esta sesión: login,
   search, cart, checkout y place-order validados en cuenta real. Pendientes:
   vida útil del token (`ft.`, monitorear días), comportamiento en la cuenta
   secundaria, y extraer el campo exacto de la tarifa de servicio del
   desglose del `checkout/detail`.
2. El guard bridge (Opción A) hereda del run: PUT-replace para mutar carrito,
   `resolveStoreType` (turbo→restaurant), preflight de dirección activa +
   carrito limpio, drift check contra `checkout/detail`, y `min_amount`
   tratado como replan.
3. **Checklist de guardarrails actualizado** — se suman a los 10 bloqueantes
   del análisis del puente: (11) address binding contra el mandato, (12)
   precondición de carrito limpio + PUT-replace, (13) replan estructurado
   para `MERCHANT_MIN_AMOUNT`, (14) techos y ratios sobre el total con
   tarifas, no sobre el precio de búsqueda.
4. La decisión #29 propuesta absorbe esta evidencia como justificación
   empírica (drift medido, direcciones partidas, mínimo de tienda).

## 4. Próximos pasos

1. Construir el guard bridge (Opción A: CLI sin modificar en
   `localhost:3100` + capa Python con guardarrails) — ya no hay bloqueo de
   factibilidad.
2. Ensayo F1 en DRY_RUN ×6 en la cuenta secundaria (incluye 1 replay de
   idempotencia y 1 drift forzado).
3. Registrar #29 y abrir `contracts/rappi-bridge.yaml` para desbloquear la
   superficie HTTP (Dev 3).

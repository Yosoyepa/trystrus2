# PLAN v2.3 — "El comprador que no es humano" · NextWave Hackathon 2026 (Yuno × Nauta)

> **🗺️ Nota de adaptación a este repo (Aval):** plan maestro de investigación y arquitectura, respaldado por ~140 fuentes (agosto 2026). La nomenclatura del repo difiere en un solo eje: el servicio que aquí se llama **`api`** es **`kernel/`** en el repo (mismos routers: mandates/escalations [Dev A], verify/purchases/audit/events [Dev B]); el job `watcher` vive dentro de `agent/`; la "UI Auditor" del plan es la **control tower** de `web/`. Los contratos congelados están en [`../contracts/`](../contracts/). El decision log calificado vive en [`../DECISIONS.md`](../DECISIONS.md) (los ADRs de este documento son su fuente). Idioma de los planes: español; idioma del repo: inglés.

> **Documento de planificación y contraste.** Analiza la solución planteada en whiteboard (imágenes adjuntas) contra: (a) los requisitos del reto, (b) el estado del arte investigado por 9 subagentes expertos (~140 fuentes, agosto 2026), y (c) lo que ya existe en el workspace (repo AP2 con SDK Python + sd-jwt + pytest).
>
> **v2 — CAMBIO MAYOR: despliegue en Google Cloud Platform.** Toda la arquitectura, ADRs, gates y plan de ejecución fueron revisados para el target GCP.
>
> **v2.1 — CAMBIO DE RAIL DE PAGO: sin acceso a Yuno → PayPal sandbox** (vaulting de payment tokens + Customer Disputes API). Coinbase/x402 evaluado con evidencia y documentado como alternativa (ADR-014).
>
> **Complemento de ejecución:** ver [PLAN-PARALELO.md](PLAN-PARALELO.md) — diagrama de componentes por áreas, contratos entre módulos (`contracts/`) y 4 workstreams paralelizables para 4 devs.
>
> **v2.2 — TOPOLOGÍA CONFIRMADA: microservicios con el frontend como servicio separado** (ADR-022, decisión del equipo: "front aparte"). Workstreams recalculados en PLAN-PARALELO.
>
> **v2.3 — ADR-006 REVISADO: orquestador del agente = grafo híbrido propio** — las ideas críticas de LangGraph (grafo explícito, checkpointing, interrupt-before-pay) SIN el framework, para eliminar el cuello de botella de implementación. Además: protocolo de documentación obligatoria en el repo (devlogs por workstream + decisiones con guard de CI — PLAN-PARALELO §6 regla 9).
>
> **Fecha:** 2026-08-29 · **Estado:** Borrador v2.3 para validación del equipo

---

## 0. Veredicto ejecutivo

**La tesis del equipo es viable y está direccionalmente correcta — la industria convergió exactamente donde está el whiteboard.** Entre 2025 y 2026, Google (AP2), Stripe+OpenAI (ACP), Mastercard (Agent Pay / Verifiable Intent), Visa (Intelligent Commerce) y la FIDO Alliance estandarizaron el mismo concepto: **un canal de confianza humano↔comercio mediado por mandatos criptográficos verificables**. La intuición del whiteboard ("canal entre personas y comercio para resolver la confianza", HITL, reglas de negocio, no-repudio) **es** la arquitectura ganadora del ecosistema.

**Correcciones de fondo (v1) — siguen vigentes:**

| # | Corrección | Por qué |
|---|---|---|
| 1 | El **mandato debe ser un artefacto criptográfico firmado de primera clase** (SD-JWT, RFC 9901), no una fila en BD con "principio de no-repudio" | El no-repudio sin firma verificable por el merchant es una afirmación, no un mecanismo. Es EL diferenciador del reto. |
| 2 | **Eliminar RabbitMQ y S3 como dependencias duras** → Postgres append-only (outbox + hash-chained audit) | Un contenedor menos que mantener; la auditoría en la misma transacción ACID que el negocio es *más* defendible. |
| 3 | **Guardrails deterministas FUERA del LLM** (gate en el wrapper de `pay()`) | Los guardrails por prompt son LLM01 en OWASP 2025 y caen ante ataques adaptativos (AgentDojo). El reto pide resistir "creative paths". |
| 4 | **El instrumento de pago jamás toca al agente**: PayPal vaulting (`payment_token_id` vía setup tokens — el humano aprueba UNA vez en PayPal) + Presidio para scrubbing PII | Sin acceso al sandbox de Yuno, PayPal ofrece el equivalente: cobros server-to-server solo con `vault_id`. Nunca tocamos PAN ni credenciales — cero datos de pago en nuestro perímetro. |
| 5 | **HIL con canal que funcione en vivo**: Telegram primario, WhatsApp secundario pre-calentado, timeout fail-closed | WhatsApp Cloud API exige templates aprobadas por Meta — frágil sin preparación. |

**Correcciones nuevas por GCP (v2):**

| # | Corrección | Hallazgo verificado |
|---|---|---|
| 6 | **Dominio propio es OBLIGATORIO** (comprarlo el Día 0) | `run.app` está en la Public Suffix List y WebAuthn exige que `rpId` sea un *registrable domain* → **los passkeys NO funcionan en `https://…run.app`**. Un dominio `.app`/`.dev` (~US$12/año) mapeado a Cloud Run con TLS gestionado resuelve rpId + credibilidad. |
| 7 | **Relé de eventos sin `LISTEN/NOTIFY`** → polling `FOR UPDATE SKIP LOCKED` | Cloud Run escala a cero y corta conexiones persistentes; `LISTEN/NOTIFY` (v1) no es serverless-safe. Pub/Sub queda explícitamente descartado como overkill (10 GiB gratis, pero una pieza más). |
| 8 | **Claves de firma: dos niveles** — Secret Manager (PEM Ed25519 para emitir SD-JWT) + **Cloud KMS `EC_SIGN_ED25519`** para roots del ledger y webhooks firmados | KMS soporta Ed25519 puro (algoritmo exacto: `EC_SIGN_ED25519`, no `ED25519_SIGN`) y la clave jamás sale del servicio (~US$0.06/mes). El issuer SD-JWT usa clave local del Secret Manager por compatibilidad con la librería `sd-jwt`. |
| 9 | **Witness de roots firmados → bucket GCS versionado** (en vez de gist) | GCP-native: Object Versioning + lectura pública del prefijo `/roots` + Cloud Audit Logs como capa complementaria. |
| 10 | **Watcher de precios → Cloud Run job + Cloud Scheduler con OIDC** | Sin endpoint HTTP público que proteger; Scheduler da 3 jobs gratis y la invocación lleva token OIDC validado por IAM (`roles/run.invoker`). |
| 11 | **LLM del agente: Vertex AI Gemini (pagado, cubierto por créditos) o OpenAI** — el free tier de Gemini API **no es opción** | El free tier de Gemini API fue recortado (dic-2025: Flash ~20 requests/día) — inutilizable para demo en vivo. Los US$300/90 días de la cuenta nueva cubren Vertex AI; el hackathon es apoyado por OpenAI (si hay créditos, OpenAI es igual de válido: el gate es agnóstico al modelo). |
| 12 | **Región única `southamerica-east1` (São Paulo)** | Soporta todo el stack (Cloud Run, Cloud SQL, Artifact Registry, Secret Manager); RTT Bogotá–São Paulo ~50–70 ms vs ~90–110 ms a us-east1; evita latencia multi-región Cloud Run↔Cloud SQL. |

**Corrección nueva por rail de pago (v2.1):**

| # | Corrección | Hallazgo verificado |
|---|---|---|
| 13 | **PayPal sandbox como rail principal** en lugar de Yuno | Vaulting de payment tokens (`setup-tokens` → aprobación humana única → `payment_token_id`) = el equivalente directo del `vaulted_token`; cobros solo con `vault_id` sin interacción del comprador; **Customer Disputes API simulable en sandbox** (alimenta el bonus de disputas); **revocación del mandato = `DELETE` del payment token** (el rail mismo mata la vía de pago). Sandbox instantáneo, gratuito, sin approval. |
| 14 | **Coinbase/x402 documentado como alternativa, no como rail principal** | HTTP-402 nativo para máquinas (EIP-3009 gasless, settlement USDC en Base ~2–5 s, testnet gratis con faucet CDP) — pero **irreversible: no existen chargebacks**, así que no permite demostrar la reversión de una disputa. SDK Python menos maduro (sin spending controls nativos). |

**Encuadre ganador (reforzado en v2.1):** no inventar el concepto — **alinear la semántica con AP2** (repo ya clonado con SDK) y **cerrar los 2 gaps que AP2 deja abiertos hoy**: revocación en vivo con verificación síncrona en pay-time y flujo de disputa resuelto por el trail criptográfico. Esos 2 gaps son los bonus points del reto. **PayPal co-creó AP2 con Google** — su Intent Mandate (Human-Not-Present) pre-aprueba "budget, categories, timing": exactamente nuestro mandato.

**Costo total estimado en GCP: ~US$12–27/mes (US$0 desembolsados con los créditos de prueba de US$300).** Detalle en §6.3.

---

## 1. Lectura del whiteboard (solución propuesta, reconstruida)

Del análisis de las imágenes:

- **Problema declarado:** falta de confianza en transacciones hechas por agentes.
- **Requisitos clave identificados:** trazabilidad · delegación de potestad · usabilidad · seguridad · guardrails · acceso a transacciones (repetición, significado y transparencia).
- **Flujo:** Canales de usuario (Slack / Web / Telegram) → **Auth** → **Plataforma** que contiene: Agente (con MCP), BFF, Logs, **RabbitMQ**, BD, **S3** → conexión al **Merchant vía API / MCP**.
- **Insights de diseño:** Human-in-the-loop (confirmación para generar acción del agente), reglas de negocio, **principio de no-repudio**, permisos de acción otorgados por el usuario.
- **Casos de uso:** vuelos (VuelaYa), retail (Amazon/Meli), logística. *(El despliegue no estaba especificado → v2 lo fija en GCP; el rail de pago no estaba fijado → v2.1 lo fija en PayPal.)*

**Evaluación por componente:**

| Componente whiteboard | Veredicto | Nota |
|---|---|---|
| Canales Slack/Web/Telegram | ✅ KEEP | Telegram primario para HIL en vivo; Web para dashboards; WhatsApp "wow" secundario. Slack opcional. |
| Auth | ✅ KEEP + reforzar | **Passkeys/WebAuthn** para crear/revocar mandato — exige dominio propio (ver ADR-018). |
| Agente (MCP) | ✅ KEEP | MCP correcto como capa de tools; el pago NO puede ser tool libre — ver ADR-005. |
| BFF | ✅ KEEP | Se convierte en el servicio `api` en Cloud Run. |
| BD | ✅ KEEP + especificar | **Cloud SQL Postgres** como fuente única de verdad: mandatos con máquina de estados + auditoría hash-chained. |
| RabbitMQ | ❌ DROP | Outbox en Postgres + relé por polling `SKIP LOCKED` (serverless-safe). Pub/Sub también descartado (ADR-010). |
| S3 (logs) | ❌ DROP como dependencia | Auditoría = tabla `audit_events` hash-chained en Cloud SQL; witness de roots en **GCS versionado**; Cloud Logging + Cloud Audit Logs como complemento (no como evidencia). |
| Logs → auditoría verificable | 🔄 TRANSFORMAR | Hash chain + roots firmados con KMS. |
| Merchant API/MCP | ✅ KEEP + añadir | Endpoint de verificación síncrona `POST /mandates/:id/verify` — el corazón del reto. |
| No-repudio (principio) | 🔄 CONCRETAR | SD-JWT del mandato + firma por transacción del agente + approval receipts. |
| HITL, reglas de negocio, guardrails | ✅ KEEP + concretar | ADR-005, ADR-008, ADR-011. |

---

## 2. Alcance (contrastado con el reto)

### 2.1 In-scope — requisitos obligatorios del reto

- [ ] **R1.** Humano crea mandato: qué, cuánto, hasta cuándo, con qué método de pago — sin entregar la tarjeta cruda.
- [ ] **R2.** Merchant verifica el mandato antes de aceptar: agente legítimo, mandato válido, compra dentro de límites.
- [ ] **R3.** Compra end-to-end: agente descubre, decide y paga; humano recibe registro de qué compró y bajo qué mandato.
- [ ] **R4.** Casos feos explícitos: fuera de mandato → rechazado o escalado, **nunca silenciosamente aprobado**; revocación en vivo; agente suplantado; disputa posterior.
- [ ] **R5.** Trail auditable legible por humano, merchant y auditor.
- [ ] **R6. Trial by fire:** los jueces revocan/limitan en vivo y el sistema reacciona sin intervención del equipo.
- [ ] **R7 (v2).** Despliegue en GCP: ambiente de demo estable en URL propia, deploy reproducible desde el repo.

### 2.2 In-scope — bonus (priorizados)

- **B1.** Flujo de disputa completo ("nunca autorizé esto" → evidence bundle resuelve quién tiene razón) — **con disputa real simulada en el sandbox de PayPal**.
- **B2.** Mandatos con condiciones ricas ("si baja de $150", "hasta 3 veces al mes") evaluados correctamente.
- **B3.** Defensa contra agente adversario (prompt injection embebida en catálogo → mini-AgentDojo en vivo, bloqueado por el gate).

### 2.3 Out-of-scope (declarar explícitamente)

- Cumplimiento legal real (PSD2/Reg E/AI Act se **citan** en la defensa, no se certifica).
- Dinero real (sandbox PayPal + mocks).
- Multi-región, HA, escalabilidad, Terraform completo (gcloud scripts idempotentes; IaC declarativa como roadmap).
- Rails reales Stripe ACP / x402 en producción (x402 queda documentado como alternativa en ADR-014).
- Compras en merchant real (VuelaYa ficticio; catálogo simulado + APIs propias).

### 2.4 Partes del sistema (3 actores + 1 observador)

1. **Marta (humano):** crea/revoca mandatos con passkey; aprueba el instrumento de pago UNA vez en PayPal; recibe registros; aprueba/descarta escalamientos desde su chat.
2. **Agente comprador:** identidad criptográfica propia (Ed25519); descubre ofertas, decide dentro del mandato, firma intents de compra.
3. **VuelaYa (merchant):** catálogo + checkout; verifica el mandato criptográficamente antes de aceptar el pago.
4. **Auditor:** lee el trail completo y re-verifica la cadena de hash en vivo.

---

## 3. Enfoque: el "canal de confianza" como producto

La idea central del equipo — un canal persona↔comercio que resuelve la confianza — se concreta en **tres piezas** que hoy no existen en práctica (los gaps abiertos de AP2):

```
┌─────────────────────────────────────────────────────────────────┐
│                    EL CANAL DE CONFIANZA                         │
│                                                                  │
│  1. MANDATE REGISTRY   → emite mandatos firmados (SD-JWT) con    │
│                          condiciones JsonLogic + limits;          │
│                          passkey humana como proof-of-intent      │
│  2. VERIFY ENDPOINT    → POST /mandates/:id/verify — chequeo     │
│                          SÍNCRONO en pay-time (firma, límites,    │
│                          estado, revocación) + reserva atómica    │
│  3. PROOF LEDGER       → audit trail append-only hash-chained    │
│                          con roots firmados (KMS) → gana disputas │
└─────────────────────────────────────────────────────────────────┘
```

**Semántica alineada a AP2** (Google → FIDO Alliance, 2026): la cadena *Intent Mandate → Cart Mandate → Payment Mandate* mapea directo al circuito del reto. El SDK Python de AP2 ya está en el workspace (`sd-jwt 0.10.4`, `jwcrypto`, `cryptography`, `pytest`). **Decisión: reutilizar el modelo semántico y las primitivas cripto (SD-JWT + Ed25519), con implementación propia simplificada** — no montar los servers A2A completos del repo. PayPal (co-creador de AP2) publica el mismo patrón en su Intent Mandate.

**El canal es cloud-agnostic y rail-agnostic; GCP le agrega tres cosas defendibles ante jueces:** (1) claves de firma en **Cloud KMS** que nunca salen del servicio, (2) **Cloud Audit Logs** complementando el ledger de aplicación, y (3) identidad servicio-a-servicio con **tokens OIDC firmados por Google** — el mismo principio de identidad verificable del mandato, aplicado a la infraestructura.

---

## 4. Objetivos medibles

| ID | Objetivo | Métrica de éxito |
|---|---|---|
| O1 | Mandato firmado verificable | Un mandato alterado en 1 byte es rechazado por el merchant en el 100% de casos (test) |
| O2 | Verificación pre-pago | Toda compra pasa por `verify` en pay-time; 0 rutas de pago que eviten el gate |
| O3 | Revocación viva | Revocar → siguiente intento falla **≤ 2 s** (check síncrono + `DELETE` del payment token en PayPal — doble kill-switch) |
| O4 | Nunca aprobación silenciosa fuera de mandato | Escalamiento con diff legible o rechazo explícito en el 100% de tests (incluidas inyecciones) |
| O5 | Trail auditable | `/audit/verify` re-valida la hash chain en vivo; mutar 1 evento en BD es detectado (test) |
| O6 | Trial by fire superado | Ensayo cronometrado con 2 "jueces" externos al código revocando/cambiando límites, **contra el ambiente desplegado en GCP** |
| O7 | Instrumento de pago fuera del alcance del agente | Agente solo ve `payment_token_id` (referencia); Presidio scrubbea trazas (demo antes/después) |
| O8 (v2) | Despliegue reproducible en GCP | Deploy desde checkout limpio con 1 pipeline (GH Actions); demo corre en `https://app.<dominio>` con TLS gestionado; sin cold starts durante la ventana de demo (`--min-instances=1`) |

---

## 5. ADRs — Architecture Decision Records (semilla del decision log entregable)

> Formato: Contexto → Alternativas → Decisión → Consecuencias. Cada ADR = 1 entrada del decision log del entregable.

### ADR-001 · Semántica del mandato: alinear con AP2 en vez de inventar
- **Contexto:** El reto permite inventar protocolos; pero el ecosistema 2025-26 ya estandarizó mandatos (AP2 con 60+ socios; UCP de Google/Shopify reutiliza AP2 mandates; FIDO creó TWGs de Agentic Authentication y Payments).
- **Alternativas:** (a) inventar schema propio; (b) ACP/Stripe (OAuth delegate auth, sin mandate criptográfico autónomo); (c) x402 (wallets M2M, irreversible, sin disputas); (d) **AP2-aligned propio**.
- **Decisión:** (d). Reutilizar semántica AP2 (Intent→Cart→Payment) y su stack cripto (SD-JWT), implementación mínima propia.
- **Consecuencias:** +defensa técnica ("no inventamos: implementamos el estándar y cerramos sus gaps"); −coste de aprender spec (mitigado: repo AP2 local + samples).

### ADR-002 · Formato del mandato firmado: SD-JWT (RFC 9901) con Key Binding
- **Contexto:** El mandato debe ser portable, firmado, con minimización de PII y verificación offline por el merchant.
- **Alternativas:** VC JSON-LD (pesado para hackathon); JWT plano (sin selective disclosure); **SD-JWT** (RFC 9901, Proposed Standard nov-2025).
- **Decisión:** SD-JWT con claims tipados: `jti`, `max_per_txn`, `total_budget`, `currency`, `categories[]`, `merchants[]`, `validity`, `payment_method_ref` (**`payment_token_id` de PayPal**, nunca PAN ni credenciales), `agent_id`, `cnf.jwk` (clave del agente), condiciones **JsonLogic**, bloque `limits`. PII (dirección) como claims seleccionables. **La clave de emisión vive como PEM en Secret Manager** (la librería `sd-jwt` firma con clave local; KMS-backed issuer queda como mejora post-hackathon).
- **Consecuencias:** Firma + selective disclosure + key binding con la librería ya instalada; verificación contra JWKS del issuer (`/.well-known/jwks.json`).

### ADR-003 · Prueba de intención humana: passkeys/WebAuthn
- **Contexto:** Crear y revocar mandato exige probar intención humana fuerte; un agente NO puede completar ceremonia WebAuthn (gesto físico + user verification).
- **Alternativas:** OTP (phishable); contraseña (compartible); **passkey `userVerification:'required'`**; firma de PDF (no machine-verifiable).
- **Decisión:** Passkey para crear, modificar límites y revocar. El challenge WebAuthn incluye el hash canónico del mandato → se firma la intención exacta. **Requisito de plataforma: dominio propio** (ADR-018).
- **Consecuencias:** +Alineación con FIDO Verifiable Intent; ver ADR-018 para el detalle del dominio en GCP.

### ADR-004 · Identidad del agente separada de la humana
- **Contexto:** El reto pide "agent identity separate from human identity"; la impersonación es un caso feo.
- **Alternativas:** credenciales del humano reutilizadas (confused deputy); API key estática (sin prueba por transacción); **par Ed25519 propio del agente** referenciado en el mandato vía `cnf.jwk` (RFC 7800) / `did:key`.
- **Decisión:** Cada agente tiene par Ed25519 (generado al registrar el agente; privado en Secret Manager). Por transacción, el agente firma (JWS detached) el payment-intent canónico `{mandate_jti, merchant, item, amount, currency, nonce, iat, exp≈60s}`. El merchant verifica la firma contra `cnf.jwk` → **un agente suplantador sin la clave no puede comprar**.
- **Consecuencias:** Anti-replay con `nonce`/`jti` + constraint UNIQUE en el merchant.

### ADR-005 · Guardrails: policy engine determinista FUERA del LLM
- **Contexto:** OWASP Top 10 LLM 2025 (LLM01 = prompt injection) + OWASP Agentic Top 10 (dic-2025); AgentDojo demuestra que las defensas por prompt caen ante ataques adaptativos.
- **Alternativas:** (a) guardrails de prompt / NeMo conversacional; (b) Guardrails AI (valida outputs, no decisiones); (c) firewall comercial (Lakera — detección); (d) **gate determinista in-process en el wrapper de `pay()`** (+ showcase opcional OPA sidecar en Rego, ~30 líneas).
- **Decisión:** (d). **"El agente propone, el policy engine dispone":** el LLM no tiene NINGUNA ruta a la API de pago que no pase por el gate: firma del mandato, `amount ≤ límite`, categoría/merchant allowlist, ventana temporal, estado no-revocado (check síncrono), `spend_so_far + amount ≤ budget` contra ledger, idempotency key. Defense-in-depth: outputs del merchant (descripciones, reviews) entran delimitados como **datos, no instrucciones** (spotlighting / dual-LLM estilo CaMeL).
- **Consecuencias:** El ataque "compra el iPhone de $5.000" muere en el gate aunque el LLM sea convencido — demostrable en vivo (B3).

### ADR-006 · Orquestación del agente: grafo híbrido propio — las ideas críticas de LangGraph, sin LangGraph (REVISADO v2.3)
- **Contexto:** v2.2 elegía LangGraph por su `interrupt()` first-class y su checkpointer. Decisión del equipo: en un build de 2.5 días con 4 devs, el framework es un **cuello de botella concentrado en C3** — curva de aprendizaje, configuración del checkpointer, dependencias/versiones y debugging de framework ajeno bajo presión de demo. Se adopta una **adaptación híbrida**: retener las ideas críticas que encajan en la solución, eliminar el framework.
- **Las 4 ideas críticas retenidas (y su implementación sin framework):**
  1. **Grafo explícito como flujo de control** — nodos deterministas `perceive → search → propose → gate → await_human? → pay → receipt`; el LLM vive **solo** en `propose`. El agente no elige su camino: el grafo es ~100–150 líneas de código propio (dict de nodos + loop `run()` con hook de persistencia por transición) — auditable de una sentada.
  2. **Checkpointing** — tabla `agent_runs` (schemas.md §6) en el mismo Cloud SQL: cada transición persiste `node + state JSONB + status` → el run sobrevive reinicios/re-deploys de Cloud Run y se depura con un SELECT.
  3. **Interrupt-before-pay** — nodo `await_human` persiste `status='awaiting_human'` y devuelve el control; el resume lo dispara `escalation.resolved` (la aprobación re-entra por el gate, nunca lo bypasea — schemas.md §5). Idempotente por contrato (T7).
  4. **Tools como contrato acotado** — MCP tools según schemas.md §10, outputs = datos, no instrucciones. (Ya era nuestro; el framework solo lo envolvería.)
- **Qué se elimina:** la dependencia `langgraph`, su checkpointer configurable, su curva de aprendizaje y su superficie de debugging. Los nodos son funciones planas testeables con fakes inyectados (LLM, MCP client, `/purchases`) — T3/T7/T11 no cambian.
- **Línea para jueces:** *"El 'framework' del agente son 150 líneas nuestras: un grafo explícito donde el modelo solo propone, y un estado persistido donde cada salto del grafo puede ser un evento de auditoría. Cero magia."*
- **Consecuencias:** C3 no aprende un framework nuevo; el checkpointer ES nuestra tabla; cada transición puede emitirse como evento `agent.node.*` al ledger. Reversible: los nodos son funciones envoltables por LangGraph si el agente creciera post-hackathon.

### ADR-007 · Manejo del instrumento de pago y PII: PayPal vaulting + Presidio (REVISADO v2.1)
- **Contexto:** R1 exige autorizar sin entregar la tarjeta. **El equipo no tiene acceso al sandbox de Yuno**; se necesita un rail con: aprobación única del humano, cobros posteriores solo con token, sandbox instantáneo y gratis, y API de disputas simulable.
- **Alternativas:** Yuno Vault `vaulted_token` (la referencia de diseño — sin acceso); Stripe Issuing virtual cards (requiere permisos de issuing); x402/Coinbase (agentic-native pero irreversible — ver ADR-014); **PayPal vaulting de payment tokens**.
- **Decisión:**
  1. **Enrollment (1 vez):** `POST /v3/vault/setup-tokens` (con `return_url`) → el humano se autentica y aprueba en PayPal (**el "authentication moment"**, análogo funcional al 3DS) → `POST /v3/vault/payment-tokens` → el backend guarda `payment_token_id` como `payment_method_ref` del mandato firmado. El setup token expira en 3 días; el payment token **no expira** hasta `DELETE` — exactamente la semántica de revocación del mandato.
  2. **Compra:** `POST /v2/checkout/orders` con `payment_source.paypal.vault_id` + `intent: CAPTURE` (o `AUTHORIZE` para reservas con captura diferida) → `COMPLETED` **sin interacción del comprador**; idempotencia con `PayPal-Request-Id`. REST directo con `httpx` (los SDKs Python oficiales están deprecated; solo se necesitan ~6 endpoints).
  3. **Disputas:** Customer Disputes API simulable en sandbox (`POST /v1/customer/disputes` con `reason: UNAUTHORISED` + `PayPal-Auth-Assertion` del buyer; `adjudicate` BUYER_FAVOR/SELLER_FAVOR) → alimenta el bonus B1.
  4. **Presidio** (MIT) scrubbea todo lo que entra/sale del agente: logs, trazas LLM, contexts → placeholders.
  5. **Acceso sandbox:** Developer Dashboard → REST app → activar feature "Vault" (checkbox, sin approval en sandbox). Montos en **USD** (COP no es currency soportada).
- **Línea para jueces:** *"El agente nunca ve tarjeta ni cuenta: el instrumento vive tokenizado en PayPal, aprobado una vez por el humano; el agente opera con una referencia sujeta a mandato firmado con límites; cobramos solo con `vault_id` — cero datos de pago en nuestro perímetro, y toda traza LLM pasa redacción PII."*
- **Nota Yuno:** `payment_method_ref` es una abstracción rail-agnostic — el `vaulted_token` de Yuno sería drop-in en producción (así se declara en el decision log; el diseño no cambia de rail).
- **Nota GCP:** el scrubbing ocurre **antes** de emitir a Cloud Logging; credenciales PayPal en Secret Manager; webhooks (`PAYMENT.CAPTURE.COMPLETED`, `CUSTOMER.DISPUTE.*`, `VAULT.PAYMENT-TOKEN.*`) verificados vía `/v1/notifications/verify-webhook-signature` llegando a `api.<dominio>`.

### ADR-008 · HIL: Telegram primario + WhatsApp secundario, fail-closed
- **Contexto:** Escalamiento fuera de mandato con aprobación humana; jueces operan en vivo.
- **Alternativas:** Slack Block Kit (requiere workspace compartido); email (lento); solo dashboard web; **Telegram Bot API** (gratis, inline keyboards, `editMessageText` muta "Pendiente…→APROBADO por JuezX 14:32" — visible en teléfono y pantalla a la vez); **WhatsApp Cloud API** (vistoso pero frágil: business-initiated requiere template aprobada; ventana gratis 24 h si el juez escribe primero).
- **Decisión:** Telegram primario (webhook HTTPS apuntando al servicio `api` en Cloud Run — el dominio propio del ADR-018 sirve) + WhatsApp pre-calentado secundario. Mensaje: agente, ítem, merchant, importe, **por qué escala** ("$89 > límite $50"), countdown, botones `[Aprobar] [Rechazar] [Ver detalle]`; sticky: "aprobar una vez" vs "aprobar categoría hasta $X" (mini-mandato firmado nuevo). **Timeout 120 s → auto-deny (fail-closed)**.
- **Consecuencias:** Approval receipt (quién, cuándo, canal, device) → insumo del evidence bundle.

### ADR-009 · Trail de auditoría: hash chain + roots firmados (KMS) + witness en GCS
- **Contexto:** R5 + B1: trail legible (humano/merchant/auditor) y **verificable** (mutación detectable). HMAC chain no da no-repudio; Trillian/Merkle completo es desproporcionado.
- **Alternativas:** logs en Cloud Logging/GCS (no verificable criptográficamente); Elasticsearch; Trillian; **cadena SHA-256 por evento (`prev_hash`) + root firmado**.
- **Decisión:** Tabla `audit_events` append-only: `seq, mandate_id, type, payload JSONB, prev_hash, hash, root_sig, created_at`; inserción serializada con `pg_advisory_xact_lock` en Cloud SQL. El **root se firma con Cloud KMS (`EC_SIGN_ED25519`)** — la clave jamás sale de Google; el servicio solo llama `asymmetricSign`. Endpoint `/audit/verify` re-computa la cadena en vivo ante los jueces. **Witness externo:** bucket GCS con Object Versioning y lectura pública del prefijo `/roots` — el root firmado vive fuera del servidor del demo (lo que convierte tamper-evidence en responsabilidad real). Cloud Audit Logs se citan como capa complementaria de infraestructura.
- **Consecuencias:** Efecto wow criptográficamente defendible; costo KMS despreciable (~US$0.06/mes).

### ADR-010 · Infraestructura de eventos: outbox en Postgres + polling SKIP LOCKED (REVISADO v2)
- **Contexto v1:** whiteboard proponía RabbitMQ; v1 propuso outbox + `LISTEN/NOTIFY`. **En GCP serverless eso no funciona:** Cloud Run escala a cero y corta conexiones persistentes — `LISTEN/NOTIFY` exige conexión viva permanente.
- **Alternativas:** Kafka/EventStoreDB (overkill); RabbitMQ autogestionado (contenedor que mantener — lo que queríamos evitar); Pub/Sub (managed, 10 GiB/mes gratis — pero añade una pieza y el fan-out real es de 2–3 consumidores); **polling `FOR UPDATE SKIP LOCKED` cada 1–2 s** desde el servicio `api` hacia Cloud SQL.
- **Decisión:** Outbox en Cloud SQL como fuente de verdad + relé por polling `SKIP LOCKED` (1–2 s de latencia, irrelevante para la demo) que alimenta: SSE del dashboard, bot de Telegram y webhook al merchant. Pub/Sub queda documentado como camino de evolución si el fan-out crece — decisión explícita, no omisión.
- **Consecuencias:** Cero brokers; transacciones atómicas reales (outbox en la misma tx que el negocio); argumento de defensa más fuerte. La BD ya está pagada y conectada por unix socket.

### ADR-011 · Condiciones del mandato: JsonLogic + limits declarativos
- **Contexto:** Bonus B2: "si baja de $150" y "hasta 3 veces al mes" evaluados correctamente y mostrables en UI de auditoría.
- **Alternativas:** CEL (strings — difícil de firmar canónicamente y renderizar); json-rules-engine (async rompe determinismo); Rego para condiciones (necesita OPA); LLM evaluando (no determinista — inaceptable); **JsonLogic** (reglas = JSON puro = AST firmable, versionable, renderizable; puertos JS/Python).
- **Decisión:** Condiciones binarias en JsonLogic (`{"<": [{"var":"offer.price"}, 150]}`) embebidas en el mandato firmado + bloque `limits` declarativo (counters: `max_txn {count:3, period:"month"}`, `total_budget`) evaluado por el gate contra el ledger.
- **Consecuencias:** La misma expresión que firma Marta es la que evalúa el gate y la que ve el auditor — trazabilidad de extremo a extremo.

### ADR-012 · Revocación viva: check síncrono en pay-time + kill-switch del rail (REVISADO v2.1)
- **Contexto:** R6/Trial by fire: revocar → siguiente intento falla. Un push perdido o una cache vencida rompen el guion.
- **Alternativas:** solo push async (se pierde → falla el trial); solo cache TTL (ventana de vulnerabilidad); CRL/OCSP; **verificación síncrona en pay-time contra la fuente de verdad + push async para UX**.
- **Decisión:** (1) Al pagar, el merchant llama `POST /mandates/:id/verify` (idempotency key); en UNA transacción: re-evalúa JsonLogic + status + límites + **reserva atómica de presupuesto** (`UPDATE mandates SET reserved = reserved + $amt WHERE id=$1 AND status='active' AND limit_remaining >= $amt` — 0 filas = rechazo). (2) Revocar = passkey de Marta + transición con guard (`AND status IN ('active','suspended')`) + evento auditado + push a canales + **`DELETE /v3/vault/payment-tokens/{id}` en PayPal — el rail mismo deja de cobrar (doble kill-switch: aplicación y payment rail)**. (3) El agente nunca cachea la política.
- **Consecuencias:** TOCTOU cerrado por diseño; latencia objetivo ≤ 2 s (misma región Cloud Run↔Cloud SQL). La revocación es defendible en dos capas independientes.

### ADR-013 · Integración merchant: API REST de verificación + MCP server para el agente
- **Contexto:** VuelaYa debe verificar mandatos; el agente debe descubrir/comprar. MCP tiene riesgos documentados (tool poisoning, confused deputy, token passthrough).
- **Decisión:** (1) **REST** `POST /mandates/:id/verify` + webhooks firmados; (2) **MCP server** con tools de solo-lectura (catálogo, precios) + tool `purchase` cuyo handler pasa INTERNAMENTE por el policy gate y el verify endpoint — nunca un endpoint de pago directo. Outputs del merchant = datos delimitados; nunca reenviar tokens del usuario.
- **Consecuencias:** El "creative path" es imposible por construcción: única ruta al dinero = gate. Servicio-a-servicio interno protegido con **ID tokens OIDC + `roles/run.invoker`** (`--no-allow-unauthenticated`).

### ADR-014 · Rail de pago: PayPal sandbox — x402/Coinbase documentado como alternativa (REVISADO v2.1)
- **Contexto:** El pago puede ser mockeable, pero un rail real suma. **Sin acceso a Yuno**, se evaluaron con evidencia: **PayPal** (sandbox instantáneo/gratuito; vaulting de payment tokens; disputas simulables; rails reversibles = encaja con el framing de chargeback del reto) y **Coinbase x402** (HTTP-402 nativo para máquinas, EIP-3009 gasless — el payer no paga gas, el facilitator relaya; settlement USDC en Base en ~2–5 s; testnet Base Sepolia gratis con faucets CDP/Circle; wallet del agente server-side vía CDP Wallets API sin tocar private keys; facilitator CDP gratis hasta 1.000 settlements/mes).
- **Decisión:** **PayPal sandbox como rail principal** (flujo en ADR-007): enrollment con setup tokens → `payment_token_id` en el mandato → cobros con `vault_id` → disputas simuladas → revocación con `DELETE` del payment token. **x402 queda como alternativa documentada y camino de evolución**: si sobra tiempo el Día 3, un endpoint "machine-payable" de micro-compra ($0.01–0.50 USDC en Base Sepolia) con wallet CDP del agente — costo 0 y gran valor demo. **No reemplaza a PayPal** porque on-chain no existe reversión: no permitiría demostrar el flujo de disputa (B1), y el SDK Python de x402 no trae spending controls nativos (el gate compensa).
- **Defensa:** PayPal co-creó AP2 con Google — su Intent Mandate (Human-Not-Present) pre-aprueba "budget, categories, timing": exactamente nuestro mandato. La solución replica la dirección oficial del rail elegido.
- **Nota GCP:** llamadas desde Cloud Run a `api-m.sandbox.paypal.com` (egress estándar, sin VPC); credenciales en Secret Manager.

### ADR-015 · Plataforma de despliegue: Cloud Run (NUEVO v2)
- **Contexto:** El sistema se despliega en GCP. Stack: servicios FastAPI + agente con grafo propio (ADR-006) + frontend React + webhook Telegram + watcher cron.
- **Alternativas:** GKE (Kubernetes — operativo para 2-3 días de build); Compute Engine (servers que parchear); Cloud Functions (por request, mal para SSE/conexiones y checkpointer); App Engine (menos flexible con contenedores); **Cloud Run** (contenedores serverless: sidecars GA, `--min-instances`, streaming SSE nativo, hasta 60 min de timeout, Cloud SQL integrado con unix socket, ID tokens servicio-a-servicio, custom domain con TLS gestionado, y `gcloud run deploy --source` construye con buildpacks sin Dockerfile).
- **Decisión:** **Todo en Cloud Run, región `southamerica-east1`**: `api` (BFF: mandates, verify, audit, webhook Telegram, SSE; `--min-instances=1` solo en ventana de demo), `agent` (grafo propio, ADR-006; `--min-instances=1` en demo), `merchant` (VuelaYa mock + integración PayPal), `web` (React estático, concurrencia alta), y **`watcher` como Cloud Run job** (ver ADR-019). Cada servicio con service account dedicada de mínimo privilegio; interno con `--no-allow-unauthenticated`.
- **Consecuencias:** Deploy el Día 0 con `--source`; costos dentro de free tier salvo min-instances y Cloud SQL.

### ADR-016 · Datos: Cloud SQL Postgres Enterprise `db-f1-micro` (NUEVO v2)
- **Contexto:** Fuente única de verdad (mandatos, ledger, outbox, runs del agente — checkpointing `agent_runs` del ADR-006) en Postgres.
- **Alternativas:** AlloyDB (orientado a performance, ~10× costo); Firestore (no relacional — las transacciones con guard y SKIP LOCKED son del corazón del diseño); **Postgres como sidecar en Cloud Run** (barato pero efímero — inaceptable para revocación/exhaustibilidad que deben sobrevivir reinicios); **Cloud SQL** (managed, backups, unix socket desde Cloud Run sin VPC ni IP pública).
- **Decisión:** **Cloud SQL Postgres, edición Enterprise, `db-f1-micro`** (0.2 vCPU compartido / 640 MB — suficiente para la demo), **sin IP pública**, conexión vía unix socket `/cloudsql/<PROJECT>:<REGION>:<INSTANCE>` con `cloud-sql-python-connector` + pool (SQLAlchemy/asyncpg). Región `southamerica-east1`.
- **Consecuencias:** ~US$10/mes; sin VPC connector; límite de conexiones bajo → pool pequeño y máximo 1–2 instancias por servicio en demo.

### ADR-017 · Claves y secretos: Secret Manager + Cloud KMS `EC_SIGN_ED25519` (NUEVO v2)
- **Contexto:** Hay tres tipos de material sensible: (1) claves de emisión de mandatos (SD-JWT), (2) clave de roots/webhooks, (3) credenciales de terceros (PayPal, Telegram, LLM).
- **Alternativas:** claves en variables de entorno del código (inaceptable — aparecen en logs/builds); claves en BD; todo en Secret Manager; **Secret Manager + Cloud KMS para firmas**.
- **Decisión:**
  - **Secret Manager** (≤6 versiones gratis, cacheadas en runtime): PEM Ed25519 del issuer SD-JWT (compatibilidad con librería `sd-jwt`), PEM del agente comprador, **credenciales PayPal (`client_id`/`secret`)**, Telegram, LLM.
  - **Cloud KMS** key purpose `ASYMMETRIC_SIGN`, algoritmo **`EC_SIGN_ED25519`** (EdDSA puro sobre datos crudos — nombre exacto; NO existe `ED25519_SIGN` en GCP): firma los roots del ledger y los webhooks. La clave jamás sale; el servicio llama `asymmetricSign`. Costo ~US$0.06/mes.
  - JWKS del issuer publicado en `https://api.<dominio>/.well-known/jwks.json` (el `kid` referencia la versión activa — rotación story para jueces).
- **Consecuencias:** Historia de manejo de claves de nivel producción por centavos; rotación documentada.

### ADR-018 · Dominio, TLS y passkeys en GCP (NUEVO v2)
- **Contexto (hallazgo crítico):** `run.app` está en la **Public Suffix List** (sección de Google). La spec WebAuthn L3 exige que `rpId` sea un *registrable domain* — **los passkeys NO funcionan en `https://SERVICE.PROJECT.REGION.run.app`** (el navegador rechaza `create()/get()`).
- **Alternativas:** demo de passkeys en localhost (funciona pero destruye la narrativa "desplegado en GCP"); renunciar a passkeys por OTP (pierde el alignment FIDO); **comprar un dominio y mapearlo a Cloud Run**.
- **Decisión:** Comprar dominio (`.app`/`.dev`, ~US$12/año — Cloud Domains o cualquier registrar) el **Día 0** (la propagación DNS/TLS gestionado puede tardar). Mapear subdominios a Cloud Run: `app.<dominio>` (web), `api.<dominio>` (API + webhook Telegram + JWKS), `merchant.<dominio>` (VuelaYa). TLS gestionado automático. `rpId = <dominio>`, `origins = https://app.<dominio>`. **Fallback si el dominio no llega a tiempo:** passkeys en localhost para la ceremonia y el resto del circuito en GCP (documentar como contingencia).
- **Consecuencias:** Necesita comprarse antes del build del Día 1 (tarea crítica path).

### ADR-019 · Watcher de precios: Cloud Run job + Cloud Scheduler OIDC (NUEVO v2)
- **Contexto:** El agente "vigila" precios de VuelaYa y compra cuando JsonLogic se cumple. Necesita ejecución periódica sin exponer endpoints.
- **Alternativas:** loop `setInterval` dentro del servicio `api` (muere con la instancia, indeterminista); endpoint HTTP público con token propio (superficie de ataque + secretos); cron en GKE/CE; **Cloud Run job disparado por Cloud Scheduler con token OIDC** (el Scheduler invoca el job con identidad de service account; IAM valida — patrón documentado por Google).
- **Decisión:** `watcher` como **Cloud Run job** (mismo contenedor del agente, comando watcher): consulta catálogo → evalúa JsonLogic → dispara compra vía el gate. Scheduler cada 1–5 min (3 jobs gratis). **Además, trigger manual `POST` autenticado con OIDC para controlar el timing en vivo ante jueces** (el guion no depende del cron).
- **Consecuencias:** Sin endpoints públicos extra; timing de demo controlable.

### ADR-020 · CI/CD e IaC: GitHub Actions + WIF → Artifact Registry → Cloud Run; bootstrap con gcloud (NUEVO v2)
- **Contexto:** Repo público en GitHub es entregable del reto; deploy reproducible (O8) y rápido de configurar en 2-3 días.
- **Alternativas:** deploys manuales (no reproducibles — falla O8); Cloud Build triggers (válido; 2.500 min/mes gratis); Terraform desde el día 1 (curva + tiempo); **GitHub Actions con Workload Identity Federation** (sin claves de servicio en el repo — patrón oficial `google-github-actions/auth` + `deploy-cloudrun`) + bootstrap con `gcloud run deploy --source` (buildpacks, sin Dockerfile) el Día 0.
- **Decisión:** Día 0: bootstrap con `gcloud --source` (velocidad). Día 1: pipeline GH Actions (WIF → build → push a Artifact Registry → deploy a Cloud Run) disparado por push a `main`; `bootstrap.sh` idempotente (proyecto, APIs, Cloud SQL, AR, secrets, dominio, scheduler) versionado en el repo. Terraform = roadmap post-hackathon (se declara en el decision log).
- **Consecuencias:** Cada push deja el ambiente de demo actualizado; el ensayo (G6) corre contra el ambiente real.

### ADR-021 · LLM del agente: Vertex AI Gemini (pagado, cubierto por créditos) u OpenAI (NUEVO v2)
- **Contexto:** El free tier de Gemini API fue recortado en dic-2025 (Flash ~20 requests/día) — **inutilizable para demo en vivo**. Los US$300/90 días de la cuenta nueva cubren Vertex AI (pagado), no la API de AI Studio. El hackathon es apoyado por OpenAI (posibles créditos). El gate es agnóstico al modelo.
- **Alternativas:** Gemini API free tier (descartado por rate limit); **Vertex AI Gemini** (GCP-native, REST/SDK directo, facturado dentro de los créditos); **OpenAI API** (si hay créditos del hackathon; misma arquitectura).
- **Decisión:** Elegir por créditos disponibles y latencia observada en `southamerica-east1`; la arquitectura no cambia. *Recomendación por defecto: Vertex AI (todo en una nube, un solo billing); si el equipo tiene créditos OpenAI del hackathon, usarlos.* La API key/credenciales en Secret Manager; el LLM NUNCA recibe la clave del agente ni tokens de pago (solo el prompt y las tools).
- **Consecuencias:** Decidir el Día 0 (D5 en §14) para no retrasar el build del Día 1.

### ADR-022 · Topología de despliegue: microservicios con el frontend como servicio independiente (NUEVO v2.2)
- **Contexto:** 4 devs construyendo en paralelo sobre GCP. La arquitectura v2.1 ya despliega servicios separados en Cloud Run (`api`, `agent`, `merchant`, `web`, `watcher`), pero el plan de ejecución v1 repartía las vistas del frontend entre 3 devs dentro de una sola app React. Decisión explícita del equipo: **"front aparte aparte"** — el frontend es un servicio y un workstream propio.
- **Alternativas:** (a) **monolito modular** (un solo deployable con módulos por dueño): menos infra, pero acopla deploys y CI, genera conflictos de merge en un código compartido y un módulo roto tumba el demo entero — inaceptable para 4 builds asíncronos; (b) **microservicios con frontend compartido entre devs** (v1 del plan paralelo): el backend queda limpio pero el frontend se convierte en hotspot de coordinación (shell, rutas, estado global, PRs cruzados de 3 dueños); (c) **microservicios + frontend como servicio/workstream de un solo dev**, consumiendo exclusivamente contratos públicos.
- **Decisión:** (c). Cinco deployables independientes en Cloud Run: `web` (SPA React estática), `api` (routers de mandatos [A] y decisión [B]), `agent` + `watcher` job [C], `merchant` [C], más los servicios gestionados (Cloud SQL, KMS, Secret Manager, GCS, Scheduler). **El frontend solo consume los contratos congelados** (`contracts/api.yaml`, SSE, JWKS): cero código compartido con el backend. Los tipos TypeScript se **generan** del OpenAPI (`openapi-typescript`) desde la misma fuente de la que deriva `trustlib` (Pydantic) — una sola fuente de verdad, dos generaciones. CORS por contrato para `app.<dominio>`; sesión de Marta entre `web` y `api` (cookie SameSite; BFF-lite solo si el ensayo lo exige).
- **Consecuencias:** + cada servicio se despliega y escala por separado (CI dispara por rutas del monorepo); + el Dev del frontend trabaja 100% contra mocks/staging sin tocar Python; + un fallo en un servicio no derriba las vistas de los demás (el demo degrada, no muere); − un contrato más que mantener con disciplina de versionado (reglas §6 de PLAN-PARALELO); − duplicación aparente de tipos eliminada por codegen pero exige regenerar en cada bump del contrato; − CORS/auth como superficie adicional propiedad de `web`.

---

## 6. Arquitectura objetivo (vista lógica + vista de despliegue GCP)

### 6.1 Vista lógica

```mermaid
flowchart TB
    subgraph HUMAN["MARTA (humano)"]
        UI[Web App: crear/revocar mandato<br/>dashboards]
        TG[Telegram/WhatsApp<br/>aprobar · pausar]
        PK[(Passkey<br/>WebAuthn)]
    end

    subgraph CHANNEL["CANAL DE CONFIANZA (nuestro producto)"]
        BFF[BFF / API Gateway]
        MR[Mandate Registry<br/>emite SD-JWT firmado<br/>JWKS /.well-known]
        VR[Verify Endpoint<br/>POST /mandates/:id/verify<br/>check síncrono + reserva atómica]
        PG[Policy Gate determinista<br/>JsonLogic + limits + ledger<br/>FUERA del LLM]
        PL[(Proof Ledger<br/>audit_events hash-chained<br/>roots firmados KMS)]
        OUT[Outbox en Postgres<br/>→ relé SKIP LOCKED → SSE · bot · webhooks]
        PS[Presidio PII-scrubber<br/>logs & LLM traces]
    end

    subgraph AGENT["AGENTE COMPRADOR"]
        LLM[Grafo propio del agente<br/>nodo await_human antes de pay<br/>LLM solo en propose]
        AK[(Par Ed25519 propio<br/>cnf.jwk del mandato)]
        MCP[MCP client<br/>catálogo/precios]
    end

    subgraph MERCHANT["VUELAYA (merchant)"]
        CAT[Catálogo / ofertas API<br/>(+ descripciones con inyección<br/>para el demo adversarial)]
        CHK[Checkout<br/>verifica mandato antes de aceptar]
    end

    subgraph RAIL["PAYPAL (rail de pago)"]
        VLT[Vault · payment tokens<br/>setup token → aprobación<br/>humana única]
        PAY[Sandbox Orders v2<br/>capture con vault_id]
        DSP[Customer Disputes API<br/>disputa simulada sandbox]
    end

    subgraph AUD["AUDITOR"]
        AV[UI Auditor<br/>/audit/verify en vivo]
    end

    UI -->|passkey challenge<br/>con hash del mandato| MR
    UI -.aprueba instrumento 1 vez.-> VLT
    TG <--> OUT
    MR -->|SD-JWT| BFF
    MCP -->|descubre ofertas| CAT
    LLM -->|purchase intent firmado| PG
    AK -.firma intent.-> LLM
    PG -->|gate OK| VR
    VR -->|verificación síncrona| CHK
    CHK -->|cobra con vault_id| PAY
    VLT --> PAY
    UI -.disputa B1.-> DSP
    MR --> PL
    VR --> PL
    OUT --> PL
    PS -.envuelve.-> LLM
    AV --> PL
    TG -->|revocar/pausar| MR
```

### 6.2 Vista de despliegue en GCP (región única `southamerica-east1`)

```mermaid
flowchart TB
    subgraph INTERNET["Usuarios / Jueces"]
        B[navegador<br/>https://app.dominio.app]
        T[Telegram / WhatsApp]
        J[Jueces operando en vivo]
    end

    subgraph DNS["dominio propio (OBLIGATORIO: passkeys)"]
        D1[app.dominio → web]
        D2[api.dominio → api]
        D3[merchant.dominio → merchant]
    end

    subgraph CLOUDRUN["Cloud Run (serverless) · suramerica-east1"]
        WEB[web · React estático<br/>allow-unauthenticated]
        API[api · FastAPI<br/>BFF + mandates + verify + audit<br/>+ webhook Telegram + SSE<br/>min-instances=1 en demo]
        AG[agent · grafo propio (ADR-006)<br/>MCP client + gate wrapper<br/>min-instances=1 en demo]
        MER[merchant · VuelaYa mock<br/>+ integración PayPal]
        WT[watcher · Cloud Run JOB<br/>ejecuta bajo Scheduler]
    end

    subgraph MANAGED["Servicios gestionados"]
        SQL[(Cloud SQL Postgres<br/>Enterprise db-f1-micro<br/>sin IP pública · unix socket)]
        KMS[Cloud KMS<br/>EC_SIGN_ED25519<br/>firma roots/webhooks]
        SM[Secret Manager<br/>PEMs · PayPal · Telegram · LLM]
        GCS[(GCS bucket versionado<br/>witness de roots firmados)]
        SCHED[Cloud Scheduler<br/>cada 1-5 min · OIDC → watcher]
        AR[Artifact Registry]
    end

    subgraph EXT["Externos"]
        PP[PayPal sandbox<br/>api-m.sandbox.paypal.com<br/>vault · orders · disputes]
        LLMV[Vertex AI Gemini<br/>u OpenAI API]
    end

    B --> WEB
    T -->|webhook HTTPS| API
    D2 --> API
    J --> T
    API <-->|unix socket /cloudsql| SQL
    AG <-->|unix socket| SQL
    MER <-->|unix socket| SQL
    AG --> LLMV
    MER -->|REST · vault_id| PP
    API -->|asymmetricSign| KMS
    API -->|roots| GCS
    SCHED -->|OIDC| WT
    WT -->|invoca via ID token| AG
    API -->|ID token OIDC| AG
    API -->|ID token OIDC| MER
    PP -.webhooks firmados.-> API
    SM -.monta secretos.-> API
    SM -.monta secretos.-> AG
```

**Topología de servicio-a-servicio:** `api`, `agent` y `merchant` se llaman entre sí con **ID tokens OIDC firmados por Google** + `roles/run.invoker` (`--no-allow-unauthenticated`) — misma disciplina de identidad verificable que predicamos para los mandatos, aplicada a nuestra propia infraestructura. Públicos solo: `web`, y `api` en las rutas de webhook Telegram (validadas con el token secreto del bot), webhooks PayPal (verificación de firma) y las APIs de browser.

### 6.3 Costos estimados (1 mes, región suramerica-east1)

| Servicio | Config | Costo aprox/mes |
|---|---|---|
| Cloud Run | free tier (2M req, 180k vCPU-s, 360k GiB-s) | US$0 (+US$5–15 si min-instances 24/7 durante todo el mes; solo activar en ventana de demo) |
| Cloud SQL | Enterprise `db-f1-micro` + 10 GB, sin IP pública | ~US$10 |
| Cloud KMS | 1–2 llaves `EC_SIGN_ED25519` | ~US$0.06 |
| Secret Manager | ≤6 versiones + accesos | ~US$0 |
| Artifact Registry | 2–5 GB imágenes | ~US$1–3 |
| Cloud Storage | roots witness (<1 GB) | ~US$0 |
| Cloud Scheduler | 1–2 jobs (3 gratis) | US$0 |
| Cloud Logging | 50 GiB/mes gratis | US$0 |
| **Total** | | **~US$12–28 → US$0 desembolsado con créditos de prueba (US$300/90 días)** |
| Dominio `.app`/`.dev` | registro anual | ~US$12/año |
| PayPal sandbox | gratis | US$0 |
| LLM | Vertex AI Gemini pagado (cubierto por créditos) o créditos OpenAI | variable, dependerá del ensayo |

---

## 7. Máquina de estados del mandato + casos feos (R4)

```
draft → active → (suspended ⇄ active) → {revoked | expired | exhausted}   [terminales]
```

| Caso feo | Mecanismo que lo cubre | Evidencia en demo |
|---|---|---|
| Monto excedido ($300 > $150) | Gate: `amount ≤ max_per_txn` → reject o escalate (HIL con diff) | Mensaje Telegram: "fuera de mandato: $300 > $150" + botones |
| Categoría prohibida | Gate: `category ∈ allowlist` | Rechazo explícito, evento auditado |
| Mandato expirado | Gate/verify: ventana temporal | Rechazo con motivo `MANDATE_EXPIRED` |
| **Revocación en vivo** | Verify síncrono en pay-time + guard en transición + **`DELETE` del payment token en PayPal (doble kill-switch)** | Juez revoca desde su teléfono → siguiente intento falla ≤ 2 s con `POLICY_REVOKED` |
| Presupuesto agotado ("3 veces al mes") | Contador en ledger + reserva atómica | 4ª compra rechazada `LIMIT_EXHAUSTED` |
| Agente suplantado | Firma Ed25519 del intent vs `cnf.jwk` | Attack demo: agente clon sin clave → `INVALID_PROOF_OF_POSSESSION` |
| Prompt injection ("creative path") | Gate determinista + tool outputs como datos delimitados | Descripción maliciosa → LLM convencido igualmente bloqueado en el gate |
| Doble compra concurrente (race) | Reserva atómica `UPDATE ... WHERE limit_remaining >= amt` | Test concurrente: solo 1 de 2 pasa |
| Replay del intent | `nonce`/`jti` + constraint UNIQUE | Reenvío del mismo intent → `DUPLICATE_JTI` |
| Disputa posterior | Evidence bundle del ledger: mandato → intent firmado → approval receipt → captura PayPal → entrega; **disputa real simulada con Customer Disputes API** | Veredicto automático en UI auditor + adjudicación sandbox |
| **PayPal sandbox caído (v2.1)** | Circuit breaker en BFF → mock idéntico en interfaz | El circuito mandato/verificación (lo evaluable) sigue funcionando |
| **Reinicio/re-deploy de servicios (v2)** | Estado vive en Cloud SQL; Cloud Run es stateless | Re-deploy en vivo sin perder mandatos ni ledger |

---

## 8. Supuestos (a validar / declarar)

| ID | Supuesto | Impacto si falla | Mitigación |
|---|---|---|---|
| S1 (v2.1) | PayPal sandbox: cuenta developer + REST app + checkbox "Vault" accesibles al instante, sin approval | Medio | Smoke test Día 0 (30 min); si el checkbox no aparece → plan B Braintree sandbox o mock con la misma interfaz |
| S2 | Equipo trabaja en Python (AP2 SDK) + React para frontend | Medio | Orquestador del agente = grafo propio sin framework (ADR-006): nada que "trabe" — ~150 líneas propias |
| S3 | Jueces tienen teléfono para Telegram / el equipo lo provee | Medio | Dashboard web con los mismos botones como respaldo |
| S4 | Ventana WhatsApp 24h pre-calentada (juez escribe primero) | Bajo | WhatsApp secundario; Telegram primario |
| S5 | Latencias LLM aceptables para demo en vivo | Medio | Modelo rápido; trigger manual del watcher controla el timing |
| S6 | **Dominio propio comprado y DNS propagado antes del Día 1** (passkeys) | **Alto** | Comprar el Día 0; fallback localhost solo para la ceremonia passkey |
| S7 | Catálogo VuelaYa con inyecciones adversariales suficiente para B3 | Bajo | Fixtures de test |
| S8 | No se requiere cumplimiento legal real (demo) | — | Se citan PSD2 SCA, Reg E, EU AI Act Art.50 (en vigor 02-ago-2026) |
| S9 | Duración del build ≈ 2–3 días | Alto | Priorización estricta §11; happy path primero |
| S10 (v2) | Cuenta GCP con billing activo y créditos US$300/90 días | Alto | Registrarla el Día 0; monitorear presupuesto |
| S11 (v2) | Cuotas/rate limits de Vertex AI (o créditos OpenAI) suficientes | Medio | Decidir ADR-021 el Día 0; probar latencia desde suramerica-east1 |
| S12 (v2) | `db-f1-micro` soporta la carga del demo (pocas conexiones) | Bajo | Pool pequeño; máximo 1–2 instancias por servicio; carga de demo trivial |
| S13 (v2.1) | Disputas sandbox funcionan sobre transacciones wallet-vaulted (no solo card-funded) | Medio | Probar en smoke test; fallback: Webhook Simulator / `process-chargeback` |
| S14 (v2.1) | Montos en USD (COP no soportado) — aceptable para jueces LATAM | Bajo | Mostrar equivalencia aproximada en la UI |

---

## 9. Gates de calidad (criterios de avance entre fases)

| Gate | Criterio de salida | Falla típica que previene |
|---|---|---|
| **G0 · Diseño congelado** | ADRs 1–22 revisados; schema del mandato congelado; diagrama v1; **dominio comprado y proyecto GCP con bootstrap corriendo; PayPal sandbox con smoke test en verde** | Refactor de cripto/infra a mitad del build |
| **G1 · Cripto núcleo** | Crear + verificar mandato SD-JWT; mutar 1 byte → rechazo; KB-JWT con nonce; **root firmado por KMS verificable** | "No-repudio" que no resiste un curl |
| **G2 · Circuito feliz** | Compra end-to-end dentro de mandato (Marta→agente→gate→verify→captura PayPal con `vault_id`→receipt→Telegram) SIN intervención manual — **corriendo en Cloud Run, no en localhost** | Demo que requiere "tocar algo" |
| **G3 · Casos feos** | Los 12 casos de §7 con test automatizado en verde; revocación ≤ 2 s y race de doble compra | Trial by fire fallido |
| **G4 · Adversario** | Mini-AgentDojo: inyecciones embebidas en catálogo → 100% bloqueadas por el gate (no por el prompt) | Que el jurado hackee al agente en vivo |
| **G5 · Disputa + auditor** | "Nunca autorizé esto" → disputa simulada PayPal + evidence bundle + veredicto; `/audit/verify` re-valida cadena en vivo; mutación de 1 evento detectada | Bonus points perdidos |
| **G6 · Ensayo general** | Guion cronometrado ejecutado 2× por personas ajenas al código **contra el ambiente GCP con min-instances activados** | Improvisación el día D; cold starts |
| **G7 · Entregables** | Slides + README + diagrama + decision log (ADRs) + video backup | Requisito de entrega incompleto |
| **G8 · Despliegue (v2)** | Push a `main` → pipeline GH Actions despliega automáticamente; `bootstrap.sh` reproduce el ambiente desde cero; demo corre en `https://app.<dominio>` con TLS y passkeys funcionando | Demo atada a la laptop de alguien |

---

## 10. Estrategia TDD (test-driven, trial-first)

**Principio rector: el guion del "trial by fire" se escribe primero como tests — el demo es un test que pasa en vivo.**

| ID | Test | Tipo | Asegura |
|---|---|---|---|
| T1 | **Property-based (hypothesis):** cualquier intent aleatorio fuera de límites es rechazado — invariant: *"nunca aprueba fuera de mandato"* | Propiedad | R4/O4 — el corazón del reto |
| T2 | Firma/verificación SD-JWT: válido pasa; payload mutado 1 byte, firma de otro issuer, `exp` vencido, `jti` duplicado → rechazados | Unit | G1 |
| T3 | KB-JWT: nonce incorrecto, `aud` equivocado, sin key binding → `INVALID_PROOF_OF_POSSESSION` | Unit | Anti-impersonación |
| T4 | Verify endpoint: revocado/expirado/exhaustado/límite → códigos de error explícitos distintos | Contract | R4 |
| T5 | **Race double-spend:** 2+ requests concurrentes → exactamente 1 confirma (reserva atómica) | Concurrencia | Integridad del ledger |
| T6 | **TOCTOU revocación:** revocar entre decisión del gate y execute → verify dentro de la misma tx falla | Integración | Trial by fire |
| T7 | **Idempotencia de reanudación:** run del agente (`agent_runs`, nodo `await_human`) reanudado 2× → 1 solo cargo | Integración | Sin doble cargo en HIL |
| T8 | Máquina de estados: transiciones inválidas → 0 filas + evento de rechazo | Unit | Integridad |
| T9 | Hash chain: mutar/eliminar/reordenar 1 evento → `/audit/verify` falla; root KMS inválido → falla | Unit + E2E | R5/O5, B1 |
| T10 | JsonLogic: condiciones ricas ("<$150 Y flights", "3/mes", ventana, borde $150) | Unit | B2 |
| T11 | **Injection suite:** ≥10 prompts maliciosos en descripciones → bloqueados por gate aunque el LLM "quiera" | E2E adversarial | B3/G4 |
| T12 | Scrubber Presidio: email/PAN/nombre en payload → placeholder en log/traza | Unit | ADR-007 |
| T13 | **Demo-as-code:** guion completo (crear → comprar → $300 rechazado → revocar → falla → disputa) como test E2E contra APIs | E2E | Ensayo reproducible |
| T14 | Webhooks firmados: firma inválida → rechazado | Contract | ADR-013 |
| T15 (v2) | **Smoke contra GCP:** T13 apuntando a `https://api.<dominio>` (ejecutado post-deploy desde CI y antes del ensayo) — valida TLS, passkey ceremony (rpId), webhook Telegram, unix socket a Cloud SQL, firma KMS | E2E infra | G8/O8 |
| T16 (v2) | **Bootstrap idempotente:** correr `bootstrap.sh` 2× no duplica recursos ni rompe el estado | Script | G8 |
| T17 (v2.1) | **Rail PayPal:** enrollment produce payment token; captura con `vault_id` idempotente (`PayPal-Request-Id` repetido → mismo resultado); `DELETE` token → cobro posterior falla (revocación a nivel rail) | Integración | ADR-007/012/014, trial by fire |
| T18 (v2.1) | **Disputa PayPal:** crear disputa `UNAUTHORISED` en sandbox → webhook firmado verificado → evidence bundle resuelve → adjudicación coherente | E2E | B1 |

Orden de escritura: **T1–T2 primero** (definen el contrato del gate y del mandato), luego T4–T6, el resto en paralelo al build. **Paridad local:** `docker-compose` (Postgres + servicios) para iterar rápido — con PayPal sandbox compartido; los tests T13–T15 corren contra el ambiente desplegado.

---

## 11. Plan de ejecución (Día 0 + 2.5 días)

### Día 0 — Bootstrap GCP, dominio y PayPal (crítico path, ~3–4 h)
1. Crear proyecto GCP con billing (créditos US$300), habilitar APIs (run, sqladmin, artifactregistry, secretmanager, cloudkms, scheduler, iamcredentials).
2. **Comprar el dominio** (ADR-018) y empezar el mapeo a Cloud Run (la propagación corre en paralelo).
3. `bootstrap.sh`: Cloud SQL `db-f1-micro` (sin IP pública), Artifact Registry, Secret Manager (seeds), KMS `EC_SIGN_ED25519`, bucket GCS versionado, service accounts con mínimo privilegio.
4. Deploy "hello world" de cada servicio con `gcloud run deploy --source` — probar unix socket, SSE y dominio con TLS.
5. Decidir ADR-021 (Vertex vs OpenAI) midiendo latencia desde suramerica-east1.
6. Bot de Telegram creado + webhook apuntando a `api.<dominio>`.
7. **PayPal sandbox (v2.1):** cuenta developer → REST app con feature "Vault" activada → cuentas buyer/business de prueba pre-creadas → **smoke test de 30 min** (OAuth → setup token → aprobación → payment token → order con `vault_id` → disputa simulada → `DELETE` token → cobro falla). Si el checkbox Vault no aparece: activar plan B (Braintree/mock) HOY, no el Día 2.
8. Congelar ADRs + schema de mandato (G0).

### Día 1 — Fundación + circuito feliz (Gates G1–G2, G8 base)
1. Mandate Registry: emisión SD-JWT + JWKS + passkey ceremony (G1: T2–T3).
2. Policy Gate in-process (JsonLogic + limits + ledger) — TDD T1, T10.
3. Verify endpoint con reserva atómica — T4–T6.
4. VuelaYa mock: catálogo + checkout + **cobro PayPal con `vault_id`** (fallback mock idéntico).
5. Happy path end-to-end **desplegado en Cloud Run** (G2) + notificación Telegram básica.
6. Pipeline GH Actions (WIF → AR → Cloud Run) funcionando (base G8).

### Día 2 — Casos feos + adversario + HIL (Gates G3–G4)
7. Máquina de estados + revocación con passkey + `DELETE` del payment token + propagación (T6, T8, T17).
8. Grafo propio del agente (ADR-006): nodos deterministas + nodo `await_human` antes de pay (checkpointing en `agent_runs`); escalation Telegram con diff + timeout fail-closed + sticky approvals (T7).
9. Ledger hash-chained + roots firmados con KMS + witness GCS + `/audit/verify` (T9).
10. Watcher como Cloud Run job + Scheduler + **trigger manual OIDC**.
11. Mini-AgentDojo: injection fixtures (T11) + impersonación (T3) + replay.
12. Presidio scrubber en el pipeline del agente (T12).

### Día 3 — Disputa + auditor + ensayo + entregables (Gates G5–G8)
13. Flujo de disputa → **disputa simulada PayPal (T18)** + evidence bundle → veredicto (B1).
14. UIs: dashboard humano, vista merchant, vista auditor (trail + verify en vivo).
15. **Ensayo general ×2 contra el ambiente GCP** con min-instances activados (G6) + smoke T15.
16. Slides + README + diagrama + decision log (ADRs) + video backup (G7). *(Opcional si sobra tiempo: endpoint x402 machine-payable de demo — ADR-014.)*

**Backlog de contingencia (cortar en este orden si falta tiempo):** WhatsApp secundario → OPA sidecar showcase (queda el gate in-process) → vista merchant separada (fusionar con auditor) → sticky approvals → endpoint x402 opcional → video (si la demo en vivo es estable).

---

## 12. Mapeo a criterios de evaluación y entregables

| Requisito del reto | Dónde se resuelve | Verificación |
|---|---|---|
| Mandato sin tarjeta cruda | ADR-002/007 (SD-JWT + PayPal payment tokens) | G2 |
| Verificación del merchant | Verify endpoint + firma intent (ADR-004/012) | G2, T4 |
| Compra end-to-end + registro | Flujo §6.1 | G2, T13 |
| Fuera de mandato rechazado/escalado | Gate + HIL (ADR-005/008) | G3, T1 |
| Revocación viva | ADR-012 (verify síncrono + DELETE del payment token) | G3, T6/T17 — **trial by fire** |
| 3 vistas (humano/merchant/auditor) | UIs Día 3 | G5 |
| Bonus: disputa completa | Evidence bundle + **Customer Disputes API sandbox** (ADR-007/009) | G5, T18 |
| Bonus: condiciones ricas | JsonLogic + limits (ADR-011) | T10 |
| Bonus: adversario resistente | Gate + delimitación (ADR-005) | G4, T11 |
| **Despliegue GCP (requisito equipo v2)** | ADR-015..020, ambiente `app.<dominio>` | G8, T15 |
| **Slides** | Día 3 | G7 |
| **Demo vivo/video** | T13 = demo-as-code + video backup | G6/G7 |
| **Repo + README** | Desde Día 0 | G7 |
| **Diagrama de arquitectura** | §6 (mermaid → draw.io para slides) | G7 |
| **Decision log** | ADRs §5 | G7 |

---

## 13. Riesgos principales y respuesta

| Riesgo | Prob. | Respuesta |
|---|---|---|
| PayPal sandbox falla en vivo (v2.1) | Media | Circuit breaker → mock idéntico; mandato/verificación no dependen del rail |
| Redirect de approval PayPal lento/confuso en vivo (v2.1) | Media | Cuentas sandbox pre-logueadas en el navegador del demo; enrollment pre-grabado como respaldo (el enrollment es 1 vez, no parte del trial by fire) |
| Disputas sandbox requieren transacción card-funded (v2.1) | Media | Probar en smoke test Día 0 (S13); fallback Webhook Simulator / `process-chargeback` |
| Bug en el resume del orquestador propio (v2.3) | Media | T7 primero (idempotencia por contrato); estado del run = 1 tabla legible con un SELECT; el gate es agnóstico al orquestador |
| WhatsApp no pre-calentado | Alta | Telegram primario por diseño; WhatsApp solo "wow" |
| Complejidad criptográfica traba el Día 1 | Media | SD-JWT ya en venv; samples AP2 locales; simplificar a JWS firmado por el issuer si SD-JWT completa traba |
| Scope creep (3 casos de uso del whiteboard) | Alta | **VuelaYa es el único caso de la demo**; retail/logística = roadmap en slides |
| Jueces hackean por ruta no cubierta | Media | Invariant T1 + regla: única ruta al dinero = gate (revisión G4) |
| **Passkeys no funcionan por dominio (v2)** | Media | Dominio comprado Día 0 (S6); fallback localhost solo para ceremonia; probar T15 antes del ensayo |
| **Cold starts en la demo (v2)** | Media | `--min-instances=1` en `api` y `agent` durante la ventana; desactivar después (costo) |
| **Límite de conexiones de `db-f1-micro` (v2)** | Baja | Pool pequeño (5–10), máximo 1–2 instancias/servicio, carga trivial |
| **Rate limit del LLM en vivo (v2)** | Media | ADR-021 con Vertex pagado (créditos) u OpenAI; probar el Día 0; cachear decisiones del watcher |
| **Propagación DNS/TLS tardía (v2)** | Baja | Comprar dominio Día 0; verificar certificado emitido en el hello-world |
| **Quota/región de algún servicio (v2)** | Baja | Todo el stack verificado disponible en suramerica-east1; fallback us-east1 (misma topología) |

---

## 14. Decisiones pendientes (requieren input del equipo)

1. ~~Stack~~ **RESUELTA v2:** Python backend (FastAPI, AP2 SDK en venv) + React frontend — encaja directo con buildpacks de Cloud Run.
2. **¿OPA sidecar como showcase visible** (policy-as-code en Rego con UI mostrando la evaluación) **o solo gate in-process?** *Recomendación: gate in-process obligatorio; OPA solo si sobra tiempo del Día 2.*
3. **¿Escalamiento crea mini-mandato firmado nuevo** (sticky approval) **o flag transitorio?** *Recomendación: mini-mandato firmado — mismo mecanismo reutilizado.*
4. ~~LLM~~ **ACOTADA v2 (ADR-021):** Vertex AI Gemini (créditos GCP) u OpenAI (créditos hackathon) — **elegir el Día 0** midiendo latencia.
5. **Nombre del producto/canal** (slides y narrativa). *Sugerencias de trabajo: "Mandate Channel" / "TrustRail" — branding el Día 1.*
6. **Dominio concreto a comprar** (Día 0, crítico path) — dependerá del branding (#5); se puede comprar un dominio neutro y decidir subdominios después.
7. (v2.1) **¿Incluir el endpoint x402 opcional del Día 3** como "wow machine-payable" o dejarlo solo como roadmap del decision log? *Recomendación: solo si G5–G6 ya están en verde.*

---

## 15. Fuentes clave de la investigación (selección)

**Protocolos:** AP2 (github.com/google-agentic-commerce/AP2 · ap2-protocol.org tras donación a FIDO) · UCP spec 2026-01-11 (ucp.dev) · ACP (agenticcommerce.dev) · x402 (x402.org, Linux Foundation) · Mastercard Agent Pay · Visa Intelligent Commerce/TAP · PayPal agent-toolkit · MCP spec 2025-11-25/2026-07-28 + SEP-2009.
**Cripto:** RFC 9901 SD-JWT · W3C VC 2.0 + vc-jose-cose · W3C Bitstring Status List · IETF OAuth Token Status List draft-21 · FIDO Agentic Authentication/Payments TWG · jose npm · RBI e-Mandate · EMV 3DS/Tokenisation · SEPA e-mandates ISO 20022.
**PII:** Presidio (github.com/microsoft/presidio) · PCI DSS v4.0.1 (referencia de compliance story) · Yuno Vault (referencia de diseño: y.uno/en/product/vault) · Stripe Issuing spending controls · Privacy.com.
**PayPal (v2.1, verificado):** Orders v2 (developer.paypal.com/api/orders/v2) · Payment tokens save-without-purchase (developer.paypal.com/api/payment-tokens) · Customer Disputes v1 + disputes test-and-go-live · Webhooks Management + verify-webhook-signature · Idempotency (PayPal-Request-Id) · Auth-capture · Currency codes · Sandbox accounts · Blog transición SDKs (REST directo) · Agent Toolkit (github.com/paypal/agent-toolkit) · Blog AP2 de PayPal · Agentic Commerce Services.
**x402/Coinbase (v2.1, alternativa):** x402.org + whitepaper v2 (jun-2026) · docs.cdp.coinbase.com (x402 seller/buyer quickstart, facilitator, wallets pricing, faucet) · pypi.org/project/x402 + monorepo x402-foundation/x402 · EIP-3009 transferWithAuthorization (gasless) · Circle Refund Protocol · Stripe machine/x402.
**Guardrails/Frameworks:** OWASP Top 10 LLM 2025 + Agentic Top 10 · AgentDojo · CaMeL DeepMind (simonwillison.net/2025/Apr/11/camel) · Dual LLM pattern · LangGraph interrupts · OpenAI Agents SDK HITL · NeMo Guardrails · OPA vs Cedar.
**HIL/Disputas:** OpenAI Operator · Google Mariner · Telegram Bot API inline keyboards · WhatsApp Cloud API pricing · EMV 3DS2 liability shift · Visa CE 3.0 / reason code 10.4 · Mastercard 4837 · Reg E (12 CFR 1005) · EU AI Act Art. 50 (en vigor 02-ago-2026).
**Backend:** JsonLogic (jsonlogic.com) · Postgres event sourcing + outbox + SKIP LOCKED · RFC 6962/Certificate Transparency · Saga pattern · RFC 7009.
**GCP (v2, verificado):** Cloud Run sidecars/streaming/min-instances/timeout (cloud.google.com/run/docs) · Cloud Run jobs on schedule con OIDC · Cloud SQL connector unix socket + pricing + ediciones · **Cloud KMS algoritmos (`EC_SIGN_ED25519`)** (cloud.google.com/kms/docs/algorithms) · **Public Suffix List (run.app) + W3C WebAuthn L3 rpId** (publicsuffix.org · w3.org/TR/webauthn-3) · Pub/Sub pricing · Free tier/US$300 (cloud.google.com/free) · **Gemini API rate limits dic-2025** (ai.google.dev/gemini-api/docs/rate-limits) · GitHub Actions WIF + deploy-cloudrun (google-github-actions) · Regiones LATAM (southamerica-east1).

*(Informes completos de los 9 subagentes con ~140 URLs en el historial de esta sesión; consolidar los relevantes en `docs/decisions/` al ejecutar G0.)*

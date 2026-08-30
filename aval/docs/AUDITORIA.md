# Auditoría de TryTrust

Revisión de código sobre `main` (`8120fdd`): 24 679 líneas de Python en `agent`,
`api`, `merchant`, `trustlib` y `yuno_sim`.

Cada hallazgo trae la evidencia que lo sostiene y el arreglo concreto. La
coincidencia de los canonicalizadores y la interoperabilidad de firmas se
comprobaron **ejecutándolas**, no leyéndolas; la complejidad y la duplicación
salen de análisis AST sobre el árbol completo.

## Veredicto

| | |
|---|---|
| **Estado** | Sólido, con tres huecos |
| **Bloqueantes** | 3 — F1, F2, F3 |
| **Pruebas** | 398 pasando (356 + 42), 1 falla, 6 omitidas |
| **Complejidad** | 10 de 944 funciones con CC ≥ 15 |
| **Duplicación** | 16 conceptos definidos en 2+ módulos |

La criptografía y la cadena de evidencia están bien hechas y aguantan preguntas.
Los tres bloqueantes son de control de acceso, no de diseño: **el sistema sabe
qué permitir y todavía no sabe a quién creerle.**

---

## 1. Arquitectura

La arquitectura de `architecture.html` se respetó: el agente propone, un gate
determinista decide, el comerciante verifica, todo queda en una cadena. El
problema es que ese diseño está implementado dos veces.

### F1 · Bloqueante — Hay dos rutas de enforcement y el demo corre por la que no es

`src/agent/kernel.py` nació como *mock* del carril de Dev 2, con la tabla de
decisiones real dentro. Dev 2 después construyó el gate de verdad en
`src/api/decision/`. Ninguno de los dos se enteró:

```
$ grep -rn "from src.api\|DecisionService" src/agent --include=*.py
  (sin resultados)

$ grep -rl trustlib src/agent    → 0 archivos
$ grep -rl trustlib src/api      → 11 archivos
$ grep -rl trustlib src/merchant → 8 archivos
```

Consecuencia práctica: lo que un juez ejecute con `cli demo` pasa por el mock. El
`DecisionService` de 1 116 líneas —con reserva atómica, idempotencia, velocity y
step-up— nunca se ejerce en el camino del demo.

Y ya divergieron: el mock no tiene velocity ni step-up; el real no tiene el
registro de herramientas que rechaza `pay`.

**Arreglo.** Convertir `agent/kernel.py` en un cliente HTTP de `POST /purchases` y
`POST /mandates/{id}/verify`. El `MerchantPort` ya existe y el agente ya habla MCP
contra procesos externos, así que es sustituir un import por una llamada. Borrar
la tabla de decisiones duplicada: mientras haya dos, una miente.

### A1 · Bien hecho — Puertos y adaptadores en los dos carriles

`api/audit/ports.py`, `api/decision/ports.py` y `agent/ports/base.py` definen
protocolos con implementaciones en memoria y en Postgres detrás. La inversión de
dependencias está bien aplicada, y es lo que hace que F1 sea barato de arreglar.

---

## 2. Mínimo privilegio

**No se cumple.** Doce rutas HTTP expuestas, cero dependencias de autenticación en
todo `src/api` y `src/merchant`.

### F2 · Bloqueante — Cualquiera que alcance la API puede leer y crear mandatos ajenos

```
$ grep -rn "HTTPBearer|APIKeyHeader|Authorization|api_key" src/api src/merchant
  (sin resultados)

GET  /mandates?user_id=X   → enumera los mandatos de cualquiera
POST /mandates             → crea un mandato para cualquier user_id
GET  /escalations          → lista todo lo pendiente de aprobar
```

La ceremonia passkey sí protege *activar* y *revocar* —esas dos exigen una
aserción WebAuthn y están bien—. Todo lo demás está abierto, y la lectura de
mandatos ajenos ya es una fuga: los `claims` incluyen límites, alcance y la
referencia al instrumento de pago.

**Arreglo.** El carril del agente ya resolvió esto: `src/agent/auth.py` tiene
tokens con hash, cuatro roles y la regla de que el rol da la capacidad y la
vinculación al agente da la instancia. Subirlo a un `Depends` de FastAPI y
aplicarlo a los routers es trabajo de una hora, no un rediseño.

### F6 · Alta — Un solo rol de base de datos para todos los servicios

`compose.yaml` crea un único usuario `trytrust` y los cuatro servicios se conectan
con él. El esquema documenta que *«verify es el único escritor de
`reserved_amount`»*, pero eso es una convención en un comentario: nada en la base
impide que el agente, el comerciante o el rail escriban esa columna.

**Arreglo.** Un rol por servicio con `GRANT` por tabla, y `REVOKE UPDATE` sobre
las columnas de saldo para todos menos el kernel. Es la diferencia entre decir que
hay un solo escritor y que lo haya.

### A2 · Bien hecho — El agente sí aplica mínimo privilegio sobre sí mismo

El registro de herramientas solo admite efectos `read` y `submit` —el constructor
rechaza cualquier otro—, así que la herramienta `pay` que exponen los comerciantes
queda registrada como rechazada y nunca se llama. La lista blanca de egreso en
`agent/net.py` bloquea destinos no configurados y deja registro. Ambas son
verificables, no declarativas.

---

## 3. No repudio

Fuerte en el mandato, **roto justo en la aprobación humana** — que es precisamente
el momento en que alguien autoriza gastar por encima del límite.

### F3 · Bloqueante — El aprobador se declara a sí mismo y la plataforma firma por él

```
src/api/routers/escalations.py
    approver = body.approver              ← campo del cuerpo, sin verificar

src/api/services/escalations.py:136
    signing = key_store().issuer_key()    ← la clave del EMISOR
    receipt_sig = sign_compact(receipt_payload, signing.key, ...)
```

El recibo se firma con la clave de la plataforma, no con la del aprobador. Lo que
demuestra es «la plataforma registró que alguien que dijo llamarse Marta aprobó»,
no que Marta aprobara. Como además `approver` llega en el cuerpo sin autenticar,
cualquiera aprueba como cualquiera, y la propia plataforma podría fabricar el
recibo. En una disputa, esa firma no sostiene nada.

Contrasta con el resto del sistema, donde el no repudio está bien resuelto: el
mandato lo firma la passkey de la persona, el intent lo firma la clave del agente
ligada en `cnf.jwk`, y la cadena encadena hashes con triggers que la base impone.
El eslabón débil es exactamente el que más importa.

**Arreglo.** Exigir una aserción passkey para aprobar, igual que ya se exige para
revocar —`/mandates/{id}/revoke/options` ya tiene el patrón montado—. Si no cabe
en el tiempo restante, el mínimo honesto es autenticar al aprobador y decir en la
defensa que el recibo es un registro de la plataforma, no una firma del humano.

### F5 · Alta — Dos cadenas de auditoría sin raíz común

`agent/audit.py` mantiene cadenas por mandato con checkpoints firmados;
`api/audit/` mantiene la suya con KMS y testigo en GCS. Un auditor que quiera
reconstruir una compra tiene que confiar en que dos cadenas independientes cuentan
la misma historia, y nada las liga.

**Arreglo.** Una sola cadena. Si no da tiempo, que el checkpoint de una incluya la
cabeza de la otra: una línea de código y el árbol vuelve a tener una raíz.

---

## 4. KISS

Bien en general —944 funciones y solo 10 con complejidad ciclomática ≥ 15—. El
problema no es difuso, está localizado.

| CC | Líneas | Función | Lectura |
|---:|---:|---|---|
| 63 | 104 | `api/domain/policy.py:344` `_jsonlogic()` | Intérprete completo escrito a mano. El pico del repo. |
| 34 | 53 | `agent/jsonlogic.py:49` `evaluate()` | El mismo intérprete, otra vez. |
| 30 | 143 | `api/domain/policy.py:880` `evaluate()` | 143 líneas en una sola función de decisión. |
| 24 | 76 | `trustlib/sdjwt.py:159` `verify()` | Justificable: la verificación tiene muchos modos de fallo. |
| 23 | 123 | `api/audit/service.py:127` `verify_chain()` | Replay completo con validaciones intercaladas. |
| 22 | 144 | `agent/kernel.py:136` `verify()` | Desaparece si se resuelve F1. |

### F8 · Alta — Dos intérpretes de JsonLogic, ambos escritos a mano

Las condiciones que la persona **firma dentro del mandato** las evalúan dos
motores distintos: uno en el gate real y otro en el mock. Con CC 63 y 34, ninguno
es trivial. Si difieren en un operador, el mismo mandato firmado dice cosas
distintas según quién lo lea — y esa divergencia no la detecta ninguna prueba
actual, porque cada suite ejerce solo su propio motor.

**Arreglo.** Un motor en `trustlib`, importado por ambos. Es además el sitio
natural: `trustlib` ya lo usan `api`, `merchant` y `yuno_sim`; el único que no lo
toca es el agente.

### F12 · Media — Firmas que delatan contratos débiles

```
policy.py:185  price_matches(intent_amount, offer_amount)
policy.py:194  compare_price(intent_amount, offer_amount)   ← lo mismo, otro nombre

policy.py:138  def _value(source, name, default=None)
service.py:82  def _value(source, name, default=None)       ← copiado

policy.py:660  def burst_check(*args, **kwargs)             ← borra el tipado
```

`_value()` lee atributos por pato porque los objetos que cruzan la frontera entre
`domain` y `decision` no tienen un tipo común. Ese helper no es el problema: es el
síntoma de una interfaz que falta.

---

## 5. SOLID

| Principio | Estado | Evidencia |
|---|---|---|
| **S** Responsabilidad única | Violado | `DecisionService`: 33 métodos, 1 116 líneas. Hace evaluación, reserva, idempotencia, escalaciones, eventos y persistencia. |
| **O** Abierto/cerrado | Bien | Añadir un comerciante es una clase y un `register()`; añadir un backend es un adaptador. Nada que modificar. |
| **L** Sustitución | Riesgo | `repository_memory` (555) y `repository_postgres` (642) son implementaciones paralelas del mismo puerto. Solo las pruebas evitan que diverjan. |
| **I** Segregación | Flojo | `_value()` con acceso por pato es lo que se hace cuando no hay una interfaz estrecha que pedir. |
| **D** Inversión | Bien | Ambos carriles dependen de protocolos, no de implementaciones. Es lo mejor del diseño. |

### F7 · Alta — `DecisionService` es una clase-dios

1 116 líneas y 33 métodos. Los prefijos delatan las costuras por donde se parte
sola: `_reserve`/`_release`, `_claim_idempotency`/`_save_idempotency`,
`_save_escalation`/`_read_escalation`, `_emit`/`_new_event`. Cuatro colaboradores
esperando a que alguien los nombre.

**Arreglo.** Extraer `ReservationManager`, `IdempotencyGuard`,
`EscalationCoordinator` y `EventEmitter`. Mecánico, sin cambiar comportamiento.
No es urgente para el demo; sí lo es antes de que alguien más toque ese archivo.

---

## 6. Redundancia

Dieciséis conceptos viven en dos o más módulos. Es consecuencia directa de
construir cuatro carriles en paralelo contra contratos congelados: fue la decisión
correcta para avanzar, y ahora hay que pagarla.

| Concepto | Definido en | Riesgo |
|---|---|---|
| `canonical_json` | 5 archivos, 3 implementaciones | **Verificado hoy: las tres coinciden byte a byte.** Nada garantiza que sigan haciéndolo. Si divergen, toda firma cruzada falla y el error dirá «firma inválida». |
| `Decision` | 3 módulos | Tres modelos del mismo veredicto: `agent/kernel`, `api/domain`, `trustlib`. |
| Servidor MCP | 2 completos | `agent/ports/mcp_server.py` y `merchant/mcp_server.py` exponen las mismas tres herramientas. |
| `RailError` | 3 módulos | Tres jerarquías de error para el mismo rail. |
| `jwks` | 3 módulos | Tres formas de publicar o consumir el mismo conjunto de claves. |

> **La prueba que falta.** Que las tres implementaciones canónicas coincidan hoy
> es disciplina, no arquitectura. Una prueba de una línea que compare las tres
> sobre los payloads que sí se firman convierte esa disciplina en garantía — y es
> la prueba más barata de todo este informe.

---

## 7. Complejidad algorítmica

Nada cuadrático. Sí consultas dentro de bucles.

### F9 · Media — La cadena de mandatos se recorre con una consulta por nivel

```
agent/kernel.py:300  chain()          while → fetchone() por ancestro
agent/kernel.py:310  reserve_chain()  for   → fetchone() por ancestro
agent/kernel.py:341  _unreserve()     for   → una consulta por ancestro
agent/kernel.py:355  settle()         for   → una consulta por ancestro
```

Cada compra recorre la ascendencia del mandato cuatro veces, con una ida a la base
por nivel. Hoy la profundidad es 1 o 2 (un mini-mandato *sticky* sobre su padre),
así que el costo real es despreciable. Pero es O(profundidad) consultas **dentro
de la transacción que sostiene el lock de reserva**, y ahí es donde menos conviene
acumular latencia.

**Arreglo.** Un `WITH RECURSIVE` que traiga la ascendencia completa en una
consulta. Postgres ya está; es reescribir cuatro funciones cortas.

### F10 · Baja — `verify_all()` reproduce todas las cadenas en cada llamada

Lineal en el número de eventos, que es lo correcto para una verificación honesta
—no hay atajo que siga probando algo—. Pero la consola de auditoría la llama en
cada carga. Con los volúmenes del demo no se nota; conviene saber que el costo
crece con la historia y que el arreglo es verificar desde el último checkpoint
firmado, no desde el génesis.

---

## 8. Menores

| # | Hallazgo | Dónde |
|---|---|---|
| F13 | 28 decisiones en el índice, 13 registros completos. Es entregable calificado. | `aval/docs/decisions/` |
| F14 | Una prueba fija su URL de base de datos e ignora `AVAL_TEST_DATABASE_URL`; falla contra cualquier Postgres que no sea socket local. | `tests/test_webhooks_and_mcp.py` |
| F15 | El canonicalizador del agente acepta enteros > 2⁵³ que `rfc8785` rechaza. Hoy no firmamos ninguno. | `agent/crypto/canonical.py` |
| F16 | Dos caminos de esquema: `schema.sql` y las migraciones de Alembic. Van a divergir. | `contracts/fixtures/` · `alembic/` |

---

## 9. Qué hacer, en este orden

Ordenado por lo que un juez puede romper en vivo, no por elegancia.

| Orden | Acción | Cierra | Esfuerzo |
|---:|---|---|---|
| 1 | Autenticar la API y exigir passkey para aprobar una escalación | F2 · F3 | 2–3 h |
| 2 | Apuntar el agente al gate real y borrar la tabla de decisiones duplicada | F1 | 2 h |
| 3 | Prueba que compara las tres implementaciones canónicas | F4 | 15 min |
| 4 | Un solo JsonLogic en `trustlib` | F8 | 1 h |
| 5 | Ligar las dos cadenas en un checkpoint común | F5 | 1 h |
| 6 | Roles de base de datos por servicio | F6 | 1 h |
| 7 | Completar los 15 registros de decisión faltantes | F13 | — |
| 8 | Partir `DecisionService` en cuatro colaboradores | F7 | post-demo |

---

## Lo que hay que decir en la defensa

La criptografía, la cadena de evidencia y la separación en puertos están bien
hechas y aguantan preguntas. Los tres bloqueantes son de control de acceso, no de
diseño: el sistema sabe **qué** permitir y todavía no sabe **a quién** creerle.

Nombrarlo antes de que lo encuentre un juez vale más que esconderlo.

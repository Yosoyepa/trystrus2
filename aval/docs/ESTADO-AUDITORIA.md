# Estado de la auditoría: qué se resolvió y qué falta

Análisis tras el pull de `main` (`b9896d1`). Complementa
[`AUDITORIA.md`](AUDITORIA.md) (21 puntos) y [`BRECHAS.md`](BRECHAS.md).

Yosoyepa subió cuatro commits que atacan justo lo que estaba arriba de la lista:
una SPA completa, la orquestación en Docker y un puente HTTP hacia el agente.
Son 11 671 líneas y mueven tres de los ítems más caros. También abren cinco
frentes nuevos, dos de ellos serios.

---

## Resuelto

| Punto | Estado | Evidencia |
|---|---|---|
| **Tres vistas** (checklist) | ✅ | `web/` con 11 671 líneas: consolas, SSE, contexto, componentes |
| **No se puede levantar el sistema** | ✅ | `compose.yaml` levanta `db`, `kernel`, `yuno_sim`, `merchant`, `web` |
| **El kernel no expone el carril del agente** | ✅ | `src/api/routers/agent_bridge.py`: `/agent/ask`, `/runs`, `/watches`, `/limits` |
| **Bases de datos separadas** | ⚠️ parcial | El agente ya lee `AVAL_DATABASE_URL`; los esquemas todavía chocan (ver N3) |

El puente es exactamente lo que buscaba `service.py`: la API envuelve la fachada
del agente en lugar de reimplementarla. Buen encaje.

---

## Nuevos hallazgos

### N1 · La consola cae a datos simulados en silencio · **Alto**

`web/src/services/api.ts` intenta el backend real y, si falla, usa
`mockEngine.ts` (990 líneas) sin decírselo a nadie:

```js
if (res.ok) return await res.json();
} catch (err) {
  console.warn('Real agent call failed, falling back to local simulation', err);
```

En vivo, si el backend tose, la consola sigue mostrando compras, mandatos y
trazas **plausibles y falsas**. Un juez podría estar viendo una compra simulada
mientras le explicas que la cadena de auditoría la respalda. Es peor que un
error: un error se ve, esto no.

**Arreglo.** Un indicador visible de modo (`REAL` / `SIMULADO`) en la cabecera, y
que las vistas de auditoría **no** tengan fallback: si no hay backend, que digan
que no hay backend. Simular una compra es aceptable para probar la interfaz;
simular la evidencia contradice el producto entero.

### N2 · La colisión de esquemas se materializó · **Alto**

La auditoría lo anticipó: *«el momento en que compartan una base, el DDL que
corra primero gana y el otro leerá mal sus propias filas»*. Ya pasó.

El contenedor ahora carga `contracts/fixtures/schema.sql` al arrancar, y las
migraciones de Alembic describen las mismas tablas con tipos distintos:

```
alembic upgrade head  →  ProgrammingError
column "active" is of type integer but expression is of type boolean
```

Con la base `aval`, la suite del agente pasa de **42/42 a 4/42**. No es una
regresión de nadie en particular: son dos descripciones de la misma base
conviviendo, que es F16 de la auditoría cobrándose la factura.

**Arreglo.** Una sola fuente. O `schema.sql` es la verdad y Alembic la aplica, o
al revés — pero no las dos. Mientras tanto, el agente necesita su propia base o
tipos alineados en `offers.active`, `claims`, `reserved_amount` y los timestamps.

### N3 · Hay dos ficheros de compose · **Medio**

`compose.yaml` y `docker-compose.yml` existen los dos, con los mismos servicios y
diferencias solo en comentarios. Docker avisa y elige `compose.yaml` por
precedencia:

```
warning: Found multiple config files with supported names
warning: Using /.../compose.yaml
```

Hoy da igual porque son casi idénticos. En cuanto alguien edite uno solo,
`docker compose up` levantará algo distinto de lo que esa persona cree.

**Arreglo.** Borrar uno. Treinta segundos.

### N4 · El agente no está en compose · **Medio**

`compose.yaml` levanta `db`, `kernel`, `yuno_sim`, `merchant` y `web`. El agente
sigue arrancándose a mano. El puente `/agent/ask` lo invoca en proceso dentro del
kernel, así que el chat funciona — pero el watcher y el relé de outbox, que son
procesos de fondo, no los levanta nadie.

Consecuencia concreta: **las escalaciones no expiran solas**. El timeout de 120 s
lo aplica el `tick` del watcher, y sin watcher una escalación pendiente se queda
pendiente para siempre.

### N5 · Un contenedor a medio crear se queda sin puertos · **Bajo, operacional**

Si `docker compose up` falla porque el puerto está ocupado, el contenedor queda
creado sin publicar puertos, y un `up` posterior lo reutiliza tal cual: arranca
sano, `healthy`, e inalcanzable desde el host. Cuesta veinte minutos de
diagnóstico en el peor momento posible.

**Arreglo.** `docker compose up -d --force-recreate` en el guion de pre-demo.

---

## Sigue abierto

Sin cambios respecto a la auditoría:

| Punto | Nota |
|---|---|
| **F1** Dos rutas de enforcement | El puente va de `api` → `agent`, no al revés. La compra sigue pasando por `agent/kernel.py`. |
| **F2** API sin autenticación | `agent_bridge.py` tampoco tiene auth, y **amplía la superficie**: `POST /agent/watches` crea órdenes de compra automáticas sin token. |
| **F3** El aprobador se declara solo | Sin cambios. |
| **F5** Dos cadenas de auditoría | Sin cambios. |
| **F6** Un solo rol de base de datos | Sin cambios; ahora más relevante, porque todos comparten `aval` de verdad. |
| **F7 · F8 · F9 · F10 · F12** | Sin cambios. Ninguno rompe el demo. |
| **F13 · F16** | F16 dejó de ser teórico (ver N2). |
| **G1 · G2** Los merchants no piden mandato | Sin cambios. Seis herramientas liquidan sin autorización. |
| Despliegue y dominio | Sin cambios. `trytrust.lat` no resuelve. |

---

## Qué haría ahora

| Orden | Acción | Cierra | Esfuerzo |
|---:|---|---|---|
| 1 | Unificar el esquema: una sola fuente | N2 · F16 | 1 h |
| 2 | Indicador de modo en la consola y quitar el fallback de las vistas de auditoría | N1 | 30 min |
| 3 | Borrar uno de los dos compose y añadir el watcher | N3 · N4 | 30 min |
| 4 | `pay` con mandato en las tres apps Next.js | G1 · G2 | 2 h |
| 5 | Autenticar la API, incluido `/agent/watches` | F2 · F3 | 2–3 h |
| 6 | Desplegar y apuntar el dominio | Checklist | 2 h |

Lo primero no es opcional: con la base compartida, la suite del agente está en
4/42 y eso es la señal de que dos mitades del sistema leen la misma tabla de
formas distintas.

---

## Balance

El proyecto avanzó de verdad hoy: la consola existe, la orquestación existe y el
kernel ya expone el agente. Tres ítems caros, resueltos.

Lo que cambió de naturaleza es el riesgo. Antes era *«faltan piezas»*; ahora es
*«las piezas están y no encajan del todo»* — un esquema con dos descripciones y
una consola que puede mentir sin querer. Las dos se arreglan en hora y media, y
las dos son peores calladas que dichas.

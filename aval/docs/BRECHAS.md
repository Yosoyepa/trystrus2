# Qué nos falta y qué podemos mejorar

Investigación sobre las tres ramas del backend y los tres repositorios del
proyecto. Complementa [`AUDITORIA.md`](AUDITORIA.md): aquella mira el código que
existe, ésta mira **lo que no existe todavía**.

## Estado de las ramas

Nada quedó sin mergear. Las tres ramas del backend (`main`, `agent-system`,
`identity-plus-merchant-service`) están al día, y los otros dos repositorios
tienen una sola rama cada uno.

| Repositorio | Commits | Código | Estado |
|---|---:|---:|---|
| `Hackthon-Yuno-Nauta-` (backend) | 25 | 24 679 líneas Python | Los cuatro carriles integrados en `main` |
| `trytrust-merchants` | 10 | 22 340 líneas TS | Tres apps completas con MCP |
| `trytrust-platform` (control tower) | 4 | **108 líneas TS** | Andamiaje de Next.js sin tocar |

---

## 1. El hueco que más pesa

### G1 · Los comerciantes no hablan con el kernel

22 340 líneas de aplicaciones de comercio y **ni una sola referencia** a un
mandato, a `verify` o al kernel:

```
$ grep -rn "KERNEL|mandate_jti|verify" trytrust-merchants/apps/*/lib apps/*/app
  (nada relevante)
```

Peor: las tres apps exponen herramientas MCP que liquidan sin pedir mandato.

| App | Herramientas que liquidan sin mandato |
|---|---|
| `vuela-ya` | `pay` |
| `mami` | `pay` |
| `logistics` | `request_ride`, `request_package_delivery`, `request_freight`, `pay` |

Son **seis** puntos por los que un agente cualquiera compra sin autorización de
nadie. `logistics` es el más expuesto: `request_ride` compromete dinero de forma
directa, sin siquiera pasar por un `pay`.

### G2 · Hay dos VuelaYa y la bonita es la insegura

`src/merchant` (Python) **sí** verifica: descarga el JWKS, valida la firma del
mandato, llama a `verify` del kernel y solo entonces cobra. Funciona y está bien
hecho — pero no tiene interfaz, así que en la demo no se ve.

`apps/vuela-ya` (Next.js) tiene 9 307 líneas de interfaz cuidada, catálogo real,
mapa de asientos… y ninguna verificación.

**Lo que un juez va a mirar es la segunda.** Ese es el problema, y no es de
código: es de qué historia se cuenta con qué pieza.

**Arreglo.** Que el `pay` de las tres apps exija `mandate_jti` y llame a
`POST /mandates/{id}/verify`, liquidando solo con `APPROVED`. El cliente ya está
escrito en `src/merchant/kernel_client.py`; es portarlo a TypeScript. Está
detallado en [`MCP-HANDOFF.md`](MCP-HANDOFF.md) desde hace horas.

---

## 2. El control tower no existe

El repositorio entero es esto:

```tsx
export default function Home() {
  return <div className="text-2xl font-bold">Tower Control</div>;
}
```

El reto pide explícitamente **tres vistas**: el registro de la persona, la
verificación del comerciante y el rastro del auditor. Es el único ítem del
checklist con cero líneas escritas, y es además la superficie por la que los
jueces operarían el sistema.

Lo que sí está listo para alimentarlas:

- `src/agent/service.py` — `ask`, `trail`, `verify`, `mandate_view`, `events_since`
- `GET /audit/events` y `GET /audit/verify` en el kernel
- `GET /mandates`, `GET /escalations` con sus modelos Pydantic
- El relé de outbox ya mantiene un búfer para SSE

Es decir: **los datos existen, falta quien los dibuje.** Tres pantallas de
lectura contra endpoints que ya responden.

---

## 3. No se puede levantar el sistema

`compose.yaml` levanta un solo servicio: la base de datos.

```
$ grep -E "^  [a-z-]+:" compose.yaml
  db
```

Los cuatro procesos (`api`, `merchant`, `yuno_sim`, `agent`) hay que arrancarlos
a mano, cada uno con sus variables, y **no hay ningún documento que explique
cómo**. Ningún README dice cómo correr el sistema completo.

Tampoco hay despliegue: el único workflow de GitHub es `docs-guard`. No hay
Cloud Run, ni CI de deploy, ni `bootstrap.sh` para GCP. Y `trytrust.lat` no
resuelve — lo que además impide passkeys fuera de localhost (decisión #3: fallan
en `*.run.app`).

**Arreglo.** Añadir los cuatro servicios a `compose.yaml` con sus puertos y
`depends_on`. Es media hora y convierte «hay que arrancar cuatro cosas» en
`docker compose up`.

---

## 4. Ninguna prueba cruza servicios

398 pruebas, y cada carril prueba el suyo:

| Suite | Qué ejercita |
|---|---|
| `src/agent/tests.py` (42) | El agente contra su propio kernel |
| `tests/` + `src/api/tests/` (356) | Kernel, merchant y rail por separado |

**Cero pruebas** levantan dos servicios y hacen pasar una compra de punta a
punta. El plan maestro lo llamaba T13, «demo-as-code»: el guion completo
—crear, comprar, rechazar 300, revocar, fallar— como prueba automatizada.
`src/agent/demo.py` cubre solo el carril del agente.

Esto importa más de lo que parece: es exactamente el hueco por el que se coló
F1 del informe anterior (dos gates que nadie compara) y por el que se colaría
cualquier divergencia entre los tres canonicalizadores.

---

## 5. Mejoras técnicas menores

| # | Hallazgo | Detalle |
|---|---|---|
| G3 | Observabilidad escasa | 10 de 144 archivos usan `logging`. En la demo, si algo falla no habrá dónde mirar. |
| G4 | 57 `except` amplios | `except Exception` fuera de pruebas. Algunos son deliberados (un run que revienta debe quedar denegado, nunca pagado); otros esconden errores. |
| G5 | La clave de prueba usa el `kid` de producción | `fixtures/issuer_key.json` trae `kid=v1`, el mismo que usa el emisor real. Está bien etiquetada y el runtime carga de Secret Manager, pero si algo llegara a confiar en `kid=v1` a ciegas, aceptaría un mandato firmado con una clave pública del repositorio. Basta con renombrarla a `kid=test-v1`. |
| G6 | Dos caminos de esquema | `contracts/fixtures/schema.sql` y las migraciones de Alembic describen la misma base. Van a divergir. |
| G7 | 15 registros de decisión faltantes | 28 entradas en el índice, 13 registros completos. Es entregable calificado. |

---

## 6. Checklist del reto

| Requisito | Estado |
|---|---|
| Mandato sin entregar la tarjeta | ✅ |
| El comerciante verifica antes de aceptar | ✅ en `src/merchant`; ❌ en las apps Next.js |
| Compra end-to-end dentro del mandato | ✅ |
| Fuera de límite: rechazado o escalado | ✅ |
| Revocación viva | ✅ ~0,03 s |
| Agente suplantado | ✅ |
| Rastro auditable | ✅ |
| **Tres vistas** | ❌ 108 líneas de andamiaje |
| **Jueces lo operan solos** | ❌ sin despliegue ni dominio |
| **Slides, demo, repo, diagrama, decision log** | ⚠️ repo ✅, diagrama ✅, log parcial, slides y video ❌ |

Siete de diez, y los tres que faltan son los más visibles.

---

## 7. Por dónde empezar

Ordenado por lo que cambia la nota, no por lo que es más interesante.

| Orden | Acción | Cierra | Esfuerzo |
|---:|---|---|---|
| 1 | Las tres vistas contra los endpoints que ya responden | Checklist | 3–4 h |
| 2 | `pay` con mandato en las tres apps Next.js | G1 · G2 | 2 h |
| 3 | Los cuatro servicios en `compose.yaml` + cómo levantarlo | G-3 | 30 min |
| 4 | Desplegar y apuntar el dominio | Checklist | 2 h |
| 5 | Autenticar la API y passkey al aprobar | F2 · F3 | 2–3 h |
| 6 | Apuntar el agente al kernel real | F1 | 2 h |
| 7 | Una prueba end-to-end que cruce servicios | §4 | 1 h |
| 8 | Slides y video de respaldo | Checklist | 2 h |

**Lo que no cabe y no pasa nada:** partir `DecisionService`, el `WITH RECURSIVE`,
unificar los dos servidores MCP, el bot de Telegram.

---

## Una lectura del conjunto

El proyecto tiene **más backend del que puede enseñar**. 24 679 líneas de Python
resuelven bien lo difícil —criptografía, cadena de evidencia, reserva atómica,
revocación en 30 ms— y hay 22 340 líneas de comercio que se ven muy bien y no
tocan nada de eso.

Lo que falta no es capacidad técnica: son las tres pantallas que conectan ambas
mitades y el `verify` que hace que la mitad bonita cuente la historia correcta.
Las dos cosas son de horas, no de días.

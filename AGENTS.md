# Reglas de mint

Instrucciones para quien trabaje en esta app — persona o agente. Si vas a tocar
conciliación bancaria, leelas antes.

mint es el **motor de conciliación** del ecosistema Domina: se migró desde `l10n_ve`,
que hoy conserva un shim (`l10n_ve/overrides/payment_reconciliation.py`) que re-exporta
estos símbolos. Lo que se rompa acá se rompe en todos los sites que lo consumen.

Cada regla existe porque **ya se rompió**, y abajo está el caso concreto. No son
preferencias de estilo.

---

## 1. Un cobro en efectivo o por pasarela NO espera depósito

**Dónde:** `mark_cash_like_reconciled()` en `mint/apis/reconciliation.py`

`CASH_LIKE_TYPES = ("Cash", "Gateway", "Gangway")`. Esos cobros se concilian **solos**:
el efectivo se recibe en caja y la pasarela (C2P, Biopago) acredita sin generar un
movimiento que se importe como Bank Transaction. Si igual aparece un depósito con su
referencia se enlaza, pero **su ausencia no puede dejar el cobro pendiente**.

Si agregás otra vía que apruebe cobros, pasala por esa función. No escribas la condición
al lado: ya estuvo duplicada entre `on_submit_receive_payment` y el barrido nocturno
`_approve_drafts`, que es exactamente donde dos copias se desincronizan.

**Caso.** El `RECON_DONE` estaba **dentro** de un `if deposit:`, así que un cobro de
pasarela —que por definición nunca tiene depósito— quedaba en «Conciliación Pendiente»
para siempre. Medido en producción: **20 cobros de pasarela y 480 en efectivo, ninguno
con `clearance_date`**, repasados inútilmente por el barrido todas las noches.

> Un cobro **bancario** sí exige su depósito: eso es la regla 4 y no se toca.

---

## 2. El estado de conciliación lo manda `clearance_date`, no el Select

`on_change_payment_entry` deriva `custom_reconciliation_status` de la existencia de
`clearance_date`:

```python
if doc.clearance_date and status != RECON_DONE:            -> RECON_DONE
elif not doc.clearance_date and status != RECON_PENDING:   -> RECON_PENDING
```

O sea que **escribir sólo `custom_reconciliation_status` no sirve**: se deshace en el
siguiente guardado del documento. Si querés dejar un cobro conciliado, escribí la fecha.

---

## 3. `_is_duplicate` no ve los depósitos que entraron por el extracto

Ese guard casa por **`transaction_id`**, y ese campo lo pone **sólo el webhook**. Una
Bank Transaction creada por la importación del extracto **no lo tiene**, así que el guard
no la encuentra y da vía libre a crear el depósito por segunda vez.

Si escribís cualquier cosa que reprocese o reinyecte notificaciones, **no te apoyes sólo
en `_is_duplicate`**. Agregá una segunda comparación por lo que compararía un humano:
misma cuenta bancaria, misma fecha, mismo monto.

**Caso (2026-08-04).** Reprocesar 188 notificaciones de Bancaribe que el webhook había
rechazado habría creado **55 depósitos duplicados por Bs 35.921.035,91** —tres de ellos
de Bs 11.000.000, Bs 9.595.543 y Bs 8.000.000—, todos ya presentes por extracto. La
consulta por referencia decía que no existía ninguno; la de cuenta+fecha+monto los
encontró a todos.

---

## 4. Sólo se concilia contra un comprobante EMITIDO

`reconcile_vouchers` verifica `docstatus == 1` antes de enlazar. Un borrador o un
cancelado se rechazan.

**Caso:** referencia `61873142037` (JUAN JOSE FERRER USECHE). Un extracto emitido quedó
conciliado contra un Payment Entry en **borrador** y contra el PE de **otro cliente**.

---

## 5. Al desconciliar, iterá sobre una COPIA de la lista

**No uses `transaction.remove_payment_entries()`**. El core de ERPNext hace
`for pe in self.payment_entries: self.remove_payment_entry(pe)`: itera y muta la misma
lista, así que **salta una fila de cada dos** y deja el extracto a medio desconciliar.

Del mismo incidente que la regla 4: al desconciliar sólo se limpiaba la mitad de las filas.

---

## 6. Un cobro bancario exige su depósito, y no se elige a dedo

`before_submit_receive_payment` **detiene la aprobación** si no encuentra un depósito con
la referencia del cobro en su cuenta. Y si encuentra **más de uno**, también: el operador
tiene que borrar o cancelar el incorrecto. Conciliar contra uno elegido a dedo es cómo se
paga la factura de otro cliente.

Cuando sí lo encuentra, el monto autoritativo es **el del banco**, no el tecleado:
`_apply_deposit_amount` lo fija y se recalcula todo el documento.

---

## 7. Los literales `RECON_*` son opciones de un Select en español

`RECON_PENDING = "Conciliación Pendiente"`, `RECON_DONE = "Conciliado"`,
`RECON_REVIEW = "Revisar"`. Son los valores del campo `custom_reconciliation_status` tal
como están en la base.

Si cambiás uno, hay que cambiarlo **también** en el bloque `except ImportError` del shim
de `l10n_ve` (que es el que corre en sites sin mint). Un literal divergente no casaría
nunca contra la BD y dejaría estados fantasma. Hay un test de paridad que lo vigila:
`l10n_ve/tests/test_reconciliation_shim_parity.py`.

---

## Convenciones generales

- **Código y comentarios en inglés.** Identificadores (funciones, variables, clases,
  tests), nombres de archivo y carpeta —incluidos los `name` de Report, Workspace,
  Number Card, Dashboard y Print Format, que al exportarse se vuelven rutas en disco—
  y también **comentarios y docstrings**. En español va **sólo lo que lee el usuario
  final**: labels, mensajes de error, títulos y descripciones visibles. Buena parte del
  código existente tiene los comentarios en español: es deuda, no precedente. (Los
  mensajes de commit sí van en español.)
- **Los tests corren SIN site**, con `frappe.local.db` bindeado a un `MagicMock` y las
  dependencias del módulo parcheadas. Seguí ese patrón: son milisegundos y no piden
  fixtures.
- **La suite viene con fallos previos**: `origin/development` da 6 failures y 21 errors
  (mocks incompletos, parseo de HTML). Antes de culpar a tu cambio, corré la suite contra
  un checkout limpio y compará los números.
- **El build NO chequea tipos.** `yarn build` es `vite build` a secas, sin `tsc` ni
  `vue-tsc`: un error de tipos en el frontend **no rompe el CI**. Si tocás TypeScript,
  verificalo aparte.
- **mint SÍ traduce** con `_()`, a diferencia de las apps `domina_*`. Tiene `locale/`,
  `translations/` y extractores configurados.
- **4 espacios de indentación** (no hay config de ruff que lo imponga; es lo que usa el
  código). `bank_reconciliation.py` tiene unas pocas líneas con tab: no las propagues.
- `ignore_permissions=True` en save/insert/delete.
- Rama feature + PR a `development`. Los backfills se exponen como `dry_run()` /
  `execute()` manuales y **no** van en `patches.txt`.

# Registro de cambios

Formato de [Keep a Changelog](https://keepachangelog.com/es-ES/1.1.0/);
versiones según [SemVer](https://semver.org/lang/es/).

## [No publicado]

Sin cambios todavía.

## [0.5.1] — 2026-09-22

Sin cambios en la API. Actualiza documentación y metadatos.

## [0.5.0] — 2026-09-19

### Añadido

- Las 18 operaciones M2M que faltaban: perfil y política de reintentos de cuenta,
  resumen del panel, planes públicos, insights, finanzas y suscripción. Los planes
  públicos se solicitan sin cabecera `Authorization`.
- La prueba de superficie compara las 66 operaciones completas del spec público y
  ejercita rutas contra grabaciones sanitizadas en un servidor HTTP local.

### Cambiado

- `validate_account()` deja de formar parte del SDK: su operación ya no es M2M
  pública. En `0.x` esta retirada es incompatible.
- Los reportes mensuales, por contraparte, por banco y contables usan `csv` por
  omisión, igual que el contrato público. `preview` se solicita explícitamente.

## [0.4.1] — 2026-09-19

Primera versión publicada en PyPI. No cambia la superficie del SDK respecto de
`0.4.0`.

Los cambios del producto se documentan en el
[changelog de la API](https://docs.veriko.mx/es/changelog).

## [0.4.0] — 2026-09-18

La superficie pasa de 48 operaciones a 49: `beneficiaries` gana la cancelación de
una importación, y con ella el SDK cubre todas las operaciones de máquina a máquina de
sus cinco familias.

### Añadido

- `beneficiaries.import_cancel()`: cancela una importación que todavía no se
  confirmó (`DELETE /beneficiaries/imports/{id}`).

### Cambiado

- `test_operations.py` exige que las cinco familias no traigan operaciones de máquina a
  máquina sin método, en lugar de admitir una excepción.

## [0.3.1] — 2026-09-18

Las familias `validations`, `webhooks` y `catalog` pasan a seguir el spec público. Varias formas
de lo que devuelve el SDK cambian.

### Corregido

- `set_retry_policy()` envía el cuerpo envuelto en `retry_policy`, como exige el spec.
  Antes lo mandaba sin envolver.
- `set_retry_policy()` y `cancel_retries()` devuelven el estado del ciclo (`RetryState`) y no una
  validación vacía: la respuesta del spec trae sólo `retry_state`.
- `ValidationSummary` deja de tener `has_cep`, `links` y `completed_at`, que un elemento de listado
  no trae, y gana los campos que sí trae (`bank_name`, `amount`, `tracking_key`, `retry_state`...).
- `RetryAttempt` lee los campos del spec: `attempt_number`, `dispatched_at`, `new_status`...
- `Bank` gana `aliases` y pierde `short_name`, que el spec no declara.
- `list(playground=True)` y `with_deleted=True` viajaban como `True`; ahora `playground` envía `1` y
  `with_deleted` envía `1` o `0`. El transporte ya no codifica ningún booleano como `True`.
- `webhooks.deliveries()` y `export_deliveries()` descartaban `status` y `event_type` cuando se
  pasaba un endpoint. Ahora consultan el listado global con `endpoint_id`.
- Una lectura condicional que recibe `304` levanta `APIError` con `status=304`, como decía la
  documentación. Antes `get()` devolvía una validación vacía y `banks()` una lista vacía.
- `webhooks.update(events=[])` levanta `events_required` en lugar de descartar el cambio.
- `validations.image()` propone un nombre con extensión cuando la respuesta no trae
  `Content-Disposition`.

### Cambiado

- `stats()`, `bin_lookup()`, `banxico_status()`, `banxico_timeseries()` y `send_cep_to_telegram()`
  devuelven los atributos del recurso, como ya hace `client.usage`: `stats["total"]`.
- `catalog.banks()` devuelve un `BankList`, una lista de `Bank` con el `ETag` en `etag`.
- `RetryState` gana `max_retries`, `interval_seconds` y `outcomes`.
- `WebhookEndpoint` gana `description`, `secret_hint`, `last_delivery_at` y `updated_at`;
  `WebhookDelivery` gana `endpoint_id`, `endpoint_url`, `validation_id`, `attempt`,
  `response_body` y `next_retry_at`.

### Añadido

- `description` en `webhooks.create()` y `webhooks.update()`.
- `spec/openapi.yaml`, copia del spec público, y tres pruebas que lo usan:
  `test_operations.py` contrasta cada operación con el spec y exige que sea de máquina a
  máquina; `test_models_follow_spec.py` comprueba que los campos de cada modelo existan en él;
  `test_spec_public.py` vigila que la copia cumpla el contrato de la API.
- Las grabaciones de las familias `validations`, `webhooks` y `catalog` se recortan de los
  ejemplos del spec público.

## [0.3.0] — 2026-09-18

La superficie pasa de 27 operaciones a 48: se suman las familias
`beneficiaries` y `usage`.

### Añadido

- `client.beneficiaries`: registrar, listar, cambiar y archivar cuentas
  beneficiarias; resolución de una cuenta en la lista propia (`lookup`);
  exportación en CSV y XLSX; y la importación masiva como ciclo
  (`import_template`, `import_start`, `import_status`, `import_preview`,
  `import_edit_row`, `import_remove_row`, `import_commit` e `import_wait`).
- `client.usage`: cuota del plan (`summary`), historial mensual (`history`),
  desglose por operación (`breakdown`), límites de tasa (`limits`), mapa de
  calor (`heatmap`), métricas de la API (`api_usage`) y su exportación
  (`export`).
- Los tipos `Beneficiary`, `BeneficiaryLookup`, `BeneficiaryImportJob`,
  `BeneficiaryImportRow` y `UsageSummary`.
- `BeneficiaryImportJob.is_settled`: la condición que espera `import_wait()`
  (`preview_ready` o un estado final).
- Subida de archivos `multipart/form-data` en el transporte, sin dependencias
  de runtime.

### Cambiado

- El transporte codifica los valores de consulta que son listas como
  parámetros repetidos, para el filtro `buckets` de la vista previa.
- El arnés de pruebas entiende `PATCH`, que usa la corrección de filas.

## [0.2.0] — 2026-09-18

La superficie pasa de 3 operaciones a 27: las familias `validations`, `webhooks`
y `catalog` completas.

### Añadido

- `client.validations`: validación por imagen (`validate_ocr`), modo asíncrono
  (`enqueue`, `enqueue_ocr`) con sondeo por `ETag` (`wait_for`), listado con
  paginación (`list`, `iter`), estadísticas, exportación en CSV y XLSX, imagen
  del comprobante, intentos de reintento, cambio de la política de reintentos,
  cancelación, retirada del historial y envío del comprobante a Telegram.
- `client.webhooks`: registrar, listar, cambiar y retirar endpoints; evento de
  prueba; rotación del secreto; historial de entregas con paginación y su
  exportación.
- `client.catalog`: catálogo de bancos SPEI, banco emisor de una tarjeta y
  estado del servicio de Banxico con su serie temporal.
- `Page` y los iteradores `iter*`, que piden la página siguiente sólo cuando la
  anterior se agota.
- `Validation.is_settled`: distingue un veredicto firme de uno terminal que
  todavía puede cambiar porque hay reintentos en marcha. Es lo que espera
  `wait_for()`.
- Los tipos `ValidationSummary`, `WebhookEndpoint`, `WebhookDelivery`,
  `WebhookTestResult`, `RetryAttempt`, `Bank` y `Document`.

### Cambiado

- `validate_transfer()`, `get_validation()` y `get_cep()` siguen en la raíz del
  cliente y se comportan igual: ahora delegan en la familia correspondiente.
- `Validation` gana el campo `etag`, que `get()` rellena con la cabecera de la
  respuesta.

## [0.1.0] — 2026-09-18

Primera versión del SDK oficial de Python.

### Añadido

- `Veriko.validate_transfer()`: valida una transferencia SPEI contra el CEP de
  Banxico (`POST /v1/validate`), con la política de reintentos de la API
  (`retry_policy`) y la cabecera `Idempotency-Key`.
- `Veriko.get_validation()`: lee una validación por su identificador, con el
  estado del ciclo de reintentos.
- `Veriko.get_cep()`: descarga el comprobante oficial en XML o en PDF.
- `verify_webhook()` y `parse_webhook()`: verificación HMAC-SHA256 de la firma
  de una entrega, con comparación en tiempo constante.
- Reintentos automáticos de los `5xx`, el `408` y el `429`, con espera
  exponencial y respeto del `Retry-After` de la respuesta.
- Jerarquía de excepciones por estado HTTP, con el `code` de la API, el arreglo
  completo de `errors` y el `request_id`.
- Tipado completo (`py.typed`), sin dependencias de runtime. Python 3.9 o
  superior.


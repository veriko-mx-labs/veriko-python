# Registro de cambios

Formato de [Keep a Changelog](https://keepachangelog.com/es-ES/1.1.0/);
versiones según [SemVer](https://semver.org/lang/es/).

## [No publicado]

## [0.3.0] — 2026-09-18

La superficie pasa de 27 operaciones a 48: se suman las familias
`beneficiaries` y `usage`. Es la segunda y última tanda del alcance fijado.

### Añadido

- `client.beneficiaries`: registrar, listar, cambiar y archivar cuentas
  beneficiarias; validación estructural de una cuenta sin gastar cuota
  (`validate_account`); resolución de una cuenta en la lista propia (`lookup`);
  exportación en CSV y XLSX; y la importación masiva como ciclo
  (`import_template`, `import_start`, `import_status`, `import_preview`,
  `import_edit_row`, `import_remove_row`, `import_commit` e `import_wait`).
- `client.usage`: cuota del plan (`summary`), historial mensual (`history`),
  desglose por operación (`breakdown`), límites de tasa (`limits`), mapa de
  calor (`heatmap`), métricas de la API (`api_usage`) y su exportación
  (`export`).
- Los tipos `Beneficiary`, `BeneficiaryLookup`, `AccountValidation`,
  `BeneficiaryImportJob`, `BeneficiaryImportRow` y `UsageSummary`.
- `BeneficiaryImportJob.is_settled`: la condición que espera `import_wait()`
  (`preview_ready` o un estado final).
- Subida de archivos `multipart/form-data` en el transporte, sin dependencias
  de runtime.

### Cambiado

- El transporte codifica los valores de consulta que son listas como
  parámetros repetidos, para el filtro `buckets` de la vista previa.
- El arnés de pruebas entiende `PATCH`, que usa la corrección de filas.

### Pendiente para versiones siguientes

- Nada dentro del alcance fijado. Quedan fuera de propósito finanzas, métricas
  propias, catálogo de planes, suscripción y el resumen del panel.

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

### Pendiente para versiones siguientes

- Beneficiarios, con su importación masiva.
- Métricas de consumo y límites de tasa.

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

### Pendiente para versiones siguientes

- Modo asíncrono (`?async=1`) y sondeo con `ETag`.
- Validación por OCR de una imagen de comprobante.
- Beneficiarios, importación masiva y finanzas.

[No publicado]: https://github.com/veriko-mx-labs/veriko-python/compare/v0.3.0...HEAD
[0.3.0]: https://github.com/veriko-mx-labs/veriko-python/releases/tag/v0.3.0
[0.2.0]: https://github.com/veriko-mx-labs/veriko-python/releases/tag/v0.2.0
[0.1.0]: https://github.com/veriko-mx-labs/veriko-python/releases/tag/v0.1.0

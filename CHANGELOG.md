# Registro de cambios

Formato de [Keep a Changelog](https://keepachangelog.com/es-ES/1.1.0/);
versiones según [SemVer](https://semver.org/lang/es/).

## [No publicado]

Sin cambios todavía.

## [0.8.1] — 2026-11-14

### Cambiado

- El README y este registro de cambios vuelven a estar en español.

## [0.8.0] — 2026-11-13

### Cambiado

- Las peticiones usan los nombres de campo de la API: `send_date`, `amount`, `tracking_key`,
  `numeric_reference`, `sender_bank`, `receiver_bank`, `beneficiary_account`, `candidate_accounts` y
  `recipient_is_bank`. Esto vale para `validate_transfer()`, `validations.validate()`,
  `validations.validate_ocr()`, `validations.enqueue()` y `validations.enqueue_ocr()`. `amount` sigue
  siendo obligatorio. Los nombres de campo de versiones anteriores siguen funcionando para las
  integraciones que ya existen y emiten un `DeprecationWarning`; no forman parte de la API tipada.
- El `User-Agent` pasa a `veriko-sdk-python/<versión> (<implementación> <versión de Python>;
  <sistema> <arquitectura>)`. Ya no incluye la dirección del repositorio.
- El tiempo de espera por omisión pasa de 30 a 60 segundos: con 30, el cliente cortaba una validación
  síncrona que la API puede sostener hasta 45 segundos antes de responder `202`.
- Un `POST` sólo se reintenta cuando lleva un `Idempotency-Key`. Las seis operaciones que aceptan el
  encabezado siempre mandan uno, generado cuando no se pasa `idempotency_key`, y lo reutilizan en cada
  reintento. Una clave que no cumple `[A-Za-z0-9_-]{1,255}` levanta `InvalidRequestError` con
  `code="invalid_idempotency_key"` antes de que salga la petición.
- Un `409` con `idempotency_key_in_progress` espera `Retry-After` y repite la petición con la misma
  clave, hasta `RetryConfig.idempotency_wait_seconds` (30 por omisión), y después levanta
  `IdempotencyKeyInProgressError`.
- Un `Retry-After` mayor que `RetryConfig.max_retry_after_seconds` (60 por omisión) levanta el error de
  inmediato en lugar de esperar.
- Un `429` con `quota_exceeded` ya no se reintenta: la cuota del plan se renueva en el siguiente
  periodo, no en segundos. Un `429` con `rate_limit_exceeded` se sigue reintentando.
- Un `DELETE` que responde `404` después de un reintento se devuelve como hecho: el primer intento ya lo
  había borrado.
- `webhooks.regenerate_secret()` recibe `immediate=False`. Por omisión, el secreto anterior sigue
  firmando durante 24 horas.
- `parse_webhook()` y `verify_webhook_timestamped()` rechazan por omisión una `t` fuera de una ventana de
  5 minutos. `verify_webhook()` compara en tiempo constante y devuelve `False` ante una entrada no ASCII
  en lugar de levantar un error.
- `wait_for()` e `import_wait()` esperan lo que pide la API (`meta.next_poll_after_seconds` o
  `Retry-After`) y nunca pasan de `timeout`. Levantan `VerikoTimeoutError`, que también es un
  `TimeoutError`.
- Las comprobaciones locales levantan `InvalidRequestError` con los mismos códigos de la API
  (`account_required`, `tracking_key_or_reference_required`,
  `account_and_candidates_mutually_exclusive`, `candidate_accounts_invalid`) y `local=True`.
  `empty_update` ahora es `no_valid_fields`.
- Una lectura condicional que no encuentra nada nuevo levanta `NotModifiedError`, una subclase de
  `APIError` con `status=304`. Un `3xx` es un `APIError`: no se siguen las redirecciones.
- `recipient_is_bank` es un booleano. `0` y `1` se siguen aceptando.
- `beneficiaries.import_preview(buckets=[...])` envía `buckets[]`, la forma de arreglo que lee la API.
- `Validation.is_settled` ya no espera un estado de reintentos con `enabled=True` y sin ciclo.
- Los métodos que devolvían un `dict` simple devuelven un diccionario tipado (`ValidationStats`,
  `BinLookup`, `BanxicoStatus`, `UsageHistory`, `UserProfile`, `DashboardSummary`,
  `BillingSubscription`, `PublicPlan`, `InsightsOverview`, `FinanceSummary`, `CepExport` y otros). Se
  leen como antes (`result["field"]`, `.get()`, `in`, la iteración, `==` contra un diccionario,
  `json.dumps()`) y traen `response_info`. Cada campo del spec también es un atributo, y `id`, `type`,
  `links`, `meta` y `raw` guardan lo que la respuesta trae fuera de los atributos.
- `validations.iter()`, `validations.stats()`, `validations.export()`, `webhooks.iter_deliveries()`,
  `webhooks.export_deliveries()` y `beneficiaries.iter_import_preview()` reciben argumentos explícitos y
  tipados en lugar de `**filters`: un filtro mal escrito levanta `TypeError`.
- Una `Page` sin contadores informa el tamaño por omisión de su endpoint: 10 validaciones, 50 entregas,
  25 filas de importación. Los iteradores piden la página más grande que da el endpoint, salvo que se
  pase `per_page`.
- Los identificadores en una ruta se revisan contra el formato del spec y se codifican con porcentaje.
  Los identificadores de beneficiarios e importaciones también aceptan un `int`.
- Las peticiones reutilizan las conexiones a la API (HTTP keep-alive), sin dependencias nuevas.
- `accept_language` acepta `es`, `en` y las etiquetas que empiezan con ellos; cualquier otro valor
  levanta `ConfigurationError`.
- `validate_ocr()` y `enqueue_ocr()` rechazan antes de enviar un comprobante de más de 12 MB
  (`body_too_large`), un `image_url` que no es `https` (`invalid_url_scheme`) y un PDF sin comprimir de
  más de 3 páginas (`pdf_too_many_pages`). Un `str` que empieza con `data:` va como URI de datos.
- `candidate_accounts` recibe al menos 2 cuentas; el máximo lo fija el servicio.

### Añadido

- `beneficiaries.validate_account(account, *, type=None)`: revisa la estructura de una cuenta CLABE,
  una tarjeta o un número de celular (`GET /beneficiaries/validate-account`) y devuelve
  `AccountValidation`. No consume cuota. Con ella, el SDK cubre las 75 operaciones M2M.
- `AsyncVeriko`: las mismas familias y métodos como corrutinas, y los `iter_*` como iteradores
  asíncronos. El SDK sigue sin dependencias en tiempo de ejecución.
- `Veriko(user_agent_prefix=...)` y `build_user_agent(prefix=None, suffix=None)`.
- `response_info` en cada resultado: `status`, `request_id`, `rate_limit`, `deprecation`, `sunset`,
  `idempotent_replayed`, `etag` y `retry_after`. Una respuesta con un encabezado `Deprecation` emite un
  `DeprecationWarning` por proceso.
- `Veriko.with_options(timeout=, max_retries=, headers=, accept_language=)`: un cliente con valores
  cambiados por llamada. El original no cambia.
- Errores: `APIError.entries`, `hint`, `docs_url`, `codes`, `field_errors`, `has_code()`,
  `validation_id` y `local`; `GoneError` (`410`), `PayloadTooLargeError` (`413`),
  `QuotaExceededError`, `IdempotencyKeyInProgressError`, `NotModifiedError`, `VerikoTimeoutError` y
  `VerikoConnectionError`. `ConnectionError` expone `cause` y `timed_out`.
- `finance.wait_for_cep_export()` y `CEP_EXPORT_TERMINAL_STATUSES`.
- `verify_delivery()`: comprueba `X-Webhook-Signature-Timestamped` y `X-Webhook-Signature-Old` contra
  uno o varios secretos. `verify_webhook()` y `parse_webhook()` también reciben varios secretos.
  `WebhookEvent.delivery_id` y `attempt_timestamp`.
- `WebhookEndpoint.previous_secret_expires_at`.
- `Validation.ocr_result`, `ocr_confidence`, `normalized_data`, `normalization_warnings`,
  `banxico_result`, `enqueued_at`, `processing_started_at`, `expires_at`, `etag_version`,
  `image_path`, `is_masked`, `batch_id`, `batch_position` e `is_pending`. `QueuedValidation` trae
  `enqueued_at`, `expires_at`, `etag_version`, `client_ref` y `retry_state`.
  `ValidationSummary.numeric_reference`, `bank_code` y `purged_at`.
- `recipient_is_bank` en `validations.validate_ocr()`.
- Los montos `Decimal` se serializan como números JSON.
- Python 3.14 en los clasificadores y ejemplos de importación masiva, rotación de secreto, exportación
  de CEP y cliente asíncrono.
- Se exportan `Transport`, `MultipartFile`, `ResponseInfo` y `RateLimitInfo`.
- `Veriko.close()` y `with Veriko() as client:` cierran las conexiones abiertas; `AsyncVeriko.aclose()`
  también.
- `Page.next_page()` pide la página siguiente con los mismos filtros.
- `WEBHOOK_EVENTS`, `WEBHOOK_TEST_EVENT` y `WebhookEventName`: los tipos de evento del spec.
  `WebhookEvent.is_validation_event`, `is_billing_event` e `is_test`.
- `CepExport` con `status`, `from_`, `to`, `item_count`, `size_bytes`, `error_code`, `is_ready`,
  `is_terminal` y `download_path`, y las constantes `CEP_EXPORT_STATUSES` y
  `CEP_EXPORT_ERROR_CODES`.
- `Document.headers`, `finance_folio`, `finance_items` e `iter_bytes()`; `CepDocument.headers`.

### Corregido

- Un error de red que no es un `URLError` (`http.client.IncompleteRead`, `BadStatusLine`) levanta
  `ConnectionError` en lugar de escaparse, y una falla de TLS no se reintenta.

## [0.7.2] — 2026-11-09

### Añadido

- `Validation.account_cross_checked`: `True` cuando la cuenta consultada en Banxico es del propio cliente
  (la enviada, una de las candidatas o la de un beneficiario activo). Con `False`, un `valid` confirma
  que la transferencia existe, pero no que se pagó a la cuenta esperada. Es `None` cuando la API no lo
  informa.

### Cambiado

- `validate()` y `validate_ocr()` documentan que la API responde `202` en `processing` cuando la
  consulta a Banxico tarda más de 45 segundos; el veredicto se recoge con `get()`.
- `amount` documenta el tope de 13 dígitos enteros.

## [0.7.1] — 2026-11-08

### Cambiado

- Una operación autenticada revisa la forma de la clave de API antes de enviar la petición. Si la clave
  no es `veriko_` seguido de 64 caracteres hexadecimales en minúscula, levanta `ConfigurationError` y no
  sale a la red. El mensaje de error no incluye la clave.

## [0.7.0] — 2026-11-08

### Añadido

- Exportaciones de CEP en segundo plano, para rangos que no conviene descargar en una sola petición:
  `finance.create_cep_export()`, `finance.list_cep_exports()`, `finance.get_cep_export()` y
  `finance.download_cep_export()`.

### Cambiado

- `send_date` es opcional en `validate_transfer()`, `validations.validate()` y `validations.enqueue()`,
  como en el contrato público `1.168.2`. Si falta o no es utilizable, la API prueba fechas candidatas
  contra Banxico; sólo rechaza con `422` (`send_date_required`) cuando no tiene datos para adivinarla.

## [0.6.0] — 2026-10-27

### Cambiado

- `account.my_profile()` sigue el contrato público `1.111.0`. El diccionario ya no incluye `role`, y
  `permissions` enumera los permisos efectivos con claves como `validations.records.read` en lugar de
  pares `recurso:acción`. Si tu integración leía `role`, usa `roles`.

### Añadido

- `platform_access` en el perfil: `True` cuando la cuenta puede entrar al panel de administración.
- `roles` en el perfil: los roles vigentes de la cuenta, cada uno con `key`, `scope_type` (`platform` u
  `organization`) y `scope_id`.

## [0.5.9] — 2026-10-15

### Añadido

- `Beneficiary.reactivated`: `True` cuando `beneficiaries.create()` reactivó una cuenta archivada con el
  mismo número (la API responde `200` con `meta.reactivated`); `False` en un alta nueva y en las demás
  lecturas.

## [0.5.8] — 2026-10-15

### Añadido

- `catalog.spei_calendar()` (`GET /v1/public/spei-calendar/{year}`): el calendario SPEI de días
  inhábiles de un año. Devuelve `SpeiCalendar` con `year`, `non_business_days`, `source` y
  `covered_years`. Acepta `if_none_match`, y su `ETag` queda en `SpeiCalendar.etag`. No consume cuota.
  Un año sin calendario levanta `NotFoundError` con `spei_calendar_year_not_covered`.
- `validations.validate_ocr()` y `validations.enqueue_ocr()` aceptan `sender_bank` y `receiver_bank`: la
  clave SPEI de 4 o 5 dígitos sustituye al banco leído del comprobante antes de consultar a Banxico.
- `validations.list()`, `validations.stats()` y `validations.export()` aceptan `banks`, hasta 50 claves
  SPEI en lista o separadas por comas; filtra por banco emisor o receptor.
- `webhooks.deliveries()` y `webhooks.export_deliveries()` aceptan `response_status`, `validation_id`,
  `q`, `from_`, `to`, `min_attempt` y `min_response_time_ms`. `status`, `event_type` y `response_status`
  aceptan una lista, y `webhook_id` una lista de hasta 20 endpoints.

## [0.5.7] — 2026-10-02

### Añadido

- `validations.recheck()` (`POST /v1/validations/{id}/recheck`): consulta de nuevo a Banxico el estado
  del pago de una validación `valid` creada hace 72 horas como máximo, sin consumir cuota. Devuelve
  `RecheckResult` con la validación y con `checked_at`, `changed` y `previous_status`. Si Banxico
  reporta la devolución, la validación pasa a `returned`. No se reintenta sola.
- `Validation.payment_status` (`PaymentStatus`): el estado del pago que dio Banxico en la última
  revisión. `None` si la respuesta no lo trae.
- `validations.prepare_purge()` y `validations.execute_purge()`: el borrado definitivo de una
  validación, en dos pasos. Devuelven `PurgePreparation` y `PurgeResult`. `execute_purge()` no se
  reintenta sola. `Validation.purged_at` marca la validación depurada y `Validation.is_purged` lo
  resume.
- `candidate_accounts` en `validate_transfer()`, `validations.validate()`, `validations.validate_ocr()`,
  `validations.enqueue()` y `validations.enqueue_ocr()`: al menos 2 cuentas, hasta el máximo que fije el
  servicio, en una sola validación y con una sola unidad de cuota. `Validation.candidate_match`
  (`CandidateMatch`) trae la posición y los últimos 4 dígitos de la cuenta que coincidió.
- `retain_image` en `validations.validate_ocr()` y `validations.enqueue_ocr()`, y
  `Validation.image_retained`: con `retain_image=False` la plataforma no conserva el archivo del
  comprobante.
- `WebhookEvent.payment_status` en el evento `validation.returned`, que avisa cuando una validación
  `valid` pasa después a `returned`. `None` en el resto de los eventos.

### Cambiado

- `beneficiary_account` deja de ser obligatoria cuando se envía `candidate_accounts`. Sin ninguna de las
  dos, el SDK levanta `InvalidRequestError` con `account_required`, y con las dos, con
  `account_and_candidates_mutually_exclusive`. En los dos casos no llama a la API.

## [0.5.6] — 2026-10-01

### Añadido

- `client_ref` en `validate_transfer()`, `validations.validate()`, `validations.validate_ocr()`,
  `validations.enqueue()` y `validations.enqueue_ocr()`: tu propia referencia de 1 a 64 caracteres, que
  vuelve en `Validation.client_ref` y en `ValidationSummary.client_ref`.
- El filtro exacto `client_ref` en `validations.list()`, `validations.iter()`, `validations.stats()` y
  `validations.export()`.
- `Validation.duplicate_of` y `Validation.account_conflict`, con los tipos `DuplicateOf` y
  `AccountConflict`. Son `None` si la respuesta no los trae.
- `verify_webhook_timestamped()` comprueba el encabezado `X-Webhook-Signature-Timestamped`
  (`t=<segundos>,v1=<hex>`): compara en tiempo constante el HMAC-SHA256 de `<t>.<cuerpo>` con cada `v1`
  y rechaza la entrega cuando `t` está a más de `tolerance` segundos del reloj, 300 por omisión.
  `timestamped_signature_from_headers()` encuentra el encabezado con cualquier grafía.
  `verify_webhook()` no cambia.
- `parse_webhook()` entrega el `client_ref` en `event.validation` cuando el webhook de la validación lo
  trae.

### Cambiado

- `beneficiary_account` es obligatoria en `validate_transfer()`, `validations.validate()` y
  `validations.enqueue()`. Sin ella, el SDK levanta `InvalidRequestError` con `account_required` y no
  llama a la API.

## [0.5.5] — 2026-09-29

Sin cambios en la superficie. El `422` de la validación documenta los códigos por campo con los que se
rechaza, antes de consultar a Banxico, una petición cuyos datos se contradicen:
`clabe_receptor_mismatch`, `tarjeta_receptor_mismatch` y `clave_longitud_invalida`. Llegan en
`errors[].code` de `InvalidRequestError`. Ningún rechazo `preflight_failed` de esa ruta consume cuota.

## [0.5.4] — 2026-09-28

### Añadido

- `UsageSummary.quota_kind` (`cycle` o `trial`) y `UsageSummary.renews`: una cuenta que aún no activa el
  plan gratuito consume validaciones de prueba, que no se reponen. `None` si la respuesta no los trae.

## [0.5.3] — 2026-09-27

### Cambiado

- `validations.validate_ocr()` y `validations.enqueue_ocr()` documentan el comprobante como PDF, de 1 a
  3 páginas.
- `validations.image()` acepta un comprobante en PDF y lo nombra con la extensión `.pdf`.

## [0.5.2] — 2026-09-26

### Añadido

- `WebhookEvent.banxico_confirmed`: el monto, la fecha, la clave de rastreo, ambos bancos y la cuenta
  del beneficiario enmascarada, tal como los confirmó Banxico, cuando la entrega lo trae. `None` en
  otro caso.

## [0.5.0] — 2026-09-19

### Añadido

- Las 18 operaciones M2M que faltaban: perfil y política de reintentos de la cuenta, resumen del panel,
  planes públicos, métricas, finanzas y suscripción. Los planes públicos se piden sin encabezado
  `Authorization`.

### Cambiado

- `validate_account()` deja de ser parte del SDK: su operación ya no es M2M pública. En `0.x` esta
  baja es incompatible.
- Los reportes mensual, por contraparte, por banco y contable usan `csv` por omisión, como el contrato
  público. `preview` se pide de forma explícita.

## [0.4.1] — 2026-09-19

Primera versión publicada en PyPI. No cambia la superficie del SDK respecto de `0.4.0`.

Los cambios del producto están en el [registro de cambios de la API](https://docs.veriko.mx/es/changelog).

## [0.4.0] — 2026-09-18

### Añadido

- `beneficiaries.import_cancel()`: cancela una importación que todavía no se confirmó
  (`DELETE /beneficiaries/imports/{id}`). Con ella, el SDK cubre todas las operaciones de máquina a
  máquina de sus cinco familias.

## [0.3.1] — 2026-09-18

Las familias `validations`, `webhooks` y `catalog` ahora siguen el spec público. Cambian varias formas
de lo que devuelve el SDK.

### Corregido

- `set_retry_policy()` envía el cuerpo envuelto en `retry_policy`, como exige el spec. Antes lo
  enviaba sin envolver.
- `set_retry_policy()` y `cancel_retries()` devuelven el estado del ciclo (`RetryState`) y no una
  validación vacía: la respuesta del spec trae sólo `retry_state`.
- `ValidationSummary` ya no tiene `has_cep`, `links` ni `completed_at`, que un elemento de listado no
  trae, y gana los campos que sí trae (`bank_name`, `amount`, `tracking_key`, `retry_state`...).
- `RetryAttempt` lee los campos del spec: `attempt_number`, `dispatched_at`, `new_status`...
- `Bank` gana `aliases` y pierde `short_name`, que el spec no declara.
- `list(playground=True)` y `with_deleted=True` iban como `True`; ahora `playground` envía `1` y
  `with_deleted` envía `1` o `0`. El transporte ya no codifica ningún booleano como `True`.
- `webhooks.deliveries()` y `export_deliveries()` perdían `status` y `event_type` cuando se pasaba un
  endpoint. Ahora consultan el listado global con `endpoint_id`.
- Una lectura condicional que recibe `304` levanta `APIError` con `status=304`, como decía la
  documentación. Antes, `get()` devolvía una validación vacía y `banks()` una lista vacía.
- `webhooks.update(events=[])` levanta `events_required` en lugar de descartar el cambio.
- `validations.image()` propone un nombre con extensión cuando la respuesta no trae
  `Content-Disposition`.

### Cambiado

- `stats()`, `bin_lookup()`, `banxico_status()`, `banxico_timeseries()` y `send_cep_to_telegram()`
  devuelven los atributos del recurso, como ya lo hacía `client.usage`: `stats["total"]`.
- `catalog.banks()` devuelve un `BankList`, una lista de `Bank` con el `ETag` en `etag`.
- `RetryState` gana `max_retries`, `interval_seconds` y `outcomes`.
- `WebhookEndpoint` gana `description`, `secret_hint`, `last_delivery_at` y `updated_at`;
  `WebhookDelivery` gana `endpoint_id`, `endpoint_url`, `validation_id`, `attempt`, `response_body` y
  `next_retry_at`.

### Añadido

- `description` en `webhooks.create()` y `webhooks.update()`.

## [0.3.0] — 2026-09-18

La superficie pasa de 27 operaciones a 48: se agregan las familias `beneficiaries` y `usage`.

### Añadido

- `client.beneficiaries`: registrar, listar, cambiar y archivar cuentas de beneficiarios; resolución de
  una cuenta en la lista propia (`lookup`); exportación en CSV y XLSX; y la importación masiva como
  ciclo (`import_template`, `import_start`, `import_status`, `import_preview`, `import_edit_row`,
  `import_remove_row`, `import_commit` e `import_wait`).
- `client.usage`: cuota del plan (`summary`), historial mensual (`history`), desglose por operación
  (`breakdown`), límites de uso (`limits`), mapa de calor (`heatmap`), métricas de la API (`api_usage`)
  y su exportación (`export`).
- Los tipos `Beneficiary`, `BeneficiaryLookup`, `BeneficiaryImportJob`, `BeneficiaryImportRow` y
  `UsageSummary`.
- `BeneficiaryImportJob.is_settled`: la condición que espera `import_wait()` (`preview_ready` o un
  estado final).

## [0.2.0] — 2026-09-18

La superficie pasa de 3 operaciones a 27: las familias completas `validations`, `webhooks` y `catalog`.

### Añadido

- `client.validations`: validación por imagen (`validate_ocr`), modo asíncrono (`enqueue`,
  `enqueue_ocr`) con sondeo por `ETag` (`wait_for`), listado paginado (`list`, `iter`), estadísticas,
  exportación en CSV y XLSX, imagen del comprobante, intentos de reintento, cambio de la política de
  reintentos, cancelación, retiro del historial y envío del comprobante a Telegram.
- `client.webhooks`: registrar, listar, cambiar y retirar endpoints; evento de prueba; rotación del
  secreto; historial de entregas con paginación y su exportación.
- `client.catalog`: catálogo de bancos SPEI, banco emisor de una tarjeta y estado del servicio de
  Banxico con su serie en el tiempo.
- `Page` y los iteradores `iter*`, que piden la página siguiente sólo cuando se agota la anterior.
- `Validation.is_settled`: distingue un veredicto firme de uno terminal que todavía puede cambiar porque
  hay reintentos en marcha. Es lo que espera `wait_for()`.
- Los tipos `ValidationSummary`, `WebhookEndpoint`, `WebhookDelivery`, `WebhookTestResult`,
  `RetryAttempt`, `Bank` y `Document`.

### Cambiado

- `Validation` gana el campo `etag`, que `get()` llena con el encabezado de la respuesta.

## [0.1.0] — 2026-09-18

Primera versión del SDK oficial de Python.

### Añadido

- `Veriko.validate_transfer()`: valida una transferencia SPEI contra el CEP de Banxico
  (`POST /v1/validate`), con la política de reintentos de la API (`retry_policy`) y el encabezado
  `Idempotency-Key`.
- `Veriko.get_validation()`: lee una validación por su identificador, con el estado del ciclo de
  reintentos.
- `Veriko.get_cep()`: descarga el comprobante oficial en XML o PDF.
- `verify_webhook()` y `parse_webhook()`: verificación HMAC-SHA256 de la firma de una entrega, con
  comparación en tiempo constante.
- Reintentos automáticos de los `5xx`, el `408` y el `429`, con espera exponencial y respeto por el
  `Retry-After` de la respuesta.
- Jerarquía de excepciones por estado HTTP, con el `code` de la API, el arreglo completo de `errors` y
  el `request_id`.
- Tipado completo (`py.typed`), sin dependencias en tiempo de ejecución. Python 3.9 o superior.

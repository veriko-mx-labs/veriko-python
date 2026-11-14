# veriko: SDK de Python para la API de Veriko

Cliente oficial de [Veriko](https://veriko.mx) para Python. Valida transferencias SPEI mexicanas
contra el CEP de Banxico, descarga el comprobante oficial y verifica la firma de los webhooks.

Sin dependencias en tiempo de ejecución. Python 3.9 o superior.

```python
from veriko import Veriko

client = Veriko()  # lee VERIKO_API_KEY del entorno

validation = client.validate_transfer(
    send_date="2025-03-15",
    amount=15000.50,
    tracking_key="MXBA20250315001234",
    beneficiary_account="012180004412345678",
)

print(validation.status)  # 'valid'
```

## Qué es el CEP

El **Comprobante Electrónico de Pago (CEP)** es el documento que expide el **Banco de México
(Banxico)** por cada transferencia que pasa por el SPEI, el sistema de pagos interbancarios de
México. Lleva el sello digital y la cadena original de la institución receptora, así que acredita
que una transferencia ocurrió y por qué monto.

El CEP no tiene efectos fiscales: no sustituye a una factura.

El portal de Banxico consulta un comprobante a la vez.

## Qué hace la API

La API consulta el CEP y devuelve un veredicto en `status`:

| veredicto | significado |
| --- | --- |
| `valid` | Banxico devolvió un CEP que coincide con los datos enviados |
| `not_found` | Banxico no tiene un CEP con esos datos. Puede ser temporal: un CEP tarda en publicarse |
| `cep_unavailable` | Banxico no pudo responder. No dice nada sobre la transferencia |
| `returned` | El pago se liquidó y la institución receptora lo devolvió después |
| `invalid` | Banxico respondió de una forma que el servicio no reconoce |
| `error` | Falló el procesamiento; el motivo viaja en `error_code` |

Con un veredicto `valid`, el comprobante queda disponible en XML y en PDF. Un `valid` es el veredicto
del momento de la consulta: Banxico puede reportar una devolución hasta 72 horas después, y la
validación pasa a `returned`.

## Familias de operaciones

El cliente agrupa las 75 operaciones M2M de la API en once familias:

| familia | qué cubre |
| --- | --- |
| `client.validations` | Validar por campos o por imagen, consultar, listar, exportar, revisar el estado del pago, borrar, la política de reintentos y la descarga del comprobante |
| `client.webhooks` | Registrar endpoints, rotar su secreto, enviar un evento de prueba y leer el historial de entregas |
| `client.catalog` | Catálogo de bancos SPEI, calendario de días inhábiles SPEI, consulta del banco emisor de una tarjeta y estado del servicio de Banxico |
| `client.beneficiaries` | Cuentas de beneficiarios guardadas y la importación masiva, como ciclo completo |
| `client.usage` | Cuota de validaciones, límites de uso y registro de actividad |
| `client.account` | Perfil y política de reintentos predeterminada de la cuenta |
| `client.dashboard` | Resumen del panel |
| `client.plans` | Catálogo y comparación de planes públicos, sin clave de API |
| `client.insights` | Resumen, tendencias, bancos y beneficiarios principales |
| `client.finance` | Resúmenes, estados de cuenta, vistas previas, descargas y exportaciones de CEP |
| `client.billing` | Suscripción activa |

Las tres operaciones de uso más frecuente están también en la raíz del cliente: `validate_transfer()`,
`get_validation()` y `get_cep()`.

## Instalación

```bash
pip install veriko
```

Mientras la versión sea `0.x`, una versión menor puede ajustar la superficie pública del SDK. La
versión `1.0.0` se reserva para una integración estable en producción.

## Conexiones e idioma

El cliente mantiene abiertas las conexiones a la API y las reutiliza, con `http.client` de la
biblioteca estándar. `client.close()` cierra las inactivas, y `with Veriko() as client:` lo hace al
salir. `accept_language` es `"es"` o `"en"` y fija el idioma de `error.detail`; cualquier otro valor
levanta `ConfigurationError`. Para una sola llamada, usa
`client.with_options(accept_language="en", timeout=5)`.

## Identifica a tu cliente

El SDK manda `User-Agent: veriko-sdk-python/<versión> (<implementación> <versión de Python>; <sistema>
<arquitectura>)`. Un producto que envuelve al SDK agrega su propio texto:

```python
client = Veriko(user_agent_prefix="mi-producto/1.2", user_agent_suffix="integracion-x")
```

## Autenticación

Crea la clave de API en el [panel](https://app.veriko.mx). Empieza con `veriko_`.

```python
client = Veriko(api_key="veriko_...")
```

Sin `api_key`, el cliente lee `VERIKO_API_KEY`. `VERIKO_BASE_URL` cambia la raíz de la API, y una raíz
`http://` sólo se acepta para `localhost`. Una clave que no cumple `veriko_<64 hex>` levanta
`ConfigurationError` antes de cualquier petición.

Opciones que valen para todas las llamadas:

| argumento | valor por omisión | efecto |
| --- | --- | --- |
| `timeout` | `60.0` | segundos de espera por intento |
| `max_retries` | `2` | reintentos automáticos además del primer intento |
| `retry` | ninguno | un `RetryConfig` con la configuración completa de la espera entre reintentos |
| `accept_language` | idioma de la cuenta | `es` o `en`, para `error.detail` |

Para cambiar una opción en una sola llamada, deriva un cliente:

```python
client.with_options(timeout=5.0, max_retries=0).validations.get(validation_id)
client.with_options(headers={"X-Trace": "t1"}).validations.get(validation_id)
```

`with_options()` devuelve un cliente nuevo y deja el original sin cambios.

## Valida una transferencia

```python
validation = client.validate_transfer(
    send_date="2025-03-15",
    amount=15000.50,                         # también acepta Decimal
    tracking_key="MXBA20250315001234",
    numeric_reference="1234567",
    sender_bank="BANCO NACIONAL DE MEXICO",
    receiver_bank="BBVA MEXICO",
    beneficiary_account="012180004412345678",
    client_ref="pedido-1042",
)
```

`amount` es obligatorio, además de `beneficiary_account` (o `candidate_accounts`) y de `tracking_key` o
`numeric_reference`. `send_date` es la fecha de envío en formato `YYYY-MM-DD` y es opcional: sin ella,
la API prueba fechas candidatas. Cada llamada consume cuota del plan.

El cliente revisa los argumentos antes de enviar y levanta `InvalidRequestError` con `local=True` y el
mismo código que usaría la API (`account_required`, `tracking_key_or_reference_required`,
`account_and_candidates_mutually_exclusive`, `candidate_accounts_invalid`).

### A partir del comprobante

```python
validation = client.validations.validate_ocr(
    image="comprobante.png", beneficiary_account="012180004412345678"
)
```

`image` acepta bytes, una ruta o una cadena que empiece con `data:` (un URI de datos). `image_url`
acepta un comprobante ya publicado en HTTPS. `recipient_is_bank=True` marca un pago a la propia
institución receptora.

Antes de enviar, el SDK rechaza un archivo de más de 12 MB (`body_too_large`), un `image_url` que no es
`https` (`invalid_url_scheme`) y un PDF sin comprimir de más de 3 páginas (`pdf_too_many_pages`). El
error es un `InvalidRequestError` con `local=True`.

### Sin esperar el veredicto

`validations.enqueue()` y `validations.enqueue_ocr()` aceptan los mismos argumentos que los métodos
anteriores, ponen la petición en cola y devuelven una `QueuedValidation`. Un argumento desconocido
levanta `TypeError`. `wait_for()` recoge el veredicto:

```python
queued = client.validations.enqueue(
    amount=15000.50, tracking_key="MXBA20250315001234", beneficiary_account="012180004412345678"
)
validation = client.validations.wait_for(queued.id, timeout=120)
```

`wait_for()` manda `If-None-Match` en cada sondeo y espera lo que pide la API
(`next_poll_after_seconds` o `Retry-After`), sin pasar nunca de `timeout`. Espera a
`Validation.is_settled`: un `not_found` con un ciclo de reintentos en marcha sigue cambiando. Cuando se
acaba el tiempo, levanta `VerikoTimeoutError`, que también es un `TimeoutError` y trae `last`.

Sin `enqueue()`, la API responde `202` cuando Banxico tarda más de 45 segundos; recoge el veredicto con
`get_validation()`.

### Referencia propia, duplicado y conflicto de cuenta

`client_ref` (de 1 a 64 caracteres) vuelve en `Validation.client_ref` y en los webhooks.
`Validation.duplicate_of` apunta a una validación `valid` anterior de la misma transferencia, y
`Validation.account_conflict` avisa que el CEP nombra otra cuenta. Ninguna de las dos cambia el
veredicto.

### Varias cuentas candidatas

Cuando no se sabe qué cuenta recibió la transferencia, envía `candidate_accounts` (de 2 hasta el límite
que fije la API) en lugar de `beneficiary_account`. Una coincidencia llena `Validation.candidate_match`.
Enviar las dos levanta `account_and_candidates_mutually_exclusive`.

### Conserva el comprobante

Las validaciones por OCR conservan la imagen por omisión. `retain_image=False` la borra cuando la
validación ya no la necesita, y `Validation.image_retained` pasa a `False`. `validations.image(id)` la
descarga.

### Lista y recorre el historial

```python
page = client.validations.list(per_page=50, status="valid", from_="2025-03-01")
for validation in client.validations.iter(status="valid"):
    ...
```

Una `Page` se comporta como una secuencia y trae `page`, `per_page`, `total` y `total_pages`.
`page.next_page()` pide la página siguiente con los mismos filtros y devuelve `None` en la última. Sin
`per_page`, una página trae 10 validaciones, 50 entregas o 25 filas de importación. Los iteradores
(`iter()`, `iter_deliveries()`, `iter_import_preview()`) piden el máximo que da cada endpoint (50, 100 y
100), salvo que `per_page` diga otra cosa, y piden la página siguiente sólo cuando hace falta.

`list()`, `iter()`, `stats()` y `export()` reciben los mismos filtros tipados, así que un filtro mal
escrito levanta `TypeError`. `type` es `"direct"` u `"ocr"` y `retry_state` es `"pending"`,
`"resolved"`, `"exhausted"` o `"cancelled"`.

Los identificadores se revisan contra el formato del spec antes de enviar la petición: un UUID para
validaciones, endpoints y exportaciones de CEP, un entero para beneficiarios e importaciones.

## Descarga el CEP

```python
cep = client.get_cep(validation.id, format="pdf")   # o "xml"
cep.write_to(cep.filename)
```

El CEP de una validación depurada responde `410` con `validation_purged` (`GoneError`).

## Revisa el pago después

`validations.recheck(id)` consulta de nuevo el estado del pago y devuelve un `RecheckResult`. Una
devolución que Banxico reporta más tarde convierte la validación en `returned`, con
`Validation.payment_status`.

## Borra y depura

`validations.delete(id)` quita una validación del historial. `prepare_purge(id)` y
`execute_purge(id, confirmation_token=...)` borran su contenido de forma definitiva y dejan una lápida.

## Registra un webhook

```python
endpoint = client.webhooks.create(
    url="https://example.com/hooks/veriko",
    events=["validation.completed", "validation.returned"],
)
secret = endpoint.secret   # la API lo entrega una sola vez
```

`WEBHOOK_EVENTS` enumera los 12 eventos a los que se suscribe un endpoint, y `WebhookEvent` indica su
tipo con `is_validation_event`, `is_billing_event` e `is_test`. `webhooks.test(id)` envía un evento de
prueba, `webhooks.deliveries()` e `iter_deliveries()` leen el historial, y `export_deliveries()` lo
descarga.

### Rota el secreto

```python
endpoint = client.webhooks.regenerate_secret(webhook_id)                  # el secreto anterior vale 24 h
endpoint = client.webhooks.regenerate_secret(webhook_id, immediate=True)  # el secreto anterior deja de valer ya
print(endpoint.previous_secret_expires_at)
```

## Catálogo y estado de Banxico

`catalog.banks()`, `catalog.spei_calendar(year)`, `catalog.bin_lookup(bin)`, `catalog.banxico_status()`
y `catalog.banxico_timeseries()`.

## Lecturas condicionales

`validations.get()`, `catalog.banks()` y `catalog.spei_calendar()` aceptan `if_none_match`. Cuando nada
cambió, la API responde `304` y el SDK levanta `NotModifiedError`, un `APIError` con `status=304` y el
`etag`.

## Verifica la firma de un webhook

Verifica el **cuerpo crudo**, antes de interpretarlo. Leerlo en un diccionario y volver a escribirlo
cambia los bytes.

```python
from veriko import SignatureVerificationError, parse_webhook

try:
    event = parse_webhook(request.get_data(), request.headers, [current_secret, previous_secret])
except SignatureVerificationError:
    return "", 401

if event.event == "validation.completed":
    ...
```

`parse_webhook()` con los encabezados de la petición comprueba `X-Webhook-Signature-Timestamped`, recurre
a `X-Webhook-Signature-Old` (el secreto anterior, durante 24 horas después de una rotación) y rechaza una
`t` fuera de una ventana de 5 minutos (`tolerance=300`). Pasa un secreto o varios. Con sólo el valor de
`X-Webhook-Signature`, comprueba el cuerpo sin ventana de tiempo.

| encabezado | contenido |
| --- | --- |
| `X-Webhook-Signature` | `sha256=<hex>` sobre el cuerpo |
| `X-Webhook-Signature-Timestamped` | `t=<segundos>,v1=<hex>` sobre `<t>.<cuerpo>` |
| `X-Webhook-Signature-Old` | el mismo formato, firmado con el secreto anterior |
| `X-Veriko-Event` | el nombre del evento |
| `X-Veriko-Delivery-Id` | el identificador de la entrega, en `WebhookEvent.delivery_id` |

`verify_webhook()`, `verify_webhook_timestamped()` y `verify_delivery()` devuelven `True` o `False` y
nunca levantan; comparan en tiempo constante y aceptan bytes o texto.

Un evento `validation.returned` trae `WebhookEvent.payment_status`. `validation.completed` trae
`banxico_confirmed` con el monto que Banxico confirmó.

## Reintentos

El SDK reintenta lo que es seguro repetir, con espera exponencial y variación aleatoria:

| situación | se reintenta |
| --- | --- |
| `429` (`rate_limit_exceeded`) | siempre |
| `429` con `quota_exceeded` | nunca: la cuota se renueva en el siguiente periodo |
| `408`, `5xx` y errores de red | `GET`, `HEAD`, `OPTIONS`, `PUT`, `DELETE`, y un `POST` con `Idempotency-Key` |
| Fallas de TLS y de certificado | nunca |

Se respeta `Retry-After`. Cuando excede `RetryConfig.max_retry_after_seconds` (60), el error se levanta
de inmediato con `retry_after` puesto. Un `DELETE` que responde `404` después de un reintento cuenta como
hecho.

```python
from veriko import RetryConfig, Veriko

client = Veriko(retry=RetryConfig(max_retries=4, backoff_base_seconds=1.0, max_retry_after_seconds=30))
```

## Idempotencia

Las seis operaciones que aceptan `Idempotency-Key` siempre mandan una: la que pasas en
`idempotency_key`, o una clave generada que cada reintento de esa llamada reutiliza. Pasa tu propia
clave para proteger un reenvío posterior:

```python
client.validate_transfer(..., idempotency_key="pedido-1042")
```

Una clave mide de 1 a 255 caracteres de `[A-Za-z0-9_-]`. Un `409` con `idempotency_key_in_progress`
espera y repite con la misma clave, hasta `RetryConfig.idempotency_wait_seconds` (30), y después levanta
`IdempotencyKeyInProgressError`. `response_info.idempotent_replayed` indica que la respuesta es una
repetición.

## Metadatos de la respuesta

Cada resultado trae `response_info`:

```python
validation = client.get_validation(validation_id)
info = validation.response_info
info.rate_limit.remaining, info.request_id, info.etag, info.idempotent_replayed
```

`info.deprecation` e `info.sunset` guardan los encabezados `Deprecation` y `Sunset`. La primera
respuesta con un encabezado `Deprecation` emite un `DeprecationWarning` por proceso. Los métodos que
devuelven una `list` o un `dict` devuelven `ApiList` y `ApiDict`, que se comportan igual y traen
`response_info`.

Los métodos que devuelven los atributos de un recurso (`usage`, `account`, `insights`, `finance`,
`billing`, `plans`, `dashboard` y el estado del catálogo y la consulta de BIN) devuelven un diccionario
tipado de `veriko.responses`, como `UsageHistory` o `CepExport`. Sigue siendo el diccionario:
`result["months"]`, `result.get("months")`, `"months" in result`, la iteración y `==` contra un
diccionario funcionan, y también `json.dumps(result)`. Los campos del spec además son atributos,
`result.months`, y un campo que la respuesta no trae se lee como `None`. `result.id`, `result.links`,
`result.meta` y `result.raw` guardan lo que la respuesta trae fuera de los atributos.

## Errores

Todo lo que levanta el SDK hereda de `VerikoError`.

| clase | cuándo |
| --- | --- |
| `ConfigurationError` | falta la clave de API o está mal formada, o un argumento no se puede usar |
| `ConnectionError` (`VerikoConnectionError`) | no hubo respuesta tras los reintentos; `cause`, `timed_out` |
| `AuthenticationError`, `ForbiddenError`, `NotFoundError` | `401`, `403`, `404` |
| `NotModifiedError` | `304` en una lectura condicional |
| `ConflictError`, `IdempotencyKeyInProgressError` | `409` |
| `GoneError` | `410`: `validation_purged`, `image_not_retained` |
| `InvalidRequestError`, `PayloadTooLargeError` | `400`, `422`, `413` |
| `RateLimitError`, `QuotaExceededError` | `429`; `retry_after`, `limit`, `remaining`, `reset` |
| `ServerError` | `5xx` |
| `VerikoTimeoutError` | una espera se quedó sin tiempo |

Ramifica según `code`, nunca según `detail`, que se traduce y puede cambiar de redacción. Un `422` trae
una entrada por cada campo inválido:

```python
try:
    client.validate_transfer(...)
except InvalidRequestError as error:
    for entry in error.entries:
        print(entry.code, entry.pointer, entry.hint, entry.docs_url)
    print(error.request_id)
```

También están disponibles `error.codes`, `error.field_errors`, `error.has_code("invalid_date")` y
`error.local`.

## Beneficiarios

`beneficiaries.create()`, `list()`, `update()`, `delete()`, `lookup(account)` y
`validate_account(account, type=None)`, que revisa la estructura de una cuenta CLABE, tarjeta o número
de celular sin consumir cuota.

### Importación masiva

```python
job = client.beneficiaries.import_start("beneficiarios.csv")   # csv, xls, xlsx, txt, json o pdf, hasta 20 MB
job = client.beneficiaries.import_wait(job.id)
for row in client.beneficiaries.iter_import_preview(job.id, buckets=["correctable", "fatal"]):
    ...
job = client.beneficiaries.import_commit(job.id)
```

`import_edit_row()`, `import_remove_row()` e `import_cancel()` corrigen o descartan filas. El endpoint de
estado no tiene `ETag`, así que `import_wait()` descarga el cuerpo completo en cada sondeo.

## Uso

`usage.summary()`, `history()`, `breakdown()`, `limits()`, `heatmap()`, `api_usage()` y `export()`.

## Finanzas y exportaciones de CEP

```python
export = client.finance.create_cep_export(from_="2026-01-01", to="2026-01-31")
export = client.finance.wait_for_cep_export(export.id)
if export.is_ready:
    client.finance.download_cep_export(export.id).write_to("ceps.zip")
```

Una exportación termina como `ready`, `failed` o `expired` (`CEP_EXPORT_TERMINAL_STATUSES`). Una
`failed` trae su motivo en `error_code` (`CEP_EXPORT_ERROR_CODES`).

Un `Document` descargado conserva los `headers` de la respuesta. `finance_folio` y `finance_items` leen
`X-Finance-Folio` y `X-Finance-Items`, e `iter_bytes()` entrega el contenido en trozos.

## Cliente asíncrono

```python
import asyncio

from veriko import AsyncVeriko


async def main():
    async with AsyncVeriko() as client:
        validation = await client.validate_transfer(
            amount=15000.50, tracking_key="MXBA20250315001234", beneficiary_account="012180004412345678"
        )
        async for item in client.validations.iter(status="valid"):
            ...


asyncio.run(main())
```

`AsyncVeriko` recibe los mismos argumentos y expone las mismas familias que `Veriko`; cada método es una
corrutina y los `iter_*` son iteradores asíncronos. El SDK no tiene dependencias en tiempo de ejecución,
así que cada llamada ejecuta el cliente síncrono en un hilo de trabajo. `scripts/generate_async.py`
regenera el espejo.

## Nombres de versiones anteriores

Los nombres de campo de versiones anteriores siguen funcionando para las integraciones que ya existen y
emiten un `DeprecationWarning`; no forman parte de la API tipada.

## Desarrollo

```bash
pip install -e ".[dev]"
ruff check . && ruff format --check . && mypy
pytest
```

Las pruebas corren contra respuestas grabadas que sirve un servidor HTTP local; ninguna llama a la API.
`spec/openapi.yaml` es una copia del spec público, y las pruebas del spec comparan el SDK contra ella.

## Enlaces

- Documentación: https://docs.veriko.mx/es/
- Errores: https://docs.veriko.mx/es/concepts/errors
- Webhooks: https://docs.veriko.mx/es/concepts/webhooks-architecture
- Registro de cambios: [CHANGELOG.md](CHANGELOG.md)

## Licencia

MIT. Consulta [LICENSE](LICENSE).

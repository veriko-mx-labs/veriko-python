# veriko · SDK de Python para la API de Veriko

Cliente oficial de [Veriko](https://veriko.mx) para Python. Valida transferencias SPEI mexicanas
contra el CEP de Banco de México, descarga el comprobante oficial y verifica la firma de los
webhooks.

Sin dependencias de runtime. Python 3.9 o superior.

```python
from veriko import Veriko

client = Veriko()  # lee VERIKO_API_KEY del entorno

validation = client.validate_transfer(
    fecha="2025-03-15",
    monto=15000.50,
    clave_rastreo="MXBA20250315001234",
    cuenta_beneficiaria="012180004412345678",
)

print(validation.status)  # 'valid'
```

## Qué es el CEP

El **Comprobante Electrónico de Pago (CEP)** es el documento que expide el **Banco de México
(Banxico)** por cada transferencia que pasa por el SPEI, el sistema de pagos interbancarios
mexicano. Contiene el sello digital y la cadena original de la institución receptora, de modo que
acredita que una transferencia ocurrió y por qué importe.

El CEP no tiene efectos fiscales: no sustituye a una factura.

Su consulta manual se hace en el portal de Banxico, un comprobante a la vez.

## Qué hace la API

La API consulta el CEP y devuelve un veredicto en el campo `status`:

| veredicto | significado |
| --- | --- |
| `valid` | Banxico devolvió un CEP que coincide con los datos enviados |
| `not_found` | Banxico no tiene un CEP con esos datos. Puede ser temporal: un CEP tarda en publicarse |
| `cep_unavailable` | Banxico no pudo responder. No dice nada sobre la transferencia |
| `returned` | El pago se liquidó y la institución beneficiaria lo devolvió después |
| `invalid` | Los datos enviados no forman una consulta válida |
| `error` | Fallo durante el procesamiento; el motivo viaja en `error_code` |

Con veredicto `valid`, el comprobante queda disponible en XML y en PDF. Un `valid` es el veredicto
del momento de la consulta: Banxico puede reportar la devolución hasta 72 horas después, y la
validación pasa a `returned`.

## Las familias de operaciones

El cliente agrupa las 69 operaciones M2M de la API en once familias:

| familia | qué cubre |
| --- | --- |
| `client.validations` | Validar por campos o por imagen, consultar, listar, exportar, revisar el estado de pago, borrar, la política de reintentos y la descarga del comprobante |
| `client.webhooks` | Registrar endpoints, rotar su secreto, enviar un evento de prueba y leer el historial de entregas |
| `client.catalog` | Catálogo de bancos SPEI, banco emisor de una tarjeta y estado del servicio de Banxico |
| `client.beneficiaries` | Cuentas beneficiarias guardadas y la importación masiva, como ciclo completo |
| `client.usage` | Cuota de validaciones, límites de tasa y registro de actividad de la API |
| `client.account` | Perfil y política de reintentos predeterminada de la cuenta |
| `client.dashboard` | Resumen del panel |
| `client.plans` | Catálogo y comparación de planes públicos, sin clave de API |
| `client.insights` | Resumen, tendencias, bancos y beneficiarios principales |
| `client.finance` | Resumen, estado de cuenta, vistas previas y descargas financieras |
| `client.billing` | Suscripción activa |

Las tres operaciones de uso más frecuente están también en la raíz del cliente,
como atajo: `validate_transfer()`, `get_validation()` y `get_cep()`.

## Instalación

```bash
pip install veriko
```

Mientras la versión sea `0.x`, una versión menor puede ajustar la superficie pública del SDK.
La versión `1.0.0` se reservará para una integración estable en producción.

## Autenticación

La clave de API se obtiene en el panel ([app.veriko.mx](https://app.veriko.mx)) y empieza con
`veriko_`. El cliente la toma de la variable de entorno `VERIKO_API_KEY`:

```bash
export VERIKO_API_KEY=veriko_tu_clave_aqui
```

Sólo `client.plans.list_public()` y `client.plans.get_public_plan_comparison()` se pueden usar sin
clave y no envían `Authorization`. El resto conserva la autenticación de la API: una petición sin
clave falla localmente antes de salir a la red.

El argumento `api_key` la recibe directamente cuando la gestiona otro mecanismo:

```python
client = Veriko(api_key="veriko_tu_clave_aqui")
```

## Validar una transferencia

La operación exige la fecha de envío, el importe, la cuenta beneficiaria y **la clave de rastreo
o la referencia numérica**. Enviar las dos precisa la búsqueda. El banco emisor y el receptor son
opcionales y mejoran la identificación. La API rechaza con `422` (`preflight_failed`) una petición
sin cuenta: no la busca entre los beneficiarios guardados. Cuando no se sabe cuál fue la cuenta,
`cuentas_candidatas` sustituye a `cuenta_beneficiaria`.

```python
from veriko import Veriko

client = Veriko()

validation = client.validate_transfer(
    fecha="2025-03-15",  # YYYY-MM-DD, la fecha de envío
    monto=15000.50,  # en pesos, con centavos
    clave_rastreo="MXBA20250315001234",
    referencia_numerica="1234567",
    emisor="BANCO NACIONAL DE MEXICO",
    receptor="BBVA MEXICO",
    cuenta_beneficiaria="012180004412345678",  # CLABE, tarjeta o celular DiMo
)

if validation.status == "valid":
    print("Pago confirmado en", validation.processing_time_ms, "ms")
elif validation.status == "not_found":
    print("Banxico no encuentra el pago todavía")
```

Los argumentos conservan el nombre que viaja en el JSON de la API.

Cada llamada consume cuota del plan, y se descuenta al aceptar la petición.

### Desde el comprobante

```python
validation = client.validations.validate_ocr(
    image="comprobante.png",  # ruta, bytes o un objeto Path
    cuenta_beneficiaria="012180004412345678",  # obligatoria para celular DiMo
)
```

El SDK lee el archivo y lo codifica en base64. `image_url` sirve para un
comprobante ya publicado en HTTPS. Formatos: JPEG, PNG, WebP o PDF de 1 a 3 páginas.
`cuentas_candidatas` reemplaza a `cuenta_beneficiaria` cuando la imagen no muestra la cuenta.

### Sin esperar al veredicto

Para volumen, la API acepta la petición y responde con el identificador:

```python
queued = client.validations.enqueue(
    fecha="2025-03-15",
    monto=15000.50,
    clave_rastreo="MXBA20250315001234",
)

validation = client.validations.wait_for(queued.id)  # sondea hasta el veredicto
```

`wait_for()` manda el `ETag` de la respuesta anterior en cada vuelta, así que un
sondeo que no encuentra cambios no descarga otra vez el mismo cuerpo. Espera a
que el veredicto quede firme, no sólo a que el estado sea terminal: una
validación con reintentos en marcha llega a `not_found` y sigue cambiando
después. Esa distinción es `Validation.is_settled`.

La alternativa a sondear es suscribirse al webhook `validation.completed`.

### Referencia propia

`client_ref` es una referencia propia de 1 a 64 caracteres, sin saltos de línea ni emoji. Sirve
para relacionar la validación con un pedido propio. No debe contener datos personales.

```python
validation = client.validate_transfer(
    fecha="2025-03-15",
    monto=15000.50,
    clave_rastreo="MXBA20250315001234",
    cuenta_beneficiaria="012180004412345678",
    client_ref="orden-4812",
)
print(validation.client_ref)  # orden-4812

pagina = client.validations.list(client_ref="orden-4812")
```

`validate_ocr()`, `enqueue()` y `enqueue_ocr()` también la aceptan. Vuelve en
`Validation.client_ref`, en `ValidationSummary.client_ref` y en los webhooks de validación, y
`list()`, `stats()` y `export()` la usan como filtro de coincidencia exacta. En un webhook,
`parse_webhook()` la entrega en `evento.validation.client_ref`. La API rechaza con `422`
(`invalid_client_ref`) una referencia que no cumple las reglas.

### Duplicado y conflicto de cuenta

`Validation.duplicate_of` trae el `id` y el `created_at` de una validación previa `valid`, de la
misma cuenta y con la misma clave de rastreo. `Validation.account_conflict` trae los últimos 4
dígitos de la cuenta enviada (`sent_last4`) y de la que muestra la imagen (`read_last4`) en una
validación por OCR. Ninguno cambia el veredicto, y los dos valen `None` cuando la API no los
informa.

### Varias cuentas candidatas

`cuentas_candidatas` lleva de 2 a 3 cuentas en una sola validación, con una sola unidad de cuota.

```python
validation = client.validate_transfer(
    fecha="2025-03-15",
    monto=15000.50,
    clave_rastreo="MXBA20250315001234",
    cuentas_candidatas=["012180004412345678", "002010077777777771"],
)

if validation.candidate_match is not None:
    print(validation.candidate_match.index)  # posición en la lista enviada, desde 0
    print(validation.candidate_match.account_last4)  # 7771
```

`validate_ocr()`, `enqueue()` y `enqueue_ocr()` también la aceptan. La API consulta las cuentas en el
orden enviado y adopta la primera que coincide con la transferencia. `Validation.candidate_match`
trae su posición y sus últimos 4 dígitos, y la cuenta completa queda en
`normalized_data.cuenta_beneficiaria`. El veredicto no cambia.

Se envía una de las dos formas, no las dos: con `cuenta_beneficiaria` y `cuentas_candidatas` juntas,
o sin ninguna, el SDK lanza `InvalidRequestError` (`cuenta_y_candidatas_excluyentes` o
`cuenta_required`) y no llama a la API. Una lista que no cumple se rechaza con `422`
(`cuentas_candidatas_invalidas`).

### Conservar el comprobante

La plataforma conserva el archivo de una validación por imagen, y `client.validations.image(id)` lo
descarga. Con `retain_image=False` lo borra en cuanto la validación llega a un estado terminal del
que ya no lo necesita. El veredicto y los datos extraídos se conservan.

```python
validation = client.validations.validate_ocr(image="comprobante.png", retain_image=False)
print(validation.image_retained)  # False
```

`enqueue_ocr()` también acepta `retain_image`. `image()` responde `410` con `image_not_retained`
cuando el archivo no se conserva, y la API rechaza con `422` (`invalid_retain_image`) un valor que
no es booleano.

### Listar y recorrer el historial

```python
pagina = client.validations.list(status="valid", per_page=50)
print(pagina.total, "validaciones,", pagina.total_pages, "páginas")

for validation in client.validations.iter(from_="2025-03-01", to="2025-03-31"):
    print(validation.id, validation.status)
```

`iter()` pide la página siguiente sólo cuando la anterior se agota. El filtro de
fecha es `from_` con guion bajo, porque `from` es palabra reservada de Python;
viaja como `from`.

`status` acepta un estado o una lista. `with_deleted=True` devuelve sólo las validaciones retiradas
y `False` sólo las activas, y `playground=True` limita a las del banco de pruebas.

Un listado trae menos campos que `get()`: no incluye los datos enviados, el resultado de Banxico ni
los enlaces al comprobante, y por eso `ValidationSummary` no tiene `has_cep`.

El historial se exporta entero con `client.validations.export(format="csv")`, que
admite también `xlsx`.

## Descargar el CEP

```python
cep = client.get_cep(validation.id, format="pdf")  # o format="xml"
cep.write_to(cep.filename)  # CEP-<id>.pdf
```

`get_cep()` devuelve el archivo: el XML que emitió Banxico, con su sello digital y su cadena
original, o el PDF equivalente. `Validation.has_cep` indica si existe antes de pedirlo; cuando no,
la API responde `404` con `cep_not_available` y el SDK lanza `NotFoundError`.

## Revisar el pago después

`client.validations.recheck(id)` vuelve a consultar a Banxico el estado de pago de una validación
`valid` creada hace 72 horas como máximo, sin consumir cuota.

```python
resultado = client.validations.recheck(validation.id)

print(resultado.changed)  # True si pasó de valid a returned
print(resultado.previous_status)  # valid
print(resultado.validation.status)  # returned
```

Si Banxico reporta `devuelto` o `en_proceso_devolucion`, la validación pasa a `returned`, conserva su
CEP y la API emite el webhook `validation.returned`. El estado del pago queda en
`Validation.payment_status`. Una validación que ya estaba en `returned` responde su estado actual sin
consultar a Banxico, y `checked_at` vale `None`.

Cada validación admite una consulta cada 10 minutos: antes, el SDK lanza `RateLimitError` con
`recheck_rate_limited`, y `retry_after` trae los segundos que faltan. Una validación en otro estado,
o con más de 72 horas, lanza `InvalidRequestError` con `recheck_not_eligible` o
`recheck_window_expired`. Si Banxico no entrega un estado legible, lanza `ServerError` con
`recheck_unavailable`: la validación conserva su intervalo y se puede reintentar de inmediato.

## Borrar una validación

`client.validations.delete(id)` retira una validación del historial, pero el registro, el CEP, los
datos extraídos y el archivo del comprobante siguen existiendo. El borrado definitivo los elimina en
dos pasos: el primero describe lo que se borraría y emite un token de un solo uso, y el segundo lo
confirma.

```python
preparacion = client.validations.prepare_purge(validation.id)

print(preparacion.will_delete)  # lo que se borraría
print(preparacion.expires_in)  # segundos de vigencia del token

resultado = client.validations.execute_purge(
    validation.id, confirmation_token=preparacion.confirmation_token
)
print(resultado.purged_at, resultado.file_removal)
```

**El borrado es irreversible y no devuelve cuota.** La validación queda como una lápida: conserva el
veredicto, las fechas y el monto, y trae `Validation.purged_at`. Después, el CEP, el comprobante, los
reintentos y `recheck()` responden `410` con `validation_purged`. `file_removal` vale `pending`
cuando algún archivo no se pudo borrar en ese momento y el barrido diario lo termina.

Una validación en curso lanza `ConflictError` con `purge_validation_in_progress`. Un token vencido,
mal formado o de otra validación lanza `InvalidRequestError`, y uno ya usado, `ConflictError` con
`confirmation_token_already_used`. Si la respuesta de `execute_purge()` no llega,
`client.get_validation(id)` dice si el borrado se completó.

## Registrar un webhook

```python
endpoint = client.webhooks.create(
    url="https://miapp.example.com/hooks/pagos",
    events=["validation.completed"],
)

print(endpoint.secret)  # 64 hex
```

El secreto de firma viaja **una sola vez**, en esta respuesta. Si se pierde,
`client.webhooks.regenerate_secret(endpoint.id)` devuelve uno nuevo, y el
anterior deja de valer.

`client.webhooks.test(endpoint.id)` manda un evento de prueba y dice si el
receptor lo aceptó. Las entregas de prueba no cuentan para el contador de fallos
consecutivos que apaga un endpoint a los tres seguidos; cuando eso pasa, el
estado queda en `auto_disabled` y se reactiva con
`client.webhooks.update(id, status="active")`.

El historial de intentos está en `client.webhooks.deliveries()`, con o sin
identificador de endpoint. Los filtros `status` y `event_type` sólo existen en el listado global:
con un endpoint y un filtro, el SDK consulta ese listado con `endpoint_id`.

`create()` y `update()` aceptan `description`, una etiqueta libre que `update(id, description=None)`
borra.

## Catálogo y estado de Banxico

```python
bancos = client.catalog.banks()  # instituciones SPEI con su código
tarjeta = client.catalog.bin_lookup("455632")  # banco emisor de una tarjeta
estado = client.catalog.banxico_status()

print(estado["status"])  # 'operational'
```

`banxico_status()` sirve para distinguir un `cep_unavailable` propio de la transferencia de una caída
del servicio, y `banxico_timeseries()` devuelve la serie de latencia o de veredictos por ventana. Las
consultas que devuelven un recurso sin modelo propio (`stats()`, `bin_lookup()`, `banxico_status()`,
`banxico_timeseries()`) entregan sus atributos como diccionario.

## Lecturas condicionales

`client.validations.get()` y `client.catalog.banks()` admiten `if_none_match`. Cada lectura trae su
`ETag` (`Validation.etag`, `BankList.etag`), y cuando nada cambió la API responde `304`, que el SDK
levanta como `APIError` con `status=304`.

## Verificar la firma de un webhook

Cada entrega llega firmada con HMAC-SHA256 del cuerpo, usando el secreto que devolvió
`POST /v1/webhooks` al registrar el endpoint:

```
X-Webhook-Signature: sha256=<hex>
```

Lo que se firma es el cuerpo **tal como llegó**. Interpretar el JSON y volver a serializarlo
cambia los bytes y la firma deja de cuadrar.

```python
import os
from flask import Flask, request
from veriko import parse_webhook, SignatureVerificationError

app = Flask(__name__)
SECRET = os.environ["VERIKO_WEBHOOK_SECRET"]

@app.post("/hooks/veriko")
def recibir():
    try:
        evento = parse_webhook(
            request.get_data(),  # el cuerpo crudo
            request.headers.get("X-Webhook-Signature"),
            SECRET,
        )
    except SignatureVerificationError:
        return "", 400

    if evento.event == "validation.completed" and evento.validation is not None:
        print(evento.validation.id, evento.validation.status)

    return "", 200  # responder 2xx primero, procesar después
```

`verify_webhook(payload, signature, secret)` hace la misma comprobación y devuelve `True` o
`False`, para decidir el código de respuesta aparte. La comparación es en tiempo constante.

El receptor completo está en [`examples/flask_webhook.py`](examples/flask_webhook.py).

### Las cinco cabeceras de una entrega

| cabecera | contenido |
| --- | --- |
| `X-Webhook-Signature` | `sha256=` seguido del HMAC en hexadecimal |
| `X-Webhook-Signature-Timestamped` | `t=<segundos>,v1=<hex>`: firma de `<t>.<cuerpo>` con la hora del intento |
| `X-Veriko-Event` | Tipo de evento, por ejemplo `validation.completed` |
| `X-Veriko-Delivery-Id` | Identificador de la entrega, estable entre reintentos |
| `X-Veriko-Timestamp` | Momento del envío, ISO 8601 con sufijo `Z` |

El `Delivery-Id` es el valor que permite descartar entregas repetidas.

### Firma con marca de tiempo

`X-Webhook-Signature-Timestamped` firma la hora del intento junto con el cuerpo, de modo que el
receptor puede descartar una entrega vieja que alguien vuelva a enviar. `X-Webhook-Signature` no
cambia.

```python
from veriko import verify_webhook_timestamped

if not verify_webhook_timestamped(
    request.get_data(),  # el cuerpo crudo
    request.headers.get("X-Webhook-Signature-Timestamped"),
    SECRET,
):
    return "", 400
```

`verify_webhook_timestamped(payload, header, secret, *, tolerance=300, now=None)` devuelve `True` o
`False`. Calcula el HMAC-SHA256 de `<t>.<cuerpo>`, lo compara en tiempo constante con cada `v1` de
la cabecera y rechaza la entrega si `t` se aleja del reloj del receptor más de `tolerance`
segundos, 300 por omisión. `now` fija la hora actual en segundos Unix, para las pruebas.
`timestamped_signature_from_headers(headers)` encuentra la cabecera en cualquiera de sus grafías.

La ventana se aplica a `t`, la hora del intento, y no al campo `timestamp` del cuerpo, que es la hora
del evento. Un reintento llega hasta unas 8,6 horas después del evento, pero lleva una `t` recién
generada. Medir la ventana contra el `timestamp` del cuerpo rechazaría reintentos legítimos. La
ventana no sustituye a la deduplicación: guarda el `Delivery-Id` al menos 24 horas.

### Una devolución posterior

`validation.returned` avisa cuando una validación que había salido `valid` pasa después a
`returned`. El endpoint recibe sólo los eventos a los que se suscribió, así que hay que añadirlo
además de `validation.completed`:

```python
endpoint = client.webhooks.create(
    url="https://miapp.example.com/hooks/pagos",
    events=["validation.completed", "validation.returned"],
)
```

El cuerpo es el de `validation.completed` más `attributes.payment_status`, que `parse_webhook()`
entrega en `evento.payment_status`:

```python
if evento.event == "validation.returned" and evento.payment_status is not None:
    print(evento.payment_status.code)  # devuelto o en_proceso_devolucion
```

`checked_at` es el instante de la consulta. En el resto de los eventos `evento.payment_status` vale
`None`. El evento no se emite al crear la validación: una operación que llega ya devuelta se entrega
con `validation.completed` y `status` `returned`.

### El monto que Banxico confirmó

Cuando Banxico confirmó el pago (`banxico_status` es `valid`, o `returned` con el comprobante ya
descargado), la entrega trae `evento.banxico_confirmed`: el monto, la fecha, la clave de rastreo,
ambos bancos y la cuenta del beneficiario (enmascarada a los últimos 4 dígitos), tal como los
confirmó Banxico. En el resto de los casos vale `None`. Compáralo contra el pedido antes de darlo
por pagado: el monto de una imagen de comprobante puede no ser el mismo.

## Reintentos

Hay dos mecanismos distintos con el mismo nombre.

**Los del cliente** repiten una petición que falló por causas pasajeras. El SDK reintenta los
`5xx`, el `408` y el `429`, y respeta el `Retry-After` de la respuesta cuando lo trae. El resto de
los `4xx` no se reintenta, porque la petición hay que corregirla antes de repetirla. `recheck()` y
`execute_purge()` no se reintentan solas: la primera tiene su propio tope de una consulta cada 10
minutos, y el borrado es irreversible.

```python
client = Veriko(max_retries=3)  # 0 los desactiva; por omisión son 2
```

**Los de la API** siguen consultando a Banxico durante horas, porque un CEP tarda en publicarse, y
avisan por webhook cuando el veredicto cambia.

```python
from veriko import RetryPolicy

validation = client.validate_transfer(
    fecha="2025-03-15",
    monto=15000.50,
    clave_rastreo="MXBA20250315001234",
    retry_policy=RetryPolicy(
        max_retries=3,
        interval_seconds=600,  # de 300 a 86 400
        outcomes=["not_found", "cep_unavailable"],  # qué veredictos reintentar
    ),
)
```

El avance del ciclo se lee en `client.get_validation(id).retry_state`, o se espera al webhook
`validation.retry.resolved`. Los intentos ya hechos están en `client.validations.retry_attempts(id)`.

La política de una validación ya creada se cambia con `client.validations.set_retry_policy(id,
policy)` y el ciclo se detiene con `client.validations.cancel_retries(id)`. Las dos devuelven el
estado del ciclo (`RetryState`), no la validación completa.

## Idempotencia

Reintentar un `POST` sin clave de idempotencia puede duplicar la validación, y su cargo, cuando la
respuesta se perdió pero la petición llegó. Con clave, la repetición devuelve la respuesta original
durante 24 horas.

```python
validation = client.validate_transfer(
    fecha="2025-03-15",
    monto=15000.50,
    clave_rastreo="MXBA20250315001234",
    idempotency_key="pedido-4f3a2b1c",  # el identificador del intento de negocio
)
```

La clave se deriva del intento de negocio (el número de pedido, de lote o de transacción), no se
genera al azar en cada envío: una clave aleatoria por reintento anula la protección.

Sin `idempotency_key`, el SDK genera una por llamada y la repite en sus propios reintentos. Esa
clave no sobrevive al proceso que la generó, así que un reenvío posterior sí se ejecuta dos veces.

## Errores

Todas las excepciones heredan de `VerikoError`. Las de la API traen `code`, que es el contrato
estable, y `detail`, que se traduce y puede reformularse entre versiones.

```python
from veriko import InvalidRequestError, RateLimitError, NotFoundError

try:
    validation = client.validate_transfer(...)
except InvalidRequestError as error:  # 400, 413, 422
    for entrada in error.errors:  # un 422 trae una entrada por campo
        print(entrada["code"], entrada["source"]["pointer"])
except RateLimitError as error:  # 429
    print("esperar", error.retry_after, "segundos")
except NotFoundError:  # 404
    ...
```

| excepción | estado |
| --- | --- |
| `AuthenticationError` | `401` |
| `ForbiddenError` | `403` |
| `NotFoundError` | `404` |
| `ConflictError` | `409` |
| `InvalidRequestError` | `400`, `413`, `422` |
| `RateLimitError` | `429` |
| `ServerError` | `5xx` |
| `APIError` | Cualquier otro estado de la API, por ejemplo `410` |
| `ConnectionError` | Sin respuesta, con los reintentos agotados |
| `SignatureVerificationError` | La firma de un webhook no cuadra |

Cada error de la API trae `request_id`, que identifica la petición en los registros del sistema.

## Beneficiarios

`client.beneficiaries` guarda las cuentas a las que se paga: CLABE, tarjeta o celular DiMo. El tipo
se detecta por longitud y el banco se deriva del número, salvo en un celular, que exige `bank_code`.

```python
beneficiary = client.beneficiaries.create(
    account_number="012180004412345678",
    label="Proveedor ABC",
)

beneficiary = client.beneficiaries.lookup("012180004412345678")  # la cuenta ya guardada
```

La lista se recorre con `client.beneficiaries.list()`, y se exporta con
`client.beneficiaries.export(format="csv")`.

### Importación masiva

La importación es un ciclo, no una sola operación:

```python
plantilla = client.beneficiaries.import_template(format="csv")  # 1. descargar
plantilla.write_to(plantilla.filename)

trabajo = client.beneficiaries.import_start("beneficiarios.csv")  # 2. subir
trabajo = client.beneficiaries.import_wait(trabajo.id)  # 3. esperar la vista previa

for fila in client.beneficiaries.iter_import_preview(trabajo.id):  # 4. revisar
    if fila.status == "correctable":
        client.beneficiaries.import_edit_row(trabajo.id, fila.id, parsed_bank_code="40012")

client.beneficiaries.import_commit(trabajo.id)  # 5. confirmar
```

Nada se persiste hasta `import_commit()`. `import_wait()` espera a `preview_ready` o a un estado
final; el endpoint de estado no expone `ETag`, así que cada vuelta descarga el cuerpo. Una
importación que todavía no se confirmó se cancela con `import_cancel()`.

## Consumo

```python
summary = client.usage.summary()
print(summary.used, "de", summary.limit, "(", summary.tone, ")")

historial = client.usage.history(months=6)
limites = client.usage.limits()
```

`client.usage.export(format="csv")` descarga el registro de actividad.

## Superficie M2M

El SDK cubre exactamente las 69 operaciones del spec público: `security: []` para las públicas y
`ApiKeyAuth` para las autenticadas.

Entre ellas están el perfil y su política de reintentos, el resumen del panel, los dos endpoints
públicos de planes, las cuatro vistas de insights, las siete operaciones financieras y la
suscripción. Por ejemplo:

```python
profile = client.account.my_profile()
trends = client.insights.get_trends(range="30d", metric="latency")
statement = client.finance.get_statement(month="2026-04", format="pdf")
statement.write_to(statement.filename)
```

Los reportes mensual, por contraparte, por banco y contable conservan el valor por omisión de la API:
`format="csv"`. Usa `format="preview"` explícitamente cuando necesites la respuesta JSON para
procesarla en memoria.

## Desarrollo

```bash
pip install -e ".[dev]"
pytest          # las pruebas
ruff check .    # el estilo
mypy            # los tipos
```

Ninguna prueba llama a la API. El arnés levanta un servidor HTTP local que sirve las respuestas
guardadas en [`tests/recordings/`](tests/recordings), recortadas de los ejemplos del spec público.

`spec/openapi.yaml` es copia de [docs.veriko.mx/openapi.yaml](https://docs.veriko.mx/openapi.yaml).
`tests/test_operations.py` contrasta cada operación con ese spec (ruta, parámetros, cabeceras y
cuerpo, y que sea de máquina a máquina), y `tests/test_models_follow_spec.py` comprueba que los
campos de cada modelo existan en él. `tests/test_spec_public.py` vigila que la copia cumpla el
contrato de la API.

La prueba de operaciones compara el conjunto M2M completo, ejerce cada ruta contra el servidor
HTTP local y falla si aparece una operación sin método o un método ajeno al contrato.

## Enlaces

- Documentación de la API: [docs.veriko.mx](https://docs.veriko.mx)
- Spec de OpenAPI: [docs.veriko.mx/openapi.yaml](https://docs.veriko.mx/openapi.yaml)
- Ejemplos en otros lenguajes: [veriko-mx-labs/examples](https://github.com/veriko-mx-labs/examples)
- El CEP, en detalle: [docs.veriko.mx/es/concepts/cep-concept](https://docs.veriko.mx/es/concepts/cep-concept)
- Registro de cambios: [CHANGELOG.md](CHANGELOG.md)

## Licencia

MIT — ver [`LICENSE`](LICENSE).

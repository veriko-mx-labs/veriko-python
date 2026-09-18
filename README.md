# veriko · SDK de Python para la API del CEP

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

La distinción entre `not_found` y `cep_unavailable` es la que importa al integrar: el primero es
una respuesta de Banxico sobre la transferencia, el segundo es la ausencia de respuesta.

Con veredicto `valid`, el comprobante queda disponible en XML y en PDF.

## Las familias de operaciones

El cliente agrupa la API en tres familias:

| familia | qué cubre |
| --- | --- |
| `client.validations` | Validar por campos o por imagen, consultar, listar, exportar, la política de reintentos y la descarga del comprobante |
| `client.webhooks` | Registrar endpoints, rotar su secreto, enviar un evento de prueba y leer el historial de entregas |
| `client.catalog` | Catálogo de bancos SPEI, banco emisor de una tarjeta y estado del servicio de Banxico |

Las tres operaciones de uso más frecuente están también en la raíz del cliente,
como atajo: `validate_transfer()`, `get_validation()` y `get_cep()`.

## Instalación

El paquete todavía no está publicado en PyPI. Mientras tanto se instala desde el repositorio:

```bash
pip install "git+https://github.com/veriko-mx-labs/veriko-python.git"
```

## Autenticación

La clave de API se obtiene en el panel ([app.veriko.mx](https://app.veriko.mx)) y empieza con
`veriko_`. El cliente la toma de la variable de entorno `VERIKO_API_KEY`:

```bash
export VERIKO_API_KEY=veriko_tu_clave_aqui
```

El argumento `api_key` la recibe directamente cuando la gestiona otro mecanismo:

```python
client = Veriko(api_key="veriko_tu_clave_aqui")
```

## Validar una transferencia

La operación exige la fecha de envío, el importe y **la clave de rastreo o la referencia
numérica**. Enviar las dos precisa la búsqueda. El banco emisor, el receptor y la cuenta
beneficiaria son opcionales y mejoran la identificación.

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

### Desde la imagen del comprobante

```python
validation = client.validations.validate_ocr(
    image="comprobante.png",  # ruta, bytes o un objeto Path
    cuenta_beneficiaria="012180004412345678",  # obligatoria para celular DiMo
)
```

El SDK lee el archivo y lo codifica en base64. `image_url` sirve para una imagen
ya publicada en HTTPS. Formatos: JPEG, PNG o WebP.

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

## Registrar un webhook

```python
endpoint = client.webhooks.create(
    url="https://miapp.example.com/hooks/pagos",
    events=["validation.completed"],
)

print(endpoint.secret)  # whsec_… — guárdalo: la API no lo vuelve a entregar
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
identificador de endpoint.

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

### Las cuatro cabeceras de una entrega

| cabecera | contenido |
| --- | --- |
| `X-Webhook-Signature` | `sha256=` seguido del HMAC en hexadecimal |
| `X-Veriko-Event` | Tipo de evento, por ejemplo `validation.completed` |
| `X-Veriko-Delivery-Id` | Identificador de la entrega, estable entre reintentos |
| `X-Veriko-Timestamp` | Momento del envío, ISO 8601 con sufijo `Z` |

El `Delivery-Id` es el valor que permite descartar entregas repetidas.

## Reintentos

Hay dos mecanismos distintos con el mismo nombre.

**Los del cliente** repiten una petición que falló por causas pasajeras. El SDK reintenta los
`5xx`, el `408` y el `429`, y respeta el `Retry-After` de la respuesta cuando lo trae. El resto de
los `4xx` no se reintenta, porque la petición hay que corregirla antes de repetirla.

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
`validation.retry.resolved`.

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

La clave se deriva del intento de negocio —el número de pedido, de lote o de transacción—, no se
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
| `ConnectionError` | Sin respuesta, con los reintentos agotados |
| `SignatureVerificationError` | La firma de un webhook no cuadra |

Cada error de la API trae `request_id`, que identifica la petición en los registros del sistema.

## Alcance de esta versión

Las familias `validations`, `webhooks` y `catalog` completas: 27 operaciones de la
API, incluidas la validación por imagen, el modo asíncrono con sondeo por `ETag`,
la paginación, las exportaciones y el ciclo de vida de los endpoints de webhook.

Fuera del alcance, y previsto para la siguiente versión: beneficiarios, con su
importación masiva, y las métricas de consumo.

Fuera del alcance a propósito: finanzas, métricas propias, catálogo de planes,
suscripción y el resumen del panel. Son superficie de interfaz, se consumen una
vez o desde la propia aplicación, y cada una arrastra formatos de exportación que
no aportan al SDK. Están en la [referencia](https://docs.veriko.mx) para quien las
necesite con un cliente HTTP.

## Desarrollo

```bash
pip install -e ".[dev]"
pytest          # las pruebas
ruff check .    # el estilo
mypy            # los tipos
```

Ninguna prueba llama a la API. El arnés levanta un servidor HTTP local que sirve las respuestas
guardadas en [`tests/recordings/`](tests/recordings).

## Enlaces

- Documentación de la API: [docs.veriko.mx](https://docs.veriko.mx)
- Spec de OpenAPI: [docs.veriko.mx/openapi.yaml](https://docs.veriko.mx/openapi.yaml)
- Ejemplos en otros lenguajes: [veriko-mx-labs/examples](https://github.com/veriko-mx-labs/examples)
- El CEP, en detalle: [docs.veriko.mx/es/concepts/cep-concept](https://docs.veriko.mx/es/concepts/cep-concept)
- Registro de cambios: [CHANGELOG.md](CHANGELOG.md)

## Licencia

MIT — ver [`LICENSE`](LICENSE).

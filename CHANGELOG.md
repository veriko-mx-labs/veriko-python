# Registro de cambios

Formato de [Keep a Changelog](https://keepachangelog.com/es-ES/1.1.0/);
versiones según [SemVer](https://semver.org/lang/es/).

## [No publicado]

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

[No publicado]: https://github.com/veriko-mx-labs/veriko-python/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/veriko-mx-labs/veriko-python/releases/tag/v0.1.0

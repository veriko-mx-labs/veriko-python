# veriko: Python SDK for the Veriko API

Official [Veriko](https://veriko.mx) client for Python. It validates Mexican SPEI transfers against
the Banco de México CEP, downloads the official receipt and verifies webhook signatures.

No runtime dependencies. Python 3.9 or later.

```python
from veriko import Veriko

client = Veriko()  # reads VERIKO_API_KEY from the environment

validation = client.validate_transfer(
    send_date="2025-03-15",
    amount=15000.50,
    tracking_key="MXBA20250315001234",
    beneficiary_account="012180004412345678",
)

print(validation.status)  # 'valid'
```

## What the CEP is

The **Comprobante Electrónico de Pago (CEP)** is the document the **Banco de México (Banxico)** issues
for each transfer that goes through SPEI, the Mexican interbank payment system. It carries the digital
seal and the original string of the receiving institution, so it proves that a transfer happened and
for what amount.

The CEP has no tax effect: it does not replace an invoice.

Banxico's portal checks one receipt at a time.

## What the API does

The API queries the CEP and returns a verdict in `status`:

| verdict | meaning |
| --- | --- |
| `valid` | Banxico returned a CEP that matches the data sent |
| `not_found` | Banxico has no CEP with that data. It can be temporary: a CEP takes time to publish |
| `cep_unavailable` | Banxico could not answer. It says nothing about the transfer |
| `returned` | The payment settled and the receiving institution returned it afterwards |
| `invalid` | Banxico answered in a way the service does not recognize |
| `error` | Processing failed; the reason travels in `error_code` |

With a `valid` verdict the receipt is available as XML and PDF. A `valid` is the verdict at the moment
of the query: Banxico can report a return up to 72 hours later, and the validation becomes `returned`.

## Families of operations

The client groups the 75 M2M operations of the API into eleven families:

| family | covers |
| --- | --- |
| `client.validations` | Validate by fields or by image, read, list, export, check the payment status, delete, the retry policy and the receipt download |
| `client.webhooks` | Register endpoints, rotate their secret, send a test event and read the delivery history |
| `client.catalog` | SPEI bank catalog, SPEI non-business-day calendar, card issuer lookup and Banxico service status |
| `client.beneficiaries` | Saved beneficiary accounts and the bulk import, as a full cycle |
| `client.usage` | Validation quota, rate limits and the activity log |
| `client.account` | Profile and default retry policy of the account |
| `client.dashboard` | Dashboard summary |
| `client.plans` | Catalog and comparison of public plans, without an API key |
| `client.insights` | Overview, trends, top banks and top beneficiaries |
| `client.finance` | Summaries, statements, previews, downloads and CEP exports |
| `client.billing` | Active subscription |

The three most used operations are also at the root of the client: `validate_transfer()`,
`get_validation()` and `get_cep()`.

## Install

```bash
pip install veriko
```

While the version is `0.x`, a minor version can adjust the public surface of the SDK. Version `1.0.0`
is reserved for a stable production integration.

## Connections and language

The client keeps connections to the API open and reuses them, with `http.client` from the standard
library. `client.close()` closes the idle ones, and `with Veriko() as client:` does it on exit.
`accept_language` is `"es"` or `"en"` and sets the language of `error.detail`; any other value raises
`ConfigurationError`. For a single call, use `client.with_options(accept_language="en", timeout=5)`.

## Identify the client

The SDK sends `User-Agent: veriko-sdk-python/<version> (<implementation> <Python version>; <system>
<architecture>)`. A product that wraps the SDK adds its own text:

```python
client = Veriko(user_agent_prefix="my-product/1.2", user_agent_suffix="integration-x")
```

## Authentication

Create the API key in the [dashboard](https://app.veriko.mx). It starts with `veriko_`.

```python
client = Veriko(api_key="veriko_...")
```

Without `api_key`, the client reads `VERIKO_API_KEY`. `VERIKO_BASE_URL` overrides the API root, and an
`http://` root is accepted only for `localhost`. A key that does not match `veriko_<64 hex>` raises
`ConfigurationError` before any request.

Settings that apply to every call:

| argument | default | effect |
| --- | --- | --- |
| `timeout` | `60.0` | seconds to wait per attempt |
| `max_retries` | `2` | automatic retries besides the first attempt |
| `retry` | none | a `RetryConfig` with the full backoff configuration |
| `accept_language` | account language | `es` or `en`, for `error.detail` |

To change a setting for one call, derive a client:

```python
client.with_options(timeout=5.0, max_retries=0).validations.get(validation_id)
client.with_options(headers={"X-Trace": "t1"}).validations.get(validation_id)
```

`with_options()` returns a new client and leaves the original unchanged.

## Validate a transfer

```python
validation = client.validate_transfer(
    send_date="2025-03-15",
    amount=15000.50,                         # also accepts Decimal
    tracking_key="MXBA20250315001234",
    numeric_reference="1234567",
    sender_bank="BANCO NACIONAL DE MEXICO",
    receiver_bank="BBVA MEXICO",
    beneficiary_account="012180004412345678",
    client_ref="order-1042",
)
```

`amount` is required, plus `beneficiary_account` (or `candidate_accounts`) and `tracking_key` or
`numeric_reference`. `send_date` is the sending date as `YYYY-MM-DD` and is optional: without it the
API tries candidate dates. Each call consumes plan quota.

The client checks the arguments before sending and raises `InvalidRequestError` with `local=True` and
the API's own code (`account_required`, `tracking_key_or_reference_required`,
`account_and_candidates_mutually_exclusive`, `candidate_accounts_invalid`).

### From the receipt

```python
validation = client.validations.validate_ocr(
    image="receipt.png", beneficiary_account="012180004412345678"
)
```

`image` takes bytes, a path, or a string that starts with `data:` (a data URI). `image_url` takes a
receipt already published over HTTPS. `recipient_is_bank=True` marks a payment to the receiving
institution itself.

The SDK refuses before sending a file over 12 MB (`body_too_large`), an `image_url` that is not `https`
(`invalid_url_scheme`) and an uncompressed PDF of more than 3 pages (`pdf_too_many_pages`). The error is
an `InvalidRequestError` with `local=True`.

### Without waiting for the verdict

`validations.enqueue()` and `validations.enqueue_ocr()` accept the same arguments as the methods above,
queue the request and return a `QueuedValidation`. An unknown argument raises `TypeError`. `wait_for()`
collects the verdict:

```python
queued = client.validations.enqueue(
    amount=15000.50, tracking_key="MXBA20250315001234", beneficiary_account="012180004412345678"
)
validation = client.validations.wait_for(queued.id, timeout=120)
```

`wait_for()` sends `If-None-Match` on each poll and waits what the API asks (`next_poll_after_seconds`
or `Retry-After`), never past `timeout`. It waits for `Validation.is_settled`: a `not_found` with a retry
cycle under way keeps changing. When the time runs out it raises `VerikoTimeoutError`, which is also a
`TimeoutError` and carries `last`.

Without `enqueue()`, the API answers `202` when Banxico takes more than 45 seconds; collect the verdict
with `get_validation()`.

### Own reference, duplicate and account conflict

`client_ref` (1 to 64 characters) comes back in `Validation.client_ref` and in the webhooks.
`Validation.duplicate_of` points at an earlier `valid` validation of the same transfer, and
`Validation.account_conflict` reports that the CEP names another account. Neither changes the verdict.

### Several candidate accounts

When it is not known which account received the transfer, send `candidate_accounts` (2 up to the limit
the API sets) instead of `beneficiary_account`. A match fills `Validation.candidate_match`. Sending both
raises `account_and_candidates_mutually_exclusive`.

### Keep the receipt

OCR validations keep the image by default. `retain_image=False` deletes it once the validation no
longer needs it, and `Validation.image_retained` becomes `False`. `validations.image(id)` downloads it.

### List and walk the history

```python
page = client.validations.list(per_page=50, status="valid", from_="2025-03-01")
for validation in client.validations.iter(status="valid"):
    ...
```

A `Page` behaves as a sequence and carries `page`, `per_page`, `total` and `total_pages`.
`page.next_page()` requests the next page with the same filters and returns `None` on the last one.
Without `per_page`, a page holds 10 validations, 50 deliveries or 25 import rows. The iterators
(`iter()`, `iter_deliveries()`, `iter_import_preview()`) ask for the most each endpoint gives (50, 100
and 100) unless `per_page` says otherwise, and request the next page only when needed.

`list()`, `iter()`, `stats()` and `export()` take the same typed filters, so a misspelled filter raises
`TypeError`. `type` is `"direct"` or `"ocr"` and `retry_state` is `"pending"`, `"resolved"`,
`"exhausted"` or `"cancelled"`.

Identifiers are checked against the format of the spec before a request goes out: a UUID for
validations, endpoints and CEP exports, an integer for beneficiaries and imports.

## Download the CEP

```python
cep = client.get_cep(validation.id, format="pdf")   # or "xml"
cep.write_to(cep.filename)
```

The CEP of a purged validation answers `410` with `validation_purged` (`GoneError`).

## Check the payment afterwards

`validations.recheck(id)` queries the payment status again and returns a `RecheckResult`. A return
Banxico reports later turns the validation into `returned`, with `Validation.payment_status`.

## Delete and purge

`validations.delete(id)` removes a validation from the history. `prepare_purge(id)` and
`execute_purge(id, confirmation_token=...)` delete its content for good and leave a tombstone.

## Register a webhook

```python
endpoint = client.webhooks.create(
    url="https://example.com/hooks/veriko",
    events=["validation.completed", "validation.returned"],
)
secret = endpoint.secret   # the API delivers it once
```

`WEBHOOK_EVENTS` lists the 12 events an endpoint subscribes to, and `WebhookEvent` tells its kind with
`is_validation_event`, `is_billing_event` and `is_test`. `webhooks.test(id)` sends a test event, `webhooks.deliveries()` and `iter_deliveries()` read the
history, and `export_deliveries()` downloads it.

### Rotate the secret

```python
endpoint = client.webhooks.regenerate_secret(webhook_id)                  # old secret valid 24 h
endpoint = client.webhooks.regenerate_secret(webhook_id, immediate=True)  # old secret stops now
print(endpoint.previous_secret_expires_at)
```

## Catalog and Banxico status

`catalog.banks()`, `catalog.spei_calendar(year)`, `catalog.bin_lookup(bin)`, `catalog.banxico_status()`
and `catalog.banxico_timeseries()`.

## Conditional reads

`validations.get()`, `catalog.banks()` and `catalog.spei_calendar()` accept `if_none_match`. When
nothing changed the API answers `304` and the SDK raises `NotModifiedError`, an `APIError` with
`status=304` and the `etag`.

## Verify a webhook signature

Verify the **raw body**, before parsing it. Reading it into a dictionary and writing it back changes
the bytes.

```python
from veriko import SignatureVerificationError, parse_webhook

try:
    event = parse_webhook(request.get_data(), request.headers, [current_secret, previous_secret])
except SignatureVerificationError:
    return "", 401

if event.event == "validation.completed":
    ...
```

`parse_webhook()` with the request headers checks `X-Webhook-Signature-Timestamped`, falls back to
`X-Webhook-Signature-Old` (the previous secret, for 24 hours after a rotation) and rejects a `t` outside
a 5-minute window (`tolerance=300`). Pass one secret or several. With only the value of
`X-Webhook-Signature`, it checks the body without a time window.

| header | content |
| --- | --- |
| `X-Webhook-Signature` | `sha256=<hex>` over the body |
| `X-Webhook-Signature-Timestamped` | `t=<seconds>,v1=<hex>` over `<t>.<body>` |
| `X-Webhook-Signature-Old` | the same format, signed with the previous secret |
| `X-Veriko-Event` | the event name |
| `X-Veriko-Delivery-Id` | the delivery identifier, in `WebhookEvent.delivery_id` |

`verify_webhook()`, `verify_webhook_timestamped()` and `verify_delivery()` return `True` or `False` and
never raise; they compare in constant time and accept bytes or text.

A `validation.returned` event carries `WebhookEvent.payment_status`. `validation.completed` carries
`banxico_confirmed` with the amount Banxico confirmed.

## Retries

The SDK retries what is safe to repeat, with exponential backoff and jitter:

| situation | retried |
| --- | --- |
| `429` (`rate_limit_exceeded`) | always |
| `429` with `quota_exceeded` | never: the quota renews on the next period |
| `408`, `5xx` and network errors | `GET`, `HEAD`, `OPTIONS`, `PUT`, `DELETE`, and a `POST` with `Idempotency-Key` |
| TLS and certificate failures | never |

A `Retry-After` is honored. When it exceeds `RetryConfig.max_retry_after_seconds` (60), the error is
raised at once with `retry_after` set. A `DELETE` that answers `404` after a retry counts as done.

```python
from veriko import RetryConfig, Veriko

client = Veriko(retry=RetryConfig(max_retries=4, backoff_base_seconds=1.0, max_retry_after_seconds=30))
```

## Idempotency

The six operations that accept `Idempotency-Key` always send one: the one passed in `idempotency_key`,
or a generated key that every retry of that call reuses. Pass your own key to protect a later resend:

```python
client.validate_transfer(..., idempotency_key="order-1042")
```

A key is 1 to 255 characters of `[A-Za-z0-9_-]`. A `409` with `idempotency_key_in_progress` waits and
repeats with the same key, up to `RetryConfig.idempotency_wait_seconds` (30), then raises
`IdempotencyKeyInProgressError`. `response_info.idempotent_replayed` tells a replayed answer.

## Response metadata

Every result carries `response_info`:

```python
validation = client.get_validation(validation_id)
info = validation.response_info
info.rate_limit.remaining, info.request_id, info.etag, info.idempotent_replayed
```

`info.deprecation` and `info.sunset` hold the `Deprecation` and `Sunset` headers. The first response
with a `Deprecation` header emits one `DeprecationWarning` per process. Methods that return a `list` or
a `dict` return `ApiList` and `ApiDict`, which behave the same and carry `response_info`.

The methods that return a resource's attributes (`usage`, `account`, `insights`, `finance`, `billing`,
`plans`, `dashboard` and the catalog status and BIN lookup) return a typed dictionary from
`veriko.responses`, such as `UsageHistory` or `CepExport`. It is still the dictionary:
`result["months"]`, `result.get("months")`, `"months" in result`, iteration and `==` against a
dictionary work, and so does `json.dumps(result)`. The fields of the spec are also attributes,
`result.months`, and a field the response lacks reads as `None`. `result.id`, `result.links`,
`result.meta` and `result.raw` hold what the response carries outside the attributes.

## Errors

Everything the SDK raises inherits from `VerikoError`.

| class | when |
| --- | --- |
| `ConfigurationError` | missing or malformed API key, unusable argument |
| `ConnectionError` (`VerikoConnectionError`) | no response after the retries; `cause`, `timed_out` |
| `AuthenticationError`, `ForbiddenError`, `NotFoundError` | `401`, `403`, `404` |
| `NotModifiedError` | `304` on a conditional read |
| `ConflictError`, `IdempotencyKeyInProgressError` | `409` |
| `GoneError` | `410`: `validation_purged`, `image_not_retained` |
| `InvalidRequestError`, `PayloadTooLargeError` | `400`, `422`, `413` |
| `RateLimitError`, `QuotaExceededError` | `429`; `retry_after`, `limit`, `remaining`, `reset` |
| `ServerError` | `5xx` |
| `VerikoTimeoutError` | a wait helper ran out of time |

Branch on `code`, never on `detail`, which is translated and can change wording. A `422` carries one
entry per invalid field:

```python
try:
    client.validate_transfer(...)
except InvalidRequestError as error:
    for entry in error.entries:
        print(entry.code, entry.pointer, entry.hint, entry.docs_url)
    print(error.request_id)
```

`error.codes`, `error.field_errors`, `error.has_code("invalid_date")` and `error.local` are also
available.

## Beneficiaries

`beneficiaries.create()`, `list()`, `update()`, `delete()`, `lookup(account)` and
`validate_account(account, type=None)`, which checks the structure of a CLABE, card or mobile number
without consuming quota.

### Bulk import

```python
job = client.beneficiaries.import_start("beneficiaries.csv")   # csv, xls, xlsx, txt, json or pdf, up to 20 MB
job = client.beneficiaries.import_wait(job.id)
for row in client.beneficiaries.iter_import_preview(job.id, buckets=["correctable", "fatal"]):
    ...
job = client.beneficiaries.import_commit(job.id)
```

`import_edit_row()`, `import_remove_row()` and `import_cancel()` fix or drop rows. The status endpoint
has no `ETag`, so `import_wait()` downloads the whole body on each poll.

## Usage

`usage.summary()`, `history()`, `breakdown()`, `limits()`, `heatmap()`, `api_usage()` and `export()`.

## Finance and CEP exports

```python
export = client.finance.create_cep_export(from_="2026-01-01", to="2026-01-31")
export = client.finance.wait_for_cep_export(export.id)
if export.is_ready:
    client.finance.download_cep_export(export.id).write_to("ceps.zip")
```

An export ends as `ready`, `failed` or `expired` (`CEP_EXPORT_TERMINAL_STATUSES`). A `failed` one
carries its reason in `error_code` (`CEP_EXPORT_ERROR_CODES`).

A downloaded `Document` keeps the response `headers`. `finance_folio` and `finance_items` read
`X-Finance-Folio` and `X-Finance-Items`, and `iter_bytes()` yields the content in chunks.

## Asynchronous client

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

`AsyncVeriko` takes the same arguments and exposes the same families as `Veriko`; every method is a
coroutine and `iter_*` are asynchronous iterators. The SDK has no runtime dependencies, so each call runs
the synchronous client in a worker thread. `scripts/generate_async.py` regenerates the mirror.

## Names from earlier versions

Field names from earlier versions keep working for existing integrations and emit a
`DeprecationWarning`; they are not part of the typed API.

## Develop

```bash
pip install -e ".[dev]"
ruff check . && ruff format --check . && mypy
pytest
```

The tests run against recorded responses served by a local HTTP server; none calls the API.
`spec/openapi.yaml` is a copy of the public spec, and the spec tests compare the SDK against it.

## Links

- Documentation: https://docs.veriko.mx/en/
- Errors: https://docs.veriko.mx/en/concepts/errors
- Webhooks: https://docs.veriko.mx/en/concepts/webhooks-architecture
- Changelog: [CHANGELOG.md](CHANGELOG.md)

## License

MIT. See [LICENSE](LICENSE).

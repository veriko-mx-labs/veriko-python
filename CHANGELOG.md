# Changelog

Format based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versions follow [SemVer](https://semver.org/).

## [Unreleased]

No changes yet.

## [0.8.0] - 2026-11-13

### Changed

- Requests use the English names of the API: `send_date`, `amount`, `tracking_key`,
  `numeric_reference`, `sender_bank`, `receiver_bank`, `beneficiary_account`, `candidate_accounts` and
  `recipient_is_bank`. This applies to `validate_transfer()`, `validations.validate()`,
  `validations.validate_ocr()`, `validations.enqueue()` and `validations.enqueue_ocr()`, and the body
  sent always carries the English name. `amount` stays required.
- `ValidationSummary` exposes `numeric_reference`.
- The `User-Agent` changes to `veriko-sdk-python/<version> (<implementation> <Python version>;
  <system> <architecture>)`, for example `veriko-sdk-python/0.8.0 (cpython 3.12.4; linux x86_64)`.
  It no longer includes the repository address, and carries no more machine data than those three.
- The test recordings use the English names.
- Every public docstring, comment, runtime message, test name and example is in English. Field
  names from earlier versions keep working for existing integrations and emit a
  `DeprecationWarning`; they are not part of the typed API.
- A `POST` is retried only when it carries an `Idempotency-Key`. The six operations that accept the
  header (`validations.validate()`, `validations.validate_ocr()`, `validations.set_retry_policy()`,
  `validations.cancel_retries()`, `account.update_my_retry_policy()` and
  `finance.create_cep_export()`) always send one, generated when `idempotency_key` is not passed, and
  reuse it on every retry. A key that does not match `[A-Za-z0-9_-]{1,255}` raises
  `InvalidRequestError` with `code="invalid_idempotency_key"` before the request goes out.
- A `409` with `idempotency_key_in_progress` waits `Retry-After` (1 second when absent) and repeats
  the request with the same key, up to `RetryConfig.idempotency_wait_seconds` (30 by default), then
  raises `IdempotencyKeyInProgressError`.
- A `Retry-After` above `RetryConfig.max_retry_after_seconds` (60 by default) raises the error at once
  instead of sleeping.
- A `DELETE` that answers `404` after a retry returns as done: the first attempt had already deleted it.
- `webhooks.regenerate_secret()` takes `immediate=False`. By default the previous secret keeps signing
  for 24 hours.
- `parse_webhook()` and `verify_webhook_timestamped()` reject a `t` outside a 5-minute window by
  default. `verify_webhook()` compares in constant time and returns `False` on non-ASCII input
  instead of raising.
- `wait_for()` and `import_wait()` wait what the API asks (`meta.next_poll_after_seconds` or
  `Retry-After`) and never past `timeout`. They raise `VerikoTimeoutError`, which is also a
  `TimeoutError`.
- Local checks raise `InvalidRequestError` with the API's own codes (`account_required`,
  `tracking_key_or_reference_required`, `account_and_candidates_mutually_exclusive`,
  `candidate_accounts_invalid`) and `local=True`. `empty_update` is now `no_valid_fields`.
- A conditional read that finds nothing new raises `NotModifiedError`, a subclass of `APIError` with
  `status=304`.
- `recipient_is_bank` is a boolean. `0` and `1` are still accepted.
- `beneficiaries.import_preview(buckets=[...])` sends `buckets[]`, the array form the API reads.
- `Validation.is_settled` no longer waits on a retry state with `enabled=True` and no cycle.

- The methods that returned a plain `dict` return a typed dictionary: `ValidationStats`,
  `TelegramQueued`, `BinLookup`, `BanxicoStatus`, `BanxicoTimeseries`, `UsageHistory`,
  `UsageBreakdown`, `UsageLimits`, `UsageHeatmap`, `ApiUsage`, `UserProfile`, `SavedRetryPolicy`,
  `DashboardSummary`, `BillingSubscription`, `PublicPlan`, `PlanComparison`, `InsightsOverview`,
  `InsightsTrends`, `InsightsTopBanks`, `InsightsTopBeneficiaries`, `FinanceSummary`,
  `FinancePreview` and `CepExport`. They read as before (`result["field"]`, `.get()`, `in`, iteration,
  `==` against a dictionary, `json.dumps()`) and carry `response_info`. Each field of the spec is also an
  attribute, and `id`, `type`, `links`, `meta` and `raw` keep what the response carries outside the
  attributes. `plans.list_public()` and `finance.list_cep_exports()` return an `ApiList` of them.
- `validations.iter()`, `validations.stats()`, `validations.export()`, `webhooks.iter_deliveries()`,
  `webhooks.export_deliveries()` and `beneficiaries.iter_import_preview()` take explicit, typed
  arguments instead of `**filters`: a misspelled filter raises `TypeError`. `type` and `retry_state`
  are `Literal` types (`ValidationType`, `RetryStateFilter`).
- A `Page` without counters reports the default size of its endpoint: 10 validations, 50 deliveries,
  25 import rows. The iterators ask for the largest page the endpoint gives (50, 100, 100) unless
  `per_page` is passed.
- Identifiers in a path are checked against the format of the spec (UUID for validations, endpoints and
  CEP exports; integer for beneficiaries, imports and rows; digits for a BIN; four digits for a year) and
  percent-encoded. Beneficiary and import identifiers also accept an `int`.
- Requests reuse connections to the API (HTTP keep-alive) through `http.client`, with no new dependency.
  A request that finds the connection closed by the server is sent again on a new one when repeating it
  is safe. A `3xx` is an `APIError`: redirects are not followed.
- `accept_language` accepts `es`, `en` and tags that start with them; any other value raises
  `ConfigurationError`.
- `validate_ocr()` and `enqueue_ocr()` refuse before sending a receipt over 12 MB (`body_too_large`), an
  `image_url` that is not `https` (`invalid_url_scheme`) and an uncompressed PDF of more than 3 pages
  (`pdf_too_many_pages`). A `str` that starts with `data:` goes as a data URI.
- `candidate_accounts` takes at least 2 accounts; the maximum is set by the service.

### Added

- `beneficiaries.validate_account(account, *, type=None)`: checks the structure of a CLABE, a card or a
  mobile number (`GET /beneficiaries/validate-account`) and returns `AccountValidation`. It does not
  consume quota. With it, the SDK covers the 75 M2M operations.
- `Veriko(user_agent_prefix=...)`: text that goes before the `User-Agent`, for a product that wraps the
  SDK. `user_agent_suffix` still goes at the end.
- `build_user_agent(prefix=None, suffix=None)`: returns the `User-Agent` the SDK sends.
- `AsyncVeriko`: the same families and methods as coroutines, and `iter_*` as asynchronous iterators.
  It runs the synchronous client in worker threads, so the SDK keeps no runtime dependencies.
  `scripts/generate_async.py` generates it from `resources.py`.
- `response_info` on every result: `status`, `request_id`, `rate_limit` (`limit`, `remaining`, `reset`),
  `deprecation`, `sunset`, `idempotent_replayed`, `etag` and `retry_after`. Methods that returned a
  `list` or a `dict` return `ApiList` and `ApiDict`, subclasses that behave the same. A response with a
  `Deprecation` header raises one `DeprecationWarning` per process.
- `Veriko.with_options(timeout=, max_retries=, headers=, accept_language=)`: a client with per-call
  overrides. The original is unchanged.
- Errors: `APIError.entries`, `hint`, `docs_url`, `codes`, `field_errors`, `has_code()`, `validation_id`
  and `local`; `GoneError` (`410`), `PayloadTooLargeError` (`413`), `QuotaExceededError`,
  `IdempotencyKeyInProgressError`, `NotModifiedError`, `VerikoTimeoutError` and
  `VerikoConnectionError`. `ConnectionError` exposes `cause` and `timed_out`.
- `finance.wait_for_cep_export()` and `CEP_EXPORT_TERMINAL_STATUSES`.
- `verify_delivery()`: checks `X-Webhook-Signature-Timestamped` and `X-Webhook-Signature-Old` against
  one or several secrets. `verify_webhook()` and `parse_webhook()` also take several secrets.
  `WebhookEvent.delivery_id` and `attempt_timestamp`.
- `WebhookEndpoint.previous_secret_expires_at`.
- `Validation.ocr_result`, `ocr_confidence`, `normalized_data`, `normalization_warnings`,
  `banxico_result`, `enqueued_at`, `processing_started_at`, `expires_at`, `etag_version`,
  `image_path`, `is_masked`, `batch_id`, `batch_position` and `is_pending`. `QueuedValidation` carries
  `enqueued_at`, `expires_at`, `etag_version`, `client_ref` and `retry_state`.
  `ValidationSummary.bank_code` and `purged_at`.
- `recipient_is_bank` on `validations.validate_ocr()`.
- `Decimal` amounts serialize as JSON numbers. `.json` files import with `application/json`.
- Python 3.14 in the classifiers and the CI matrix, an SPDX `license`, an `Issues` URL and examples for
  bulk import, secret rotation, CEP export and the asynchronous client.
- `Transport`, `MultipartFile`, `ResponseInfo` and `RateLimitInfo` are exported.
- `Veriko.close()` and `with Veriko() as client:` close the kept connections; `AsyncVeriko.aclose()` does too.
- `Page.next_page()` requests the next page with the same filters.
- `WEBHOOK_EVENTS`, `WEBHOOK_TEST_EVENT` and `WebhookEventName`: the event types of the spec.
  `WebhookEvent.is_validation_event`, `is_billing_event` and `is_test`.
- `CepExport` with `status`, `from_`, `to`, `item_count`, `size_bytes`, `error_code`, `is_ready`,
  `is_terminal` and `download_path`, and the constants `CEP_EXPORT_STATUSES` and
  `CEP_EXPORT_ERROR_CODES`.
- `Document.headers`, `finance_folio`, `finance_items` and `iter_bytes()`; `CepDocument.headers`.

### Fixed

- The default timeout goes from 30 to 60 seconds. With 30, the client cut a synchronous validation
  that the API can hold for up to 45 seconds before answering `202`.
- A `429` with `quota_exceeded` is no longer retried: the plan quota renews on the next period, not in
  seconds. A `429` with `rate_limit_exceeded` is still retried.
- A network error that is not a `URLError` (`http.client.IncompleteRead`, `BadStatusLine`) raises
  `ConnectionError` instead of escaping, and a TLS failure is not retried.

### Deprecated

Field names from earlier versions keep working for existing integrations and emit a
`DeprecationWarning`; they are not part of the typed API.

## [0.7.2] - 2026-11-09

### Added

- `Validation.account_cross_checked`: `True` when the account queried at Banxico belongs to the client
  (the one sent, one of the candidates or that of an active beneficiary). With `False`, a `valid`
  confirms the transfer exists, but not that it was paid to the expected account. It is `None` when
  the API does not report it.

### Changed

- The bundled spec moves to the public contract `1.181.7`.
- `validate()` and `validate_ocr()` document that the API answers `202` in `processing` when the
  Banxico query takes more than 45 seconds; the verdict is collected with `get()`.
- `amount` documents the cap of 13 integer digits.

## [0.7.1] - 2026-11-08

### Changed

- An authenticated operation checks the shape of the API key before sending the request. If the key
  is not `veriko_` followed by 64 lowercase hexadecimal characters, it raises `ConfigurationError` and
  does not go out to the network. The error message does not include the key.

## [0.7.0] - 2026-11-08

### Added

- Background CEP exports, for ranges that should not be downloaded in a single request:
  `finance.create_cep_export()`, `finance.list_cep_exports()`, `finance.get_cep_export()` and
  `finance.download_cep_export()`.

### Changed

- `send_date` is optional in `validate_transfer()`, `validations.validate()` and `validations.enqueue()`,
  as in the public contract `1.168.2`. If it is missing or unusable, the API tries candidate dates
  against Banxico; it only rejects with `422` (`send_date_required`) when it has no data to guess it.

## [0.6.0] - 2026-10-27

### Changed

- `account.my_profile()` follows the public contract `1.111.0`. The dictionary no longer includes
  `role`, and `permissions` lists the effective permissions with keys such as
  `validations.records.read` instead of `resource:action` pairs. If your integration read `role`, use
  `roles`.

### Added

- `platform_access` in the profile: `True` when the account can enter the admin panel.
- `roles` in the profile: the current roles of the account, each with `key`, `scope_type` (`platform`
  or `organization`) and `scope_id`.

## [0.5.9] - 2026-10-15

### Added

- `Beneficiary.reactivated`: `True` when `beneficiaries.create()` reactivated an archived account with
  the same number (the API answers `200` with `meta.reactivated`); `False` on a new registration and
  on the other reads.

## [0.5.8] - 2026-10-15

### Added

- `catalog.spei_calendar()` (`GET /v1/public/spei-calendar/{year}`): the SPEI non-business-day
  calendar of a year. It returns `SpeiCalendar` with `year`, `non_business_days`, `source` and
  `covered_years`. It accepts `if_none_match`, and its `ETag` is left in `SpeiCalendar.etag`. It does
  not consume quota. A year without a calendar raises `NotFoundError` with
  `spei_calendar_year_not_covered`.
- `validations.validate_ocr()` and `validations.enqueue_ocr()` accept `sender_bank` and
  `receiver_bank`: the 4 or 5 digit SPEI key replaces the bank read from the receipt before querying
  Banxico.
- `validations.list()`, `validations.stats()` and `validations.export()` accept `banks`, up to 50 SPEI
  keys as a list or comma-separated; it filters by sender or receiver bank.
- `webhooks.deliveries()` and `webhooks.export_deliveries()` accept `response_status`,
  `validation_id`, `q`, `from_`, `to`, `min_attempt` and `min_response_time_ms`. `status`,
  `event_type` and `response_status` accept a list, and `webhook_id` a list of up to 20 endpoints.

## [0.5.7] - 2026-10-02

### Added

- `validations.recheck()` (`POST /v1/validations/{id}/recheck`): queries Banxico again for the payment
  status of a `valid` validation created at most 72 hours ago, without consuming quota. It returns
  `RecheckResult` with the validation and with `checked_at`, `changed` and `previous_status`. If
  Banxico reports the return, the validation becomes `returned`. It is not retried on its own.
- `Validation.payment_status` (`PaymentStatus`): the payment status Banxico gave at the last recheck.
  `None` if the response does not carry it.
- `validations.prepare_purge()` and `validations.execute_purge()`: the permanent deletion of a
  validation, in two steps. They return `PurgePreparation` and `PurgeResult`. `execute_purge()` is not
  retried on its own. `Validation.purged_at` marks the purged validation and `Validation.is_purged`
  summarizes it.
- `candidate_accounts` in `validate_transfer()`, `validations.validate()`,
  `validations.validate_ocr()`, `validations.enqueue()` and `validations.enqueue_ocr()`: at least 2
  accounts, up to the maximum the service sets, in a single validation and with a single quota unit. `Validation.candidate_match`
  (`CandidateMatch`) carries the position and the last 4 digits of the account that matched.
- `retain_image` in `validations.validate_ocr()` and `validations.enqueue_ocr()`, and
  `Validation.image_retained`: with `retain_image=False` the platform does not keep the receipt file.
- `WebhookEvent.payment_status` in the `validation.returned` event, which signals when a `valid`
  validation later becomes `returned`. `None` in the other events.

### Changed

- `beneficiary_account` is no longer required when `candidate_accounts` is sent. With neither of the
  two, the SDK raises `InvalidRequestError` with `account_required`, and with both, with
  `account_and_candidates_mutually_exclusive`. In both cases it does not call the API.

## [0.5.6] - 2026-10-01

### Added

- `client_ref` in `validate_transfer()`, `validations.validate()`, `validations.validate_ocr()`,
  `validations.enqueue()` and `validations.enqueue_ocr()`: your own reference of 1 to 64 characters
  that comes back in `Validation.client_ref` and in `ValidationSummary.client_ref`.
- The exact-match `client_ref` filter in `validations.list()`, `validations.iter()`,
  `validations.stats()` and `validations.export()`.
- `Validation.duplicate_of` and `Validation.account_conflict`, with the types `DuplicateOf` and
  `AccountConflict`. They are `None` if the response does not carry them.
- `verify_webhook_timestamped()` checks the `X-Webhook-Signature-Timestamped` header
  (`t=<seconds>,v1=<hex>`): it compares the HMAC-SHA256 of `<t>.<body>` with each `v1` in constant time
  and rejects the delivery when `t` is more than `tolerance` seconds away from the clock, 300 by
  default. `timestamped_signature_from_headers()` finds the header in any of its spellings.
  `verify_webhook()` does not change.
- `parse_webhook()` delivers the `client_ref` in `event.validation` when the validation webhook
  carries it.

### Changed

- `beneficiary_account` is required in `validate_transfer()`, `validations.validate()` and
  `validations.enqueue()`. Without it, the SDK raises `InvalidRequestError` with `account_required` and
  does not call the API.

## [0.5.5] - 2026-09-29

No surface changes. The `422` of the validation documents the per-field codes with which a request
whose data contradict each other is rejected before querying Banxico: `clabe_receptor_mismatch`,
`tarjeta_receptor_mismatch` and `clave_longitud_invalida`. They arrive in `errors[].code` of
`InvalidRequestError`. No `preflight_failed` rejection of that route consumes quota, whether for data
that contradict each other or for a malformed field.

## [0.5.4] - 2026-09-28

### Added

- `UsageSummary.quota_kind` (`cycle` or `trial`) and `UsageSummary.renews`: an account that has not yet
  activated the free plan consumes trial validations, which are not replenished. `None` if the
  response does not carry them.

## [0.5.3] - 2026-09-27

### Changed

- `validations.validate_ocr()` and `validations.enqueue_ocr()` document the receipt as a PDF, of 1 to 3
  pages.
- `validations.image()` accepts a receipt as a PDF and names it with the `.pdf` extension.

## [0.5.2] - 2026-09-26

### Added

- `WebhookEvent.banxico_confirmed`: the amount, the date, the tracking key, both banks and the masked
  beneficiary account, as Banxico confirmed them, when the delivery carries it. `None` otherwise.

## [0.5.1] - 2026-09-22

No API changes. Updates documentation and metadata.

## [0.5.0] - 2026-09-19

### Added

- The 18 M2M operations that were missing: account profile and retry policy, dashboard summary, public
  plans, insights, finance and subscription. Public plans are requested without an `Authorization`
  header.
- The surface test compares the 66 complete operations of the public spec and exercises routes against
  sanitized recordings on a local HTTP server.

### Changed

- `validate_account()` is no longer part of the SDK: its operation is no longer public M2M. In `0.x`
  this removal is incompatible.
- The monthly, by-counterparty, by-bank and accounting reports use `csv` by default, like the public
  contract. `preview` is requested explicitly.

## [0.4.1] - 2026-09-19

First version published on PyPI. It does not change the SDK surface with respect to `0.4.0`.

Product changes are documented in the [API changelog](https://docs.veriko.mx/en/changelog).

## [0.4.0] - 2026-09-18

The surface goes from 48 operations to 49: `beneficiaries` gains the cancellation of an import, and
with it the SDK covers all the machine-to-machine operations of its five families.

### Added

- `beneficiaries.import_cancel()`: cancels an import that has not been confirmed yet
  (`DELETE /beneficiaries/imports/{id}`).

### Changed

- `test_operations.py` requires that the five families carry no machine-to-machine operations without a
  method, instead of allowing an exception.

## [0.3.1] - 2026-09-18

The `validations`, `webhooks` and `catalog` families now follow the public spec. Several shapes of
what the SDK returns change.

### Fixed

- `set_retry_policy()` sends the body wrapped in `retry_policy`, as the spec requires. Before, it sent
  it unwrapped.
- `set_retry_policy()` and `cancel_retries()` return the cycle state (`RetryState`) and not an empty
  validation: the spec response carries only `retry_state`.
- `ValidationSummary` no longer has `has_cep`, `links` and `completed_at`, which a listing item does
  not carry, and gains the fields it does carry (`bank_name`, `amount`, `tracking_key`,
  `retry_state`...).
- `RetryAttempt` reads the fields of the spec: `attempt_number`, `dispatched_at`, `new_status`...
- `Bank` gains `aliases` and loses `short_name`, which the spec does not declare.
- `list(playground=True)` and `with_deleted=True` went as `True`; now `playground` sends `1` and
  `with_deleted` sends `1` or `0`. The transport no longer encodes any boolean as `True`.
- `webhooks.deliveries()` and `export_deliveries()` dropped `status` and `event_type` when an endpoint
  was passed. Now they query the global listing with `endpoint_id`.
- A conditional read that receives `304` raises `APIError` with `status=304`, as the documentation
  said. Before, `get()` returned an empty validation and `banks()` an empty list.
- `webhooks.update(events=[])` raises `events_required` instead of dropping the change.
- `validations.image()` proposes a name with an extension when the response carries no
  `Content-Disposition`.

### Changed

- `stats()`, `bin_lookup()`, `banxico_status()`, `banxico_timeseries()` and `send_cep_to_telegram()`
  return the attributes of the resource, as `client.usage` already does: `stats["total"]`.
- `catalog.banks()` returns a `BankList`, a list of `Bank` with the `ETag` in `etag`.
- `RetryState` gains `max_retries`, `interval_seconds` and `outcomes`.
- `WebhookEndpoint` gains `description`, `secret_hint`, `last_delivery_at` and `updated_at`;
  `WebhookDelivery` gains `endpoint_id`, `endpoint_url`, `validation_id`, `attempt`, `response_body`
  and `next_retry_at`.

### Added

- `description` in `webhooks.create()` and `webhooks.update()`.
- `spec/openapi.yaml`, a copy of the public spec, and three tests that use it: `test_operations.py`
  checks each operation against the spec and requires it to be machine-to-machine;
  `test_models_follow_spec.py` checks that the fields of each model exist in it;
  `test_spec_public.py` checks that the copy meets the API contract.
- The recordings of the `validations`, `webhooks` and `catalog` families are cut from the examples of
  the public spec.

## [0.3.0] - 2026-09-18

The surface goes from 27 operations to 48: the `beneficiaries` and `usage` families are added.

### Added

- `client.beneficiaries`: register, list, change and archive beneficiary accounts; resolution of an
  account in your own list (`lookup`); export as CSV and XLSX; and the bulk import as a cycle
  (`import_template`, `import_start`, `import_status`, `import_preview`, `import_edit_row`,
  `import_remove_row`, `import_commit` and `import_wait`).
- `client.usage`: plan quota (`summary`), monthly history (`history`), breakdown by operation
  (`breakdown`), rate limits (`limits`), heatmap (`heatmap`), API metrics (`api_usage`) and its export
  (`export`).
- The types `Beneficiary`, `BeneficiaryLookup`, `BeneficiaryImportJob`, `BeneficiaryImportRow` and
  `UsageSummary`.
- `BeneficiaryImportJob.is_settled`: the condition `import_wait()` waits for (`preview_ready` or a
  final state).
- `multipart/form-data` file upload in the transport, without runtime dependencies.

### Changed

- The transport encodes query values that are lists as repeated parameters, for the `buckets` filter of
  the preview.
- The test harness understands `PATCH`, which the row correction uses.

## [0.2.0] - 2026-09-18

The surface goes from 3 operations to 27: the complete `validations`, `webhooks` and `catalog`
families.

### Added

- `client.validations`: validation by image (`validate_ocr`), asynchronous mode (`enqueue`,
  `enqueue_ocr`) with `ETag` polling (`wait_for`), paginated listing (`list`, `iter`), statistics,
  export as CSV and XLSX, receipt image, retry attempts, retry policy change, cancellation, removal
  from the history and sending the receipt to Telegram.
- `client.webhooks`: register, list, change and remove endpoints; test event; secret rotation; delivery
  history with pagination and its export.
- `client.catalog`: SPEI bank catalog, issuing bank of a card and Banxico service status with its time
  series.
- `Page` and the `iter*` iterators, which request the next page only when the previous one runs out.
- `Validation.is_settled`: tells a firm verdict from a terminal one that can still change because
  retries are under way. It is what `wait_for()` waits for.
- The types `ValidationSummary`, `WebhookEndpoint`, `WebhookDelivery`, `WebhookTestResult`,
  `RetryAttempt`, `Bank` and `Document`.

### Changed

- `validate_transfer()`, `get_validation()` and `get_cep()` stay at the root of the client and behave
  the same: they now delegate to the corresponding family.
- `Validation` gains the `etag` field, which `get()` fills with the response header.

## [0.1.0] - 2026-09-18

First version of the official Python SDK.

### Added

- `Veriko.validate_transfer()`: validates a SPEI transfer against the Banxico CEP
  (`POST /v1/validate`), with the retry policy of the API (`retry_policy`) and the `Idempotency-Key`
  header.
- `Veriko.get_validation()`: reads a validation by its identifier, with the state of the retry cycle.
- `Veriko.get_cep()`: downloads the official receipt as XML or PDF.
- `verify_webhook()` and `parse_webhook()`: HMAC-SHA256 verification of the signature of a delivery,
  with constant-time comparison.
- Automatic retries of the `5xx`, the `408` and the `429`, with exponential wait and respect for the
  `Retry-After` of the response.
- Exception hierarchy by HTTP status, with the `code` of the API, the complete `errors` array and the
  `request_id`.
- Full typing (`py.typed`), without runtime dependencies. Python 3.9 or later.

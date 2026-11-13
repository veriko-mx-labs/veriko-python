# Contributing

Issues and pull requests are welcome.

## Report a bug or request a change

Open an issue with the [bug report](https://github.com/veriko-mx-labs/veriko-python/issues/new?template=bug_report.yml) or [feature request](https://github.com/veriko-mx-labs/veriko-python/issues/new?template=feature_request.yml) form. Search the open issues first.

A bug report needs the versions you use, the steps that reproduce it and, when the API answered with an error, its `request_id` (`error.request_id` on the raised error). Remove API keys, receipts, account numbers and customer data from anything you paste.

Do not open a public issue for a security vulnerability. Follow [SECURITY.md](SECURITY.md).

## Propose a change

1. For anything larger than a fix, open an issue first so the change can be agreed before you write it.
2. Fork the repository and create a branch from `main`.
3. Make the change. Add or update the tests in `tests/`.
4. Run the checks of this repository:

   ```bash
   pip install -e ".[dev]"
   ruff check .
   ruff format --check .
   mypy
   python -m compileall -q examples
   pytest
   ```

   `ruff format .` fixes the formatting. The tests never reach the API. Use Python 3.9 or later; CI runs 3.9 to 3.14.

5. Update README.md and CHANGELOG.md when the behavior that users see changes.
6. Open a pull request against `main` and fill in the template.

Write commit messages in English, in the imperative, and keep one change in each commit.

`src/veriko/_async_resources.py` is generated from `src/veriko/resources.py`. After you change a public method, run `python scripts/generate_async.py`. A test fails when the generated file is out of date.

The package has no runtime dependencies. Do not add one without opening an issue first.

## The API contract

The API contract is the public spec at <https://docs.veriko.mx/openapi.yaml>. `spec/openapi.yaml` is a copy that a scheduled workflow syncs. Do not edit it by hand. When the API behaves differently from the spec, open an issue.

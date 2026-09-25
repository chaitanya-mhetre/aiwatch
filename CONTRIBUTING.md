# Contributing

1. `uv sync` installs everything, including the provider SDKs used by the tests.
2. `make check` runs ruff, `mypy --strict` and pytest. It must pass before a PR.
3. Tests never touch the network. Provider SDKs get an injected HTTP client with a `MockTransport`
   (see `tests/mock_http.py`). Add a fixture response for any new provider behaviour.
4. Adding a provider: write `src/aiwatch/instrument/<name>.py` with a `CallSpec` (how to read the
   model, usage and stream events) and a `patches()` function, then register it in `instrument/__init__.py`.
5. Prices: never commit real prices without `source` and `checked_on`; `aiwatch prices validate` checks this.
6. Use conventional commit messages (`feat:`, `fix:`, `docs:`, `test:`).

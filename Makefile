.PHONY: check lint fmt type test build bench
check: lint type test
lint:
	uv run ruff check .
	uv run ruff format --check .
fmt:
	uv run ruff format .
	uv run ruff check --fix .
type:
	uv run mypy
test:
	uv run pytest -q
build:
	uv build
bench:
	uv run python benchmarks/overhead.py

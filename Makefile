.PHONY: setup build test format format-check

PY_SRC := src/ tests/

setup:
	uv sync --group dev

build:
	uv build

test:
	uv run pytest tests/

format:
	uv run ruff format $(PY_SRC)
	uv run ruff check --fix $(PY_SRC)
	uv run mypy $(PY_SRC)

format-check:
	uv run ruff format --check $(PY_SRC)
	uv run ruff check $(PY_SRC)
	uv run mypy $(PY_SRC)
	uv run pylint $(PY_SRC)

.PHONY: install run lint format test build

install:
	uv sync --locked

run:
	uv run project

lint:
	uv run ruff check .

format:
	uv run ruff format .

test:
	uv run python -m unittest discover -s tests -v

build:
	uv build
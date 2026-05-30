.PHONY: setup dev test lint format eval clean

PYTHON ?= python3

setup:
	$(PYTHON) -m venv .venv
	. .venv/bin/activate && pip install --upgrade pip
	. .venv/bin/activate && pip install -e ".[dev]"

dev:
	. .venv/bin/activate && python -c "from nova.settings import get_settings; print(f'{get_settings().app_name} foundation ready')"

test:
	. .venv/bin/activate && pytest

lint:
	. .venv/bin/activate && ruff check .

format:
	. .venv/bin/activate && ruff format .

eval:
	. .venv/bin/activate && python -m evals.runners.run_all

clean:
	rm -rf .pytest_cache .ruff_cache build dist *.egg-info nova.db
	find . -type d -name __pycache__ -prune -exec rm -rf {} +

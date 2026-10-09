PY := .venv/bin/python
PIP := .venv/bin/pip
PYTEST := .venv/bin/pytest

.PHONY: venv install run-once fast test test-all validate-sources digest clean

venv:
	python3.12 -m venv .venv
	$(PIP) install --upgrade pip

install: venv
	$(PIP) install -e ".[dev]"

run-once:
	$(PY) -m dealradar run

fast:
	$(PY) -m dealradar run --fast-lane

validate-sources:
	$(PY) -m dealradar validate-sources

digest:
	$(PY) -m dealradar digest

test:
	$(PYTEST) -q

test-all:
	$(PYTEST) -q -m ""

clean:
	rm -rf .pytest_cache __pycache__ dealradar/__pycache__ tests/__pycache__

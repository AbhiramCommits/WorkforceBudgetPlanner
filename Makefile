PYTHON ?= .venv/bin/python

.PHONY: db-up db-down seed test lint

db-up:
	docker compose up -d

db-down:
	docker compose down

seed:
	PYTHONPATH=src $(PYTHON) -m wbp.generate_data

test:
	PYTHONPATH=src $(PYTHON) -m pytest

lint:
	PYTHONPATH=src $(PYTHON) -m ruff check src tests

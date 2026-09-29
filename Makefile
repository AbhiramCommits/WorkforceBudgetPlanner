PYTHON ?= .venv/bin/python

.PHONY: db-up db-down seed analytics test lint

db-up:
	docker compose up -d

db-down:
	docker compose down

seed:
	PYTHONPATH=src $(PYTHON) -m wbp.generate_data

analytics:
	PYTHONPATH=src $(PYTHON) -m wbp.analytics

test:
	PYTHONPATH=src $(PYTHON) -m pytest

lint:
	PYTHONPATH=src $(PYTHON) -m ruff check src tests
